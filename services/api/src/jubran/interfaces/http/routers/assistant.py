"""Assistant HTTP Router for AI Text Agent and Chat (REQ-004, REQ-012, REQ-014, REQ-015, REQ-019, REQ-021)."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import CustomerSessionModel, TableSessionModel, PhysicalTableModel
from jubran.infrastructure.db.transactions import end_transaction
from jubran.interfaces.http.dependencies import get_required_customer_context
from jubran.application.ai.agent_service import (
    AssistantService, AssistantUnavailableError, clear_session_history, get_session_history, interface_language,
)
from jubran.application.ai.provider_errors import user_error_message
from jubran.application.ai.transcription_service import SpeechTranscriptionService
from jubran.application.ai.voice_service import VoiceAssistantService
from jubran.application.ai.content_state import content_version
from jubran.interfaces.http.rate_limits import per_guest, public_demo_assistant_day
from jubran.domain.exceptions import DomainException
from jubran.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/assistant", tags=["AI Assistant"])
# Streamed turns run on their own, so a guest closing the page never cuts a turn in half.
_running_turns: set = set()


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    language: Optional[str] = Field(default=None, max_length=10)


class ChatResponse(BaseModel):
    response: str
    tool_calls: List[Dict[str, Any]] = []
    draft: Optional[Dict[str, Any]] = None
    action: Optional[Dict[str, Any]] = None
    # What the turn really did (requests, complaint, rating, order changes), written by the server.
    done: List[Dict[str, Any]] = []
    table_number: str
    content_version: str


@router.post("/chat", response_model=ChatResponse,
             dependencies=[per_guest("assistant_customer", "assistant_table"), public_demo_assistant_day()])
async def chat_with_assistant(
    req: ChatMessageRequest,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    """Customer chat with the Jubran AI dining assistant."""
    cust_session, table_session, physical_table = ctx
    table_number = physical_table.table_number  # read before the turn commits/rolls back
    result = await AssistantService.chat(
        db=db,
        customer_session_id=cust_session.id,
        table_session_id=table_session.id,
        table_number=table_number,
        user_message=req.message,
        language=req.language
    )
    return _chat_response(result, table_number)


def _chat_response(result: Dict[str, Any], table_number: str) -> ChatResponse:
    # Tool results hold internal search data the page does not use: names only, except while debugging.
    tool_calls = result.get("tool_calls", [])
    if not settings.DEBUG:
        tool_calls = [{"tool": call.get("tool")} for call in tool_calls]
    return ChatResponse(
        response=result.get("response", ""),
        tool_calls=tool_calls,
        draft=result.get("draft"),
        action=result.get("action"),
        done=result.get("done") or [],
        table_number=table_number,
        content_version=result["content_version"],
    )


@asynccontextmanager
async def _own_session(request: Request):
    """A database session for a streamed turn alone: it outlives the response if the guest leaves."""
    source = request.app.dependency_overrides.get(get_db_session, get_db_session)
    async with asynccontextmanager(source)() as session:
        yield session


def _event(kind: str, data: Dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.post("/chat/stream", dependencies=[per_guest("assistant_customer", "assistant_table"), public_demo_assistant_day()])
async def stream_chat_with_assistant(
    req: ChatMessageRequest,
    request: Request,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    """The same turn as /chat, shown as it happens (server-sent events): "step" (what the assistant
    is doing now), "text" (its reply while the model writes it; "reset" drops what was written),
    then "done" (the same fields as /chat) or "error" ({code, message, details})."""
    cust_session, table_session, physical_table = ctx
    customer_session_id, table_session_id = cust_session.id, table_session.id
    table_number = physical_table.table_number
    await end_transaction(db)
    events: asyncio.Queue = asyncio.Queue()

    def failed(code: str, message: str, message_en: Optional[str] = None) -> None:
        error: Dict[str, Any] = {"code": code, "message": message}
        if message_en:
            error["details"] = {"message_en": message_en}
        events.put_nowait(("error", error))

    async def run_turn() -> None:
        try:
            async with _own_session(request) as turn_db:
                result = await AssistantService.chat(
                    turn_db, customer_session_id, table_session_id, table_number, req.message, req.language,
                    events=lambda kind, data: events.put_nowait((kind, data)))
            events.put_nowait(("done", _chat_response(result, table_number).model_dump(mode="json")))
        except AssistantUnavailableError as exc:
            failed(str(exc), user_error_message(str(exc)), user_error_message(str(exc), "en"))
        except DomainException as exc:
            failed(exc.code, exc.message, (exc.details or {}).get("message_en"))
        except Exception:
            logger.exception("A streamed assistant turn failed")
            failed("AI_FAILED", user_error_message("AI_FAILED"), user_error_message("AI_FAILED", "en"))
        finally:
            events.put_nowait(None)

    task = asyncio.create_task(run_turn())
    _running_turns.add(task)
    task.add_done_callback(_running_turns.discard)

    async def stream():
        while (item := await events.get()) is not None:
            yield _event(*item)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


class ConfirmOrderRequest(BaseModel):
    language: Optional[str] = Field(default=None, max_length=10)


@router.post("/confirm-order", dependencies=[per_guest("order_submit")])
async def confirm_order_from_assistant(
    req: ConfirmOrderRequest,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session),
):
    """The guest pressed "confirm" on the summary the assistant showed (text or voice).

    Submits exactly that reviewed summary, bound to its basket version, without
    relying on the model to interpret a "yes".
    """
    cust_session, table_session, physical_table = ctx
    table_number = physical_table.table_number
    return await AssistantService.confirm_pending_order(
        db, cust_session.id, table_session.id, table_number, req.language)


@router.post("/confirm-amendment", dependencies=[per_guest("order_submit")])
async def confirm_amendment_from_assistant(
    req: ConfirmOrderRequest,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session),
):
    """The guest pressed "confirm" on a change to a sent order that the assistant showed."""
    cust_session, table_session, physical_table = ctx
    return await AssistantService.confirm_pending_amendment(
        db, cust_session.id, table_session.id, physical_table.table_number, req.language)


@router.get("/history")
async def get_chat_history(
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve session-scoped conversational history (REQ-021)."""
    cust_session, _, _ = ctx
    version = await content_version(db)
    return {"history": await get_session_history(db, cust_session.id), "content_version": version}


