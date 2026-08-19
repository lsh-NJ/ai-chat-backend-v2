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
- [x] Docker Compose 能启动 API、PostgreSQL、Redis 和 Worker
- [x] CI 自动运行 lint、类型检查和 pytest
- [x] mock LLM 下完成 50 并发压测，记录 P50、P95 和错误率

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

### 重要内容

- D-008 的背景、选择、理由和代价。
- Alembic 迁移及 downgrade。
- `MessageRetryJob` 的字段、校验和数据库唯一约束下的幂等写入逻辑。
- “重复投递为什么不能靠先 SELECT 再 INSERT”的解释。

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

- [x] 将“处理一条任务”和“持续轮询”拆开，核心逻辑可直接测试
- [x] Worker 为每条任务创建独立 AsyncSession
- [x] 幂等保存并 commit 成功后执行 `XACK`
- [x] 数据库失败时不 ACK，让任务保留在 pending
- [x] 使用 `XAUTOCLAIM` 认领超时 pending 任务，处理 Worker 崩溃场景
- [x] attempt 超过上限后写入 dead-letter stream，再 ACK 原任务
- [x] 日志包含 job_id、attempt、状态与 error_type，不含正文和异常原文

### 重要内容

- commit 与 ACK 的顺序。
- 单条任务处理函数与事务边界。
- pending/reclaim 的状态变化。
- 最大重试次数与死信条件。

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
- [x] 增加 Dockerfile，并在 Compose 中加入 API 和 Worker 服务
- [x] Worker 支持 SIGTERM/KeyboardInterrupt 优雅停止
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

- [x] `run_once()` 可在测试中有限执行并同时处理 reclaimed 与新任务
- [x] 单条数据库暂时故障保留 pending，但不阻断同批其他任务
- [x] 未知异常继续抛出，不被轮询层吞掉
- [x] stop event 设置后不再领取新任务，当前任务可以完成
- [x] Redis 暂时不可用时退避，避免 CPU 空转和日志风暴
- [x] runner 退出时关闭 Redis 客户端和 SQLAlchemy engine

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
- [x] `docker compose up` 能启动 API、PostgreSQL、Redis、Worker

### 部署验收证据

- `docker compose config --quiet` 通过。
- 同一应用镜像分别运行 `alembic upgrade head`、Uvicorn API 和 Retry Worker。
- migration 容器以退出码 0 完成，数据库处于 `b18f6a4d2c90 (head)`。
- API `/health` 返回 `{"status":"ok"}`，Compose healthcheck 为 healthy。
- Worker 收到 SIGTERM 后在 5 秒内以退出码 0 停止，随后可重新启动。
- migration 只获得数据库环境；Worker 只获得数据库和 Redis 环境，不注入 JWT 或 LLM API key。
- Docker Hub 拉取曾发生网络超时；本机使用同版本的缓存镜像覆盖 `PYTHON_IMAGE` 完成构建验证。默认仍使用官方 `python:3.12-slim`，正式 CI 需要再次验证默认镜像来源。

---

## Day 5：质量门禁、压测与 Week 5～8 封板

### 第一阶段细则：质量门禁架构

先完成可快速反馈、可在干净环境复现的质量流水线，压测和项目复盘在本阶段验收后再细化。

质量门禁分为四层：

1. `ruff`：处理格式、导入和常见静态错误，提供秒级反馈。
2. `pyright`：检查应用代码的跨模块类型契约，测试代码暂不作为严格类型门禁。
3. `pytest + coverage`：使用真实 `_test` PostgreSQL 和 Redis DB 15，覆盖率低于 70% 时失败。
4. `pre-commit + CI`：pre-commit 提供提交前快速检查；CI 在全新 Python 环境与独立服务中执行权威验收。

生产依赖和开发依赖必须分开：Docker 镜像只安装应用运行依赖；pytest、coverage、ruff、pyright 和 pre-commit 只进入开发/CI 依赖。CI 不读取本地 `.env`，必须显式声明测试数据库与 Redis 地址，并继续通过现有 fail-closed fixture 校验。

#### 重要内容

- 为什么本地检查追求快速反馈，而 CI 才是干净环境中的权威结果。
- 为什么“测试时临时覆盖开发数据库变量”不是可靠隔离，CI 必须从结构上只提供测试库。
- 为什么 lint、type check 和 test 解决的是三类不同问题，不能互相替代。
- 为什么生产镜像不应携带 pytest、pre-commit 等开发工具。
- 为什么覆盖率只是未测试代码的线索，不能直接代表测试质量。

#### 第一阶段验收

