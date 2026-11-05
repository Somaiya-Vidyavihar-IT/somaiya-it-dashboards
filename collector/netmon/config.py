"""Load and validate the collector configuration (TOML, kept outside the repository)."""
import ipaddress
import os
import re
import tomllib

PLATFORMS = {"auto", "cisco_ios", "cisco_nxos", "cisco_s300"}
DEFAULT_CONFIG = os.environ.get("NETMON_CONFIG", r"C:\netmon\config.toml")


class ConfigError(ValueError):
    pass


def load(path=None):
    path = path or DEFAULT_CONFIG
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8-sig")  # Notepad / PowerShell 5.1 may add a UTF-8 BOM
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}. Copy config.example.toml and edit it.")
    except UnicodeDecodeError:
        raise ConfigError(f"{path} is not UTF-8 text. Save it in Notepad with Encoding: UTF-8.")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        hint = ""
        if "escape" in str(exc).lower():
            hint = " Windows paths must use single quotes, e.g. data_dir = 'C:\\netmon\\data'."
        raise ConfigError(f"{path} has a mistake: {exc}.{hint}")
    cfg = validate(raw)
    cfg["_path"] = os.path.abspath(path)
    return cfg


def validate(raw):
    col = dict(raw.get("collector", {}))
    col.setdefault("data_dir", r"C:\netmon\data")
    col.setdefault("credential_service", "netmon")
    col.setdefault("switch_username", "")
    col.setdefault("wlc_username", "")
    col.setdefault("ssh_workers", 20)
    col.setdefault("scan_concurrency", 512)
    col.setdefault("connect_timeout", 1.5)
    col.setdefault("command_timeout", 60)
    col.setdefault("keep_days", 30)
    col.setdefault("error_delta_threshold", 100)
    col.setdefault("cpu_threshold", 80)
    col.setdefault("poe_threshold", 80)
    col.setdefault("core_hostname_pattern", "")
    col.setdefault("seeds", [])
    col.setdefault("exclude", [])
    col.setdefault("aruba_ouis", [])
    sets = list(col.get("switch_credential_sets") or [])
    if not sets and col["switch_username"]:
        sets = [{"name": "default", "username": col["switch_username"]}]
    if not sets:
        raise ConfigError("Define collector.switch_credential_sets (or collector.switch_username)")
    names = set()
    for i, s in enumerate(sets):
        name, user = str(s.get("name", "")).strip(), str(s.get("username", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name) or name in names:
            raise ConfigError(f"switch_credential_sets[{i}].name must be unique letters, digits, _ or -")
        if not user:
            raise ConfigError(f"switch_credential_sets[{i}].username is required")
        names.add(name)
    col["switch_credential_sets"] = [{"name": str(s["name"]).strip(), "username": str(s["username"]).strip()} for s in sets]
    for key in ("ssh_workers", "scan_concurrency", "keep_days"):
        if not isinstance(col[key], int) or col[key] < 1:
            raise ConfigError(f"collector.{key} must be a positive integer")
    if col["core_hostname_pattern"]:
        try:
            re.compile(col["core_hostname_pattern"])
        except re.error as exc:
            raise ConfigError(f"collector.core_hostname_pattern is not a valid regex: {exc}")

    subnets = []
    for i, s in enumerate(raw.get("subnets", [])):
        try:
            net = ipaddress.ip_network(s["cidr"], strict=False)
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"subnets[{i}].cidr is invalid: {exc}")
        if net.version != 4:
            raise ConfigError(f"subnets[{i}]: only IPv4 management subnets are supported")
        if net.prefixlen < 16:
            raise ConfigError(f"subnets[{i}]: {net} is larger than a /16; split it")
        platform = s.get("platform", "auto")
        if platform not in PLATFORMS:
            raise ConfigError(f"subnets[{i}].platform must be one of {sorted(PLATFORMS)}")
        subnets.append({
            "cidr": str(net), "net": net,
            "institute": str(s.get("institute", "")).strip() or "Unassigned",
            "platform": platform, "sweep": bool(s.get("sweep", True)),
        })
    if not subnets:
        raise ConfigError("At least one [[subnets]] entry is required")
    # Most specific subnet wins when ranges overlap.
    subnets.sort(key=lambda s: s["net"].prefixlen, reverse=True)
    try:
        col["exclude_nets"] = [ipaddress.ip_network(x, strict=False) for x in col["exclude"]]
    except ValueError as exc:
        raise ConfigError(f"collector.exclude has an invalid subnet: {exc}")
    _check_ips(col["seeds"], "collector.seeds (the Nexus core management IPs)")

    wlc = dict(raw.get("wlc", {}))
    wlc.setdefault("hosts", [])
    _check_ips(wlc["hosts"], "wlc.hosts")

    srv = dict(raw.get("server", {}))
    srv.setdefault("host", "0.0.0.0")
    srv.setdefault("port", 8443)
    srv.setdefault("cert_file", "")
    srv.setdefault("key_file", "")
    srv.setdefault("google_client_id", "")
    srv.setdefault("allowed_origins", ["https://somaiya-vidyavihar-it.github.io"])
    srv.setdefault("allowed_domains", ["somaiya.edu"])
    srv.setdefault("network_team", [])
    srv.setdefault("python", "")
    srv["network_team"] = sorted({e.strip().lower() for e in srv["network_team"] if e.strip()})
    srv["allowed_domains"] = [d.strip().lower() for d in srv["allowed_domains"] if d.strip()]
    return {"collector": col, "subnets": subnets, "wlc": wlc, "server": srv}


def _check_ips(values, where):
    for value in values:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ConfigError(f"{where}: {value!r} is not an IP address. Replace the <...> placeholder.")


def subnet_for(cfg, ip):
    """Return the most specific configured subnet containing ip, or None."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for s in cfg["subnets"]:
        if addr in s["net"]:
            return s
    return None


def in_scope(cfg, ip):
    s = subnet_for(cfg, ip)
    if not s:
        return False
    addr = ipaddress.ip_address(ip)
    return not any(addr in n for n in cfg["collector"]["exclude_nets"])
