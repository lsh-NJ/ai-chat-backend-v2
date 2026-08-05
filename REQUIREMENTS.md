## 项目目标
在 v1 的基础上使用 PostgreSQL 和异步数据访问替代 SQLite

## 接口清单
- [ ] `GET  /health` 检查健康
- [ ] `POST /conversations` 创建会话
  * 参数：ConversationCreatRequest
- [ ] `GET  /conversations` 查看会话列表
  * 无参数
- [ ] `GET  /conversations/{conversation_id}/messages` 获得会话的所有历史记录
  * 参数：conversation_id
- [ ] `POST /chat` 普通对话
  * 参数：ChatRequest
  * 异常：空消息返回 422，不存在的消息返回 404
  * 上下文包含20条消息
- [ ] `POST /chat/stream` 流式对话
  * 参数 ChatRequest
  * 通过 `X-Conversation-Id` 返回会话 ID
  * 异常与上下文与`/chat`一致

| 接口 | 行为 |
| --- | --- |
| `GET /health` | 200 |
| `POST /conversations` | 创建会话，返回 id/title/created_at |
| `GET /conversations` | 会话列表 |
| `GET /conversations/{id}/messages` | 历史消息，按 id 升序；不存在返回 404 |
| `POST /chat` | 无 conversation_id 自动建会话；自动标题取用户消息前 30 字；空消息 422；返回 reply + conversation_id |
| `POST /chat/stream` | 同上，但流式返回；会话 ID 通过 `X-Conversation-Id` 头返回 |

## 业务记录
- [ ] Chat 没有 conversation_id 时自动创建会话。
- [ ] 自动标题取用户消息前 30 个字符。
- [ ] 不存在的会话返回 404。
- [ ] 空消息返回 422。
- [ ] 历史消息按照从旧到新返回。
- [ ] 暂时继续使用最近 20 条消息作为上下文。
- [ ] 流式响应通过 X-Conversation-Id 返回会话 ID。
- [ ] 测试不得请求真实 LLM。
- [ ] LLM 请求期间不得保持数据库事务。


## 数据库表格

```sql
CREATE TABLE conversations (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    title VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

```sql
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
```