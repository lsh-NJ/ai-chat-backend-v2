"""把自由文本转换为经过校验的结构化数据的应用服务。"""

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
    """先保存用户输入，再执行经过校验的结构化抽取。

    如果结构化调用失败，用户消息仍然保持已提交状态。这与 Chat 服务的
    短事务语义一致：用户输入已经发生，不能因为下游 LLM 调用失败而被静默丢弃。
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
