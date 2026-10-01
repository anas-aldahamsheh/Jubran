"""Authenticated WebSocket endpoints.

Admin feed: the only requirement is a valid administrator login (the session the
browser receives after signing in with the admin email and password). Customer
feed: the browser's own customer session must belong to the requested table visit.
Rejected handshakes are closed before they are accepted, so nothing is sent.
"""
import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from jubran.application import rate_limiter
from jubran.application.ai import live_voice
from jubran.application.auth_service import AuthService
from jubran.infrastructure.auth.tokens import hash_token
from jubran.domain.exceptions import DomainException
from jubran.application.session_service import SessionService
from jubran.infrastructure.db.session import async_session_factory, get_db_session
from jubran.interfaces.http.origins import is_allowed_origin
from jubran.interfaces.websocket.gateway import ws_manager
from jubran.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Close codes the voice page understands.
CLOSE_VOICE_UNAVAILABLE = 4409   # live voice can't run now: use turn-by-turn voice
CLOSE_VOICE_RATE_LIMITED = 4429


def _admin_token(websocket: WebSocket) -> Optional[str]:
    return websocket.cookies.get(settings.ADMIN_SESSION_COOKIE_NAME)


async def _keep_open(websocket: WebSocket) -> None:
    # Clients only listen; incoming frames are keep-alive pings.
    while True:
        await websocket.receive_text()


@router.websocket("/ws/admin")
async def admin_live_feed(websocket: WebSocket, db: AsyncSession = Depends(get_db_session)):
    token = _admin_token(websocket)
    session = None
    if is_allowed_origin(websocket.headers.get("origin")):
        session = await AuthService.get_admin_session(db, token)
    # Release the database connection; the socket may stay open for hours.
    await db.close()
    if session is None or token is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws_manager.connect_admin(websocket, hash_token(token), session.expires_at)
    try:
        await _keep_open(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect_admin(websocket)


@router.websocket("/ws/customer/{table_session_id}")
async def customer_live_feed(websocket: WebSocket, table_session_id: str,
                             db: AsyncSession = Depends(get_db_session)):
    context = None
    if is_allowed_origin(websocket.headers.get("origin")):
        token = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
        context = await SessionService.get_customer_session_by_token(db, token) if token else None
    await db.close()
    if context is None or context[1].id != table_session_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws_manager.connect_customer(websocket, table_session_id)
    try:
        await _keep_open(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect_customer(websocket, table_session_id)


async def _voice_frames(websocket: WebSocket) -> AsyncIterator[live_voice.ClientFrame]:
    """Browser -> server: binary PCM16 audio frames, or small JSON control messages."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        data, text = message.get("bytes"), message.get("text")
        if data is not None:
            if 0 < len(data) <= live_voice.MAX_AUDIO_FRAME_BYTES and len(data) % 2 == 0:
                yield live_voice.ClientFrame(audio=data)
            continue
        try:
            payload = json.loads(text or "")
        except ValueError:
            continue
        kind = payload.get("type") if isinstance(payload, dict) else None
        if kind == "text" and str(payload.get("text", "")).strip():
            yield live_voice.ClientFrame(text=str(payload["text"]).strip()[:1000])
        elif kind == "confirm_order":
            yield live_voice.ClientFrame(confirm_order=True)
        elif kind == "ping":
            # The phone checks the line is still alive (answered with "pong").
            yield live_voice.ClientFrame(ping=True)
        elif kind == "end":
            # Why the phone ended the call (the guest, quiet, left the page...), for the server's notes.
            reason = "".join(ch for ch in str(payload.get("reason") or "")[:24] if ch.isalnum() or ch == "_")
            yield live_voice.ClientFrame(end=True, reason=reason)
            return


@router.websocket("/ws/assistant/voice")
async def assistant_live_voice(websocket: WebSocket, lang: Optional[str] = None, resume: bool = False,
                               db: AsyncSession = Depends(get_db_session)):
    """Live voice call for the guest whose visit cookie opened the socket (`resume`: the phone
    reconnects to a call whose line dropped, so the waiter says it is back instead of greeting)."""
    context = None
    if is_allowed_origin(websocket.headers.get("origin")):
        token = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
        context = await SessionService.get_customer_session_by_token(db, token) if token else None
    if context is None:
        await db.close()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    customer, visit, table = context
    customer_id, visit_id, table_number = customer.id, visit.id, table.table_number
    limited = False
    provider = None
    unavailable_reason = None
    try:
        await rate_limiter.hit(db, "assistant_audio", customer_id)
        provider = await live_voice.live_voice_provider(db)
    except rate_limiter.RateLimitExceeded:
        limited = True
    except live_voice.LiveVoiceUnavailable as exc:
        unavailable_reason = str(exc) or "LIVE_VOICE_UNAVAILABLE"
    finally:
        await db.close()  # the call may last minutes; never hold a connection for it

    await websocket.accept()

    async def emit_json(payload: dict) -> None:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(payload)

    async def emit_audio(chunk: bytes) -> None:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_bytes(chunk)

    close_code = status.WS_1000_NORMAL_CLOSURE
    try:
        if limited:
            await emit_json({"type": "error", "code": "RATE_LIMITED"})
            close_code = CLOSE_VOICE_RATE_LIMITED
            return
        if provider is None:
            await emit_json({"type": "error", "code": unavailable_reason})
            close_code = CLOSE_VOICE_UNAVAILABLE
            return
        call = live_voice.LiveVoiceCall(
            customer_session_id=customer_id, table_session_id=visit_id, table_number=table_number,
            language=lang, provider=provider, session_factory=async_session_factory, resume=resume,
            emit_json=emit_json, emit_audio=emit_audio)
        await call.run(_voice_frames(websocket))
    except live_voice.LiveVoiceUnavailable:
        await emit_json({"type": "error", "code": "LIVE_VOICE_UNAVAILABLE"})
        close_code = CLOSE_VOICE_UNAVAILABLE
    except DomainException as exc:  # e.g. ASSISTANT_BUSY: a text reply is still being written
        await emit_json({"type": "error", "code": exc.code, "message": exc.message})
        close_code = CLOSE_VOICE_UNAVAILABLE
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("Live voice call failed")
        await emit_json({"type": "error", "code": "LIVE_VOICE_FAILED"})
        close_code = CLOSE_VOICE_UNAVAILABLE
    finally:
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=close_code)
            except Exception:
                pass
