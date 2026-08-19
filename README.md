# AI Chat Backend V2

一个面向生产后端基础训练的多用户 AI Chat 服务。项目使用 FastAPI、异步 SQLAlchemy、PostgreSQL、Redis Streams 和独立 Worker，覆盖认证授权、流式响应、缓存、失败补偿、容器化、CI 与可复现压测。

当前版本不是“调用一次模型 API”的 Demo：它明确处理用户隔离、事务边界、流式响应已经发出后的保存失败，以及 at-least-once 消费产生的重复投递。

## 已实现能力

- JWT 登录、bcrypt 密码哈希、user/admin RBAC。
- 会话和消息按当前用户隔离，访问不存在或属于其他用户的资源统一返回 404。
- 普通与 SSE 流式 Chat；LLM 调用期间不持有数据库事务。
- PostgreSQL + Alembic 管理结构演进，当前 migration head 为 `d8e9f0a1b2c3`。
- Redis cache-aside 会话列表缓存，按 `user_id` 分键，运行期故障降级到 PostgreSQL。
- 流式 assistant 保存失败后写入 Redis Stream，由独立 Worker 重试。
- at-least-once + 数据库唯一约束实现幂等；pending reclaim、最大重试和 dead-letter stream 均有测试。
- Docker Compose 运行数据库、Redis、一次性迁移、API 和 Worker。
- Ruff、Pyright、pytest coverage、pre-commit 和 GitHub Actions 质量门禁。
- 独立 `chat_v2_load_test` 环境下的 50 用户 mock LLM 压测。

## 架构

```text
Client
  │
  ▼
FastAPI Router
  ▼
Service ───────────────► LLM HTTP API
  ▼
Repository
  ▼
PostgreSQL

Conversation Service ◄──► Redis cache-aside

Streaming save failure
  ▼
Redis Stream
  ▼
Retry Worker
  ▼
idempotent PostgreSQL write
  ▼
commit → XACK
```

API、migration 和 Worker 共用同一个生产镜像，但以不同进程运行：API 处理 HTTP 生命周期，migration 只执行一次结构升级，Worker 持续处理异步补偿任务。

## 使用 Docker Compose 启动完整服务

需要 Docker Desktop 或兼容的 Docker Engine。

```bash
cp .env.example .env
```

至少填写以下值：

```dotenv
POSTGRES_PASSWORD=使用独立的数据库密码
JWT_SECRET=至少32字符的随机密钥
DEEPSEEK_API_KEY=真实调用模型时填写
```

可以生成 JWT 随机密钥：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

启动：

```bash
docker compose up -d --build
docker compose ps --all
```

Compose 会按以下顺序启动：PostgreSQL/Redis 健康 → `alembic upgrade head` 成功 → API/Worker 启动。

验证：

```bash
curl http://127.0.0.1:8000/health
docker compose logs migrate api worker
```

- 健康检查：<http://127.0.0.1:8000/health>
- OpenAPI：<http://127.0.0.1:8000/docs>

停止容器但保留数据库 volume：

```bash
docker compose down
```

`docker compose down --volumes` 会删除数据库数据，只应在明确需要重建开发环境时使用。

## 本地开发

开发工具与生产依赖分离：`requirements.txt` 只供生产镜像使用，`requirements-dev.txt` 才包含测试和静态检查工具。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
# 编辑 .env

docker compose up -d db redis
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

另开终端启动 Worker：

```bash
.venv/bin/python -m app.workers.run_message_retry_worker
```

## 环境变量

| 变量 | 用途 |
| --- | --- |
| `POSTGRES_USER/PASSWORD/DB/HOST/PORT` | PostgreSQL 连接；Compose 内部 host 为 `db` |
| `POSTGRES_TEST_DB` | pytest 专用数据库，名称必须以 `_test` 结尾 |
| `REDIS_URL` | 运行环境 Redis；开发默认 DB 0 |
| `REDIS_TEST_URL` | pytest Redis，必须与运行环境不同且固定为 DB 15 |
| `JWT_SECRET` | HS256 签名密钥，不能为空、不能是示例值且至少 32 字符 |
| `DEEPSEEK_API_KEY/BASE_URL/MODEL` | LLM provider 配置 |
| `WORKER_CONSUMER_NAME` | 可选；未设置时使用 hostname + pid |
| `LOG_LEVEL` | API/Worker 日志等级 |
| `PYTHON_IMAGE` | 可选的 Python 基础镜像覆盖 |

`.env` 被 Git 和 Docker build context 排除。不要把真实 token、密码或 API key 写入 Compose、测试或文档。

## API

