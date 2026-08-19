import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from dotenv import load_dotenv

from app.core.deps import get_current_user
from app.core.exceptions import InvalidTokenError
from app.core.security import create_access_token, decode_access_token
from app.db.session import AsyncSessionFactory
from app.models.user import User
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.user_repository import UserRepository

load_dotenv()
JWT_SECRET = os.environ["JWT_SECRET"]

async def test_create_token_valid():
    token = create_access_token(1)
    user_id: int = decode_access_token(token)
    assert user_id == 1


async def test_token_expired():
    token = create_access_token(1, -1)

    with pytest.raises(InvalidTokenError) as exc_info:
        decode_access_token(token)

    assert str(exc_info.value) == "认证超时"


async def test_fake_secret_token():
    pyload = {
        "sub": "1", 
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }

    fake_token = jwt.encode(
        pyload,
        "37105b0c30fec67c72f89f7e1c9d78dees4e0f8b8d8a95d3c34e7vb610b3ce11",
        # JWT_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(fake_token)


async def test_fake_id_token(fresh_schema):
    pyload = {
        "sub": "-1", 
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }

    token = jwt.encode(
        pyload,
        JWT_SECRET,
        algorithm="HS256",
    )

    user_id = decode_access_token(token)
    async with AsyncSessionFactory() as session:
        user_repository = UserRepository(session)
        user = await user_repository.get_by_id(user_id)
        assert user is None


# 检查是否存在越权访问
async def test_auth(create_test_user):
    user1 = await create_test_user("AAA")
    user2 = await create_test_user("BBB")

    async with AsyncSessionFactory() as session:
        conversation_repository = ConversationRepository(session)
        await conversation_repository.create(
            title="用户 1",
            user_id=user1.id,
        )
        await conversation_repository.create(
            title="用户 2",
            user_id=user2.id,
        )
        await session.commit()

    token1 = create_access_token(user1.id)
    token2 = create_access_token(user2.id)

    async with AsyncSessionFactory() as session:
        userA: User = await get_current_user(token1, session)
        userB: User = await get_current_user(token2, session)

    async with AsyncSessionFactory() as session:
        conversation_repository = ConversationRepository(session)
        conversations1 = await conversation_repository.list_by_user_id(userA.id)
        assert len(conversations1) == 1
        assert conversations1[0].user_id == userA.id
        assert conversations1[0].title == "用户 1"

        conversations2 = await conversation_repository.list_by_user_id(userB.id)
        assert len(conversations2) == 1
        assert conversations2[0].user_id == userB.id
        assert conversations2[0].title == "用户 2"