- [x] `ruff check .` 通过
- [x] `pyright app` 通过，严格检查范围不包含测试目录
- [x] `pytest --cov` 通过且总覆盖率不低于 70%
- [x] `pre-commit run --all-files` 通过
- [x] CI 使用 PostgreSQL `_test` 数据库与 Redis DB 15，依赖不可用时失败
- [x] CI 不读取或自动回退到开发数据库、开发 Redis 和真实 LLM API
- [x] 生产镜像不包含 pytest、coverage、ruff、pyright 或 pre-commit

#### 第一阶段验收证据

- `ruff check .`：通过。
- `pyright app`：`0 errors, 0 warnings, 0 informations`。
- `pytest --cov`：129 个测试通过，总覆盖率 91.85%，分支覆盖已启用。
- `pre-commit run --all-files`：Ruff 与 Pyright hooks 均通过。
- CI 显式启动 `chat_v2_test` PostgreSQL 和 Redis，并只向测试进程提供 Redis DB 15 作为测试地址；现有 fixture 会在环境错误或依赖不可用时 fail-closed。
- 生产镜像从 `requirements.txt` 构建；一次性容器确认 pytest、coverage、Ruff、Pyright 与 pre-commit 均不存在。

### 任务

- [x] 配置 ruff、pyright、pytest coverage 和 pre-commit
- [x] CI 自动执行 lint、type check、test，且不连接开发数据库/Redis
- [x] mock LLM 下做 50 并发压测，不把真实模型延迟混入结果
- [x] 记录 P50、P95、吞吐量、错误率和测试环境
- [x] 目标错误率低于 1%；未达到时给出瓶颈和下一步，而不是修改数据
- [x] 更新 README：完整 Compose、Worker、失败恢复和压测命令
- [x] 写 `v1-to-v2.md`：列出 v1 的问题、v2 的设计变化、证据和剩余债务
- [x] 准备 45～60 分钟项目答辩材料和简历 v0
- [ ] 不看材料完成一次真实项目答辩

### 第二阶段细则：隔离环境下的 50 并发压测

本阶段只测当前系统自身的链路：Locust → API → PostgreSQL / Redis → mock LLM。mock LLM 仍通过真实 HTTP 接口提供普通 JSON 和 SSE 流式响应，但使用固定内容与固定延迟，避免把真实模型的网络波动、限流和费用混入后端数据。

压测必须使用独立的 Compose project、独立 volume 和名称以 `_test` 结尾的 `chat_v2_load_test` 数据库。不能通过运行前临时修改开发环境变量来“保证安全”，也不能复用开发数据库后再人工清理。

50 个虚拟用户各自注册并登录，准备请求不计入聊天指标。每个聊天请求创建新会话，使每个样本的历史规模一致；`/chat` 与 `/chat/stream` 分开统计。流式接口当前记录的是收到完整响应的总耗时，不把它误称为首 token 延迟。

#### 重要内容

- 并发用户数、请求吞吐量和响应时间分别描述什么，为什么不能互相替代。
- P50 代表典型体验，P95 用于观察尾延迟；只看平均值为什么会隐藏少量慢请求。
- 为什么压测必须固定 LLM 行为、测试数据和运行时长，才能进行前后对比。
- 为什么负载测试不能接触开发库或生产库，隔离必须由 Compose project、数据库名和 volume 共同保证。
- 为什么错误率不能只看 HTTP 500，还要校验响应结构和业务结果。
- 为什么一次本机短压测只能作为基线，不能直接推导生产容量。

#### 第二阶段验收

- [x] mock LLM 同时支持普通响应和带 `[DONE]` 的 SSE 流式响应
- [x] 压测栈只连接 `chat_v2_load_test`，与开发 Compose project 和 volume 隔离
- [x] 50 个并发用户运行至少 30 秒，普通与流式聊天分别有样本
- [x] 记录每个接口的请求数、P50、P95、吞吐量和错误率
- [x] 校验响应状态、JSON 字段、会话 ID 和流式正文，而不只判断 HTTP 状态码
- [x] 错误率低于 1%；若失败则保留真实结果并定位瓶颈
- [x] 运行结束后可删除临时数据库 volume，报告文件仍保留

#### 第二阶段验收证据

- 50 用户、10 用户/秒启动、40 秒运行；普通与流式接口权重各 1。
- 共完成 2,702 次聊天，失败 0 次，聚合吞吐量 67.98 RPS。
- `/chat`：P50 150 ms，P95 370 ms；`/chat/stream`：P50 150 ms，P95 380 ms。
- 数据库中 2,702 个会话、5,404 条完整消息；retry 和 dead-letter stream 均为空。
- 详细环境、边界、原始指标解释和后续实验见 `load_tests/REPORT.md`。

### 第三阶段细则：项目封板、安全审计与答辩准备

本阶段不再增加业务功能，而是把“能运行的代码”整理成别人能够启动、审查、追问和复现的工程交付。文档必须同时写成功路径、失败边界、量化证据和剩余债务，不能只写技术栈列表。