| 接口 | 权限 | 说明 |
| --- | --- | --- |
| `GET /health` | 无 | 进程与依赖健康检查 |
| `POST /auth/register` | 无 | 注册用户 |
| `POST /auth/login` | 无 | 获取 Bearer JWT |
| `GET /users` | admin | 用户列表，不返回密码哈希 |
| `POST /conversations` | 用户 | 创建当前用户的会话 |
| `GET /conversations` | 用户 | 当前用户会话列表 |
| `GET /conversations/{id}/messages` | 用户 | 当前用户会话历史 |
| `POST /chat` | 用户 | 普通回复；可自动创建会话 |
| `POST /chat/stream` | 用户 | 流式回复；会话 ID 位于 `X-Conversation-Id` |

## 事务与失败恢复

Chat 路径使用短事务：

```text
创建会话（如需要）/ 保存 user 消息 → commit
                  ↓
          LLM HTTP 调用（无 DB 事务）
                  ↓
          保存 assistant 消息 → commit
```

普通请求在 LLM 失败时保留已经提交的 user 消息。流式响应开始后无法再修改 HTTP 状态码，因此 assistant 最终保存失败不会打断客户端已经收到的内容，而是投递带稳定 `idempotency_key` 的重试任务。

Worker 的顺序固定为：

```text
读取/认领任务 → 校验用户归属 → 幂等写入 → PostgreSQL commit → XACK
```

- commit 前失败：不 ACK，任务保留在 pending。
- commit 后、ACK 前崩溃：任务会再次投递，数据库唯一约束消除重复。
- 超过最大投递次数或任务格式/归属非法：进入 dead-letter stream，再 ACK 原任务。
- PostgreSQL 与 Redis 同时失败：已经发出的流仍不能撤回，系统只能记录脱敏错误，无法保证自动补齐；这是当前双写边界的明确限制。

## 缓存

`GET /conversations` 使用 cache-aside：

```text
读：Redis → miss → PostgreSQL → 回填 Redis
写：PostgreSQL commit → 删除当前 user_id 的缓存 key
```

PostgreSQL 始终是事实来源。运行期间缓存读写失败时 fail-open 到数据库；应用启动和测试依赖检查则 fail-closed。

## 测试和质量门禁

```bash
.venv/bin/ruff check .
.venv/bin/pyright app
.venv/bin/pytest -q --cov=app --cov-branch --cov-report=term-missing
.venv/bin/pre-commit run --all-files
```

测试只允许连接名称以 `_test` 结尾的 PostgreSQL，并强制 Redis DB 15。测试会从空 schema 运行真实迁移，依赖不可用或环境不安全时直接失败，不会回退到开发库。

最近一次本机验收：129 个测试通过、覆盖率 91.85%。

GitHub Actions 使用干净 Python 环境和独立 PostgreSQL/Redis services 执行 lint、类型检查、覆盖率测试和生产镜像构建。生产镜像不安装 pytest、coverage、Ruff、Pyright 或 pre-commit。

## 50 用户压测

压测使用独立 Compose project、`chat_v2_load_test` 数据库、独立 volume 和固定延迟 mock LLM：

```bash
./load_tests/run_load_test.sh
```

最近一次 40 秒基线共完成 2,702 个 Chat 请求，错误率 0%，聚合 P50 150 ms、P95 370 ms、吞吐量 67.98 RPS。该结果包含用户等待时间，只是可比较基线，不代表最大容量。

详见 [load_tests/REPORT.md](load_tests/REPORT.md)。

## 常用迁移与排障命令

```bash
.venv/bin/alembic heads
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/alembic downgrade -1

docker compose ps --all
docker compose logs --tail=100 api worker migrate
```

不要修改已经发布的 migration；模型变化必须追加新 revision，并用 `alembic check` 和空库升级测试验证一致性。

## 当前限制

- 上下文仍固定取最近 20 条，尚未按 tokenizer 和模型预算截断。
- LLM 调用仍直接绑定当前 DeepSeek 兼容协议，provider adapter 顺延到 Week 9。
- 没有 Refresh Token、主动撤销、登录限流和密钥轮换流程。
- 日志已避免正文、token 和异常原文，但尚未形成完整 request/trace ID 与指标系统。
- Redis/PostgreSQL 双写失败时没有事务性 outbox，无法保证自动补偿。
- 本机 50 用户测试不是生产容量证明，尚未做阶梯加压和资源监控。

## 文档

- [REQUIREMENTS.md](REQUIREMENTS.md)：接口需求
- [Decisions.md](Decisions.md)：架构决策记录
- [WEEK8.md](WEEK8.md)：本周过程与验收证据
- [v1-to-v2.md](v1-to-v2.md)：架构演进复盘
- [SECURITY_AUDIT.md](SECURITY_AUDIT.md)：安全审计与剩余风险
- [PROJECT_DEFENSE.md](PROJECT_DEFENSE.md)：项目答辩提纲
- [RESUME_V0.md](RESUME_V0.md)：简历项目描述草稿
