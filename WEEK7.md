# Week 7：用户系统、JWT 认证授权与 Redis 缓存

> 项目：`ai-chat-backend-v2`  
> 本周定位：在 Week 6 的分层 + 异步 + 迁移基础上，把单用户聊天后端升级为多用户、带认证授权和缓存的后端。  
> 唯一主线：先有“用户”，再有“归属”，最后用缓存优化读取路径，并把 Week 6 留下的债务（D-001 / D-002）收尾。

## 本周目标

本周结束时，项目应做到：

- [ ] 提供注册、登录接口，密码只保存哈希（bcrypt/argon2），绝不保存明文
- [ ] 使用 JWT 访问令牌做认证，受保护接口必须登录
- [ ] 会话和消息归属当前用户；访问他人资源返回 404（不泄露存在性）
- [ ] 引入 user / admin 两种角色，权限不足返回 403
- [ ] 引入 Redis：会话列表缓存 + TTL + 写后失效；缓存键带用户维度，不缓存敏感数据
- [ ] 收尾 Week 6 债务：消息 `is_complete` 完整性标记、流式保存失败兜底
- [ ] 测试覆盖认证、越权、RBAC、缓存失效；Redis 测试环境与 PostgreSQL 测试库同样 fail-closed
- [ ] 所有结构变更继续走 Alembic，不使用 `create_all()`

## 本周暂不加入

- Refresh Token 轮换、OAuth 第三方登录
- 消息队列 / 重试 Worker（D-002 的补偿队列留到后续周，本周先做到“不破坏响应 + 记录日志”）
- RAG、Embedding、向量检索
- Agent、LangGraph、MCP
- Worker、独立缓存集群
- 复杂前端（登录页等可以留给前端周）

## 执行纪律

1. 延续 Week 6 纪律：迁移先行、测试先行、fail-closed。
2. 认证相关代码不手写加密算法，只用成熟库（passlib/bcrypt、PyJWT）。
3. `JWT_SECRET`、数据库密码只从环境变量读取；缺失时 fail-closed（启动报错），不给硬编码默认密钥。
4. 每次加表、加列、加索引，先写迁移再写代码。
5. 缓存必须设计失效路径：找出所有会改变缓存内容的写操作，全部主动失效，不依赖 TTL 兜底。
6. 测试继续使用独立 PostgreSQL 测试库和独立 Redis 测试库；不访问开发数据，Redis 不可用时测试失败而不是静默跳过。
7. 密码哈希、token、请求体敏感字段不写入日志。
8. 第一次实现核心代码时，只向 AI 要解释、接口建议和代码审查。

## 目标目录（Week 6 基础上新增）

```text
app/
├── api/
│   ├── auth.py              # 注册、登录
│   └── users.py             # admin 用
├── core/
│   ├── security.py          # 密码哈希 + JWT 签发/校验
│   └── deps.py              # get_current_user / require_role
├── db/
│   └── redis.py             # 异步 Redis 客户端
├── models/
│   └── user.py
├── repositories/
│   └── user_repository.py
├── schemas/
│   ├── auth.py
│   └── user.py
└── services/
    └── auth_service.py

docker-compose.yml            # 增加 redis 服务
.env.example                  # 增加 JWT_SECRET、REDIS_URL 等
```

该目录是本周目标，不要求第一天一次建完。

---

## Day 1：用户模型、密码哈希与注册/登录


### 任务

- [x] 更新 `REQUIREMENTS.md`：新增 `/auth/register`、`/auth/login`，标注哪些接口需要登录
- [x] 设计 `users` 表（id、username 唯一、password_hash、role、created_at）
- [x] 用 Alembic 生成 `users` 表迁移
- [x] 决定 `conversations.user_id` 的迁移策略（已有数据怎么办：重建开发库 vs 迁移中 backfill），把选择写进 `Decisions.md`
- [x] 实现 User ORM Model、`user_repository`、`auth_service`、auth router
- [x] 注册接口：校验用户名唯一和密码强度；密码用 bcrypt/argon2 哈希后入库
- [x] 登录接口：验证通过后签发 JWT；失败统一返回 401，不区分“用户不存在/密码错误”
- [x] 说明异步接口里密码哈希为什么用 `asyncio.to_thread`（或等价方式）不阻塞事件循环


### 只补这些知识

- 哈希与加密的区别；加盐；bcrypt/argon2 为什么适合存密码，MD5/SHA 为什么不合适
- 为什么不要自研加密/哈希算法
- 唯一约束与注册冲突返回 409
- 401（未认证）、409（冲突）的语义

### Day 1 验收

- [x] 注册 + 登录走通（测试或 curl）
- [x] 数据库中 password_hash 是哈希值，绝无明文
- [x] 重复用户名返回 409；错误密码返回 401
- [x] 能解释 bcrypt 的“自带盐 + 慢哈希”分别解决什么问题

---

## Day 2：JWT 认证与资源归属

### 任务

- [x] 实现 `core/security.py`：JWT 签发与校验（固定 HS256、`sub`、`exp`）
- [x] 实现 `get_current_user` 依赖（`OAuth2PasswordBearer`）
- [x] 保护 `/conversations*` 和 `/chat*`：无 token 或无效 token 返回 401
- [x] 给 `conversations` 加 `user_id` 外键；所有会话查询按当前用户过滤
- [x] 创建会话、聊天时把当前用户的 `user_id` 写入
- [x] 访问他人会话返回 404（不是 403）
- [x] `JWT_SECRET` 从配置读取，缺失 fail-closed，不硬编码
- [x] 测试：无 token 401、伪造/过期 token 401、访问他人会话 404、自己的会话正常

