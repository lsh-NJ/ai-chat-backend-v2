# Week 6：PostgreSQL、SQLAlchemy 2.x 与 Alembic

> 项目：`ai-chat-backend-v2`  
> 本周定位：保留 v1 作为对照，从需求重新实现 V2，不复制旧项目代码。  
> 唯一主线：把聊天后端的数据层升级为可迁移、可测试、异步的 PostgreSQL 数据层。

## 本周目标

本周结束时，项目应做到：

- [ ] 使用 PostgreSQL 替代 SQLite
- [ ] 使用 SQLAlchemy 2.x ORM 描述数据模型
- [ ] 使用 `AsyncEngine`、`AsyncSession` 和异步 Repository
- [ ] 使用 Alembic 管理数据库结构，不使用 `create_all()` 代替迁移
- [ ] 从空数据库执行 `alembic upgrade head` 后可以启动服务
- [ ] 保持 v1 的主要接口行为兼容
- [ ] 普通和流式 LLM 测试继续使用 Mock，不调用真实模型
- [ ] 测试只访问隔离的 PostgreSQL 测试库

## 本周暂不加入

- Redis
- JWT、RBAC 和用户系统
- RAG、Embedding 和向量检索
- Agent、LangGraph 和 MCP
- Worker、消息队列
- 完整 Docker Compose 应用栈
- 复杂前端

可以使用 Docker Compose 只启动一个 PostgreSQL 服务，但不要同时扩展其他基础设施。

## 执行纪律

1. 不复制 v1 的业务代码；可以查看接口定义和测试行为。
2. 每天只推进当天的数据边界，不顺手增加新功能。
3. 第一次实现核心代码时，只向 AI 要解释、接口建议和代码审查。
4. 每次迁移、模型或 Repository 变更后立即运行测试。
5. 不使用真实 API Key 运行自动化测试。
6. 不把 `.env`、数据库密码、数据库文件或缓存提交到 Git。
7. 不在等待 LLM 响应期间保持数据库事务开启。

## 目标目录

```text
ai-chat-backend-v2/
├── alembic/
│   └── versions/
├── app/
│   ├── api/
│   ├── core/
│   ├── db/
│   │   ├── base.py
│   │   └── session.py
│   ├── models/
│   ├── repositories/
│   ├── schemas/
│   ├── services/
│   └── main.py
├── tests/
├── alembic.ini
├── docker-compose.yml
├── .env.example
├── requirements.txt
├── README.md
├── REQUIREMENTS.md
└── WEEK6.md
```

该目录是本周结束时的目标，不要求第一天全部创建。

---

## Day 1：需求、PostgreSQL 与数据模型

### 任务

- [x] 编写一页 `REQUIREMENTS.md`
- [x] 写清 v2 保留的接口、请求体、响应体和状态码
- [x] 设计 `conversations` 表
- [x] 设计 `messages` 表
- [x] 确定主键、外键、非空约束和角色约束
- [x] 为消息历史查询设计 `(conversation_id, id)` 组合索引
- [x] 启动本地 PostgreSQL
- [x] 创建开发数据库和独立测试数据库
- [x] 使用原生 SQL 手动完成一次创建会话、保存消息和查询历史
- [x] 用事务制造一次错误并验证回滚
- [x] 记录为什么本项目从 SQLite 迁移到 PostgreSQL

### 推荐数据模型

```text
conversations
- id: bigint primary key
- title: varchar(100), nullable
- created_at: timestamptz, not null

messages
- id: bigint primary key
- conversation_id: bigint, foreign key, not null
- role: varchar/check constraint, not null
- content: text, not null
- created_at: timestamptz, not null

index
- messages(conversation_id, id)
```

### 只补这些知识

- database、schema、table
- `INSERT`、`SELECT`、`UPDATE`、`DELETE`
- `JOIN`、排序和分页
- 主键、外键、唯一、非空和 CHECK 约束
- 事务、提交和回滚
- B-Tree 索引与组合索引最左前缀
- `timestamp` 与 `timestamptz`

### Day 1 验收

- [x] 不看旧代码，能画出两张表及关系
- [x] 能写 SQL 查询某个会话按顺序排列的全部消息
- [x] 能解释为什么使用 `(conversation_id, id)` 索引
- [x] 能解释外键、索引和事务分别解决什么问题
- [x] 开发库和测试库名称明确且互不相同

---

## Day 2：SQLAlchemy 2.x 异步基础

### 任务

- [x] 安装并记录 SQLAlchemy、异步 PostgreSQL Driver 和 Alembic 依赖
- [x] 使用环境变量配置 `DATABASE_URL`
- [x] 创建 `AsyncEngine`
- [x] 创建 `async_sessionmaker`
- [x] 实现每个请求独立的 `AsyncSession`
- [x] 使用 `DeclarativeBase` 建立 ORM Base
- [x] 使用 `Mapped` 和 `mapped_column` 定义两个 ORM Model
- [x] 定义外键、relationship、约束和组合索引
- [x] 写最小脚本或测试验证增删改查
- [x] 确认代码中没有 `metadata.create_all()`（测试 fixture 里的临时建表除外，Day 3 接入 Alembic 后移除）

### 只补这些知识

- Engine 与连接池
- Connection 与 Session
- ORM Model 与数据库表
- `select()` 与 `session.execute()`
- `flush()`、`commit()`、`rollback()` 和 `refresh()`
- request-scoped Session
- lazy loading 与 N+1 问题只需建立概念
  - 进行显性加载解决

