# 从 v1 到 v2：从聊天 Demo 到可验证的后端系统

## 结论

v1 证明了基本业务流程可以成立：创建会话、保存消息、调用模型并返回普通或流式结果。v2 的主要价值不是增加更多接口，而是把并发边界、数据归属、故障恢复和交付证据补完整。

这次重构最重要的变化可以概括为：

```text
v1：请求成功时功能可用
v2：明确谁能访问、何时提交、失败后如何恢复，以及如何证明这些行为
```

## 对照基线

对照项目是 `../ai-chat-backend`。以下 v1 问题来自它的代码和 README，而不是事后假设：

- 使用同步 `sqlite3`，普通 Chat 通过 `asyncio.to_thread()` 包装数据库操作。
- 流式路径使用同步 `httpx.Client` 和同步迭代器。
- 数据库结构由启动代码中的 `CREATE TABLE IF NOT EXISTS` 管理，没有版本化迁移。
- 没有用户、登录、角色和资源归属，会话 ID 是全局可访问资源。
- assistant 流式保存失败后没有可靠补偿队列。
- 没有 Redis 缓存、独立 Worker、Docker Compose、CI 或并发基线。
- 测试使用临时 SQLite 和 mock LLM，隔离简单，但没有覆盖真实 PostgreSQL/Redis 语义。

## 设计变化

| 维度 | v1 | v2 | 为什么改变 | 验证证据 |
| --- | --- | --- | --- | --- |
| 数据库 | 同步 SQLite | PostgreSQL 17 + asyncpg + SQLAlchemy AsyncSession | 学习真实事务、连接池、约束和并发写入语义 | 真实测试库、空库迁移、Repository/Service/API 测试 |
| 结构演进 | 启动时建表 | Alembic 线性 revisions | 结构变化可审查、可升级、可回退 | 单一 head、`alembic check`、空 schema upgrade |
| 身份 | 无用户 | bcrypt + JWT + RBAC + `is_active` | 资源必须绑定经过认证的主体 | 登录、过期/伪造 token、admin、禁用账户测试 |
| 数据隔离 | 按全局 conversation ID | Repository 同时按 ID 和 `user_id` 查询 | 在数据访问层阻止 IDOR | 跨用户会话/历史/Chat 负例 |
| 异步边界 | 同步 DB/流式客户端混入异步服务 | 异步 DB、共享 AsyncClient、异步 SSE | 避免事件循环被同步 I/O 阻塞 | async API/Service 测试与 50 用户基线 |
| 事务 | 每次 SQLite 函数内部 commit | 显式短事务，LLM 调用时不持有 DB 事务 | 避免慢外部调用长期占用事务/连接 | 失败后 user 消息保留、session rollback 可复用测试 |
| 流式完整性 | 保存收到的片段，但无完整性字段 | `is_complete` 区分完整/中断消息 | 历史数据不能把半截回复伪装成完整回答 | 正常、LLM 中断、客户端关闭测试 |
| 保存失败 | 日志/请求异常，没有可靠恢复 | Redis Stream + 独立 Worker | HTTP 流发出后不能再用状态码表达最终保存失败 | 入队、消费、pending、reclaim、DLQ 测试 |
| 重复消费 | 未处理 | at-least-once + `idempotency_key` 唯一约束 | commit 与 ACK 不能形成跨系统原子事务 | 重复、并发相同 key、commit 后重投测试 |
| 缓存 | 无 | 按用户分键的 cache-aside | 练习事实来源、失效和降级策略 | miss/hit/回填/失效/Redis 故障/用户隔离测试 |
| 运行方式 | 本机单进程 | 同镜像的 migrate/API/Worker 不同进程 | 职责和生命周期分离 | Compose health、migration exit 0、Worker SIGTERM |
| 质量门禁 | pytest | Ruff + Pyright + coverage + pre-commit + CI | 静态契约、运行行为和干净环境互补 | 本地门禁结果与 GitHub Actions 配置 |
| 性能证据 | 无 | 隔离 mock LLM 的 Locust 基线 | 将本系统延迟与外部模型波动分离 | 50 用户、40 秒、2,702 请求、0% 错误 |

## 三个关键架构取舍

### 1. 为什么不是 exactly-once

Worker 无法用一个本地事务同时提交 PostgreSQL 并 ACK Redis。如果先 ACK，随后数据库失败会永久丢任务；如果先 commit，ACK 前崩溃会重复投递。因此系统选择可实现、可验证的 at-least-once，并把重复写入交给 PostgreSQL 唯一约束原子裁决。

代价是每个有外部副作用的 Consumer 都必须单独设计幂等，不能把“消息 ID 唯一”误当成整个业务 exactly-once。

### 2. 为什么缓存故障可以降级，任务队列故障不能假装没事

会话列表的事实来源是 PostgreSQL，Redis 缓存失败时仍能读数据库，因此运行期可以 fail-open。重试 Stream 则承载“数据库已经失败后唯一的自动补偿机会”；如果数据库和 Redis 同时失败，系统已经没有可持久化的恢复记录，只能明确记录脱敏错误并承认无法保证补齐。

### 3. 为什么 API 和 Worker 共用镜像但分进程

两者使用相同代码与依赖，共用镜像避免构建漂移；但 API 负责 HTTP 请求生命周期，Worker 负责持续轮询、pending reclaim 和信号停止。分进程后，扩缩容、重启策略和最小环境变量可以独立配置。

## 量化证据

- 质量门禁：Ruff 与 Pyright 通过；pytest 覆盖率门槛为 70%，最终数字见 `WEEK8.md`。
- 生产镜像：仅安装运行依赖，以非 root 用户运行；开发工具缺失检查通过。
- 压测：50 用户、40 秒、固定 30 ms mock LLM；2,702 次请求，0 次失败，聚合 P50 150 ms、P95 370 ms、67.98 RPS。
- 业务核验：2,702 个会话、5,404 条完整消息；retry 与 dead-letter stream 都为 0。

这些数字只说明当前本机固定负载下的基线，不代表生产容量，也不能证明真实模型的首 token 延迟。

## v2 仍然没有解决什么

- 上下文固定最近 20 条，没有 tokenizer 和 token budget。
- LLM 协议仍直接绑定当前 provider，没有 adapter、fallback、circuit breaker 和成本记录。
- JWT 没有 refresh、撤销、轮换和登录限流。
- 没有 request/trace ID、结构化指标、告警和分布式 tracing。
- 没有 transactional outbox；PostgreSQL 与 Redis 同时失败时仍可能缺失自动补偿记录。
- Redis Stream 中含 assistant 正文；当前只限制长度，正式环境还需要网络隔离、认证、传输/静态加密和保留策略。
- 依赖尚未使用 lockfile/hash 固定，也未接入自动依赖漏洞扫描。
- 压测是单机、固定 50 用户的短基线，没有阶梯加压、资源利用率或饱和点。

## 对下一阶段的影响

Week 9 不应推翻当前数据层和可靠性边界，而应在 LLM HTTP 边界之上增加 provider adapter、token-aware context、结构化输出、timeout/backoff/fallback 和回归样例。新增能力仍必须复用当前用户归属、短事务、脱敏日志和可测试失败路径。
