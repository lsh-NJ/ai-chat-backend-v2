from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.auth import (
    RegisterRequest,
    UserOut,
    TokenOut,
)
from app.models.user import User
from app.services import auth_service
from app.core.exceptions import (
    UsernameOrPasswordError,
    UsernameAlreadyExistsError,
)
from app.core.security import create_access_token

router = APIRouter(tags=["auth"])

@router.post("/login", response_model=TokenOut)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db),
):
    try:
        user : User = await auth_service.login(
            session=session,
            username=form_data.username,
            password=form_data.password,
        )

    except UsernameOrPasswordError as e:
        raise HTTPException(
            status_code=401,
            detail=str(e)
        ) from e

    access_token = create_access_token(user.id)

    return TokenOut (
        access_token=access_token
    )


@router.post("/register", status_code=201, response_model=UserOut)
async def register(
    register_request: RegisterRequest,
    session: AsyncSession = Depends(get_db),
):
    try:
        user: User = await auth_service.register(
            session=session,
            username=register_request.username,
            password=register_request.password,
        )

    except UsernameAlreadyExistsError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e)
        ) from e

    return UserOut (
        id=user.id,
        username=user.username,
        role=user.role,
        created_at=user.created_at,
    )