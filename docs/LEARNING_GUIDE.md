# 跟着一次实际操作读懂平台

这份指南按本项目的真实代码编写。先操作、观察，再读实现和修改；不要求一次背完所有库。学习的目标是能够解释取舍、定位问题，并独立修改一条业务流程。

## 先读哪 5 个文件

| 顺序 | 文件 | 重点问题 |
| --- | --- | --- |
| 1 | `frontend/src/pages/DatasetsPage.vue` | 点击“导入数据集”后，收集了什么输入、向哪里发请求、怎样展示失败？ |
| 2 | `backend/app/main.py` | `import_dataset` 如何把 HTTP 请求交给业务层？`create_job` 怎样校验不同任务？ |
| 3 | `backend/app/models.py` | Dataset / Episode / Job / JobAttempt / JobEvent / Artifact 如何关联？为什么需要外键和唯一约束？ |
| 4 | `backend/app/worker.py` | `claim_job → execute → runner → publish` 怎样在独立进程里完成任务？ |
| 5 | `backend/app/data/lerobot_v3.py` | episode 怎样从共享 Parquet 和 MP4 中被还原？检查规则在哪里？ |

第二轮再读 `schemas.py`、`services.py`、`storage.py`、`frontend/src/pages/JobPage.vue` 和 `EpisodePage.vue`。

## 第一次学习：导入一份公开数据

### 1. 在页面发起操作

打开工作台，在“导入数据集”中选择 `public-small`。来源应为“公开数据”，地址为 Hugging Face PushT，revision 为完整固定 commit。已经导入相同内容时会复用版本，不产生第二份相同 Dataset。

文件：`frontend/src/pages/DatasetsPage.vue`。

- `openImport()` 请求 `/api/sources`，取得受控 incoming 目录中的候选源。
- `selectSource()` 用来源清单回填来源和 revision。
- `submit()` 把表单和 `idempotency_key` 发到 `/api/datasets`，获得 Job 后打开任务详情。

打开 Chrome 开发者工具 Network：点击导入，找到 POST `/api/datasets`，观察 Request Payload 和 Response。**响应中有任务 ID，并不意味着数据已经处理完成**。HTTP 202 的含义是已接收后台任务。

### 2. 请求如何到后端

文件：`frontend/src/lib/api.ts` 的 `post()` 和 `api()`。

浏览器只请求同源 `/api/...`。开发时 Vite 代理到 8000 端口；Compose 中 Nginx 代理到 API 容器。这样前端不用硬编码每个后端地址。

文件：`backend/app/main.py` 的 `import_dataset()`。

FastAPI 先用 `schemas.py::DatasetImport` 检查字段，再用 `storage.py::controlled_path()` 检查源路径。这里接受的是 incoming 下的相对目录，不允许 `/etc/...`、`../...` 或逃逸根目录的符号链接。首版通过受控本地目录导入，不提供任意服务器目录浏览或压缩包上传。

可以观察的失败：公开来源缺少 revision，响应 422；路径越界，响应 400；同一个幂等键换了参数，响应 409。

### 3. 为什么先写数据库，不直接开始检查

文件：`backend/app/services.py::enqueue()`。

函数对请求类型和参数计算 hash，并查幂等键：

- 相同键、相同请求：返回已有 Job。
- 相同键、不同请求：409，防止无意覆盖原任务。
- 首次提交：创建 queued Job 和“任务已入队”事件，提交事务。
- 两个请求同时首次提交：由 PostgreSQL 唯一约束裁决，捕获冲突后重新查询。

数据库约束承担跨进程并发一致性；仅用 Python 列表或前端按钮 disabled 无法保证两个 API 进程之间的幂等。

在终端只读观察（不会修改数据）：

```sh
/opt/homebrew/opt/postgresql@16/bin/psql -h 127.0.0.1 -p 55432 -d embodied_lab \
  -c "SELECT id,type,status,attempt,worker_id FROM jobs ORDER BY created_at DESC LIMIT 8;"
```

### 4. worker 怎样领取任务

文件：`backend/app/worker.py::claim_job()`。

独立 worker 循环查 queued Job，在数据库事务内执行 `FOR UPDATE SKIP LOCKED`。取到记录后，在同一事务中改为 running、增加 attempt、登记 worker、租约和 JobAttempt，然后提交。

两个 worker 遇到同一个最老任务时，一个锁住它，另一个跳过这行继续取后面的任务。不能把“先 SELECT、稍后另一个事务 UPDATE”当成原子领取。

真实验证位于 `tests/test_worker_reliability.py::test_skip_locked_concurrent_workers_claim_distinct_jobs`：故意持有最老任务行锁，两个真实 worker 必须在锁释放前完成另外两项统计任务。

### 5. 导入 runner 实际做了什么

文件：`backend/app/worker.py::ImportRunner.run()`。

