
# AI Chat Backend V2

一个用 **FastAPI + PostgreSQL + Redis** 实现的异步多用户 AI 聊天后端，支持 JWT 认证、RBAC 权限控制和会话列表缓存。

## 技术栈

- Python 3.12+
- FastAPI + Pydantic v2
- SQLAlchemy 2.x（`AsyncEngine` / `AsyncSession`）
- asyncpg
- Alembic
- PostgreSQL 17
- Redis
- JWT + bcrypt
- pytest + pytest-asyncio + httpx

## 目录结构

* alembic：数据库迁移
* app
  * api：接口实现
  * core：目前存放异常定义
  * db：engine 及 asyncsession 的构建
  * models：ORM 映射类定义
  * repositories：完成所有的数据库操作
  * schemas：接口请求及响应的模型
  * service：业务逻辑所在
  * tests：pytest 测试
  * main.py: FastAPI 入口

## 快速开始

### 1. 启动 PostgreSQL 和 Redis

要先运行 Docker Desktop

启动依赖服务：
```bash
docker compose up -d db redis
docker compose ps        # 检查 PostgreSQL 和 Redis 是否正常运行
```

### 2. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

修改 `DEEPSEEK_API_KEY` 和 `JWT_SECRET`。Redis 开发环境使用 DB 0，测试环境固定使用 DB 15。

### 4. 数据库迁移

```bash
.venv/bin/alembic upgrade head
```



### 5. 运行测试

```bash
.venv/bin/python -m pytest -q
```

测试只连接 `chat_v2_test` 和 Redis DB 15；配置不安全或依赖不可用时会拒绝运行（fail-closed）。整个测试 session 开始时从空 schema 执行一次 `alembic upgrade head`，每个用例通过 `TRUNCATE` 和 `flushdb()` 清理数据。LLM 调用全部 Mock，不访问真实模型或开发数据。


### 6. 启动服务

```bash
.venv/bin/uvicorn app.main:app --reload
```


- 健康检查：http://127.0.0.1:8000/health
- Swagger 文档：http://127.0.0.1:8000/docs


## 接口

| 接口 | 权限 | 说明 |
| --- | --- | --- |
| `GET /health` | 无需登录 | 健康检查，返回 `{"status": "ok"}` |
| `POST /auth/register` | 无需登录 | 注册用户 |
| `POST /auth/login` | 无需登录 | 登录并获取 JWT Access Token |
| `GET /users` | admin | 查看用户列表，不返回密码哈希 |
| `POST /conversations` | 登录用户 | 创建会话，返回 `id / title / created_at` |
| `GET /conversations` | 登录用户 | 获取当前用户的会话列表 |
| `GET /conversations/{id}/messages` | 登录用户 | 获取当前用户的历史消息（按 id 升序） |
| `POST /chat` | 登录用户 | 普通对话，返回 `reply + conversation_id` |
| `POST /chat/stream` | 登录用户 | 流式对话；会话 ID 通过 `X-Conversation-Id` 响应头返回 |


## 会话列表缓存

`GET /conversations` 使用 cache-aside：先读取按 `user_id` 隔离的 Redis 缓存，未命中时查询 PostgreSQL 并回填，TTL 为 60 秒。创建会话提交数据库后主动删除缓存。

PostgreSQL 始终是事实来源。运行期间 Redis 读写失败时会记录脱敏日志并降级到 PostgreSQL，不让缓存故障阻断核心业务。


## 分层与事务边界

`Router → Service → Repository → AsyncSession → PostgreSQL`，LLM 调用独立于数据层。

聊天接口的事务边界（两个短事务）：

```text
短事务 1：创建会话（如需要）→ 保存 user 消息 → commit
          ↓
          调用 LLM（期间不持有数据库事务）
          ↓
短事务 2：保存 assistant 消息 → commit
```



## 常用命令

```bash
docker compose up -d db redis    # 启动 PostgreSQL 和 Redis
.venv/bin/alembic upgrade head   # 应用迁移
.venv/bin/alembic current        # 查看当前迁移版本
.venv/bin/alembic downgrade -1   # 回退一步
.venv/bin/python -m pytest -q    # 运行全部测试
.venv/bin/uvicorn app.main:app --reload   # 启动服务
```

## 状态码
|状态码|错误详情|
|---|---|
|200|链接正常|
|201|请求成功并创建了新的资源|
|401|未认证|
|403|身份认证成功，但权限不足|
|409|账号已存在|
|422|请求不合法|


## 相关文档

学习流程
- [WEEK6.md](WEEK6.md)
- [WEEK7.md](WEEK7.md)
