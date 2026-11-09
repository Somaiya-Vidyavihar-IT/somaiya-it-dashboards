"""Load and validate the collector configuration (TOML, kept outside the repository)."""
import ipaddress
import os
import re
import tomllib

PLATFORMS = {"auto", "cisco_ios", "cisco_nxos", "cisco_s300"}
# IP phones that advertise LLDP "bridge" and so look like switches (seen live: Fanvil X1SG, X301G and
# X3SG, named after their model). Matching neighbours are phones: never crawled, never logged in to.
DEFAULT_PHONE_MODELS = ["X1SG", "X301G", "X3SG"]
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
    col.setdefault("phone_models", list(DEFAULT_PHONE_MODELS))
    models = col["phone_models"]
    if not isinstance(models, list) or not all(isinstance(m, str) and m.strip() for m in models):
        raise ConfigError('collector.phone_models must be a list of model names, e.g. ["X1SG", "X301G"]')
    # Whole-word, case-insensitive match on the neighbour's name, platform or description.
    col["phone_re"] = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(m.strip()) for m in models) + r")(?![A-Za-z0-9])",
                                 re.I) if models else None
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
    return {"collector": col, "subnets": subnets, "wlc": wlc, "server": srv, "cctv": validate_cctv(raw.get("cctv", {}))}


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DEFAULT_NVR_NAME_PATTERN = r"(?<![A-Za-z])NVR(?![A-Za-z])"  # SAV-PHY-NVR-1, D.ed_NVR, KJSAC-NVR


def validate_cctv(raw):
    """[cctv]: cameras and NVRs read from SolarWinds. Disabled (enabled = False) while solarwinds_host is blank."""
    cc = dict(raw or {})
    cc.setdefault("solarwinds_host", "")
    cc.setdefault("solarwinds_port", 17774)
    cc.setdefault("solarwinds_username", "")
    cc.setdefault("solarwinds_cert_sha256", "")   # blank = trust the certificate seen first, then insist on it
    cc.setdefault("viewers", [])
    cc.setdefault("poll_minutes", 5)
    cc.setdefault("escalate_hours", 48)
    cc.setdefault("type_property", "Device_Type")
    cc.setdefault("institute_property", "InstituteName")
    cc.setdefault("camera_types", ["CCTV", "Dahua CCTV", "Camera"])
    cc.setdefault("nvr_types", ["NVR", "Dahua NVR"])
    cc.setdefault("nvr_name_pattern", DEFAULT_NVR_NAME_PATTERN)
    cc.setdefault("camera_ranges", [])
    cc.setdefault("ipam_ranges", [])
    cc["solarwinds_host"] = str(cc["solarwinds_host"]).strip()
    cc["enabled"] = bool(cc["solarwinds_host"])
    if cc["enabled"] and "<" in cc["solarwinds_host"]:
        raise ConfigError("cctv.solarwinds_host still has a <...> placeholder")
    for key, low, high in (("solarwinds_port", 1, 65535), ("poll_minutes", 1, 1440), ("escalate_hours", 1, 24 * 90)):
        if not isinstance(cc[key], int) or not low <= cc[key] <= high:
            raise ConfigError(f"cctv.{key} must be a whole number from {low} to {high}")
    for key in ("type_property", "institute_property"):
        cc[key] = str(cc[key]).strip()
        if cc[key] and not IDENTIFIER.fullmatch(cc[key]):
            raise ConfigError(f"cctv.{key} must be a SolarWinds custom property name (letters, digits, _)")
    for key in ("camera_types", "nvr_types", "viewers"):
        if not isinstance(cc[key], list) or not all(isinstance(v, str) for v in cc[key]):
            raise ConfigError(f'cctv.{key} must be a list of text values, e.g. ["a", "b"]')
    cc["camera_types"] = sorted({v.strip().lower() for v in cc["camera_types"] if v.strip()})
    cc["nvr_types"] = sorted({v.strip().lower() for v in cc["nvr_types"] if v.strip()})
    cc["viewers"] = sorted({v.strip().lower() for v in cc["viewers"] if v.strip()})
    try:
        cc["nvr_name_re"] = re.compile(cc["nvr_name_pattern"], re.I) if cc["nvr_name_pattern"] else None
    except re.error as exc:
        raise ConfigError(f"cctv.nvr_name_pattern is not a valid regex: {exc}")
    for key in ("camera_ranges", "ipam_ranges"):
        try:
            nets = [ipaddress.ip_network(x, strict=False) for x in cc[key]]
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"cctv.{key} has an invalid subnet: {exc}")
        if any(n.version != 4 for n in nets):
            raise ConfigError(f"cctv.{key}: only IPv4 subnets are supported")
        cc[key + "_nets"] = nets
    pin = str(cc["solarwinds_cert_sha256"]).replace(":", "").strip().lower()
    if pin and not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ConfigError("cctv.solarwinds_cert_sha256 must be the 64 hex characters of the SHA-256 fingerprint")
    cc["solarwinds_cert_sha256"] = pin
    return cc


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