1. 从 incoming 受控目录复制到当前 attempt 的 staging，禁止符号链接，限制总大小和文件数。
2. 读取 `meta/info.json` 和 episode 元信息，按真实格式索引。
3. 对文件名、大小和实际字节计算清单指纹，得到不可变数据版本。
4. 将完整快照移到 `storage/datasets/<job>/attempt-<n>/`。
5. 生成 JSON 导入报告。
6. `publish()` 在有效租约和 attempt 的保护下登记 Dataset、Episode、Artifact，并把 Job 标为 succeeded。

首版“索引完成”和“质量检查完成”是两件事：索引证明元信息能定位数据；质量任务再逐帧检查和验证视频。一个缺少 action 列但元信息正常的异常包可以先登记，随后得到明确的质量问题报告。

incoming 中的源文件不会被转换或覆盖。之后修改源文件再导入，如果内容变更，会形成新的指纹和新版本；历史实验仍指向原快照。

数据库事务无法同时回滚磁盘：先写临时文件，完成后发布文件，再提交数据库引用。普通失败会补偿清理；SIGKILL 恰好发生在文件操作中间，可能留下未登记的孤立目录，不能因此声称跨磁盘和数据库具有原子事务。

### 6. 日志怎样到页面

文件：`services.py::record_event()`、`main.py::events()` / `stream()`、`frontend/src/pages/JobPage.vue`。

worker 把带递增 ID 的 JobEvent 存进 PostgreSQL。SSE 只负责把已保存事件传给页面，任务执行不依赖 SSE 连接。页面先取历史，再从最后游标接流；断线可用 Last-Event-ID 补取。前端 `mergeEvents()` 按 ID 去重和排序。

你可以关闭任务页再打开，历史和最终状态仍存在。后端集成测试还会真正终止并重新启动 API，验证保存的数据仍可查询。

### 7. 报告保存在哪里

文件：`worker.py::publish()`、`main.py::download()`、`frontend/src/components/ArtifactList.vue`。

实际文件位于 `storage/artifacts/<job>/attempt-<n>/`。Artifact 保存归属、类型、相对位置、字节数、SHA256 和来源。下载接口重新校验 SHA256，防止悄悄下载已经被外部修改的内容。

导入外部结果时保留的是通过 schema 校验后、语义完整的规范化 JSON，**不保证保留原文件空格和字段顺序**；不是复制一个客户端随意给出的“成功率”。

## 第二次学习：回放为什么必须理解时间和边界

文件：`data/lerobot_v3.py::_metadata()` / `_data_path()` / `_cameras()` / `read_episode()`；`frontend/src/pages/EpisodePage.vue`；`frontend/src/lib/timeline.ts`。

本项目公开 PushT 的 206 个 episode 共用一个数据 Parquet 和一个 MP4。不能按“episode 1 就读取 file-001”找文件。

表格用 episode 元信息中的 `[dataset_from_index, dataset_to_index)` 过滤全局 index，再验证行里的 episode_index 和 frame_index。视频用该相机的 from_timestamp / to_timestamp 还原片段范围。每个相机有自己的偏移。

页面游标是 episode 局部时间，视频定位是 `该相机起点 + 局部时间`。合成样本中 wrist 比 front 多 0.2 秒前导时间，所以两台相机的绝对 currentTime 应相差 0.2 秒，展示的 episode 阶段仍应一致。页内只加载一个轨迹窗口，API limit 最多 2000 帧。

`to_timestamp` 是边界，不能在末尾直接 seek 到下个 episode 的首帧。前端把可播放范围限定在最后有效数据帧和最后有效相机帧。HTML video 的 seek 不是机器人控制的实时同步：关键帧和浏览器调度会带来误差，页面按游标校正漂移，本机验收记录实测误差；不要把它表述为硬实时或所有编码器上的逐帧精确同步。

练习：在双相机样本的中间和末帧暂停，分别查看两个 video 的 currentTime；切换维度，确认曲线改变，但游标没有跳走。然后在 `timeline.test.ts` 阅读末帧边界测试。

## 第三次学习：检查、统计、划分有什么不同

- **检查**：字段、向量维度、NaN/Inf、索引、时间递增、episode 边界和视频可访问/边界解码。JSON 记录规则、总数和定位；issues 最多保留 1000 条，但完整计数不截断。
- **统计**：实际遍历 state/action，计算有限值数量、min/max/mean/std；不是拿随机数填图表。
- **划分**：对完整 episode 的列表按 seed 打乱，再分训练/验证。相邻帧具有强相关性，按帧随机分容易让同一轨迹同时进入两边。

实现：`DataRunner.run()` 选择真实 CPU runner；适配器 `quality_report()`、`statistics_report()`、`split_episodes()`。

练习：相同数据和 seed 连跑两次划分，对比 train/validation 数组应完全相同；改变 seed 可能改变列表。质量报告含错误时，意味着“检查成功完成并发现数据问题”，不意味着检查任务执行失败。

## 第四次学习：异常处理比正常路径更能说明工程能力

读 `docs/WORKER_TESTS.md` 和 `tests/test_worker_reliability.py`。

任务默认租约 15 秒，每 3 秒续租。worker 死亡后，另一 worker 会发现过期，保留 interrupted attempt，并在自动重试上限内重新入队。旧 worker 若恢复，必须再次核对 worker ID、attempt 和租约，失效后不能登记正式产物。

