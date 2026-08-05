
## 数据库迁移：

```text
d18993ee11ea -> 637e85944404 (head), add a index
<base> -> d18993ee11ea, create message and conversation tables
```

`TGRES_DB=<数据库名> .venv/bin/alembic`/`upgrade head` 或者 `downgrad <降级数字>`