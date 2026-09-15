from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from http_app import create_app  # noqa: E402
from mobile_auth_bridge import (  # noqa: E402
    BridgeError,
    FileSecretStore,
    MemorySecretStore,
    MobileAuthBridge,
    MobileAuthSessionStore,
    PixivTokenExchange,
    parse_form_body,
)


VALID_VERIFIER = "A" * 43


class FakeExchange:
    def __init__(self) -> None:
        self.calls = 0

    def exchange(self, code: str, code_verifier: str) -> dict:
        self.calls += 1
        assert code == "auth-code"
        assert code_verifier == VALID_VERIFIER
        return {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "expires_in": 3600,
            "user": {"id": 123456, "name": "trial-user"},
        }


def test_session_complete_stores_refresh_token_but_never_returns_it() -> None:
    secrets_store = MemorySecretStore()
    exchange = FakeExchange()
    bridge = MobileAuthBridge(exchange=exchange, secrets_store=secrets_store)

    session = bridge.create_session()
    result = bridge.complete_session(
        session_id=session["session_id"],
        code="auth-code",
        code_verifier=VALID_VERIFIER,
    )

    assert result == {"status": "CONNECTED", "source": "pixiv", "user_id": "123456"}
    assert secrets_store.refresh_token == "refresh-secret"
    assert exchange.calls == 1
    assert "refresh-secret" not in json.dumps(result)
    assert "access-secret" not in json.dumps(result)


def test_session_is_one_time() -> None:
    bridge = MobileAuthBridge(exchange=FakeExchange(), secrets_store=MemorySecretStore())
    session = bridge.create_session()
    bridge.complete_session(
        session_id=session["session_id"],
        code="auth-code",
        code_verifier=VALID_VERIFIER,
    )
    with pytest.raises(BridgeError) as exc:
        bridge.complete_session(
            session_id=session["session_id"],
            code="auth-code",
            code_verifier=VALID_VERIFIER,
        )
    assert exc.value.code == "SESSION_ALREADY_USED"


def test_expired_session_is_rejected_without_exchange() -> None:
    now = [1000.0]
    sessions = MobileAuthSessionStore(ttl_seconds=5, clock=lambda: now[0])
    exchange = FakeExchange()
    bridge = MobileAuthBridge(
        sessions=sessions,
        exchange=exchange,
        secrets_store=MemorySecretStore(),
    )
    session = bridge.create_session()
    now[0] += 6

    with pytest.raises(BridgeError) as exc:
        bridge.complete_session(
            session_id=session["session_id"],
            code="auth-code",
            code_verifier=VALID_VERIFIER,
        )
    assert exc.value.code == "SESSION_EXPIRED"
    assert exchange.calls == 0


def test_file_secret_store_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "pixiv-secret.json"
    store = FileSecretStore(path)
    store.save_pixiv_refresh_token("refresh-secret", "123")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pixiv"]["refresh_token"] == "refresh-secret"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_http_contract_omits_tokens() -> None:
    bridge = MobileAuthBridge(exchange=FakeExchange(), secrets_store=MemorySecretStore())
    client = TestClient(create_app(bridge))
    session_id = bridge.create_session()["session_id"]

    completed = client.post(
        "/auth/pixiv/mobile/complete",
        json={
            "session_id": session_id,
            "code": "auth-code",
            "code_verifier": VALID_VERIFIER,
        },
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "CONNECTED"
    serialized = completed.text.lower()
    assert "refresh-secret" not in serialized
    assert "access-secret" not in serialized


def test_real_exchange_request_shape_without_network() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["form"] = parse_form_body(request.content)
        captured["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(
            200,
            json={
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 3600,
                "user": {"id": 42},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    exchange = PixivTokenExchange(client)
    result = exchange.exchange("code-value", VALID_VERIFIER)

    assert result["refresh_token"] == "r"
    assert captured["url"] == "https://oauth.secure.pixiv.net/auth/token"
    assert captured["form"]["grant_type"] == ["authorization_code"]
    assert captured["form"]["code"] == ["code-value"]
    assert captured["form"]["code_verifier"] == [VALID_VERIFIER]
    assert captured["form"]["redirect_uri"] == [
        "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"
    ]
    assert captured["user_agent"].startswith("PixivIOSApp/")
