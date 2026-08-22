from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import to_http_exception
from app.core.deps import get_current_user
from app.core.exceptions import LLMServiceError
from app.db.session import get_db
from app.llm.contracts import StructuredOutputProvider
from app.models.user import User
from app.schemas.structured import (
    StructuredExtractRequest,
    StructuredExtractResponse,
)
from app.services import structured_service

router = APIRouter(tags=["structured"])


@router.post("/structured/extract", response_model=StructuredExtractResponse)
async def structured_extract(
    body: StructuredExtractRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> StructuredExtractResponse:
    provider = request.app.state.llm_provider
    if not isinstance(provider, StructuredOutputProvider):
        raise HTTPException(
            status_code=501,
            detail="当前模型供应商不支持结构化输出",
        )

    try:
        conversation_id, result = await structured_service.extract_topic_sentiment(
            provider=provider,
            session=session,
            redis=request.app.state.redis,
            user_id=current_user.id,
            text=body.text,
        )
    except LLMServiceError as exc:
        raise to_http_exception(exc) from exc

    return StructuredExtractResponse(
        conversation_id=conversation_id,
        topic=result["topic"],
        sentiment=result["sentiment"],
    )
