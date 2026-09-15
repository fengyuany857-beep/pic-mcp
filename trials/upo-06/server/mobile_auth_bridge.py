from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import parse_qs

import httpx


PIXIV_TOKEN_URL = "https://oauth.secure.pixiv.net/auth/token"
PIXIV_CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
PIXIV_CLIENT_SECRET = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
PIXIV_REDIRECT_URI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"
PIXIV_USER_AGENT = "PixivIOSApp/7.13.3 (iOS 14.6; iPhone13,2)"


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass
class AuthSession:
    session_id: str
    expires_at: float
    consumed: bool = False


class MobileAuthSessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._sessions: dict[str, AuthSession] = {}

    def create(self) -> AuthSession:
        session = AuthSession(
            session_id=secrets.token_urlsafe(32),
            expires_at=self.clock() + self.ttl_seconds,
        )
        self._sessions[session.session_id] = session
        return session

    def consume(self, session_id: str) -> AuthSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise BridgeError("SESSION_NOT_FOUND", "Unknown mobile auth session.", http_status=404)
        if session.consumed:
            raise BridgeError("SESSION_ALREADY_USED", "Mobile auth session was already used.", http_status=409)
        if self.clock() >= session.expires_at:
            raise BridgeError("SESSION_EXPIRED", "Mobile auth session expired.", http_status=410)
        session.consumed = True
        return session


class SecretStore(Protocol):
    def save_pixiv_refresh_token(self, refresh_token: str, user_id: str | None) -> None: ...


class MemorySecretStore:
    def __init__(self) -> None:
        self.refresh_token: str | None = None
        self.user_id: str | None = None

    def save_pixiv_refresh_token(self, refresh_token: str, user_id: str | None) -> None:
        self.refresh_token = refresh_token
        self.user_id = user_id


class FileSecretStore:
    """Single-user trial secret store. Writes atomically and chmods the file to 0600."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def save_pixiv_refresh_token(self, refresh_token: str, user_id: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pixiv": {
                "refresh_token": refresh_token,
                "user_id": user_id,
                "updated_at_unix": int(time.time()),
            }
        }
        fd, tmp_name = tempfile.mkstemp(prefix=".pixiv-secret-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.write("\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


class TokenExchange(Protocol):
    def exchange(self, code: str, code_verifier: str) -> dict: ...


class PixivTokenExchange:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=15.0)

    def exchange(self, code: str, code_verifier: str) -> dict:
        response = self.client.post(
            PIXIV_TOKEN_URL,
            data={
                "client_id": PIXIV_CLIENT_ID,
                "client_secret": PIXIV_CLIENT_SECRET,
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "include_policy": "true",
                "redirect_uri": PIXIV_REDIRECT_URI,
            },
            headers={
                "User-Agent": PIXIV_USER_AGENT,
                "App-OS": "ios",
                "App-OS-Version": "14.6",
            },
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise BridgeError(
                "PIXIV_TOKEN_RESPONSE_INVALID",
                f"Pixiv token endpoint returned non-JSON HTTP {response.status_code}.",
                http_status=502,
            ) from exc

        if response.status_code >= 400 or "refresh_token" not in body:
            message = "Pixiv rejected the authorization code exchange."
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict) and error.get("message"):
                    message = str(error["message"])
            raise BridgeError("PIXIV_TOKEN_EXCHANGE_FAILED", message, http_status=502)
        return body


class MobileAuthBridge:
    def __init__(
        self,
        *,
        sessions: MobileAuthSessionStore | None = None,
        exchange: TokenExchange | None = None,
        secrets_store: SecretStore | None = None,
    ) -> None:
        self.sessions = sessions or MobileAuthSessionStore()
        self.exchange = exchange or PixivTokenExchange()
        self.secrets_store = secrets_store or MemorySecretStore()

    def create_session(self) -> dict:
        session = self.sessions.create()
        return {
            "session_id": session.session_id,
            "expires_in_seconds": self.sessions.ttl_seconds,
            "status": "PENDING",
        }

    def complete_session(self, *, session_id: str, code: str, code_verifier: str) -> dict:
        if not session_id:
            raise BridgeError("SESSION_ID_REQUIRED", "session_id is required.")
        if not code or len(code) > 4096 or any(ch.isspace() for ch in code):
            raise BridgeError("INVALID_AUTHORIZATION_CODE", "Invalid Pixiv authorization code.")
        if not 43 <= len(code_verifier) <= 128 or any(ch.isspace() for ch in code_verifier):
            raise BridgeError("INVALID_CODE_VERIFIER", "PKCE code_verifier must be 43-128 URL-safe characters.")

        # Consume before exchange: Pixiv authorization codes are one-time and short-lived.
        self.sessions.consume(session_id)
        token = self.exchange.exchange(code, code_verifier)
        refresh_token = str(token["refresh_token"])
        user = token.get("user") if isinstance(token, dict) else None
        user_id = None
        if isinstance(user, dict) and user.get("id") is not None:
            user_id = str(user["id"])

        self.secrets_store.save_pixiv_refresh_token(refresh_token, user_id)

        # Deliberately omit access_token and refresh_token from the response.
        return {
            "status": "CONNECTED",
            "source": "pixiv",
            "user_id": user_id,
        }


def parse_form_body(body: bytes) -> dict[str, list[str]]:
    """Small helper used only by tests to inspect form-encoded token exchange requests."""
    return parse_qs(body.decode("utf-8"))
