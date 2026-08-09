"""
create users table and add user_id to conversations
Revision ID: a29649002607
Revises: 637e85944404
Create Date: 2026-08-06 18:37:52.088370

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a29649002607'
down_revision: Union[str, Sequence[str], None] = '637e85944404'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('users',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('username', sa.String(length=32), nullable=False),
        sa.Column('password_hash', sa.String(length=128), nullable=False),
        sa.Column('role', sa.String(length=16), server_default='user', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role IN ('user', 'admin')", name='ck_users_role'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username')
    )

    op.add_column('conversations', sa.Column('user_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        'fk_conversations_user_id_users', 'conversations', 'users',
        ['user_id'], ['id'], ondelete='CASCADE',
    )
    op.create_index(
        'idx_conversations_user_id_created_at', 'conversations',
        ['user_id', 'created_at'], unique=False,
    )

    bind = op.get_bind()

    # 1) 插入默认用户，用 RETURNING 拿回 id
    default_user_id = bind.execute(
        sa.text(
            "INSERT INTO users (username, password_hash, role, created_at) "
            "VALUES ('default', :password_hash, 'user', now()) "
            "RETURNING id"
        ),
        # 此处密码无实义，也无对应真实密码
        {"password_hash": "$2b$12$D6pRJxwBIEpO4eR/l/7OVOmJotevDNKnZxC2.0ebLckx9bpookcpu"},
    ).scalar()

    # 2) 把历史孤儿会话归属给默认用户
    bind.execute(
        sa.text("UPDATE conversations SET user_id = :uid WHERE user_id IS NULL"),
        {"uid": default_user_id},
    )



def downgrade() -> None:
    op.drop_index('idx_conversations_user_id_created_at', table_name='conversations')
    op.drop_constraint('fk_conversations_user_id_users', 'conversations', type_='foreignkey')
    op.drop_column('conversations', 'user_id')
    op.drop_table('users')