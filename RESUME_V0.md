# 简历项目描述 v0

## 项目名称

AI Chat Backend V2｜可靠多用户 LLM 后端

## 技术栈

Python、FastAPI、Pydantic、SQLAlchemy AsyncIO、PostgreSQL、Alembic、Redis Streams、JWT、Docker Compose、pytest、Ruff、Pyright、GitHub Actions、Locust

## 项目描述

从需求重新实现的多用户 AI Chat 后端，支持普通/流式对话、会话历史、JWT/RBAC、缓存和异步失败补偿，重点验证事务边界、用户隔离、至少一次消费与容器化交付。

## 简历 bullet 草稿

- 使用 FastAPI、异步 SQLAlchemy 与 PostgreSQL 重构同步 SQLite Chat Demo，按 Router/Service/Repository 分层；通过 Alembic 管理迁移，并以短事务避免 LLM HTTP 调用期间占用数据库事务。
- 实现 JWT/RBAC 和会话资源归属校验，在 Repository 层同时按 `conversation_id + user_id` 过滤，使用跨用户 API 负例覆盖 IDOR；引入账户禁用状态，阻止迁移回填身份登录或继续使用旧 JWT。
- 针对 SSE 已返回但 assistant 落库失败场景，设计 Redis Streams 独立 Worker，采用 at-least-once、pending reclaim、dead-letter 和 PostgreSQL 唯一约束幂等，验证 commit 后/ACK 前崩溃不会重复写入。
- 建立 Ruff、Pyright、pytest coverage、pre-commit 和 GitHub Actions 门禁；生产镜像剥离开发依赖并以非 root 运行，测试环境强制使用独立 `_test` PostgreSQL 与 Redis DB 15。
- 在独立 Compose project 中使用固定延迟 mock LLM 完成 50 用户、40 秒 Locust 基线：2,702 次 Chat 请求、0% 错误、P50 150 ms、P95 370 ms、67.98 RPS，并核对 5,404 条消息全部完整落库。

## 使用前必须调整

- 最终只保留 3～4 条最符合目标 JD 的 bullet。
- 补仓库链接、本人职责和项目时间，不要虚构线上用户、生产部署或商业效果。
- “GitHub Actions”目前表示已经配置流水线；只有远端实际运行通过后，才能写“CI 全绿”。
- 压测数字必须保留测试条件，不能把 67.98 RPS 写成最大吞吐量。
- 面试前必须能脱离代码回答 `PROJECT_DEFENSE.md`，否则再漂亮的 bullet 也缺乏可信度。
