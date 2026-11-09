"""Credentials come from Windows Credential Manager (keyring) or environment variables, never files.

Switches can use several credential sets (e.g. different TACACS+/local accounts per era of kit);
each set is stored under "<service>/switch:<set name>". The WLC account is "<service>/wlc" and the
read-only SolarWinds account (CCTV dashboard) is "<service>/solarwinds".
"""
import os


class CredentialError(RuntimeError):
    pass


def _service(cfg, kind, set_name=None):
    base = cfg["collector"]["credential_service"]
    if kind == "switch":
        return f"{base}/switch:{set_name}"
    return f"{base}/{kind}"  # wlc, solarwinds


def _env_name(kind, set_name=None):
    if kind == "switch":
        return f"NETMON_SWITCH_{set_name.upper().replace('-', '_')}_PASSWORD"
    return f"NETMON_{kind.upper()}_PASSWORD"  # NETMON_WLC_PASSWORD, NETMON_SOLARWINDS_PASSWORD


def _username(cfg, kind):
    return (cfg["cctv"]["solarwinds_username"] if kind == "solarwinds" else cfg["collector"].get("wlc_username")) or ""


def _lookup(cfg, kind, username, set_name=None):
    password = os.environ.get(_env_name(kind, set_name), "")
    if password:
        return password
    try:
        import keyring
        return keyring.get_password(_service(cfg, kind, set_name), username) or ""
    except Exception as exc:  # keyring backend missing or locked
        raise CredentialError(f"Could not read a password from Credential Manager: {exc}")


def switch_sets(cfg):
    """[(set name, username, password)] in config order, only sets that have a stored password."""
    out = []
    for s in cfg["collector"]["switch_credential_sets"]:
        pw = _lookup(cfg, "switch", s["username"], s["name"])
        if pw:
            out.append((s["name"], s["username"], pw))
    if not out:
        raise CredentialError("No switch passwords stored. Run: netmon set-password switch --set <name>  (as the service account)")
    return out


def get(cfg, kind="wlc"):
    """WLC or SolarWinds credentials as (username, password)."""
    username = _username(cfg, kind)
    password = _lookup(cfg, kind, username) if username else ""
    if not username or not password:
        where = "cctv.solarwinds_username" if kind == "solarwinds" else "collector.wlc_username"
        raise CredentialError(f"No {kind} credentials. Set {where} and run: netmon set-password {kind}")
    return username, password


def store(cfg, kind, password, set_name=None):
    import keyring
    col = cfg["collector"]
    if kind == "switch":
        match = [s for s in col["switch_credential_sets"] if s["name"] == set_name]
        if not match:
            names = ", ".join(s["name"] for s in col["switch_credential_sets"])
            raise CredentialError(f"Unknown credential set {set_name!r}. Configured sets: {names}")
        username = match[0]["username"]
    else:
        username = _username(cfg, kind)
        if not username:
            where = "cctv.solarwinds_username" if kind == "solarwinds" else "collector.wlc_username"
            raise CredentialError(f"Set {where} in config.toml first")
    keyring.set_password(_service(cfg, kind, set_name), username, password)
    return username


def status(cfg):
    """[(label, username, stored?)] for check-config; never returns passwords."""
    rows = []
    for s in cfg["collector"]["switch_credential_sets"]:
        rows.append((f"switch set {s['name']}", s["username"], bool(_lookup(cfg, "switch", s["username"], s["name"]))))
    if cfg["wlc"]["hosts"]:
        user = cfg["collector"].get("wlc_username") or ""
        rows.append(("wlc", user, bool(user and _lookup(cfg, "wlc", user))))
    if cfg["cctv"]["enabled"]:
        user = cfg["cctv"]["solarwinds_username"]
        rows.append(("solarwinds (cctv)", user, bool(user and _lookup(cfg, "solarwinds", user))))
    return rows
