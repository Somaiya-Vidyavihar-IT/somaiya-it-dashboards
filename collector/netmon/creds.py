"""Credentials come from Windows Credential Manager (keyring) or environment variables, never files.

Switches can use several credential sets (e.g. different TACACS+/local accounts per era of kit);
each set is stored under "<service>/switch:<set name>". The WLC account is "<service>/wlc".
"""
import os


class CredentialError(RuntimeError):
    pass


def _service(cfg, kind, set_name=None):
    base = cfg["collector"]["credential_service"]
    return f"{base}/switch:{set_name}" if kind == "switch" else f"{base}/wlc"


def _env_name(kind, set_name=None):
    return f"NETMON_SWITCH_{set_name.upper().replace('-', '_')}_PASSWORD" if kind == "switch" else "NETMON_WLC_PASSWORD"


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
    """WLC credentials as (username, password)."""
    col = cfg["collector"]
    username = col.get("wlc_username") or ""
    password = _lookup(cfg, "wlc", username) if username else ""
    if not username or not password:
        raise CredentialError("No WLC credentials. Set collector.wlc_username and run: netmon set-password wlc")
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
        username = col.get("wlc_username") or ""
        if not username:
            raise CredentialError("Set collector.wlc_username in config.toml first")
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
    return rows