### Day 2 验收

- [x] 能解释 Engine、连接池和 Session 的职责
- [x] 能解释 `flush` 与 `commit` 的区别
- [x] 能使用 AsyncSession 创建并查询一条会话
- [x] 关闭应用后数据库连接能够正确释放

---

## Day 3：Alembic 数据库迁移

### 任务

- [ ] 初始化 Alembic
- [ ] 让 Alembic 读取项目的 SQLAlchemy metadata
- [ ] 让数据库 URL 来自环境变量
- [ ] 生成第一条建表迁移
- [ ] 人工检查自动生成的迁移内容
- [ ] 从空数据库执行 `alembic upgrade head`
- [ ] 执行一次 `alembic downgrade -1`
- [ ] 再次执行 `alembic upgrade head`
- [ ] 新增一个小字段或索引，生成第二条迁移
- [ ] 验证迁移不会删除意外的表或数据
- [ ] 在 README 中记录迁移命令

### 必须掌握的命令

```bash
alembic current
alembic history
alembic revision --autogenerate -m "create conversation tables"
alembic upgrade head
alembic downgrade -1
```

### Day 3 验收

- [ ] 删除并重建空数据库后，迁移可以完整执行
- [ ] downgrade 后表结构按预期回退
- [ ] upgrade 后表结构恢复
- [ ] 能解释为什么 `create_all()` 不能替代 Alembic
- [ ] 能解释迁移文件为什么必须进入 Git

---

## Day 4：异步 Repository 与业务链重建

### 任务

- [ ] 实现异步 `conversation_repository`
- [ ] 实现异步 `message_repository`
- [ ] Repository 接收 `AsyncSession`
- [ ] Repository 不依赖 FastAPI、Request 或 HTTPException
- [ ] Service 使用 `await` 调用 Repository
- [ ] Router 只负责校验、依赖注入和 HTTP 响应
- [ ] 重建创建会话接口
- [ ] 重建会话列表接口
- [ ] 重建历史消息接口
- [ ] 重建普通聊天接口
- [ ] 重建流式聊天接口
- [ ] 保持原接口 URL、主要状态码和响应字段兼容
- [ ] 删除普通数据库路径中的 `asyncio.to_thread()`
- [ ] 明确聊天流程中的事务边界

### 推荐事务边界

```text
短事务 1：
创建会话（如需要）→ 保存用户消息 → commit

关闭事务
→ 调用 LLM

短事务 2：
保存 assistant 消息 → commit
```

不要这样做：

```text
开启事务
→ 保存用户消息
→ 等待 LLM 30～120 秒
→ 保存 assistant 消息
→ commit
```

### Day 4 验收

- [ ] API 层没有 SQL 或 ORM 查询
- [ ] Repository 层没有 FastAPI 依赖
- [ ] Router 和 Service 中没有 ORM 查询语句
- [ ] 普通数据库路径全部使用 AsyncSession
- [ ] LLM 调用期间不占用数据库事务
- [ ] 不存在会话能够稳定转换成 404
- [ ] LLM 超时和上游错误仍能稳定转换为 HTTP 响应

---

## Day 5：测试、文档与最终验收

### 任务

- [ ] 测试使用独立 PostgreSQL 测试数据库
- [ ] 每个测试之间数据库状态隔离
- [ ] 禁止使用 SQLite 代替 PostgreSQL 集成测试
- [ ] 普通和流式 LLM 全部使用 Mock
- [ ] 增加迁移 smoke test
- [ ] 增加外键约束测试
- [ ] 增加事务回滚测试
- [ ] 增加历史消息顺序测试
- [ ] 增加普通聊天和流式聊天成功测试
- [ ] 增加不存在会话测试
- [ ] 增加 LLM 超时测试
- [ ] 测试数量不少于 12 个
- [ ] 更新 README 的启动、迁移和测试命令
- [ ] 检查 Git diff 和密钥
- [ ] 创建清晰的 Week 6 提交

### 最终命令验收

```bash
alembic upgrade head
python -m compileall -q app tests
python -m pytest -q
git diff --check
git status --short
```

### Week 6 完成定义

- [ ] PostgreSQL 可以从空库通过迁移建立完整结构
- [ ] Alembic 可以 upgrade、downgrade、再次 upgrade
- [ ] 普通与流式接口保持兼容
- [ ] 数据访问集中在异步 Repository
- [ ] 普通数据库操作不阻塞事件循环
- [ ] LLM 调用期间没有长事务
- [ ] 测试不请求真实模型，不访问开发数据库
- [ ] 至少 12 个测试全部通过
- [ ] `.env`、数据库密码和缓存文件未进入 Git
- [ ] 能进行一次 20 分钟无稿项目讲解

## 必须能回答

- PostgreSQL 相比 SQLite 为当前项目带来了什么？
- Engine、连接池、Connection 和 Session 分别负责什么？
- AsyncSession 是否意味着数据库本身“变快了”？
- `flush`、`commit`、`rollback` 和 `refresh` 有什么区别？
- 为什么不能使用 `create_all()` 代替 Alembic？
- 为什么外键列通常还需要单独建立索引？
- 为什么不能在等待 LLM 时一直保持数据库事务？
- Repository、Service 和 Router 分别负责什么？
- 测试为什么必须运行在 PostgreSQL，而不能偷偷换回 SQLite？

## 本周结束后的下一步

Week 7 再引入 Redis、JWT/RBAC 和资源归属校验；只有 Week 6 验收通过后才进入下一阶段。
