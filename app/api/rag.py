"""RAG 问答 API（Week 16 Day 1-2）。

边界：
- 认证由 `get_current_user` 完成；
- `tenant_id` 由服务端根据认证用户推导，客户端请求体不包含该字段；
- Retriever 由组合根创建，API 层不关心 pgvector / FTS 细节；
- 模型回答引用了不存在的编号时，校验失败按上游响应错误返回 502。
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import to_http_exception
from app.core.deps import get_current_user
from app.core.exceptions import (
    LLMServiceError,
    RagCitationError,
    RagUploadTooLargeError,
    RagUploadValidationError,
)
from app.db.session import get_db
from app.models.user import User
from app.rag.answer import RagAnswer
from app.rag.ingestion_job import IngestionJob
from app.repositories.rag_ingestion_job_repository import (
    RagIngestionJobRepository,
)
from app.schemas.rag import (
    RagAnswerResponse,
    RagCitationOut,
    RagDocumentUploadResponse,
    RagIngestionJobResponse,
    RagQueryRequest,
)
from app.services.rag_ingestion_service import RagIngestionService
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
        refusal_reason=answer.refusal_reason,
    )


def _to_upload_response(job: IngestionJob) -> RagDocumentUploadResponse:
    assert job.created_at is not None
    return RagDocumentUploadResponse(
        job_id=job.job_id,
        status=job.status,
        filename=job.filename,
        size_bytes=job.size_bytes,
        content_sha256=job.content_sha256,
        created_at=job.created_at,
    )


def _to_job_response(job: IngestionJob) -> RagIngestionJobResponse:
    assert job.created_at is not None
    assert job.updated_at is not None
    return RagIngestionJobResponse(
        job_id=job.job_id,
        filename=job.filename,
        content_type=job.content_type,
        size_bytes=job.size_bytes,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post(
    "/rag/documents",
    response_model=RagDocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_rag_document(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RagDocumentUploadResponse:
    """接收文件并创建异步 ingestion 任务，不在请求内执行解析和 embedding。"""
    data = await file.read()
    try:
        service = RagIngestionService(
            session,
            request.app.state.rag_upload_storage,
        )
        job = await service.create_job(
            tenant_id=user_tenant_id(current_user),
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=data,
        )
    except RagUploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except RagUploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _to_upload_response(job)


@router.get(
    "/rag/documents/ingestion-jobs/{job_id}",
    response_model=RagIngestionJobResponse,
)
async def get_rag_ingestion_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RagIngestionJobResponse:
    """按租户读取 ingestion 任务状态，供前端轮询。"""
    repository = RagIngestionJobRepository(
        session,
        tenant_id=user_tenant_id(current_user),
    )
    job = await repository.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="ingestion job 不存在",
        )
    return _to_job_response(job)


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
            request.app.state.rag_embedder,
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
    except RagCitationError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    return _to_response(answer)
