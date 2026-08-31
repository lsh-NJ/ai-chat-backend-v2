import json

import pytest

from app.db.session import AsyncSessionFactory
from app.llm.contracts import ToolCall
from app.models.conversation import Conversation
from app.models.message import Message
from app.tools.conversation import (
    GET_CONVERSATION_MESSAGES_DEFINITION,
    create_conversation_messages_tool,
)
from app.tools.exceptions import ToolResourceUnavailableError
from app.tools.execution import ToolExecutionContext, ToolExecutor, ToolRegistry


async def test_conversation_tool_allows_owner_and_blocks_other_user(
    fresh_schema,
    create_test_user,
) -> None:
    owner = await create_test_user("tool-owner")
    other_user = await create_test_user("tool-other-user")
    async with AsyncSessionFactory() as session:
        conversation = Conversation(title="私有会话", user_id=owner.id)
        session.add(conversation)
        await session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content="只有 owner 可以读取",
                is_complete=True,
            )
        )
        await session.commit()
        conversation_id = conversation.id

    call = ToolCall(
        call_id="call-1",
        name="get_conversation_messages",
        arguments={"conversation_id": conversation_id},
    )
    async with AsyncSessionFactory() as session:
        executor = ToolExecutor(
            ToolRegistry([create_conversation_messages_tool(session)]),
            timeout_seconds=1,
        )

        with pytest.raises(ToolResourceUnavailableError):
            await executor.execute(
                call,
                ToolExecutionContext(user_id=other_user.id),
            )

        result = await executor.execute(
            call,
            ToolExecutionContext(user_id=owner.id),
        )

    assert result.call_id == "call-1"
    assert json.loads(result.content) == {
        "conversation_id": conversation_id,
        "messages": [
            {
                "id": 1,
                "role": "user",
                "content": "只有 owner 可以读取",
                "is_complete": True,
            }
        ],
    }


async def test_conversation_tool_does_not_distinguish_missing_from_unauthorized(
    fresh_schema,
    create_test_user,
) -> None:
    user = await create_test_user("tool-user")
    async with AsyncSessionFactory() as session:
        executor = ToolExecutor(
            ToolRegistry([create_conversation_messages_tool(session)]),
            timeout_seconds=1,
        )
        with pytest.raises(
            ToolResourceUnavailableError,
            match="conversation is unavailable",
        ):
            await executor.execute(
                ToolCall(
                    call_id="call-1",
                    name="get_conversation_messages",
                    arguments={"conversation_id": 999999},
                ),
                ToolExecutionContext(user_id=user.id),
            )


def test_conversation_tool_schema_does_not_accept_user_id() -> None:
    """模型可见的 schema 不包含 user_id，可信身份只能由应用注入。"""

    properties = GET_CONVERSATION_MESSAGES_DEFINITION.parameters["properties"]
    required = GET_CONVERSATION_MESSAGES_DEFINITION.parameters["required"]
    additional_properties = GET_CONVERSATION_MESSAGES_DEFINITION.parameters[
        "additionalProperties"
    ]

    assert "user_id" not in properties
    assert "user_id" not in required
    assert additional_properties is False
