import httpx

from app.db.session import AsyncSessionFactory
from app.models.user import User
from app.core.security import create_access_token


async def _register(
    client: httpx.AsyncClient,
    username: str,
) -> dict:
    response = await client.post(
        "/auth/register",
        json={"username": username, "password": "88888888"},
    )
    assert response.status_code == 201
    return response.json()


async def _login_headers(
    client: httpx.AsyncClient,
    username: str,
) -> dict[str, str]:
    response = await client.post(
        "/auth/login",
        data={"username": username, "password": "88888888"},
    )
    assert response.status_code == 200

    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_users_requires_authentication(client):
    response = await client.get("/users")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_registered_user_defaults_to_user_and_is_forbidden(client):
    registered = await _register(client, "normal-user")

    assert registered["role"] == "user"

    async with AsyncSessionFactory() as session:
        user = await session.get(User, registered["id"])
        assert user is not None
        assert user.role == "user"

    headers = await _login_headers(client, "normal-user")
    response = await client.get("/users", headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"


async def test_admin_can_list_users_without_exposing_password_hash(
    client,
    create_test_user,
):
    admin = await create_test_user("admin-user")
    await create_test_user("visible-user")

    # 先取得 token，再修改数据库角色，以证明授权读取的是数据库最新角色。
    headers = {"Authorization": f"Bearer {create_access_token(admin.id)}"}

    async with AsyncSessionFactory() as session:
        admin_user = await session.get(User, admin.id)
        assert admin_user is not None
        admin_user.role = "admin"
        await session.commit()

    response = await client.get("/users", headers=headers)

    assert response.status_code == 200
    users = response.json()

    users_by_name = {user["username"]: user for user in users}
    assert users_by_name["admin-user"]["role"] == "admin"
    assert users_by_name["visible-user"]["role"] == "user"

    # 对响应中每个用户做字段级检查，防止任意账户泄露密码哈希。
    assert all("password_hash" not in user for user in users)
