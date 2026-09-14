"""RAG 问答 API（Week 16 Day 1）。

边界：
- 认证由 `get_current_user` 完成；
- `tenant_id` 由服务端根据认证用户推导，客户端请求体不包含该字段；
- Retriever 由组合根创建，API 层不关心 pgvector / FTS 细节。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import to_http_exception
from app.core.deps import get_current_user
from app.core.exceptions import LLMServiceError
from app.db.session import get_db
from app.models.user import User
from app.rag.answer import RagAnswer
from app.schemas.rag import (
    RagAnswerResponse,
    RagCitationOut,
    RagQueryRequest,
)
from app.services.rag_service import RagQueryService

router = APIRouter(tags=["rag"])


def user_tenant_id(user: User) -> str:
    """Week 16 的临时租户边界：每个认证用户一个独立知识库命名空间。

    Week 17 会演进为正式的 tenant / organization 模型；替换时必须保持
    “客户端不能指定 tenant_id”这条不变量。
    """
    return f"user:{user.id}"


def _to_response(answer: RagAnswer) -> RagAnswerResponse:
    return RagAnswerResponse(
        answer=answer.answer,
        citations=[
            RagCitationOut(
                label=citation.label,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                source=citation.source,
                start=citation.start,
                end=citation.end,
                score=citation.score,
            )
            for citation in answer.citations
        ],
        retrieved_chunk_ids=list(answer.retrieved_chunk_ids),
        refused=answer.refused,
    )


@router.post("/rag/query", response_model=RagAnswerResponse)
async def rag_query(
    rag_request: RagQueryRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RagAnswerResponse:
    try:
        retriever = request.app.state.rag_retriever_factory(
            session,
            user_tenant_id(current_user),
        )
        service = RagQueryService(
            retriever=retriever,
            provider=request.app.state.llm_provider,
            context_builder=request.app.state.rag_context_builder,
        )
        answer = await service.ask(
            rag_request.question,
            top_k=rag_request.top_k,
        )
    except LLMServiceError as exc:
        raise to_http_exception(exc) from exc

    return _to_response(answer)
