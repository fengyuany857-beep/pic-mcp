from __future__ import annotations

import os

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mobile_auth_bridge import BridgeError, FileSecretStore, MobileAuthBridge


def _json_error(exc: BridgeError) -> JSONResponse:
    return JSONResponse(
        {"status": "ERROR", "error": exc.code, "message": str(exc)},
        status_code=exc.http_status,
    )


def create_app(bridge: MobileAuthBridge | None = None) -> Starlette:
    if bridge is None:
        secret_path = os.environ.get(
            "PICMCP_PIXIV_SECRET_FILE",
            "~/.picmcp/pixiv-secret.json",
        )
        bridge = MobileAuthBridge(secrets_store=FileSecretStore(secret_path))

    async def complete_session(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            result = await run_in_threadpool(
                bridge.complete_session,
                session_id=str(payload.get("session_id", "")),
                code=str(payload.get("code", "")),
                code_verifier=str(payload.get("code_verifier", "")),
            )
            return JSONResponse(result)
        except BridgeError as exc:
            return _json_error(exc)
        except Exception:
            # Never leak callback codes, PKCE verifiers, or token endpoint bodies.
            return JSONResponse(
                {
                    "status": "ERROR",
                    "error": "INTERNAL_ERROR",
                    "message": "Mobile Pixiv auth bridge failed.",
                },
                status_code=500,
            )

    return Starlette(
        debug=False,
        routes=[
            Route("/auth/pixiv/mobile/complete", complete_session, methods=["POST"]),
        ],
    )


app = create_app()
