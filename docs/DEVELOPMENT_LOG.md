# 开发过程与下载记录

日期：2026-10-22。按用户指示，在当前资料目录下新建项目；没有读取或改写其他无关项目代码，没有创建远端仓库、发送简历或购买服务。

## 阶段 0：环境与范围

现场发现没有可复用的 Vue/FastAPI 原型代码，也没有 Docker/PostgreSQL；有 uv 和 Codex 捆绑的 Node。选择 Vue3 + FastAPI + PostgreSQL + 独立 Python worker。先实际做 CPU 数据链路，再留训练接入协议。

已下载/安装：

| 内容 | 实际版本/大小 | 用途和落点 |
| --- | --- | --- |
| Python | 3.12.15，运行时下载约23.9MiB | uv 管理运行时，项目 `.venv` 隔离依赖 |
| PostgreSQL | 16.15，及 Homebrew 依赖 | 本地数据库；私有数据 `storage/pgdata`、端口55432；没有注册 brew 自启动服务 |
| Python 库 | 完整精确版本在 `uv.lock`，直接依赖在 `pyproject.toml` | FastAPI/SQLAlchemy/Alembic/Psycopg 等 HTTP 与持久化；PyArrow23.0.1/PyAV17.0.0/NumPy2.2.6 读轨迹和视频；pytest/httpx/ruff 用于验证 |
| 前端库 | 完整版本在 `frontend/pnpm-lock.yaml` | Vue3、Router、Element Plus、ECharts、Vite、TypeScript、Vitest；只安装到本项目 node_modules |
| 公共 PushT 数据 | 固定commit `7628202a2180972f291ba1bc6723834921e72c19`；7,684,382字节 | `storage/incoming/public-small`；206episode/25,650帧，完整来源和文件SHA见 SOURCE.json |
| 上游参考源码 | LeRobot v0.4.3 commit `0b067df57d21d3a02d6c511f1609172fa39ac29b` 的格式/配置源码 | 核对字段及偏移，未安装完整训练栈；来源依据在 DATA_FORMAT.md |

Node24.19.0 使用现有捆绑运行时，没有重新下载。没有下载模型权重、CUDA、Docker 引擎或容器镜像，也没有购买算力。镜像标签只核对 registry 存在；Compose 是否可实际运行仍需 Docker 环境验证。Homebrew 的额外依赖、环境来源和停止方式见 ENVIRONMENT.md。

数据下载脚本先计算公开清单总量，超过配置上限不下载；分块读取、逐文件校验、使用 `.part` 临时文件再替换。并不执行数据包里的代码。合成样本和异常包由项目脚本本地生成，不是另行下载的机器人实验结果。

## 阶段 1：持久化纵向链路

创建 Alembic 初始迁移和9类业务表（含 JobAttempt、EvaluationResult 等扩展实体），真实 PostgreSQL 运行迁移。API 入队，worker 独立领取，报告真实计算并登记。页面通过查询和 SSE 展示任务。

关键学习点：数据库保存状态和引用，文件目录保存大数据；HTTP 202 只代表任务已接收。幂等与领取锁依赖数据库约束，不能靠前端按钮或进程内锁。

集成验证创建随机隔离测试数据库，不清空实际工作台。测试会运行迁移、启动真实 API/worker，再重启 API 检查数据仍在。

## 阶段 2：数据格式与回放

先核对 LeRobot 官方格式：v3多episode共享文件。选完整小型PushT数据，不下载数百MB的其他候选。公共数据实际检查206条首末视频边界，结果0error/0warning；另生成双相机H264样本，两相机共享各自MP4，wrist带0.2秒前导偏移。

关键学习点：文件分片不等于episode，表格全局index和相机from_timestamp分别定位。状态动作曲线用同一局部时间游标，不用两个autoplay冒充同步。浏览器seek有调度和解码误差，不能宣称机器人硬实时控制精度。

## 阶段 3：异常、任务恢复和日志

加入真实缺字段/NaN/Inf/维度/时间/越界样本检查，按episode划分并保留参数与清单。PG原子claim、attempt历史、有限自动恢复、合作式取消、持久化事件与SSE游标。

验收真的启动两个worker竞争，并在公共质量任务已经处理部分帧时向进程发SIGKILL，再等租约过期、接管并重新完成。不是模拟sleep进度。日志、attempt和产物归属保留证据。普通失败/取消清理已验证，突然SIGKILL可能遗留孤立目录，需单独回收策略。

## 阶段 4：实验与结果

实验关联数据指纹、配置、代码版本和检查点标识。结果JSON逐条校验trial，平台计数，不接受客户端伪造executed。对比核对数据、任务和各任务样本数、seed、指标定义及来源。

开发中发现并修正一个异常路径：非标准JSON的NaN会让默认错误响应再次序列化失败；现在请求入口拒绝NaN/Infinity，返回明确400，合法JSON的schema错误返回422。这说明“参数不合法”也要验证完整HTTP响应，不能只看校验函数抛了异常。

## 阶段 5：最终运行与教学交付

最终命令、结果和浏览器证据在 ACCEPTANCE.md；架构在 ARCHITECTURE.md；从页面沿到数据库/worker的学习路线在 LEARNING_GUIDE.md；当前可支持的简历描述和未验证边界在 PROJECT_STATUS.md。

本地脚本使用独立session启动进程，避免启动命令结束后后台服务随进程组被清理。运行仍是本地开发模式，不等于生产部署。交付前再次运行关键测试、构建、健康检查和浏览器流程，具体数字以最终验收记录为准。
