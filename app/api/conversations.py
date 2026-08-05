from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConversationNotFoundError
from app.db.session import get_db
from app.schemas.conversation import ConversationCreateRequest, ConversationOut
from app.schemas.message import MessageOut
from app.services import conversation_service

router = APIRouter(tags=["conversations"])


@router.post("/conversations", response_model=ConversationOut)
async def create_conversation(
    body: ConversationCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await conversation_service.create_conversation(
        session=session,
        title=body.title,
    )
    return ConversationOut.model_validate(conversation)


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session: AsyncSession = Depends(get_db),
) -> list[ConversationOut]:
    conversations = await conversation_service.list_conversations(session)
    return [ConversationOut.model_validate(conversation) for conversation in conversations]


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
)
async def list_messages(
    conversation_id: int,
    session: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    try:
        messages = await conversation_service.list_messages(
            session=session,
            conversation_id=conversation_id,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    return [MessageOut.model_validate(message) for message in messages]
