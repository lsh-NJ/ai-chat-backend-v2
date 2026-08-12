import os
import httpx
import pytest
import jwt 
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

from app.repositories.user_repository import UserRepository
from app.repositories.conversation_repository import ConversationRepository
from app.db.session import AsyncSessionFactory
from app.models.user import User
from app.core.deps import get_current_user
from app.core.security import create_access_token, decode_access_token
from app.core.exceptions import InvalidTokenError

load_dotenv()
JWT_SECRET = os.environ["JWT_SECRET"]

async def test_create_token_valid(client):
    register_response: httpx.Response = await client.post(
        "/auth/register",
        json = {
            "username": "111", 
            "password": "88888888"
        }
    )
    assert register_response.status_code == 201

    body = register_response.json()
    token = create_access_token(int(body["id"]))
    user_id: int = decode_access_token(token)
    assert user_id == int(body["id"])


async def test_token_expired(client):
    register_response: httpx.Response = await client.post(
        "/auth/register",
        json = {
            "username": "111", 
            "password": "88888888"
        }
    )
    assert register_response.status_code == 201

    body = register_response.json()
    token = create_access_token(int(body["id"]), -1)

    with pytest.raises(InvalidTokenError) as e:
        user_id: int = decode_access_token(token)
        assert str(e) == "认证超时"


async def test_fake_secret_token(client):
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


async def test_fake_id_token(client):
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
async def test_auth(client):
    user_out1 = await client.post(
        "/auth/register",
        json={
            "username": "AAA",
            "password": "88888888",
        }
    )

    user_out2 = await client.post(
        "/auth/register",
        json = {
            "username": "BBB",
            "password": "999999999",
        }
    )

    assert user_out1.status_code == 201
    assert user_out2.status_code == 201

    user1 = user_out1.json()
    user_id1 = user1["id"]
    user2 = user_out2.json()
    user_id2 = user2["id"]

    async with AsyncSessionFactory() as session:
        conversation_repository = ConversationRepository(session)
        conversation_id1 = await conversation_repository.create(
            title="用户 1",
            user_id = user_id1,
        )
        conversation_id2 = await conversation_repository.create(
            title="用户 2",
            user_id=user_id2,
        )
        await session.commit()

    access_token1 = await client.post(
        "/auth/login",
        data={
            "username": "AAA",
            "password": "88888888",
        }
    )

    access_token2 = await client.post(
        "/auth/login",
        data={
            "username": "BBB",
            "password": "999999999",
        }
    )

    assert access_token1.status_code == 200
    assert access_token2.status_code == 200

    body1 = access_token1.json()
    token1 = body1["access_token"]
    body2 = access_token2.json()
    token2 = body2["access_token"]

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