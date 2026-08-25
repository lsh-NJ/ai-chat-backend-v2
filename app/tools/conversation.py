"""只读 conversation 工具实现。"""

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.contracts import ToolDefinition
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.tools.exceptions import ToolResourceUnavailableError
from app.tools.execution import (
    RegisteredTool,
    ToolExecutionContext,
    ToolResult,
)

GET_CONVERSATION_MESSAGES_DEFINITION = ToolDefinition(
    name="get_conversation_messages",
    description="读取当前用户拥有的指定会话中最近的消息",
    parameters={
        "type": "object",
        "properties": {
            "conversation_id": {
                "type": "integer",
                "minimum": 1,
            }
        },
        "required": ["conversation_id"],
        "additionalProperties": False,
    },
)


class GetConversationMessagesHandler:
    """通过资源 ID 与可信 user ID 联合查询，避免 IDOR。"""

    def __init__(self, session: AsyncSession, *, message_limit: int = 20) -> None:
        if message_limit <= 0:
            raise ValueError("message_limit must be positive")
        self._conversation_repository = ConversationRepository(session)
        self._message_repository = MessageRepository(session)
        self._message_limit = message_limit

    async def __call__(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        # Executor 已根据注册 schema 校验；cast 只向类型检查器表达该不变量。
        conversation_id = cast(int, arguments["conversation_id"])
        conversation = await self._conversation_repository.get_by_id_for_user(
            conversation_id=conversation_id,
            user_id=context.user_id,
        )
        if conversation is None:
            raise ToolResourceUnavailableError("conversation is unavailable")

        messages = await self._message_repository.list_by_conversation(
            conversation_id=conversation_id,
            limit=self._message_limit,
        )
        return {
            "conversation_id": conversation_id,
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "is_complete": message.is_complete,
                }
                for message in messages
            ],
        }


def create_conversation_messages_tool(
    session: AsyncSession,
    *,
    message_limit: int = 20,
) -> RegisteredTool:
    return RegisteredTool(
        definition=GET_CONVERSATION_MESSAGES_DEFINITION,
        handler=GetConversationMessagesHandler(
            session,
            message_limit=message_limit,
        ),
    )
