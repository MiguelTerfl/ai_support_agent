"""Human confirmation of actions with side effects (e.g. creating a ticket).

The agent NEVER runs side-effect actions on its own: it returns a pending action
signed with HMAC. The client shows it, the user confirms, and only then does the
/api/confirm endpoint run it. It is stateless: the token carries the action, and
the signature stops the browser from modifying the arguments.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, NamedTuple


class InvalidToken(ValueError):
    pass


class VerifiedAction(NamedTuple):
    tool: str
    args: dict[str, Any]
    sig: str
    exp: int
    lang: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_action(secret: str, tool: str, args: dict[str, Any], ttl: int, now: float | None = None, lang: str = "en") -> str:
    """The language travels signed in the token so /api/confirm replies in the customer's language."""
    now = time.time() if now is None else now
    payload = json.dumps({"t": tool, "a": args, "exp": int(now + ttl), "l": lang}, separators=(",", ":"), sort_keys=True).encode()
    body = _b64(payload)
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_action(secret: str, token: str, now: float | None = None) -> VerifiedAction:
    """Returns (tool, args, signature, expiry, language). Raises InvalidToken if anything doesn't add up."""
    now = time.time() if now is None else now
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise InvalidToken("format") from exc
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise InvalidToken("signature")
    try:
        payload = json.loads(_unb64(body))
        tool, args, exp = payload["t"], payload["a"], int(payload["exp"])
        lang = payload.get("l", "en")
    except Exception as exc:
        raise InvalidToken("content") from exc
    if exp < now:
        raise InvalidToken("expired")
    return VerifiedAction(tool, args, sig, exp, lang if lang in ("es", "en") else "en")


class UsedTokens:
    """Prevents reusing a token (double click or replay) while it is valid. In memory: 1 instance."""

    def __init__(self) -> None:
        self._used: dict[str, int] = {}

    def consume(self, sig: str, exp: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        for k in [k for k, e in self._used.items() if e < now]:
            self._used.pop(k, None)
        if sig in self._used:
            return False
        self._used[sig] = exp
        return True

    def release(self, sig: str) -> None:
        """If the action failed (e.g. the DB was down), allow retrying with the same token."""
        self._used.pop(sig, None)
