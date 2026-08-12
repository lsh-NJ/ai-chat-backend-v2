# 项目问题记录（Problem Log）

> 简单记录开发过程中踩过的错，方便复盘和避免重复。每条一行：现象 → 原因 → 处理。

## Week 7（用户系统 / JWT / Redis）

- [x] `alembic upgread` 命令拼写错误 → 应为 `upgrade`；原因：手滑；处理：改正命令，可用 Tab 补全。
- [x] 迁移提前给 `conversations.user_id` 加 NOT NULL → 23 个测试全红（创建会话不传 user_id，数据库拒绝 NULL）；原因：结构约束先于代码写路径落地，形成中间态；处理：从迁移删掉 `alter_column`，NOT NULL 推迟到 Day 2 与归属过滤同批落地（见 D-004）。
- [x] 模型与迁移漂移（模型 `Mapped[int]` 非空、迁移 nullable）→ `alembic check` 报漂移；原因：模型和迁移分两次改没对齐；处理：迁移补齐 backfill，用 `alembic check` 验证。
- [x] D-004 决策文字自相矛盾（「可以为空」+「改为非空」）；原因：边写边改没回读；处理：重写为 backfill + Day 2 落地 NOT NULL。
- [x] 登录接口用 JSON body → 与 `OAuth2PasswordRequestForm` 不兼容，Swagger Authorize 会失败；原因：偏离 FastAPI 标准认证流程且未记录取舍；处理：改为表单登录。
- [x] 注册接口返回 200 而非 201；处理：加 `status_code=201`。
- [x] 缺验收测试（重名 409、弱密码 422、库里存的是 bcrypt 哈希）；处理：补齐并断言哈希格式。
- [x] README 错误码表不准确（200=链接正常、401=用户名或密码错误），且误删 fail-closed 测试说明；处理：修正状态码语义、恢复测试隔离说明。
- [x] `alembic/env.py` 强制把迁移目标换成测试库（Week 6 遗留）→ 开发库永远无法迁移；原因：把安全护栏做成了偷偷覆盖环境变量；处理：删除强制覆盖，测试隔离交给 conftest 的 `_test` 校验。
- [ ] 迁移里硬编码默认用户密码哈希（P3 待办：改成随机哈希或加注释说明用途）。
- [ ] `LoginRequest` 死代码、`form_date` 拼写应为 `form_data`（P3 待办）。

## Week 6（PostgreSQL / SQLAlchemy / Alembic）

- [x] Docker daemon 未就绪 → 启动 Docker Desktop 后再 `docker compose up`。
- [x] Docker Desktop 代理手动配置 `host.docker.internal:7897` → 改为 System proxy。
- [x] 环境变量名拼错（`TGRES_DB` → `POSTGRES_DB`），当时被 env.py 的强制覆盖掩盖没报错；处理：删除强制覆盖后此类拼写会立刻暴露。
- [x] 测试 fixture 用 `create_all` 建表 → 掩盖「改了模型忘写迁移」的漂移；处理：改为程序化 `alembic upgrade head`（绝对路径）。
- [x] Day 4 审查发现：conversations 接口绕过 Service、LLM 配置错误未映射成 500；处理：补 Service 调用和异常映射。
- [x] LLM 调用期间持有数据库事务（长事务）→ 占用连接池、阻塞并发；处理：拆成两个短事务，等待 LLM 前 commit。

## Week 5（分层重构）

- [x] `app/main.py` 重复导入 `FastAPI`/`HTTPException`，lifespan 未传入 `FastAPI`。
- [x] service 层抛 `HTTPException`（应抛业务异常，由 Router 层转换）。
- [x] 同步 SQLite / HTTP 调用阻塞事件循环 → 异步化边界预备。
- [x] README 启动命令拼写错误。
