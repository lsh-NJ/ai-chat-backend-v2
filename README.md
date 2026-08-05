
# AI Chat Backend V2

一个用 **FastAPI + PostgreSQL** 实现的异步 AI 聊天后端（Week 6 学习项目）。

在 v1（SQLite 版）的基础上从零重建：数据层升级为 PostgreSQL + SQLAlchemy 2.x 异步访问 + Alembic 迁移，接口行为与 v1 保持兼容。

## 技术栈

- Python 3.12+（本机使用 3.14）
- FastAPI + Pydantic v2
- SQLAlchemy 2.x（`AsyncEngine` / `AsyncSession`）
- asyncpg
- Alembic（数据库迁移）
- PostgreSQL 17（Docker Compose 启动）
- pytest + pytest-asyncio + httpx（含接口层测试）

## 目录结构

```text
ai-chat-backend-v2/
├── alembic/                  # 数据库迁移（versions/ 下是迁移文件）
├── app/
│   ├── api/                  # 路由：只做校验、依赖注入、HTTP 响应
│   ├── core/                 # 业务异常定义
│   ├── db/                   # AsyncEngine / AsyncSession / get_db
│   ├── models/               # SQLAlchemy ORM 模型
│   ├── repositories/         # 数据访问：单步操作，不依赖 FastAPI
│   ├── schemas/              # Pydantic 请求/响应模型
│   ├── services/             # 业务流程与事务边界
│   ├── tests/                # pytest 测试
│   └── main.py               # FastAPI 入口
├── sql/day1_experiments.sql  # Day 1 的 SQL 实验（学习记录）
├── docker-compose.yml        # 仅启动 PostgreSQL
├── alembic.ini
├── pytest.ini
├── REQUIREMENTS.md           # 接口与业务规则
└── WEEK6.md                  # 学习任务与验收
```

## 快速开始

### 1. 启动 PostgreSQL

需要先运行 Docker Desktop，然后：

```bash
docker compose up -d db
docker compose ps        # 等待 db 状态变为 healthy
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

至少要把 `DEEPSEEK_API_KEY` 填成真实值，其他变量可用默认值。`.env` 已被 `.gitignore` 排除，不要提交进 Git。

### 4. 数据库迁移

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current   # 应显示 637e85944404 (head)
```

说明：当前 `alembic/env.py` 会把迁移目标固定为测试库（`POSTGRES_TEST_DB`，且要求库名以 `_test` 结尾，否则拒绝运行）。这是学习阶段的安全护栏：任何迁移实验都不会误伤开发库或主库。以后需要迁移开发库时，正确做法是把护栏改成显式目标选择（例如必须设置 `ALEMBIC_TARGET=dev|test`），而不是删掉校验。

体验回退与再升级：

```bash
.venv/bin/alembic downgrade -1   # 回退一步
.venv/bin/alembic upgrade head   # 再升回来
```

### 5. 运行测试

需要 PostgreSQL 正在运行：

```bash
.venv/bin/python -m pytest -q
```

测试只连接 `chat_v2_test`：每个用例都会清空 schema，并从空库执行 `alembic upgrade head`，保证测试走和真实环境完全相同的建库路径；LLM 调用全部 Mock，测试不需要 API Key、不访问开发库。

### 6. 启动服务

```bash
.venv/bin/uvicorn app.main:app --reload
```

启动后可访问：

- 健康检查：http://127.0.0.1:8000/health
- Swagger 文档：http://127.0.0.1:8000/docs

> 若 requirements.txt 中还没有 uvicorn，先执行 `pip install "uvicorn[standard]"`（后续建议把它补进 requirements.txt）。

> 注意：由于当前迁移护栏只作用于测试库，开发库 `chat_v2_dev` 还没有可用的建表路径。要本地联调开发库，需先按上文把 `alembic/env.py` 改为显式目标选择并执行迁移。

## 接口

| 接口 | 说明 |
| --- | --- |
| `GET /health` | 健康检查，返回 `{"status": "ok"}` |
| `POST /conversations` | 创建会话，返回 `id / title / created_at` |
| `GET /conversations` | 会话列表 |
| `GET /conversations/{id}/messages` | 历史消息（按 id 升序）；不存在返回 404 |
| `POST /chat` | 普通对话，返回 `reply + conversation_id` |
| `POST /chat/stream` | 流式对话；会话 ID 通过 `X-Conversation-Id` 响应头返回 |

业务规则：

- 没有 `conversation_id` 时自动创建会话，标题取用户消息前 30 个字。
- 不存在的会话返回 404；空消息返回 422。
- 上下文使用最近 20 条消息。
- LLM 错误映射：配置错误 → 500，上游错误 → 502，超时 → 504。

## 分层与事务边界

调用链：`Router → Service → Repository → AsyncSession → PostgreSQL`，LLM 调用独立于数据层。

聊天接口的事务边界（两个短事务）：

```text
短事务 1：创建会话（如需要）→ 保存 user 消息 → commit
          ↓
          调用 LLM（期间不持有数据库事务）
          ↓
短事务 2：保存 assistant 消息 → commit
```

禁止在等待 LLM 期间保持数据库事务开启。

## 常用命令

```bash
docker compose up -d db          # 启动 PostgreSQL
.venv/bin/alembic upgrade head   # 应用迁移
.venv/bin/alembic current        # 查看当前迁移版本
.venv/bin/alembic downgrade -1   # 回退一步
.venv/bin/python -m pytest -q    # 运行全部测试
.venv/bin/uvicorn app.main:app --reload   # 启动服务
```

## 相关文档

- [REQUIREMENTS.md](REQUIREMENTS.md)：接口契约与业务规则
- [WEEK6.md](WEEK6.md)：学习任务与验收标准
- `sql/day1_experiments.sql`：Day 1 的 PostgreSQL 实验记录
