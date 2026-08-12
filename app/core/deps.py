from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.core.security import decode_access_token
from app.core.exceptions import InvalidTokenError
from app.db.session import get_db
from app.repositories.user_repository import UserRepository

oauth2_schema = OAuth2PasswordBearer(tokenUrl="/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_schema),
    session: AsyncSession = Depends(get_db),
) -> User:
    try:
        user_id = decode_access_token(token)
    except InvalidTokenError as e:
        raise HTTPException(
            status_code=401, 
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_repository = UserRepository(session)
    user: User | None = await user_repository.get_by_id(user_id)

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="认证失败",
            headers={"WWW-Authenticate":"Bearer"},
        )

    return user
