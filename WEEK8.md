# Week 8：可靠重试 Worker、幂等与生产化封板

> 项目：`ai-chat-backend-v2`  
> 来源：`other/LearningPlan.md` 第 5～8 周“生产后端基础与 Chat Backend V2”  
> 唯一主线：兑现 D-002 的后续补偿策略，让流式 assistant 消息保存失败后能够进入队列，由独立 Worker 安全重试。

## 本周为什么做这个

Week 7 已经做到“保存失败不破坏客户端已经收到的流”，但数据库中仍可能缺少这条 assistant 消息。Week 8 要补上可靠性闭环：

```text
API 流式输出
  → 首次保存 assistant 消息
  → 保存失败时写入 Redis Stream
  → 独立 Worker 消费
  → PostgreSQL 幂等写入
  → commit 成功后 ACK
```

本周选择 Redis Streams，是因为项目已经有 Redis，并且当前 redis-py 支持 `XADD / XREADGROUP / XACK / XAUTOCLAIM`。目标是理解消息语义，不是自研一个完整消息中间件。

## 本周目标

- [x] 定义版本化的重试任务契约，任务不包含密码、token 等认证秘密
- [x] 给 assistant 消息增加数据库级幂等键，重复消费不会重复落库
- [x] 用 Redis Stream 投递失败任务，并限制 Stream 长度
- [x] 独立 Worker 使用 Consumer Group 消费，数据库 commit 成功后才 ACK
- [x] Worker 崩溃后能认领 pending 消息并继续处理
- [x] 超过最大重试次数的任务进入 dead-letter stream
- [x] `/chat/stream` 保存失败时入队；队列也失败时仍不破坏已发出的响应
- [ ] Docker Compose 能启动 API、PostgreSQL、Redis 和 Worker
- [ ] CI 自动运行 lint、类型检查和 pytest
- [ ] mock LLM 下完成 50 并发压测，记录 P50、P95 和错误率

## 本周暂不加入

- Celery、Kafka、RabbitMQ 或云消息队列
- 多 Worker 横向扩容和跨机器部署
- “Exactly once” 宣称；本周实现的是 at-least-once + 幂等
- 复杂任务调度、优先级队列和可视化后台
- RAG、Embedding、Agent、LangGraph、MCP
- token-aware 上下文截断：顺延至 Week 9，与 tokenizer 和模型上下文预算一起完成

## 核心纪律

1. PostgreSQL 仍是事实来源；Redis Stream 是待处理任务载体。
2. 幂等必须依赖数据库唯一约束，不能使用“先查再插”的竞态写法。
3. Worker 只能在数据库 commit 成功后 ACK；先 ACK 可能永久丢消息。
4. 消息系统按 at-least-once 设计，默认同一任务可能被处理多次。
5. 测试继续使用 `_test` PostgreSQL 和 Redis DB 15，依赖不可用时 fail-closed。
6. 不修改历史迁移；新增字段必须创建新的 Alembic revision。
7. 日志只记录 `job_id`、`conversation_id`、attempt、状态和异常类型，不记录消息正文、token 或异常原文。
8. Redis Stream 中包含用户可见的 assistant 内容，必须限制长度，不把队列当永久存储。
9. Worker 循环必须可以单次执行和优雅停止，不能只能依靠无限循环测试。

## 目标目录

```text
app/
├── queue/
│   └── message_retry_queue.py   # Stream key、任务入队、Consumer Group 操作
├── schemas/
│   └── retry_job.py             # 版本化任务契约
└── workers/
    └── message_retry_worker.py  # 单条处理、ACK、重试与死信

app/tests/
├── test_queue/
│   └── test_message_retry_queue.py
└── test_workers/
    └── test_message_retry_worker.py

Dockerfile
load_tests/
└── locustfile.py
```

目录可以按实际实现微调，但 API、队列和 Worker 的职责不能混在一个模块里。

---

## Day 1：任务契约与数据库幂等

### 任务

- [x] 在 `Decisions.md` 写 D-008：为什么选择 at-least-once + 数据库幂等
- [x] 定义 `MessageRetryJob`，至少包含 `version / job_id / idempotency_key / conversation_id / user_id / content / is_complete / attempt`
- [x] 给 `messages` 增加可空的 UUID `idempotency_key`
- [x] 为非空 `idempotency_key` 建立唯一约束或唯一索引
- [x] Repository 增加原子幂等写入：首次写入成功，重复 key 不产生第二条消息
- [x] 原有普通 user/assistant 写入仍可不传幂等键
- [x] 测试真实 PostgreSQL：重复 key、不同 key、`NULL` key 和并发语义

