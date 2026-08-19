# Week 8 安全审计

## 审计结论

审计日期：2026-08-18。

在当前学习项目范围内，代码、迁移、当前工作树配置、日志路径、生产镜像边界和测试隔离通过封板审计；未发现仍未处理的 critical/high 级问题。审计中发现的迁移占位账户可登录风险已通过新 revision 修复。

这不是生产环境渗透测试或合规认证。Git 历史全量密钥扫描、第三方依赖 CVE、TLS/反向代理和云基础设施权限不在本次自动审计覆盖范围内，已列入剩余风险。

## 已验证控制

| 范围 | 控制 | 证据 |
| --- | --- | --- |
| 密钥 | `.env` 被 Git 和 Docker context 排除；示例文件不再提供可直接启动的密码/JWT 值 | `.gitignore`、`.dockerignore`、`.env.example` |
| JWT | 只接受 HS256，要求 `sub/exp`；密钥为空、示例值或短于 32 字符时拒绝启动 | `app/core/security.py` 及单元测试 |
| 密码 | bcrypt 随机盐哈希，API/schema 不返回 `password_hash` | security/auth/users API 测试 |
| 账户状态 | 登录和每次 JWT 鉴权都拒绝 `is_active=false` | auth/authorization 测试 |
| 迁移占位用户 | 历史 revision 不修改；后续 revision 将 `default` 回填用户禁用 | D-009、`c7d8e9f0a1b2`、迁移测试 |
| 资源归属 | `user_id` 只取自认证上下文；Repository 同时按资源 ID 和 user ID 过滤 | 跨用户读取/Chat 负例 |
| 越权信息泄漏 | 他人资源与不存在资源统一 404；非活跃用户统一 401 | API 测试 |
| SQL | ORM/参数化 SQL；engine 设置 `hide_parameters=True` | Repository、Alembic 与 session 配置 |
| 日志 | Worker/API 错误只记录 job/conversation/attempt/status/error type，不记录正文、token、密码或异常原文 | 日志代码与 caplog 测试 |
| 队列 | payload 严格版本化校验；非法/越权任务不进入正常写路径；Stream/DLQ 都有长度上限 | queue/worker 测试 |
| 测试数据 | PostgreSQL 必须 `_test`；Redis 必须独立 DB 15；依赖不可用时 fail-closed | `app/tests/conftest.py` |
| 压测数据 | 固定 `chat_v2_load_test`、独立 project/volume/端口，脚本退出自动清理 volume | load-test Compose 与 runner |
| 容器 | 生产镜像使用非 root 用户，只安装运行依赖；API/Worker/migration 按职责分配环境变量 | Dockerfile、Compose、镜像检查 |
| CI | 明确提供测试数据库/Redis/mock LLM 配置，不读取本地 `.env` | `.github/workflows/ci.yml` |

## 审计中已修复的问题

### SA-001：迁移回填用户缺少禁用状态

- 原因：旧迁移为了给历史会话补 `user_id`，创建了带固定有效 bcrypt 哈希的 `default` 记录，但用户表没有 `is_active`。
- 风险：安全依赖“密码应该没人知道”，而不是结构保证；一旦凭据可得，历史会话可能被访问。
- 修复：新增 `users.is_active`；新注册默认 true；回填用户设为 false；登录和 JWT 鉴权都检查状态。
- 验证：非活跃账户密码正确仍返回 401；其已有 JWT 也返回 401；空库迁移后 `default.is_active=false`。

### SA-002：示例密钥容易被直接沿用

- 原因：`.env.example` 曾包含 `replace_me` 一类非空占位值。
- 风险：Compose 的“变量必须存在”检查可能被占位值绕过。
- 修复：数据库密码、JWT 和真实 LLM key 示例改为空；README 明确要求生成独立密钥；JWT 代码继续拒绝示例值和弱长度。

## 剩余风险与优先级

### P1：认证防滥用不足

- 没有登录/注册速率限制、账户锁定或异常登录告警。
- JWT 没有 refresh token、主动撤销、密钥轮换、issuer/audience。
- 当前适合学习和受控部署，不适合直接暴露到公网。

### P1：双写仍有不可恢复窗口

流式 assistant 保存失败后再写 Redis；如果 PostgreSQL 与 Redis 同时不可用，客户端可能已经收到内容，但系统无法保证历史最终补齐。正式系统可评估 transactional outbox、持久化本地 WAL 或托管消息系统。

### P1：Redis payload 包含消息正文

Stream/DLQ 为补偿必须携带 assistant 内容。当前已限制长度并禁止写日志，但本地 Compose 没有 Redis ACL/TLS。正式部署必须使用私网、认证、传输加密、静态加密和保留/删除策略。

### P2：可观测性不足

日志字段已经脱敏，但尚未统一 JSON formatter、request/trace ID、指标和告警。仅记录错误而没有告警，仍可能导致补偿失败长期无人发现。

### P2：供应链控制不足

- Python 运行依赖使用范围约束而非完整 lock/hash。
- GitHub Actions 使用版本 tag 而非 commit SHA。
- CI 尚未运行依赖漏洞和容器镜像扫描。

### P2：边缘安全由部署层承担

当前 Compose 没有 HTTPS、反向代理、安全响应头、请求体总大小限制或网络策略。它是本地/学习部署定义，不是互联网入口方案。

## 本次执行的检查

```text
ruff check .
pyright app
pytest（真实 _test PostgreSQL + Redis DB 15）
alembic heads / 空库 upgrade / alembic check
git diff --check
当前工作树敏感模式扫描
生产镜像开发依赖缺失检查
Compose 环境与服务权限检查
```

封板标准是“风险被消除或明确记录并有下一步”，不是声称系统绝对安全。
