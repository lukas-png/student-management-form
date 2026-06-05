import base64
import json
import time

import httpx


class StuMgmtAuth:
    """Sparkyservice JWT with automatic refresh (60 s safety margin before exp)."""

    def __init__(self, auth_url: str, user: str, password: str) -> None:
        self._auth_url = auth_url
        self._user = user
        self._password = password
        self._token: str | None = None
        self._expires_at: float = 0.0

    def token(self) -> str:
        """Return a valid JWT; re-authenticates transparently when expired."""
        if self._token is None or time.time() >= self._expires_at:
            self._refresh()
        assert self._token is not None
        return self._token

    def invalidate(self) -> None:
        """Force re-authentication on next token() call (called after 401)."""
        self._expires_at = 0.0

    def _refresh(self) -> None:
        resp = httpx.post(
            self._auth_url,
            json={"username": self._user, "password": self._password},
            timeout=10.0,
        )
        resp.raise_for_status()
        raw = _extract_jwt(resp.json())
        self._token = raw
        self._expires_at = _exp_from_jwt(raw) - 60.0


def _extract_jwt(data: dict) -> str:
    """Pull the JWT out of the Sparkyservice auth response.

    Handles the nested `AuthenticationInfoDto` shape ({"token": {"token": ...}})
    as well as flat variants ({"token": "..."} / {"accessToken": ...}).
    """
    tok = data.get("token")
    if isinstance(tok, dict):
        tok = tok.get("token")
    if not isinstance(tok, str) or not tok:
        tok = data.get("accessToken") or data.get("jwtToken")
    if not isinstance(tok, str) or not tok:
        raise ValueError("no JWT found in Sparkyservice auth response")
    return tok


def _exp_from_jwt(token: str) -> float:
    """Extract exp claim from JWT payload without signature verification."""
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return float(payload["exp"])
