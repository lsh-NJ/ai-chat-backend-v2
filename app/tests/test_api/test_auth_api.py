import pytest
import httpx
from sqlalchemy import select

from app.main import app
from app.db.session import AsyncSessionFactory, get_db
from app.models.user import User
from app.core.security import verify_password


# 测试注册
async def test_register(client):
    register_response: httpx.Response = await client.post(
        "/auth/register",
        json={
            "username": "111",
            "password": "88888888"
        }
    )

    assert register_response.status_code == 201
    body = register_response.json()
    assert body["username"] == "111"
    assert body["id"] > 0
    assert "password_hash" not in body

    user_id = body["id"]

    async with AsyncSessionFactory() as session:
        user_response = await session.execute(select(User).where(User.id == user_id))
        user: User | None = user_response.scalar_one_or_none()

        assert user is not None
        assert user.id == user_id
        assert user.username == "111"
        # 库里存的必须是 bcrypt 哈希，不是明文
        assert user.password_hash != "88888888"
        assert user.password_hash.startswith("$2b$")
        assert verify_password("88888888", user.password_hash)

# 测试登录
async def test_login(client):
    register_response: httpx.Response = await client.post(
        "/auth/register",
        json={
            "username": "222",
            "password": "88888888"
        }
    )
    assert register_response.status_code == 201

    login_response: httpx.Response = await client.post(
        "/auth/login",
        data={
            "username": "222",
            "password": "88888888"
        }
    )

    assert login_response.status_code == 200
    body = login_response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


# 测试登录失败（密码错误）
async def test_login_wrong_password(client):
    register_response: httpx.Response = await client.post(
        "/auth/register",
        json={
            "username": "333",
            "password": "88888888"
        }
    )
    assert register_response.status_code == 201

    login_response: httpx.Response = await client.post(
        "/auth/login",
        data={
            "username": "333",
            "password": "wrong-pass"
        }
    )

    assert login_response.status_code == 401


async def test_register_weak_password(client):
    register_response: httpx.Response = await client.post(
        "/auth/register",
        json={
            "username": "444",
            "password": "123",      # 少于 8 位
        }
    )

    assert register_response.status_code == 422
    body = register_response.json()
    assert body["detail"]


# 测试注册重名（409）
async def test_register_duplicate_username(client):
    payload = {
        "username": "555",
        "password": "88888888",
    }

    first_response: httpx.Response = await client.post("/auth/register", json=payload)
    assert first_response.status_code == 201

    second_response: httpx.Response = await client.post("/auth/register", json=payload)
    assert second_response.status_code == 409
    body = second_response.json()
    assert body["detail"]

    # 数据库里仍然只有一个该用户名的账户
    async with AsyncSessionFactory() as session:
        result = await session.execute(select(User).where(User.username == "555"))
        users = result.scalars().all()
        assert len(users) == 1
