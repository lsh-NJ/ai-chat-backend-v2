from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user_repository import UserRepository


async def list_users(session: AsyncSession) -> list[User]:
    user_repository = UserRepository(session)
    users = await user_repository.list_users()
    return list(users)