普通业务错误，例如缺少必要字段，直接失败，等待修正数据后重新导入或人工重试；不会对确定无效数据无限自动重试。手动重试保留所有 attempt。合作式取消在处理块之间检查状态，允许短暂响应延迟，不提供任意 CPU 指令级抢占。

本项目的保证是**至少一次计算＋受租约保护的发布**。不能承诺整个分布式系统 exactly-once；也没有证明任意数据库网络分区和存储故障下都能完全自动修复。

## 第五次学习：实验管理为什么还不是训练

`Experiment` 关联数据版本、配置、代码/工具版本和 Checkpoint。一个检查点先登记标识、来源、revision/checksum，不把大权重装入 PostgreSQL。

外部评测 JSON 用 `EvaluationBundle` 校验每条 trial，平台按 success 布尔值计数，计算成功率、平均步数和延迟。`compare()` 检查数据指纹、任务与每任务样本数、seed、指标口径、来源是否一致，不一致则提示不可直接比较。不同模型可以是你研究的对比变量，但相同数据和 seed 也不等于排除了所有实验混杂，需要你记录真实配置。

来源：`executed` 是本平台真正执行；`imported` 是导入的外部记录（来源由操作者负责，平台做结构校验，无法凭文件证明实验真的发生）；`synthetic_demo` 是用于流程演示的合成数据/结果。客户端不能把外部结果写成 executed。

本轮没有执行 SmolVLA 训练或 LIBERO 仿真。`training.py` 的能力检查和受控 argv 构建只是接入边界；默认返回未配置，API 对合法训练/评测请求返回 409。后续要补 Linux/GPU worker、固定数据和权重 revision、真实 CLI、取消子进程、输出解析和端到端验收，才能扩充简历中的训练能力描述。

## 面试会追问的 10 个问题

| 问题 | 回答抓手与实现 |
| --- | --- |
| 1. 平台解决谁的问题？ | 研发人员需要把轨迹、检查、划分、任务和结果连起来追溯。用一次真实演示说明，别虚构企业用户数。README 演示流程、`models.py`。 |
| 2. 为什么 API 和 worker 分离？ | 长任务独立于浏览器/API生命周期，状态先持久化。`enqueue → claim_job → execute`。 |
| 3. 为什么用了 PostgreSQL 任务表？ | 当前单用户规模可减少系统组件，并利用事务、唯一约束和行锁。吞吐/队列调度规模扩大时再评估消息队列。`services.py::enqueue`、`worker.py::claim_job`。 |
| 4. 两个 worker 如何避免同时领取？ | 同一事务内 FOR UPDATE SKIP LOCKED 加状态转移；指出实际双进程测试，别只背 SQL。`test_worker_reliability.py`。 |
| 5. worker 死了怎么办？ | 心跳租约、attempt、恢复策略和 fencing；真实 SIGKILL 测试及孤立文件限制。`JobContext`、`recover_expired`、`publish`。 |
| 6. 如何保证重复点击不产生重复任务？ | 幂等键＋请求 hash＋数据库唯一约束；前端禁按钮只是交互反馈。`services.py::enqueue`，API 集成测试。 |
| 7. LeRobot v3 的坑是什么？ | 多 episode 共用分片，全局行索引和每相机时间偏移；一维 state/action 语义不能只靠维数推断。`lerobot_v3.py` 与公开样本证据。 |
| 8. 如何避免数据泄漏？ | 按 episode 分，保存 seed/完整列表；这只排除了轨迹交叉，跨场景/操作者泄漏还要按业务分组。`split_episodes`。 |
| 9. 断网日志丢了吗？如何判断成功率？ | 日志入库再 SSE；评测从逐条 trial 计数，比较条件和来源。`stream`、`mergeEvents`、`import_result`、`compare`。 |
| 10. 这个平台里你真正掌握了什么？ | 展示能独立解释/修改/验证的流程，说明 Agent 辅助开发。当前真实证据是全栈和数据工程，训练/真机控制尚未执行，不把接口存在等同算法能力。`PROJECT_STATUS.md`。 |

## 建议你亲手做的三次小修改

1. 为统计报告增加一个你能解释的新字段，并贯通 schema（如果需要）、worker、报告和页面；用小数据手算校验。
2. 给一个真实坏样本增加检查规则，先写出期望的具体异常定位，再实现并验证，不用只测常量。
3. 关闭一个 worker，看任务如何恢复；对着数据库把每个状态和 attempt 的意义讲出来。

每次修改都保留：业务原因、改动文件、失败过的地方、验证命令和截图。这些是你自己的面试案例，比背诵一串库名有用。

## 继续向 Agent 提问的方式

> 选我刚做的一次真实数据导入，先只讲页面到 API 这一层。指出具体文件、函数、请求和数据库变化；让我复述，并让我亲手做一个小修改后再讲下一层。不要一次输出整个平台讲义，也不要把我没理解的功能算作我已经掌握。