@router.post("/clear-history")
async def clear_chat_history(
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Clear chat memory for the active table visit."""
    cust_session, _, _ = ctx
    await clear_session_history(db, cust_session.id)
    return {"success": True}


@router.post("/dictation", dependencies=[per_guest("assistant_audio"), public_demo_assistant_day()])
async def transcribe_dictation(
    audio: UploadFile = File(...),
    language: Optional[str] = None,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    """Convert speech audio to text for editing in chat textarea without auto-sending (REQ-017)."""
    transcript = await SpeechTranscriptionService.transcribe_audio(audio, db, interface_language(language))
    return {"transcript": transcript}


# --- Voice mode (REQ-016): live streaming over /ws/assistant/voice, or turn by turn here ---

class VoiceTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    language: Optional[str] = Field(default=None, max_length=10)


@router.post("/voice/session")
async def bootstrap_voice_session(
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    """Tell the web app which voice mode to use: "live" (streaming socket) or "turns"."""
    _, _, physical_table = ctx
    return await VoiceAssistantService.voice_capabilities(db, physical_table.table_number)


@router.post("/voice/turn", dependencies=[per_guest("assistant_customer", "assistant_table"), public_demo_assistant_day()])
async def voice_speech_turn(
    req: VoiceTurnRequest,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    """One typed turn in voice mode (same model, tools and guards as text chat)."""
    cust_session, table_session, physical_table = ctx
    table_number = physical_table.table_number
    return await VoiceAssistantService.interact_speech_turn(
        db=db, customer_session_id=cust_session.id, table_session_id=table_session.id,
        table_number=table_number, text_message=req.message, language=req.language)


@router.post("/voice/audio-turn",
             dependencies=[per_guest("assistant_audio", "assistant_customer", "assistant_table"),
                           public_demo_assistant_day()])
async def voice_audio_speech_turn(
    audio: UploadFile = File(...),
    language: Optional[str] = None,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    """One spoken turn: the server transcribes the recording, then answers like text chat."""
    cust_session, table_session, physical_table = ctx
    table_number = physical_table.table_number
    return await VoiceAssistantService.interact_speech_turn(
        db=db, customer_session_id=cust_session.id, table_session_id=table_session.id,
        table_number=table_number, audio_file=audio, language=language)
