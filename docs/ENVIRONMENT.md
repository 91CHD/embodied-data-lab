# 环境与本地运行

本项目支持 macOS 本地开发，运行 API、独立 worker、前端和真实 PostgreSQL。容器配置另见项目根目录 `compose.yaml`。数据库没有换成 SQLite。

## 本次实际环境

2026-10-22，在 Apple Silicon / macOS 26.6.2 中检查：

- uv 0.13.1。
- Python 3.12.15，由 `uv sync --python 3.12` 安装和固定项目环境。
- Node 24.19.0、pnpm 11.25.0。当前机器 Node 来自 Codex bundled runtime，脚本会自动探测其默认位置；其他机器安装相同主版本即可。
- PostgreSQL 16.15（Homebrew）；本次通过 `brew install postgresql@16` 安装，没有启用 `brew services` 或登录自启动。
- Docker CLI / Docker Engine 本机不存在。因此容器启动不算已验证，不能把配置文件存在当作容器运行通过。

本次 PostgreSQL 使用项目私有 `storage/pgdata`，只监听 `127.0.0.1:55432`，数据库 `embodied_lab`，用户为当前操作系统用户名。已实际执行 `SELECT version(), current_database(), current_user` 成功。这个本地单用户数据库用 trust 认证，不适用于公网或共享机器；容器提供单独的本地示例账号。

## 一条命令启动

在项目根目录运行：

```bash
bash scripts/dev.sh
```

脚本检查工具、按锁文件安装依赖、首次复制 `.env.example` 到 `.env`、初始化或复用项目 PostgreSQL、创建数据库、运行 Alembic 迁移，然后分别启动 API、worker 和前端。首次运行需要联网下载依赖。启动完成后访问 `http://127.0.0.1:5173`，API 文档为 `http://127.0.0.1:8000/docs`。

脚本保留已有 `.env`，不会覆盖自定义配置。默认数据库 URI 省略用户名，PostgreSQL 驱动会使用当前 OS 用户。如自定义数据库，请在 `.env` 指定完整 `DATABASE_URL`；本地一键脚本初始化的端口仍默认 55432。

检查环境与停止：

```bash
bash scripts/check_environment.sh
bash scripts/stop.sh
```

停止只关闭本项目记录的前端、API、worker 和项目 PostgreSQL，保留 `storage/` 的数据库、数据集、日志、报告。再次启动可恢复查看持久化记录。正常SIGTERM会合作式取消正在执行的任务，保留记录供人工重试；SIGKILL等未执行清理的失联才按租约回收策略处理。请先等待希望完成的任务结束再停止。

日志位于 `storage/logs/api.log`、`worker.log`、`frontend.log`、`postgres.log`；本地 PID 与启动时间在 `storage/run/`。通过 PID 和启动时间双重检查避免停止 PID 已被复用的其他进程。

API 正常关闭最多等待未结束请求 3 秒，避免浏览器保持的 SSE 连接一直阻塞停止。停止脚本在关闭数据库前等待进程退出；若 worker 仍在不可中断的库调用中，脚本保留数据库并提示稍后重试，避免先停库导致取消状态无法保存。

本次已实际执行整个栈的停止与重启：停止后 API / 前端连接失败、PostgreSQL 55432 报 no response；再次一条命令启动后 API / 前端返回 200、worker 健康检查成功。重启前后的 2 个数据版本及指纹、2 个实验 ID 和 4 条历史任务保持一致；随后新建公开数据质量任务，使用适配器 v2 成功检查 206 条 episode / 25,650 帧，并核对下载报告 SHA256。实际 JSON 记录见 [final_runtime_evidence.json](final_runtime_evidence.json)。

如果 PostgreSQL 安装在其他位置：

```bash
PG_BIN=/你的/PostgreSQL16/bin bash scripts/dev.sh
```

macOS 默认用 `/opt/homebrew/opt/postgresql@16/bin`。Intel Mac 或 Linux 应传入实际 `PG_BIN`，并自行安装 uv、Node 和 pnpm。脚本不需要也不应使用 root 运行。

## Docker Compose

装有 Docker Engine 和 Compose 的机器：

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f api worker
docker compose down
```

四个服务为 `db`、`api`、`worker`、`frontend`。数据库健康后 API 才迁移和启动，API 健康后 worker/前端启动；worker 和 API 共享数据目录。前端地址 `http://127.0.0.1:8080`；API 为 8000；容器 PostgreSQL 映射到宿主 55433（与本地开发数据库不同）。请先停止本地 API，再启动容器，避免 8000 冲突。

PostgreSQL 保存在 named volume `postgres_data`；文件保存到项目 `storage/` bind mount。`docker compose down` 不删数据；`down -v` 会删除数据库卷，不要用于普通停止。

CPU runner 不需要 GPU。LeRobot 训练 / LIBERO 仿真在此版本没有配置运行环境，界面和 API 应明确拒绝假训练；后续独立 Linux/GPU worker 接入与本地平台运行是两件事。