### 只补这些知识

- JWT 结构（header.payload.signature）与 HS256 共享密钥原理
- JWT 为什么“无状态”；为什么 payload 不能放敏感信息
- `OAuth2PasswordBearer` 与 Bearer token 的工作方式（概念级）
- IDOR / 越权漏洞：为什么查询资源前要先校验归属
- 401、403、404 各自的语义与选择

### Day 2 验收

- [x] 能手画 JWT 三段的结构并解释每一段
- [x] 能解释越权访问为什么返回 404 而不是 403
- [x] 所有资源接口必须先通过认证依赖
- [x] 数据库里每条 conversation 都能对应到明确的 user_id

---

## Day 3：RBAC 与 Week 6 债务收尾

### 任务

- [x] 定义 user / admin 两种角色，注册用户默认为 user
- [x] 实现 `require_role` / `require_admin` 依赖；新增一个 admin 接口（如 `GET /users`）
- [x] 权限不足返回 403
- [x] 收尾 D-001：`messages` 加 `is_complete` 列（迁移）；流式正常结束为 true，中断为 false；历史接口返回该字段
- [x] 收尾 D-002：流式保存 assistant 消息包 `try/except + logging`；保存失败不破坏已经发出的流
- [x] 测试：中断标记、LLM 错误标记、保存失败兜底、admin/user 权限矩阵

### 只补这些知识

- RBAC 基本概念：角色、权限、最小权限原则
- 403 的语义与使用场景
- 日志规范：记录错误上下文，但绝不记录密码、token、完整请求体

### Day 3 验收

- [x] 权限矩阵有测试覆盖（user 访问 admin 接口返回 403）
- [x] 中断的流式消息在数据库里 `is_complete = false`
- [x] 保存失败不会让流中断，且日志有可排查记录
- [x] `Decisions.md` 中 D-001 / D-002 的待办状态已更新

---

## Day 4：Redis 缓存

### 任务

- [x] `docker-compose.yml` 增加 redis 服务；`.env.example` 增加 `REDIS_URL`
- [x] 实现异步 Redis 客户端（连接池），在 lifespan 中正确关闭
- [x] 给 `GET /conversations` 加 Redis 缓存：TTL + 缓存键包含 `user_id`
- [x] 找出所有会改变会话列表内容的写操作（创建会话、标题变化、删除），全部主动失效
- [x] 增加缓存命中与失效的集成测试
- [x] 测试环境：真实 Redis + 独立测试库，fail-closed 校验，不静默跳过
- [ ] （可选加分）用 Redis 给 `/chat` 做滑动窗口限流

### 只补这些知识

- Redis 是什么：内存 KV 存储、TTL、常用数据结构
- cache-aside 读写策略；“先写库、后失效缓存”为什么是这个顺序
- 缓存穿透、缓存击穿、缓存雪崩的区别与各自的应对
- 为什么缓存键必须带 `user_id`：跨用户数据泄露
- 缓存一致性：为什么不能只依赖 TTL

### Day 4 验收

- [x] 能画出 cache-aside 的读流程和写流程
- [x] 失效后第二次请求能拿到新数据（有测试证明）
- [x] 能分别解释缓存穿透/击穿/雪崩并各给一个应对手段
- [x] Redis 不可用时测试 fail-closed（失败而不是假装通过）

---

## Day 5：测试、安全审计、文档与最终验收

### 任务

- [ ] 全部测试通过：接口 + 认证 + 权限矩阵 + 越权 + 缓存失效
- [ ] 安全审计清单逐项过：
  - 密码只存哈希
  - `JWT_SECRET` 不硬编码、不进 Git
  - 日志不打印敏感字段
  - 所有资源路径都有归属校验，越权统一 404
  - 缓存不跨用户、不缓存敏感数据
- [ ] 更新 README：启动（含 redis）、迁移、测试命令
- [ ] 更新 `Decisions.md`：本周新决策（归属 404 策略、缓存失效策略、JWT 方案）
- [ ] 创建清晰的 Week 7 提交
- [ ] 准备 20 分钟无稿讲解

### 最终命令验收

```bash
docker compose up -d
alembic upgrade head
python -m compileall -q app tests
python -m pytest -q
git diff --check
git status --short
```

### Week 7 完成定义

- [ ] 注册、登录、JWT 认证可用
- [ ] 所有资源接口强制登录且归属校验正确
- [ ] user / admin 角色权限生效
- [ ] Redis 缓存命中与失效可验证
- [ ] D-001 / D-002 收尾完成并有测试
- [ ] 测试全部通过且 fail-closed
- [ ] 密钥与敏感配置未进入 Git
- [ ] 能进行一次 20 分钟无稿项目讲解

## 必须能回答

- 为什么密码要加盐慢哈希，不能直接 MD5/SHA？
- JWT 三段分别是什么？HS256 的密钥起什么作用？
- 为什么越权访问返回 404 而不是 403？401 / 403 / 404 分别在什么场景用？
- RBAC 的“角色—权限”如何落到代码依赖上？
- cache-aside 的读写流程？为什么先写库再失效缓存？
- 缓存穿透、击穿、雪崩的区别和各自应对？
- 为什么缓存键要带 `user_id`？
- `is_complete` 标记解决什么问题？流式保存失败为什么不能破坏已发出的响应？
- Week 6 的工程纪律（迁移、独立测试库、fail-closed）在本周如何延续？

## 本周结束后的下一步

Week 8 可选方向：消息队列/重试 Worker（兑现 D-002 的补偿策略）、RAG 与向量检索、或接入简单前端实现登录与页面。
