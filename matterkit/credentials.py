"""The person's own API keys, kept outside every case folder.

A case folder may be zipped up and handed to a lawyer, so a key never goes in one. It lives in
the person's settings folder (~/.config/matter-kit, or $XDG_CONFIG_HOME/matter-kit, or
$MATTER_KIT_CONFIG), readable by that user only. COURTLISTENER_TOKEN, when set, wins over the
stored key. Nothing here prints or logs a key; status reports only whether one is set and where.
"""
import os
import re

TOKEN_FILE = "courtlistener-token"
_TOKEN = re.compile(r"[A-Za-z0-9]{20,100}")  # CourtListener keys are 40 hex characters


def config_dir() -> str:
    explicit = os.environ.get("MATTER_KIT_CONFIG", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "matter-kit")


def _token_path() -> str:
    return os.path.join(config_dir(), TOKEN_FILE)


def _stored() -> str:
    try:
        with open(_token_path(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def courtlistener_token() -> str:
    """The key to send, or "" when there is none."""
    return os.environ.get("COURTLISTENER_TOKEN", "").strip() or _stored()


def courtlistener_token_source() -> str | None:
    """'environment', 'settings' or None. Never the key itself."""
    if os.environ.get("COURTLISTENER_TOKEN", "").strip():
        return "environment"
    return "settings" if _stored() else None


def save_courtlistener_token(token: str) -> str:
    """Store the key (owner-only file). Returns where it went. Refuses anything that isn't key-shaped,
    so a pasted sentence or a stray line break is caught before it is saved."""
    token = (token or "").strip()
    if token.lower().startswith("token "):
        token = token[6:].strip()  # pasted with the header word in front
    if not _TOKEN.fullmatch(token):
        raise ValueError("That doesn't look like a CourtListener API key. Copy the key from "
                         "courtlistener.com/profile/api/ (40 letters and numbers) and paste only that.")
    d = config_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = _token_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    os.chmod(path, 0o600)  # an older file keeps its mode through O_TRUNC
    return path


def forget_courtlistener_token() -> bool:
    try:
        os.remove(_token_path())
        return True
    except FileNotFoundError:
        return False
