import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.core.security import(
    hash_password,
    verify_password,
)
from app.repositories.user_repository import UserRepository
from app.core.exceptions import (
    UsernameAlreadyExistsError,
    UsernameOrPasswordError,
)


async def register(
    session: AsyncSession,
    username: str,
    password: str,
) -> User:
    password_hash = await asyncio.to_thread(
        hash_password,
        password
    )
    user_repository = UserRepository(session)

    try: 
        user = await user_repository.create(username, password_hash)
    except  IntegrityError as e:
        await session.rollback()
        raise UsernameAlreadyExistsError(
            f"用户名{username}已存在",
        ) from e
    
    await session.commit()
    return user


async def login(
    session: AsyncSession,
    username: str,
    password: str,
) -> User:
    user_repository = UserRepository(session)
    user: User = await user_repository.get_by_username(username)

    if user is None:
        raise UsernameOrPasswordError()
    if not await asyncio.to_thread(
        verify_password,
        password, 
        user.password_hash
    ):
        raise UsernameOrPasswordError()

    return user

