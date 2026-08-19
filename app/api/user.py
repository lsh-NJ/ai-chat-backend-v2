from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserOut
from app.services.user_service import list_users

router = APIRouter(tags=["user"])

@router.get("/users", response_model=list[UserOut])
async def get_users(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
):
    users = await list_users(session)
    return [UserOut.model_validate(user) for user in users]

    
