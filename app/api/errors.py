"""应用层 LLM 错误到 HTTP 响应的公共映射。"""

from fastapi import HTTPException

from app.core.exceptions import (
    LLMConfigurationError,
    LLMInputTooLongError,
    LLMServiceError,
    LLMTimeoutError,
)


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
