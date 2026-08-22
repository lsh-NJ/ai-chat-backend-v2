from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.exceptions import (
    ConversationNotFoundError,
    LLMConfigurationError,
    LLMInputTooLongError,
    LLMServiceError,
    LLMTimeoutError,
)
from app.db.redis import get_redis
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
)
from app.services import chat_service

router = APIRouter(tags=["chat"])

def to_http_exception(e: LLMServiceError) -> HTTPException:
    if isinstance(e, LLMInputTooLongError):
        return HTTPException(status_code=422, detail=str(e))

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
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ChatResponse:
    try:
        chat_model = await chat_service.chat(
            session=session,
            conversation_id=chat_request.conversation_id,
            message=chat_request.message,
            provider=request.app.state.llm_provider,
            context_selector=request.app.state.context_selector,
            user_id=current_user.id,
            redis=redis,
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
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        conversation_id, chat_stream_result = await chat_service.chat_stream(
            conversation_id=chat_request.conversation_id,
            message=chat_request.message,
            provider=request.app.state.llm_provider,
            context_selector=request.app.state.context_selector,
            session=session,
            user_id=current_user.id,
            redis=redis,
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
