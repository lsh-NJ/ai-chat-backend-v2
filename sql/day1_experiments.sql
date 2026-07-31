CREATE TABLE conversations (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    title VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    conversation_id BIGINT NOT NULL,

    role VARCHAR(20) NOT NULL
        CHECK (role IN ('system', 'user', 'assistant')),

    content TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_messages_conversation
        FOREIGN KEY (conversation_id)
        REFERENCES conversations(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_messages_conversation_id_id
ON messages (conversation_id, id);

INSERT INTO conversations(id, title)
VALUES (1, 'first conversation')
RETURNING id;

INSERT INTO messages(conversation_id, role, content)
VALUES (1, 'user', 'Hello?')
RETURNING id;

INSERT INTO messages(conversation_id, role, content)
VALUES (1, 'assistant', 'Hello')
RETURNING id;

SELECT conversation_id, role, content, created_at FROM messages
WHERE conversation_id = 1
ORDER BY id ASC;

BEGIN;

INSERT INTO messages (conversation_id, role, content)
VALUES (1, 'user', 'should be rollback');

INSERT INTO messages (conversation_id, role, content)
VALUES (1, 'robot', 'wa');

ROLLBACK;

SELECT id, conversation_id, role, content
FROM messages
WHERE content = 'should be rollback';

BEGIN;

-- 仅对当前事务生效的语句，不进行 commit 将不会对数据库/别的事务产生影响

COMMIT; -- 将上面的语句永久对数据库进行真实的修改
