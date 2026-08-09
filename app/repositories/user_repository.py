from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, Result

from app.models.user import User

class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session: AsyncSession = session


    async def create(self, username: str, password_hash: str) -> User:
        user: User = User(username=username, password_hash = password_hash)
        self.session.add(user)
        await self.session.flush()
        return user


    async def get_by_username(self, username: str) -> User | None:
        user: Result = await self.session.execute(select(User).where(User.username == username))
        return user.scalar_one_or_none()


    async def get_by_id(self, user_id: int) -> User | None:
        user: Result = await self.session.execute(select(User).where(User.id == user_id))
        return user.scalar_one_or_none()
