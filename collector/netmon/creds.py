"""Credentials come from Windows Credential Manager (keyring) or environment variables, never files."""
import os


class CredentialError(RuntimeError):
    pass


def get(cfg, kind):
    """kind is 'switch' or 'wlc'. Returns (username, password)."""
    col = cfg["collector"]
    username = col.get(f"{kind}_username") or (col["switch_username"] if kind == "wlc" else "")
    password = os.environ.get(f"NETMON_{kind.upper()}_PASSWORD", "")
    if not password:
        try:
            import keyring
            password = keyring.get_password(f"{col['credential_service']}/{kind}", username) or ""
        except Exception as exc:  # keyring backend missing or locked
            raise CredentialError(f"Could not read the {kind} password from Credential Manager: {exc}")
    if not username or not password:
        raise CredentialError(
            f"No {kind} credentials. Run: python -m netmon set-password {kind}  (as the service account)")
    return username, password


def store(cfg, kind, password):
    import keyring
    col = cfg["collector"]
    username = col.get(f"{kind}_username") or col["switch_username"]
    keyring.set_password(f"{col['credential_service']}/{kind}", username, password)
    return username
