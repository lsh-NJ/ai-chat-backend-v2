
# AI Chat Backend V2

一个用 **FastAPI + PostgreSQL** 实现的异步 AI 聊天后端
由 SQLite 版重建，数据层升级为 PostgreSQL + SQLAlchemy 2.x 异步访问 + Alembic 迁移，接口基本不变

## 技术栈

- Python 3.12+
- FastAPI + Pydantic v2
- SQLAlchemy 2.x（`AsyncEngine` / `AsyncSession`）
- asyncpg
- Alembic
- PostgreSQL 17
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

### 1. 启动 PostgreSQL

要先运行 Docker Desktop

启动数据库：
```bash
docker compose up -d db
docker compose ps        # 检查数据库是否正常运行
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

修改 `DEEPSEEK_API_KEY` 其他可用默认值

### 4. 数据库迁移

```bash
.venv/bin/alembic upgrade head
```



### 5. 运行测试

```bash
.venv/bin/python -m pytest -q
```

测试只连接 `chat_v2_test`：测试库名必须以 `_test` 结尾，否则拒绝运行（fail-closed）。每个用例都会清空 schema，并从空库执行 `alembic upgrade head`，保证测试走和真实环境完全相同的建库路径；LLM 调用全部 Mock，测试不需要 API Key、不访问开发库。


### 6. 启动服务

```bash
.venv/bin/uvicorn app.main:app --reload
```


- 健康检查：http://127.0.0.1:8000/health
- Swagger 文档：http://127.0.0.1:8000/docs


## 接口

| 接口 | 说明 |
| --- | --- |
| `GET /health` | 健康检查，返回 `{"status": "ok"}` |
| `POST /conversations` | 创建会话，返回 `id / title / created_at` |
| `GET /conversations` | 会话列表 |
| `GET /conversations/{id}/messages` | 历史消息（按 id 升序）； |
| `POST /chat` | 普通对话，返回 `reply + conversation_id` |
| `POST /chat/stream` | 流式对话；会话 ID 通过 `X-Conversation-Id` 响应头返回 |


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
docker compose up -d db          # 启动 PostgreSQL
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
|409|账号已存在|
|422|请求不合法|


## 相关文档

学习流程
- [WEEK6.md](WEEK6.md)
- [WEEK7.md](WEEK7.md)
