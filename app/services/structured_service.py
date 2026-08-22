"""Application service that turns free text into validated structured data."""

import json
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.contracts import JSONSchema, LLMMessage, LLMRole, StructuredOutputProvider
from app.llm.retry import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    complete_structured_with_retry,
)
from app.services import chat_service
from app.services.conversation_service import create_conversation

EXTRACT_SCHEMA: JSONSchema = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
    },
    "required": ["topic", "sentiment"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM_PROMPT = (
    "你是一个信息抽取助手。用户会给你一段文本，"
    "你必须只输出一个 JSON 对象，不能输出解释或额外文本。"
)


def _build_extract_messages(text: str) -> list[LLMMessage]:
    schema_text = json.dumps(EXTRACT_SCHEMA, ensure_ascii=False)
    system_content = (
        f"{EXTRACT_SYSTEM_PROMPT}\n"
        f"输出必须严格匹配以下 JSON Schema：\n{schema_text}"
    )
    return [
        LLMMessage(role=LLMRole.SYSTEM, content=system_content),
        LLMMessage(role=LLMRole.USER, content=text),
    ]


async def extract_topic_sentiment(
    *,
    provider: StructuredOutputProvider,
    session: AsyncSession,
    redis: Redis,
    user_id: int,
    text: str,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> tuple[int, dict[str, Any]]:
    """Save the user input first, then run validated structured extraction.

    If the structured call fails, the user message remains committed. This
    mirrors the Chat service's short-transaction semantic: user input is a fact
    that must not be silently discarded because a downstream LLM call failed.
    """
    conversation = await create_conversation(
        session=session,
        redis=redis,
        title=text[:30],
        user_id=user_id,
    )
    conversation_id = conversation.id

    await chat_service.save_message(
        session=session,
        conversation_id=conversation_id,
        role="user",
        content=text,
        is_complete=True,
    )

    messages = _build_extract_messages(text)
    result = await complete_structured_with_retry(
        provider,
        messages,
        EXTRACT_SCHEMA,
        retry_policy,
    )
    return conversation_id, result