### 必须亲手写

- D-008 的背景、选择、理由和代价
- Alembic 迁移及 downgrade
- `MessageRetryJob` 的字段和校验
- 数据库唯一约束下的幂等写入核心逻辑
- “重复投递为什么不能靠先 SELECT 再 INSERT”的解释

### 可以由 AI 协助

- 为旧调用点补可选参数
- 测试数据构造和重复断言
- 格式整理、类型标注和迁移静态检查

### Day 1 验收

- [x] 相同 `idempotency_key` 写两次，数据库只有一条消息
- [x] 两个不同 key 可以正常写入
- [x] 原有不带 key 的消息写入不受影响
- [x] 能解释唯一约束如何消除并发竞态
- [x] Alembic 只有一个 head，模型与迁移一致

---

## Day 2：Redis Stream 生产者与任务序列化

### 任务

- [x] 定义固定 Stream key、dead-letter key 和 Consumer Group 名称
- [x] 用 `XADD` 入队，任务带 schema version
- [x] 使用 `MAXLEN` 限制队列长度，避免无限占用内存
- [x] 实现任务序列化与反序列化；格式错误不能进入数据库写路径
- [x] Consumer Group 创建过程可重复执行，不因 group 已存在而失败
- [x] 使用真实 Redis DB 15 测试入队、读取、长度限制和用户隔离信息

### 只补这些知识

- Stream entry ID、Consumer Group、consumer、pending entry
- `XADD`、`XREADGROUP`、`XACK` 各自解决什么问题
- Redis Stream 与普通 List 的差异
- 为什么任务契约需要 `version`

### Day 2 验收

- [x] 入队后能从 Consumer Group 读取并还原为 `MessageRetryJob`
- [x] 重复创建 group 不报错
- [x] 队列日志不包含 content
- [x] Redis 测试环境错误时测试失败而不是跳过

---

## Day 3：Worker、ACK 顺序与失败重试

### 任务

- [ ] 将“处理一条任务”和“持续轮询”拆开，核心逻辑可直接测试
- [x] Worker 为每条任务创建独立 AsyncSession
- [x] 幂等保存并 commit 成功后执行 `XACK`
- [x] 数据库失败时不 ACK，让任务保留在 pending
- [x] 使用 `XAUTOCLAIM` 认领超时 pending 任务，处理 Worker 崩溃场景
- [x] attempt 超过上限后写入 dead-letter stream，再 ACK 原任务
- [x] 日志包含 job_id、attempt、状态与 error_type，不含正文和异常原文

### 必须亲手写

- commit 与 ACK 的顺序
- 单条任务处理函数
- pending/reclaim 的状态变化
- 最大重试次数与死信条件

### Day 3 验收

- [x] commit 成功才 ACK
- [x] commit 失败时 entry 留在 pending
- [x] 同一任务重复处理不会重复插入消息
- [x] 崩溃后另一个 consumer 能认领并完成任务
- [x] 超限任务进入 dead-letter stream

---

## Day 4：接入流式失败路径与 Docker Worker

### 任务

- [x] 流式 assistant 第一次保存前生成一次 `idempotency_key`
- [x] 首次保存与重试任务使用同一个 key，覆盖“提交结果不确定”场景
- [x] 保存失败后入队；入队失败只记录脱敏日志，不破坏已发出的响应
- [x] 为 API、Worker 日志加入可关联的 `job_id`
- [ ] 增加 Dockerfile，并在 Compose 中加入 API 和 Worker 服务
- [ ] Worker 支持 SIGTERM/KeyboardInterrupt 优雅停止
- [x] 补齐失败矩阵测试

### 第二阶段细则：Worker 运行入口

Worker 分成三层，不能把业务处理、轮询和进程生命周期塞进同一个无限循环：

1. `process_retry_entry()`：处理单条任务，负责数据库事务、幂等和 ACK，现有实现继续复用。
2. `run_once()`：完成一轮有限调度，先认领超时 pending，再读取一批新任务；单条可重试故障不能阻断同批其他任务。
3. 独立 runner：创建 Redis 客户端、安装 SIGTERM/SIGINT handler、循环调用 `run_once()`、对 Redis 暂时故障退避，并在退出时关闭 Redis 和数据库连接池。

运行入口使用 `python -m app.workers.run_message_retry_worker`，供 Docker Worker service 调用。Consumer name 优先从 `WORKER_CONSUMER_NAME` 读取，未配置时使用 hostname + pid，避免多个 Worker 共享同一个 consumer 身份。

