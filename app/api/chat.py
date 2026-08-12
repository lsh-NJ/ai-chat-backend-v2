from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services import chat_service
from app.schemas.chat import (
    ChatResponse,
    ChatRequest,
)
from app.core.exceptions import (
    LLMConfigurationError,
    LLMServiceError,
    LLMTimeoutError,
    ConversationNotFoundError
)

router = APIRouter(tags=["chat"])

def to_http_exception(e: LLMServiceError) -> HTTPException:
    if isinstance(e, LLMConfigurationError):
        return HTTPException(
            status_code=500,
            detail=str(e),
        )

    if isinstance(e, LLMTimeoutError):
        return HTTPException(
            status_code=504,
            detail=str(e),
        )

    return HTTPException(
        status_code=502,
        detail=str(e),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    chat_request: ChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ChatResponse:
    try:
        chat_model = await chat_service.chat(
            session=session,
            conversation_id=chat_request.conversation_id,
            message=chat_request.message,
            client=request.app.state.http_client,
            user_id=current_user.id,
        )

    except ConversationNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        ) from e

    except LLMServiceError as exc:
        raise to_http_exception(exc) from exc

    return ChatResponse(
        reply=chat_model.reply,
        conversation_id=chat_model.conversation_id,
    )


@router.post("/chat/stream")
async def chat_stream(
    chat_request: ChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        conversation_id, chat_stream_result = await chat_service.chat_stream(
            conversation_id=chat_request.conversation_id,
            message=chat_request.message,
            client=request.app.state.http_client,
            session=session,
            user_id=current_user.id,
        )

    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except LLMServiceError as exc:
        raise to_http_exception(exc) from exc

    return StreamingResponse(
        chat_stream_result,
        media_type="text/plain; charset=utf-8",
        headers={
            "X-Conversation-Id": str(conversation_id),
            "Cache-Control": "no-cache",
        },
    )
