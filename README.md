# 具身数据与实验管理平台 · Embodied Data Lab

本地单用户研发工作台：导入 LeRobot v3.0 数据 → 检索和回放 episode → 执行质量检查、统计与划分 → 查看持久日志与下载报告 → 登记实验、检查点和评测结果 → 核对条件后比较实验。

平台使用真实 Vue3 前端、FastAPI、PostgreSQL 和独立 Python worker。CPU 数据任务实际执行；训练和仿真接入未配置，不会返回虚假的训练成功或模型指标。公开数据、合成演示、外部导入结果、平台实际生成产物分别标识。

![数据工作台：版本、来源与轨迹索引](docs/screenshots/01-data-workspace.jpg)

[快速启动](#快速启动) · [完整演示](#一次完整演示) · [架构设计](docs/ARCHITECTURE.md) · [运行证据](docs/ACCEPTANCE.md) · [学习路线](docs/LEARNING_GUIDE.md)

## 解决什么问题

机器人策略研发需要把视频、状态、动作和实验结果关联起来。本项目以一次 episode 为单位提供回放和检查，并把报告、划分清单与实验固定关联到具体数据版本。

| 研发需求 | 已实现能力 |
| --- | --- |
| 找到并理解一条轨迹 | 分页检索、特征 schema、视频与状态/动作曲线联动 |
| 检查数据能否使用 | 字段、维度、有限值、时间/索引/视频边界检查，定位异常并下载报告 |
| 可复现地准备实验输入 | 不可变快照、内容指纹、按 episode 划分、保存 seed 与清单 |
| 让长任务独立运行 | PostgreSQL 原子领取、独立 worker、租约恢复、取消/重试、持久日志 |
| 知道结果来自哪次实验 | 数据、配置、检查点标识、任务、产物和评测条件追溯 |

2026-10-22 本机验收：**34 项后端测试、4 项前端测试、前端生产构建通过**；包含真实 PostgreSQL、双 worker 竞争、SIGKILL 恢复及浏览器操作。这是记录过的本地验收结果，GitHub Actions 尚未配置或运行。详细命令与边界见[验收记录](docs/ACCEPTANCE.md)。

<details>
<summary>查看双相机回放与其他实际页面</summary>

![同一 episode 游标映射到两个不同视频起点](docs/screenshots/05-dual-camera-playback.jpg)

[全部 11 张实际截图与操作记录](docs/BROWSER_ACCEPTANCE.md)。演示实验中的人工 trial 均注明 synthetic_demo；不代表模型成绩。

</details>

源码仓库、在线演示与本地启动的区别，以及后续改进方向，见[发布与作品集说明](docs/PUBLISHING.md)。

## 快速启动

先获取源码：

```bash
git clone https://github.com/91CHD/embodied-data-lab.git
cd embodied-data-lab
```

已配置当前 Mac 时，在本目录执行：

```bash
bash scripts/dev.sh
```

- 页面：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

首次启动会按锁文件安装项目依赖，创建 `.env`、初始化项目私有 PostgreSQL 并迁移，然后启动三个独立进程。重复执行会复用仍在运行的本项目进程。日志保存在 `storage/logs/`。

```bash
bash scripts/check_environment.sh
bash scripts/stop.sh
```

停止保留数据库、数据集和报告；再次启动可以继续查询历史记录。首次配置新电脑，需要 Python 3.12、uv、Node 24、pnpm 11.25.0、PostgreSQL 16；macOS 可先 `brew install postgresql@16`，不要用 root 运行项目。安装来源、版本、下载过程和每个命令的含义见 [环境与下载记录](docs/ENVIRONMENT.md)。

## 准备可核对的数据

启动脚本不会自动下载模型或大型数据。下面两个命令分别生成本地合成样本、下载固定 revision 的公开小样本。

```bash
uv run python scripts/generate_fixture.py --all-variants
uv run python scripts/fetch_public_sample.py
```

公开来源为 [lerobot/pusht](https://huggingface.co/datasets/lerobot/pusht/tree/7628202a2180972f291ba1bc6723834921e72c19)，固定 revision `7628202a2180972f291ba1bc6723834921e72c19`，完整 7 个数据文件共 7,684,382 bytes（约 7.33 MiB），206 条 episode、25,650 帧。下载前检查清单和 200 MiB 上限；下载后核对长度、可用的 LFS 校验，并在 `storage/incoming/public-small/SOURCE.json` 保存逐文件 SHA256 和 URL。它是公开 PushT 仿真/交互数据，不代表本项目训练或评测成绩。

合成样本位于 `storage/incoming/synthetic-small`，3 条轨迹共享一个 Parquet、两路相机各共享一个 MP4；wrist 相机有 0.2 秒前导偏移。其他目录分别制造 NaN/Inf、维度、时间、边界和缺字段异常。它们是真实格式文件，但像素、轨迹和评测示例不来自真实机器人。格式、来源、上游源码依据和支持范围见 [数据说明](docs/DATA_FORMAT.md)。

## 一次完整演示

1. 打开“数据工作台”，点击导入，从受控目录选择 `public-small`。来源和 revision 从 `SOURCE.json` 读入；提交后会进入任务详情。
2. 查看任务从 queued 到 running，再到 succeeded；日志会随真实处理进度增加。刷新页面，历史日志与最终状态仍保留。
3. 进入数据集详情，确认来源、内容指纹、schema 和 206 条 episode。打开一条轨迹，播放视频、拖动游标或按帧定位，观察 state/action 曲线联动。公开样本是 AV1，需浏览器支持对应解码；合成样本使用 H264，可用于双相机偏移验证。
4. 返回数据集详情，点“质量检查”，在任务完成后查看 JSON 报告并下载 JSON/CSV 产物。对 `synthetic-nan` 重复操作，可以看到具体 episode、帧、字段和异常规则。
5. 点“划分数据集”，输入训练占比和 seed；下载清单，检查按完整 episode 划分、训练/验证集合无交叉。相同 seed 的列表应相同。
6. 打开“实验管理”，可先登记模型检查点标识（只登记来源，不会下载权重），再创建实验并关联已导入数据版本，填写配置和代码版本。
7. 在实验详情“导入结果文件”中上传不超过 2 MB 的 JSON。创建另一实验并导入同条件记录，回到列表勾选两项进行对比；改变 seed 或任务集合，页面应说明不能直接比较。
8. 点击训练或评测任务入口，可以查看接入参数与当前环境未配置说明；此版本不会在本机假装执行 VLA 训练。

用于试验导入界面的合成结果可以保存为以下 JSON；`source_kind` 必须保留 `synthetic_demo`，它只验证格式与计算流程。

```json
{
  "source_kind": "synthetic_demo",
  "task_set": ["demo-task"],
  "seed": 42,
  "metric_definition": "示例：successes / trials，不是模型成绩",
  "trials": [
    {"task": "demo-task", "success": true, "steps": 10, "latency_ms": 3.0},
    {"task": "demo-task", "success": false, "steps": 20, "latency_ms": 5.0}
  ],
  "notes": "仅供结果文件导入演示"
}
```

成功率由服务端对 trials 计数，不能在文件中直接填任意 `success_rate`。外部真实记录使用 `imported`，客户端不能声称 `executed`。原始结构经 schema 验证、规范化后存为 JSON 产物，保留内容与来源；不保留上传文件原本的空白/键顺序。

## 测试和构建

```bash
uv run pytest -q
uv run ruff check backend tests scripts
cd frontend
pnpm test
pnpm build
```

`tests/test_api_integration.py` 启动真实 HTTP 服务和独立临时 PostgreSQL 数据库，验证干净迁移、真实导入、API 重启持久化、幂等、媒体范围请求、日志恢复、结果导入和错误路径。数据库名以 `embodied_api_test_` 开头，结束后删除，只使用临时 storage，不清空主工作台。需要 PostgreSQL 当前用户有 CREATE DATABASE 权限；服务器不可用时会明确 skip，不能把 skip 算作验证通过。可用 `EDL_TEST_POSTGRES_DSN` 指向允许创建测试库的实例。

数据适配测试包含公开样本检查；公开文件不存在时相应用例会标记 skip。实际验收命令、结果与截图见 [验收记录](docs/ACCEPTANCE.md)，功能边界见 [项目状态](PROJECT_STATUS.md)。

## 目录与持久化

```text
backend/app/          API、schema、数据库、任务服务、worker、数据适配器
backend/migrations/  Alembic 数据库迁移
frontend/src/        Vue 页面、API 客户端、回放时间映射
scripts/             启停、环境检查、公开下载、合成数据生成
tests/               数据适配、真实 PostgreSQL / HTTP 集成、任务可靠性
storage/incoming/    可导入的原始目录
storage/datasets/    导入后的不可变内容快照
storage/staging/     尚未发布的 attempt 临时文件
storage/artifacts/   已登记报告与结果
storage/pgdata/      本机项目 PostgreSQL 数据目录
storage/logs/        进程日志与 worker 健康心跳
docs/                架构、学习、格式、环境与验收
```

`.env`、`storage/`、虚拟环境和 node_modules 已忽略，不应提交密钥、大型原始数据或运行产物。备份本机数据时，先正常停止服务，再备份完整 storage；数据库运行中不要仅复制个别 pgdata 文件。

## 容器与训练边界

`docker compose up --build -d` 定义前端、API、数据库和 worker 四个服务，前端访问 8080，数据库映射 55433。先停止本地服务避免 API 8000 冲突。当前 Mac 没有 Docker Engine，容器配置仅做静态解析和镜像标签核对，容器构建/运行尚未验证，详见环境记录。

此版本不含 GPU worker、模型下载、LIBERO 仿真、在线机器人或账号系统。训练参数与受控 argv 构造在 `backend/app/training.py`；能力接口固定说明未配置。只有后续实际执行训练/评测并取得可核对产物，才能增加相应完成声明。

建议先读 [架构](docs/ARCHITECTURE.md)，再按 [学习指南](docs/LEARNING_GUIDE.md) 跟一次真实请求走完整条链路。先读5文件、10道面试追问和建议亲手做的修改都在学习指南里；[开发与下载记录](docs/DEVELOPMENT_LOG.md)说明实际引入了什么及原因；[验收记录](docs/ACCEPTANCE.md)保留最终34项后端、4项前端和11张截图证据。
