from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConversationNotFoundError
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.conversation import ConversationCreateRequest, ConversationOut
from app.schemas.message import MessageOut
from app.services import conversation_service

router = APIRouter(tags=["conversations"])

# 创建 conversation
@router.post("/conversations", response_model=ConversationOut)
async def create_conversation(
    body: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await conversation_service.create_conversation(
        session=session,
        title=body.title,
        user_id=current_user.id,
    )
    return ConversationOut.model_validate(conversation)


# 获得所有 conversation
@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[ConversationOut]:
    conversations = await conversation_service.list_conversations(
        session,
        current_user.id,
    )
    return [ConversationOut.model_validate(conversation) for conversation in conversations]


# 获得相应 conversation_id 的 conversation
@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
)
async def list_messages(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    try:
        messages = await conversation_service.list_messages(
            session=session,
            conversation_id=conversation_id,
            user_id=current_user.id,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    return [MessageOut.model_validate(message) for message in messages]
