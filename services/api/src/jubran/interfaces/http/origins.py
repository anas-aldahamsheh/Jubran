"""Which browser origins may use the API and its live-update sockets."""
import re
from typing import Optional

from jubran.settings import settings

# Next.js prints a LAN URL for testing on another device. Allow private network
# origins on the dev port only while DEBUG is enabled; production stays strict.
_LAN_DEV_ORIGIN_REGEX = (
    r"^https?://(?:localhost|127\.0\.0\.1|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}):3001$"
)


def allowed_origin_regex() -> Optional[str]:
    return _LAN_DEV_ORIGIN_REGEX if settings.DEBUG else None


def is_allowed_origin(origin: Optional[str]) -> bool:
    """True for the site's own frontend. Browsers always send Origin on WebSocket handshakes."""
    if not origin:
        # Non-browser clients send no Origin; they still need a valid login to connect.
        return True
    if origin in settings.CORS_ORIGINS:
        return True
    pattern = allowed_origin_regex()
    return bool(pattern and re.fullmatch(pattern, origin))
