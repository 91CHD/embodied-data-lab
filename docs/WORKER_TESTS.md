# 独立进程与 PostgreSQL 可靠性验收

2026-10-22 在本机 Python 3.12、真实 PostgreSQL、独立 uvicorn HTTP 服务上运行：

```sh
.venv/bin/pytest -q tests/test_worker_reliability.py
# .... [100%]
# 4 passed in 11.66s
```

测试文件显式复用 `tests/test_api_integration.py` 的 fixture。每个测试模块创建随机的 `embodied_api_test_*` 数据库，运行真实 Alembic 迁移，使用 pytest 临时目录保存独立数据，并在结束时删除该测试数据库。不会操作开发工作台数据库。没有将 SQLite 代替 PostgreSQL，也没有把 runner 替换为 sleep 或随机进度。

公共样本来自本地已经固定 revision 下载的 `storage/incoming/public-small`，复制到测试实例的受控目录后，通过真实 `/api/datasets` 入队并运行 import worker。206 episode 的公共 PushT 质量任务使用实际 PyArrow 帧检查及 PyAV 视频解码。

## 1. 两个 worker 如何避免竞争同一条任务

`test_skip_locked_concurrent_workers_claim_distinct_jobs` 创建三项真实 statistics 任务，用一个 PostgreSQL 事务先锁定最老的一行：

```sql
SELECT id FROM jobs WHERE id = :oldest_job FOR UPDATE;
```

在**仍然持有此锁**时，启动两个独立的 `python -m app.worker --once --id ...` 进程。两个进程必须均完成退出；它们分别领取第二、第三条任务，各产生一条 attempt 和一个统计报告，worker ID 不同。最老任务仍为 queued / attempt 0。

如果代码只是等待第一行锁而没有 `SKIP LOCKED`，这一步将无法在锁释放之前通过。释放锁后，再运行第三个 worker，原来被锁定的任务完成。此测试覆盖真实行锁、两个进程和实际报告生成；它证明当前实现的这一竞争场景，没有宣称所有任务只可能执行一次。

实现入口：`backend/app/worker.py::claim_job`。

## 2. 真正 SIGKILL 后怎样恢复

`test_sigkill_real_quality_work_recovers_after_lease_and_preserves_attempts`：

1. 通过 HTTP 创建公共数据质量任务。
2. 启动 `sigkill-first-worker`，等待数据库状态变为 running，且实际帧检查已经使 progress > 0.1。
3. 对该进程发送 **SIGKILL**；核查操作系统返回码为 `-SIGKILL`。这不会执行 Python 的 finally，也不会触发优雅停止逻辑。
4. 此时数据库仍记录 running / attempt 1。测试按真实 `lease_expires_at` 等待租约超时。测试进程的 worker 环境配置为 `LEASE_SECONDS=2`、`HEARTBEAT_SECONDS=0.2`，只为了缩短验收时间；产品默认租约更长。
5. 启动 `sigkill-recovery-worker --once`，它调用恢复逻辑，再领取并实际重新执行质量检查。
6. 最终 job 为 succeeded / attempt 2；attempt 历史为 `interrupted`、`succeeded`，第一次的租约超时原因和完成时间仍保留。
7. 只有 attempt 2 的 JSON 报告及 CSV 被登记为产物。逐个 HTTP 下载，验证 SHA256 与数据库一致；报告引用相同数据指纹、job ID、attempt 2，显示 25,650 个实际检查帧。
8. 恢复前保存日志游标。恢复后 `/events?after=` 返回准确后缀；带 Last-Event-ID 的 SSE 返回相同、递增、不重复的事件 ID，最后发出 end。

实现入口：`recover_expired`、`JobContext._heartbeat`、`JobContext._owned`、`publish`。

这是“至少一次执行”的恢复证据：计算可能重做，只有仍持有有效租约和当前 attempt 的 worker 可以登记结果。它不表示 exactly-once。

## 3. 取消运行中的真实任务

`test_cancel_running_real_quality_task_publishes_no_artifact` 同样等真实帧检查 progress > 0.1，再发送 POST cancel。接口先返回 cancel_requested；worker 的合作式检查读取状态并终止，最终 job / attempt 均为 cancelled，没有登记报告产物，当前 attempt 的 staging 目录被清理，report API 返回 404。

这不是只取消一个尚未被领取的 queued 任务，也没有用前端页面状态冒充后端取消。检查单位之间会有短暂延迟，取消不是任意一条 CPU 指令都能立刻打断。

实现入口：`backend/app/main.py::cancel_job`、`JobContext.check`、`finish_failure`、`execute` 的 finally。

## 4. 失败与手动重试如何留下证据

`test_real_runner_failure_manual_retry_retains_previous_error` 生成一个真 Parquet / MP4 的合成异常样本，metadata 正常、轨迹表缺少 action。导入元信息后，statistics runner 因缺字段而真实失败。

API 手动 retry 将同一 job 重新入队，第二个 attempt 再次真实失败；两个 attempt 均保留相同字段错误，job 的创建时间不改变，也没有虚构成功或产生空报告。两次普通失败的 staging 目录均已清理。

实现入口：`backend/app/main.py::retry_job`、`backend/app/data/lerobot_v3.py::statistics_report`、`finish_failure`。

## 5. API重启不影响已经执行的任务

后续增加 `test_api_restart_during_real_worker_job_does_not_lose_execution_or_logs`：等待真实公共质量任务为running且progress>0.1，确认worker进程仍在执行，保存日志游标，然后终止并重新启动独立API进程。相同worker继续完成，attempt仍为1，真实报告检查25,650帧；重连后的事件查询和SSE都返回游标后的准确后缀和最终完成事件。没有重新入队或换成假runner。

最终全套 `.venv/bin/pytest -q` 为 **34 passed in19.96s**，包括本文件的5项可靠性测试。开头4 passed是首次独立运行的历史记录；最终总结果以ACCEPTANCE.md为准。

## 验收边界

- 上述是本机真实 PostgreSQL 和操作系统进程的四类验收，不是 Docker 整套容器故障注入，也不是 VLA 训练或机器人仿真验收。
- 普通失败和合作式取消验证了 staging 清理。SIGKILL 可以在任意文件操作之间中断，旧 attempt 可能遗留**未登记、不会作为正式结果使用**的 staging 或磁盘目录；本测试核查了数据库没有引用第一次 attempt 的产物，没有宣称磁盘完全没有孤立目录。长期运行时需要基于数据库引用和保留时间进行单独回收。
- 每个测试都会在 finally 清理自己仍存活的 worker 进程；实际 SIGKILL 后的日志文件和 pytest 临时文件由 pytest 临时目录机制保留，便于本机检查，不属于用户数据版本。