安全审计发现问题时先修复再下结论：已经发布的 migration 不允许回改；示例密钥不能成为可直接启动的弱默认值；迁移占位身份必须由账户状态禁止登录。审计结论必须明确覆盖范围，不能把一次代码检查包装成渗透测试或生产安全认证。

#### 重要内容

- README、ADR、复盘报告和代码分别服务什么读者，为什么不能互相替代。
- 为什么架构复盘必须写替代方案、代价和证据，而不只是“v2 技术更多”。
- 为什么发现 schema drift 后应追加兼容迁移，而不是改历史 revision 或手工修库。
- 为什么安全审计的专业表现是明确剩余风险，而不是宣称“系统绝对安全”。
- 为什么简历指标必须带测试条件，不能把本机基线写成生产最大容量。
- 为什么准备了答辩答案不等于已经掌握，最终仍要脱离文档完成口头推演。

#### 第三阶段验收

- [x] README 包含完整 Compose、本地开发、Worker、失败恢复、门禁和压测命令
- [x] `v1-to-v2.md` 基于真实 v1 代码列出变化、证据、代价与剩余债务
- [x] 当前工作树密钥、认证、越权、日志、迁移、测试隔离和生产镜像完成审计
- [x] 迁移占位用户已禁用，密码与旧 JWT 均无法绕过
- [x] 发现并用追加 revision 修复开发库 `is_completed/is_complete` schema drift
- [x] 准备 45～60 分钟答辩提纲、故障推演题和简历 v0
- [ ] 不看答案完成一次真实口头答辩并达到 70 分

#### 第三阶段验收证据

- 新增 D-009 与 `c7d8e9f0a1b2`，为用户增加 `is_active` 并禁用迁移回填账户。
- 新增 D-010 与 `d8e9f0a1b2c3`，旧列名存在时原地修复，规范结构下 no-op，歧义结构 fail-closed。
- `alembic current / heads / check` 一致，输出 `No new upgrade operations detected`。
- 129 个测试通过，覆盖率 91.85%；Ruff、Pyright、pre-commit 和 `pip check` 通过。
- `.env.example` 的关键密钥为空，未填写时 Compose 配置阶段拒绝运行。
- 生产镜像以非 root 运行，且不包含 pytest、coverage、Ruff、Pyright 或 pre-commit。
- 详细风险与剩余项见 `SECURITY_AUDIT.md`；答辩材料见 `PROJECT_DEFENSE.md`。

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

- [x] API 保存失败后存在可验证的补偿路径
- [x] 消费语义明确为 at-least-once，重复投递由数据库幂等处理
- [x] Worker 崩溃、数据库失败、Redis 失败均有测试
- [x] Docker Compose 包含完整运行组件
- [x] CI、覆盖率和 50 并发报告可复现
- [x] 代码、迁移、日志、密钥和测试环境通过安全审计
- [ ] 能脱离材料完整解释 ACK、pending、reclaim、dead letter、幂等和重试风暴（后续复习项，不阻断进入 Week 9）

### 最终验收结论（2026-08-19）

- **工程验收：通过。** 功能、失败路径、迁移、测试隔离、质量门禁、容器运行、压测、文档和安全审计均有可复现证据。
- **答辩材料验收：通过。** `PROJECT_DEFENSE.md` 已覆盖主要架构、关键链路、故障路径、证据、局限与后续方向，可以作为完整的项目答辩材料。
- **真实口头答辩：尚未验证。** 本周没有实际进行一次脱离材料的完整口头答辩，因此不把“材料质量”直接等同于“现场表达已经验收”；这项只作为后续穿插练习，不阻断 Week 9。
- **进度决定：Week 8 验收结束，可以进入 Week 9。** 答辩缺口保留为后续穿插复习，不通过伪造勾选掩盖，也不继续占用本周工程主线。
- **需要带入后续的习惯：** 每新增一个 LLM 能力，都要同时回答它的组件边界、状态来源、失败行为、观测证据和仍未解决的问题。

## 必须能回答

- 为什么消息队列通常只能承诺 at-least-once，而不是 exactly-once？
- 为什么 ACK 必须发生在数据库 commit 之后？
- Worker 在 commit 后、ACK 前崩溃会发生什么？为什么不会重复写？
- 为什么“先 SELECT 是否存在，再 INSERT”不能保证并发幂等？
- pending entry 和 dead-letter stream 分别解决什么问题？
- Redis 和 PostgreSQL 同时不可用时，系统还能保证什么、不能保证什么？
- 为什么不能把完整消息正文和异常原文写入日志？

## Week 9 衔接

Week 9 进入 LLM 原理与 API 工程的第一阶段：先建立 provider adapter，再完成 tokenizer 与 token-aware 上下文预算，并建立第一批回归样例。结构化输出留到后续周次展开，Week 8 不提前展开这些内容。