## 本次基础设施下载与安装过程（用于复盘）

下面记录已发生的操作，方便理解每一步的目的。它不是让你重复初始化已经存在的数据库；日常启动用 `scripts/dev.sh` 即可。

1. 先执行 `command -v brew`、`brew --version`、`brew list --versions postgresql@16`。发现 Homebrew 可用，但 PostgreSQL 没安装，Docker 不可用，因此选择真实本地 PostgreSQL，不用 SQLite 代替。
2. 执行 `brew install postgresql@16`。Homebrew 自动更新了自身元数据，先尝试配置中的 USTC portable-ruby 镜像（返回 404），随后自动从官方 `ghcr.io` 下载 portable-ruby 4.0.7_1 成功。这是 Homebrew 自身的 Ruby 运行时，不是项目应用依赖。
3. Homebrew 下载并安装 PostgreSQL 16.15_1 的预编译 bottle 与下表中的依赖。本次 PostgreSQL 安装目录实测约 77MB（`du -sh /opt/homebrew/Cellar/postgresql@16/16.15_1`）；这个数字是解压后安装大小，不是网络流量。依赖共享于 Homebrew，不放进项目源代码。

| 安装项 | 本次版本 | 用途 |
| --- | --- | --- |
| postgresql@16 | 16.15_1（服务报告 16.15） | 真正的关系数据库、事务、任务领取与元数据持久化 |
| icu4c@78 | 78.3 | 国际化字符处理和排序支持 |
| ca-certificates | 2026-10-20 | HTTPS / TLS 的公共 CA 信任根 |
| openssl@4 | 4.0.3 | TLS 和加密库 |
| krb5 | 1.22.2_2 | PostgreSQL 可选的 Kerberos 身份认证支持 |
| lz4 | 1.10.0 | 压缩库 |
| readline | 8.3.6 | psql 交互终端的编辑和历史支持 |
| xz | 5.8.4 | 压缩工具及运行库 |
| zstd | 1.5.7_1 | 压缩库 |
| json-c | 0.19 | JSON C 语言库（Homebrew 依赖链） |
| libunistring | 1.5 | Unicode 字符串处理（Homebrew 依赖链） |
| gettext | 1.0 | 本地化消息支持 |

4. Homebrew 顺带初始化了其默认 `/opt/homebrew/var/postgresql@16`，但我们没有启动该目录，也没有调用 `brew services start`。实际应用使用下面的项目私有目录；这让停止、备份和删除项目数据都有明确边界。
5. 在项目根目录按以下命令创建并启动私有实例。`$(id -un)` 使用当前操作系统用户名，让命令适用于不同开发者。

```bash
mkdir -p storage/logs
/opt/homebrew/opt/postgresql@16/bin/initdb \
  -D storage/pgdata --username="$(id -un)" \
  --auth-local=trust --auth-host=trust --locale=C --encoding=UTF8
/opt/homebrew/opt/postgresql@16/bin/pg_ctl \
  -D storage/pgdata -l storage/logs/postgres.log \
  -o '-p 55432 -h 127.0.0.1 -k /tmp' -w start
/opt/homebrew/opt/postgresql@16/bin/createdb \
  -h 127.0.0.1 -p 55432 -U "$(id -un)" embodied_lab
/opt/homebrew/opt/postgresql@16/bin/psql \
  -h 127.0.0.1 -p 55432 -U "$(id -un)" -d embodied_lab \
  -c 'SELECT version(), current_database(), current_user;'
```

`initdb` 创建数据库集群文件；`pg_ctl` 启动服务；`createdb` 在实例内创建业务数据库；`psql` 真正连到服务执行 SQL。安装软件、服务监听、数据库创建、表结构迁移是四个不同阶段。接下来 `alembic upgrade head` 才按版本迁移创建本项目的表。

6. 执行 `SHOW data_directory` 核对实际服务指向项目 `storage/pgdata`。这避免“连通了某个 PostgreSQL，但不是预期实例”的误判。
7. Docker 本机缺失，没有下载镜像。只通过 Docker Hub 官方 registry 对 Python `3.12.15-slim-bookworm`、Node `24.19.0-bookworm-slim`、PostgreSQL `16.15-bookworm`、nginx `1.28.0-alpine` 四个标签执行 manifest HEAD 请求，都返回 HTTP 200；同时用 PyYAML 解析 `compose.yaml` 并检查四服务、共享文件卷、健康检查启动依赖。**镜像存在与配置可解析不代表已经构建或运行容器。**

面试可以这样说明：项目使用真实 PostgreSQL 集成验收；本地开发独立管理数据库进程，worker 通过数据库事务领取任务；Compose 作为交付配置，但当前机器没有 Docker，因此容器运行效果需要在有 Docker 的环境进一步验证。