每次 `XREADGROUP` 必须设置有限的 block timeout，使停止信号能在明确时间内生效。收到停止信号后只设置 `asyncio.Event`：停止领取新任务，但允许当前任务完成 commit/ACK；容器的 stop grace period 必须大于单条任务的超时预算。

#### 重要内容

- 单条处理、单轮调度和进程生命周期为什么必须分层。
- 为什么 SIGTERM 不能直接取消正在 commit 的任务。
- 为什么未知编程异常应让 Worker 退出并由进程管理器重启，而数据库/Redis 暂时故障可以保留任务并退避重试。
- 为什么 Consumer Group 名可以固定，但 consumer name 应区分进程实例。
- 为什么轮询必须有有限阻塞时间，不能永久阻塞也不能无等待空转。

#### 运行入口验收

- [ ] `run_once()` 可在测试中有限执行并同时处理 reclaimed 与新任务
- [ ] 单条数据库暂时故障保留 pending，但不阻断同批其他任务
- [ ] 未知异常继续抛出，不被轮询层吞掉
- [ ] stop event 设置后不再领取新任务，当前任务可以完成
- [ ] Redis 暂时不可用时退避，避免 CPU 空转和日志风暴
- [ ] runner 退出时关闭 Redis 客户端和 SQLAlchemy engine

### 失败矩阵

| 首次 DB 保存 | Redis 入队 | 客户端响应 | 后续结果 |
| --- | --- | --- | --- |
| 成功 | 不需要 | 正常 | 消息已落库 |
| 失败 | 成功 | 正常 | Worker 后续补偿 |
| 失败 | 失败 | 正常 | 脱敏错误日志，等待人工排查 |
| 结果不确定 | 成功 | 正常 | Worker 依靠幂等键消除重复 |

### Day 4 验收

- [x] 保存失败且入队成功时，Worker 最终补齐历史消息
- [x] 队列失败不会把 StreamingResponse 弄断
- [x] 重复任务不会产生重复消息
- [ ] `docker compose up` 能启动 API、PostgreSQL、Redis、Worker

---

## Day 5：质量门禁、压测与 Week 5～8 封板

### 任务

- [ ] 配置 ruff、pyright 或 mypy、pytest coverage 和 pre-commit
- [ ] CI 自动执行 lint、type check、test，且不连接开发数据库/Redis
- [ ] mock LLM 下做 50 并发压测，不把真实模型延迟混入结果
- [ ] 记录 P50、P95、吞吐量、错误率和测试环境
- [ ] 目标错误率低于 1%；未达到时给出瓶颈和下一步，而不是修改数据
- [ ] 更新 README：完整 Compose、Worker、失败恢复和压测命令
- [ ] 写 `v1-to-v2.md`：列出 v1 的问题、v2 的设计变化、证据和剩余债务
- [ ] 完成一次 45～60 分钟项目答辩并准备简历 v0

### 最终验收命令

```bash
docker compose up -d
alembic upgrade head
python -m compileall -q app
python -m pytest -q
ruff check .
pyright app
git diff --check
git status --short
```

### Week 8 完成定义

- [ ] API 保存失败后存在可验证的补偿路径
- [ ] 消费语义明确为 at-least-once，重复投递由数据库幂等处理
- [ ] Worker 崩溃、数据库失败、Redis 失败均有测试
- [ ] Docker Compose 包含完整运行组件
- [ ] CI、覆盖率和 50 并发报告可复现
- [ ] 代码、迁移、日志、密钥和测试环境通过安全审计
- [ ] 能解释 ACK、pending、reclaim、dead letter、幂等和重试风暴

## 必须能回答

- 为什么消息队列通常只能承诺 at-least-once，而不是 exactly-once？
- 为什么 ACK 必须发生在数据库 commit 之后？
- Worker 在 commit 后、ACK 前崩溃会发生什么？为什么不会重复写？
- 为什么“先 SELECT 是否存在，再 INSERT”不能保证并发幂等？
- pending entry 和 dead-letter stream 分别解决什么问题？
- Redis 和 PostgreSQL 同时不可用时，系统还能保证什么、不能保证什么？
- 为什么不能把完整消息正文和异常原文写入日志？

## Week 9 衔接

Week 9 进入 LLM 原理与 API 工程：provider adapter、tokenizer、token-aware 上下文预算、结构化输出和回归样例。Week 8 不提前展开这些内容。
