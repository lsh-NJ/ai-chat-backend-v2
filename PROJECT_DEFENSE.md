# AI Chat Backend V2 项目答辩提纲

目标时长：45～60 分钟。答辩时不要从文件树开始讲，要从问题、约束、选择和证据开始。

## 时间安排

| 时间 | 内容 | 必须讲清 |
| --- | --- | --- |
| 0～5 分钟 | 需求与 v1 问题 | 为什么 v1 是 Demo，v2 要解决哪些系统问题 |
| 5～12 分钟 | 总体架构 | Router/Service/Repository、API/Worker/migration 的边界 |
| 12～20 分钟 | 数据与事务 | PostgreSQL、Alembic、短事务、约束、用户归属 |
| 20～28 分钟 | 认证与缓存 | JWT/RBAC/IDOR、cache-aside、失效顺序、fail-open/closed |
| 28～40 分钟 | 流式可靠性 | 已发响应后的失败、Stream、ACK、pending、reclaim、DLQ、幂等 |
| 40～47 分钟 | 交付证据 | Docker、CI、测试隔离、覆盖率、50 用户压测 |
| 47～55 分钟 | 故障推演 | DB/Redis/Worker/LLM/客户端断开分别发生什么 |
| 55～60 分钟 | 局限与下一步 | outbox、token budget、provider adapter、观测与安全债务 |

## 三分钟开场版本

> v1 已经能调用模型并保存普通和流式消息，但使用同步 SQLite，没有用户隔离、迁移、可靠补偿和部署证据。v2 用异步 PostgreSQL、Redis 和独立 Worker 重建了这条链路。核心设计是短事务、按用户的数据访问、cache-aside，以及流式保存失败后的 at-least-once + 数据库幂等。项目用真实测试数据库、CI、生产镜像和固定 mock LLM 的 50 用户压测验证，而不是只展示成功 Demo。

开场后立即画一张端到端图，再选择一条最重要的失败链路深入。

## 必须能独立回答

### 为什么消息系统通常只能承诺 at-least-once？

Worker 的业务提交和 Redis ACK 属于两个系统，没有同一个本地原子事务。为了避免永久丢任务，必须先完成业务提交再 ACK；如果两者之间崩溃，任务就会再次投递。因此可实现的语义是至少处理一次，重复由幂等消除。声称 exactly-once 往往只是把重复处理转移到了业务层。

### 为什么 ACK 必须在 commit 后？

ACK 表示消息系统可以忘记该任务。如果先 ACK 后数据库失败，任务既不在数据库，也不再 pending，形成永久丢失。先 commit 的最坏情况只是重复投递，而重复可以通过幂等处理。

### commit 后、ACK 前崩溃会怎样？

消息仍留在 pending，超过 idle 时间后被原 Worker 或另一个 Worker reclaim。它会再次尝试插入；相同 `idempotency_key` 被数据库唯一约束识别为已完成，Consumer 随后 ACK，因此不会产生第二条消息。

### 为什么“先 SELECT 再 INSERT”不可靠？

两个 Worker 可以同时 SELECT 到不存在，然后都 INSERT。检查和写入不是一个原子裁决。唯一约束由数据库在写入点串行化冲突，才是并发下的最终保证。

### pending 与 dead-letter 分别解决什么？

pending 保存已经交付但尚未 ACK 的任务，用于暂时失败和 Worker 崩溃恢复；dead-letter 保存格式非法、归属非法或超过重试上限的任务，阻止毒消息无限循环，并为人工排查保留受控证据。

### PostgreSQL 与 Redis 同时不可用还能保证什么？

如果流式内容已经发给客户端，系统不能撤回它；也不能保证 assistant 历史最终补齐，因为首次保存和补偿入队都失败。当前只能保证不让持久化异常破坏已发的流，并记录脱敏故障元数据。要缩小该窗口需引入 outbox/WAL 等更强持久化边界。

### 为什么日志不能写完整正文和异常原文？

消息可能含个人信息、公司数据、prompt injection 或密钥；数据库/HTTP 异常也可能回显参数。日志通常保留更久、访问面更广。排障应记录 job ID、conversation ID、attempt、状态和异常类型，再通过受控数据系统定位正文。

### 什么是重试风暴，当前如何限制？

依赖持续故障时，大量任务立即重试，会进一步压垮依赖并制造日志风暴。当前通过有限 batch、pending idle 时间、Worker Redis 退避、最大投递次数和 DLQ 限制；正式系统还需指数退避、抖动、全局并发限制和告警。

## 故障推演清单

答辩者应逐项说出客户端结果、数据库状态、队列状态和恢复动作：

1. LLM 普通请求超时。
2. SSE 输出一半后 LLM 断开。
3. 客户端主动关闭 SSE。
4. assistant commit 失败、Redis 正常。
5. assistant commit 与 Redis 入队都失败。
6. Worker commit 前崩溃。
7. Worker commit 后、ACK 前崩溃。
8. Redis 缓存读取失败。
9. 用户 A 猜到用户 B 的 conversation ID。
10. migration 创建的占位用户尝试登录。

## 现场演示顺序

```bash
docker compose ps --all
curl http://127.0.0.1:8000/health
.venv/bin/alembic heads
.venv/bin/ruff check .
.venv/bin/pyright app
.venv/bin/pytest -q
```

不要在答辩现场临时连接开发库做破坏性 SQL。故障证据优先展示已有自动测试和报告。

## 评分标准（100 分）

- 需求与架构边界：15
- 数据、事务与迁移：15
- 认证、授权与缓存：15
- 消息语义、幂等和失败恢复：25
- 测试、CI、容器与压测证据：15
- 局限、替代方案和表达：15

低于 70：仍停留在“记得代码怎么写”；70～84：能负责当前项目；85 以上：能主动推演失败、比较方案并诚实限定证据。
