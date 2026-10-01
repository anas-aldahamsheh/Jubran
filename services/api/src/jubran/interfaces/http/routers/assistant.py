"""Assistant HTTP Router for AI Text Agent and Chat (REQ-004, REQ-012, REQ-014, REQ-015, REQ-019, REQ-021)."""
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import CustomerSessionModel, TableSessionModel, PhysicalTableModel
from jubran.interfaces.http.dependencies import get_required_customer_context
from jubran.application.ai.agent_service import AssistantService, clear_session_history, get_session_history, interface_language
from jubran.application.ai.transcription_service import SpeechTranscriptionService
from jubran.application.ai.voice_service import VoiceAssistantService
from jubran.application.ai.content_state import content_version
from jubran.interfaces.http.rate_limits import per_guest
from jubran.settings import settings

router = APIRouter(prefix="/api/v1/assistant", tags=["AI Assistant"])


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    language: Optional[str] = Field(default=None, max_length=10)


class ChatResponse(BaseModel):
    response: str
    tool_calls: List[Dict[str, Any]] = []
    draft: Optional[Dict[str, Any]] = None
    action: Optional[Dict[str, Any]] = None
    table_number: str
    content_version: str


@router.post("/chat", response_model=ChatResponse,
             dependencies=[per_guest("assistant_customer", "assistant_table")])
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

    # Tool results hold internal search data the page does not use: names only, except while debugging.
    tool_calls = result.get("tool_calls", [])
    if not settings.DEBUG:
        tool_calls = [{"tool": call.get("tool")} for call in tool_calls]
    return ChatResponse(
        response=result.get("response", ""),
        tool_calls=tool_calls,
        draft=result.get("draft"),
        action=result.get("action"),
        table_number=table_number,
        content_version=result["content_version"],
    )


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


@router.post("/dictation", dependencies=[per_guest("assistant_audio")])
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


@router.post("/voice/turn", dependencies=[per_guest("assistant_customer", "assistant_table")])
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
             dependencies=[per_guest("assistant_audio", "assistant_customer", "assistant_table")])
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
