# 数据格式、来源与可核对边界

## 固定的上游依据

实现读取 **LeRobot v3.0**，核对上游 release **v0.4.3**，tag 指向 commit
`0b067df57d21d3a02d6c511f1609172fa39ac29b`。

- [官方 v3 格式文档](https://huggingface.co/docs/lerobot/en/lerobot-dataset-v3)
- [固定 commit 的 LeRobotDataset 源码](https://github.com/huggingface/lerobot/blob/0b067df57d21d3a02d6c511f1609172fa39ac29b/src/lerobot/datasets/lerobot_dataset.py)
- [固定 commit 的 utils / 路径 / tasks.parquet 实现](https://github.com/huggingface/lerobot/blob/0b067df57d21d3a02d6c511f1609172fa39ac29b/src/lerobot/datasets/utils.py)

核对的原始文件 SHA256：

| 文件 | SHA256 |
| --- | --- |
| lerobot_dataset.py | bb4b1f7cb2dca6b440f846b35670eba2ddb39434db0f03591a53fef6f022a9f8 |
| utils.py | b17a40e78e35584df3a2ef289eeb17252380d900703d4dfd77d196488dbd3068 |

源码中的 `LeRobotDatasetMetadata.get_data_file_path` 读取 episode 的 `data/chunk_index`、`data/file_index`，通过 info 中的模板定位分片；`get_video_file_path` 对每路相机独立定位分片。`_get_query_indices` 的 `dataset_from_index` / `dataset_to_index` 是**全局、左闭右开**索引；`_query_videos` 将 episode 相对时间加到 `videos/{camera}/from_timestamp`。

### 为何隔离轻量适配器

官方 `lerobot_dataset.py` 模块会直接导入 `torch`、`torch.utils`、`datasets`、图像与训练处理模块。首版 HTTP 服务只做数据浏览、CPU 检查和任务调度，故使用隔离的 `backend/app/data/lerobot_v3.py`，以 PyArrow 读取相同 Parquet/schema，以 PyAV 检查真实视频 PTS。没有安装官方训练 reader，也**没有声称已通过官方 LeRobotDataset 实例化/训练兼容测试**。现有证据是固定版本源码核对、完整公共数据读取及边界解码、真实格式合成测试。

后续训练 worker 应安装相应 release，在独立环境中通过官方 reader 加载数据并做数值对照。平台适配器不会替代训练中的归一化、delta_timestamps、padding、图像 transforms 或 action 空间语义处理。

## 已下载的公共样本

- 仓库：[lerobot/pusht](https://huggingface.co/datasets/lerobot/pusht)
- 固定 revision：`7628202a2180972f291ba1bc6723834921e72c19`
- 位置：`storage/incoming/public-small/`
- 完整 revision 的 7 个文件（排除 Git 属性文件），共 **7,684,382 bytes**，约 7.33 MiB；未裁剪、重写或改标。
- 206 条 episode、25,650 帧、10Hz、1 路 96×96 AV1 相机、2 维 state、2 维 action。这个 PushT 数据集是公开的仿真/交互数据集，不表示真实机械臂或本项目策略评测成绩。
- 206 条 episode 共享 `data/chunk-000/file-000.parquet` 以及 `videos/observation.image/chunk-000/file-000.mp4`。
- `SOURCE.json` 记录源 URL、固定 revision、逐文件字节数、逐文件 SHA256；视频 SHA256 为 `f58d11857651dad5983c021b56a50a68a6ce19068834c1fb5cac099219fb3a78`。
- 下载脚本 `scripts/fetch_public_sample.py` 先检查 HF tree 体积，不超过 200 MiB 才开始；固定 revision，验证响应字节数和可用的 LFS SHA256，不执行远程脚本。

```sh
.venv/bin/python scripts/fetch_public_sample.py
```

2026-10-22 实际运行质量检查：206 条 episode、25,650 帧，**0 error / 0 warning**。206 条轨迹的视频首末时间均实际通过 PyAV 解码，不是仅检查 MP4 文件存在。原始 JSON 证据见 `docs/public_quality_evidence.json`。浏览器仍需支持 AV1；服务返回原始 MP4 而非伪造预览，Chrome 可在浏览器验收时核实播放能力。

## 合成正常与异常样本

```sh
.venv/bin/python scripts/generate_fixture.py --all-variants
```

默认 `synthetic-small/`：3 条 episode，长度 12 / 16 / 14，合计 42 帧，10Hz；每条状态 3 维、动作 2 维。所有轨迹共享一个 Parquet。两路 H264 MP4 也各自共享全部 episode，wrist 视频有 2 个前导帧，所以**同一个 episode 的两个相机起点相差 0.2 秒**。合成像素和数值没有任何模型成功率含义，`SOURCE.json` 明确 `source_kind=synthetic_demo`。

变体分别覆盖：

- `synthetic-nan`：action NaN 和 state Inf。
- `synthetic-timestamp`：episode 内时间倒退，跨 episode 正常重置到 0 不算错。
- `synthetic-dimension`：单行 action 维度缺失。
- `synthetic-boundary`：全局索引超范围、episode 区间交叉、视频 end 越界。
- `synthetic-missing_field`：Parquet 缺少 action 字段。

生成的内容是真正的 Parquet 和可解码 H264 MP4。`meta/tasks.parquet` 使用上游 Pandas 索引语义的 Arrow metadata；`meta/info.json`、`meta/episodes/chunk-000/file-000.parquet`、`meta/stats.json` 使用上游路径形式。

## 读取、回放与报告的具体语义

1. `inspect_dataset` 校验支持版本、schema、完整 episode metadata、受控相对路径与文件存在，返回内容指纹与各 episode 实际偏移。不会把一条 episode 假定为一个文件。异常的轨迹行可以正常导入后再通过质量任务定位；缺少关键元信息或缺整个分片会直接拒绝。
2. `read_episode` 根据该 episode 的元信息选分片，再对 `index` 做范围过滤。仅返回当前请求的最多 2000 帧，严格核对 `index` / `episode_index` / `frame_index`，不从相邻 episode 填充缺失行。
3. 浏览器 API 的 `timestamps` 减去该 episode 原始首帧时间，首帧为 0；页面对每路视频设置 `camera.start_time + local_time`。质量任务仍检查**原始**时间戳，没有修改或掩盖源数据异常。非有限曲线值用 JSON `null` 表示。
4. `quality_report` 每个物理 Parquet 分片以 1024 行批次读取一次。检查必需字段、向量维度/有限值、局部时间、全局范围/交叉/缺口、frame 连续性、length、视频可访问性与首末帧 PTS。v2适配器还按实际 `timestamp-origin` 校验每帧是否落在每路相机的有效片段，范围及PTS误差允许该相机0.5帧容差；不会用名义帧率代替实际时间戳来掩盖越界。视频先seek到前一个关键帧，再解码至实际目标时间。浏览器解码/seek可能有调度误差，与后端范围校验分别记录，不宣称所有浏览器逐帧完全精确。
5. 报告精确累计每种规则的总异常数量，最多保存 1000 条具体 issue，明确 `issues_truncated`。视频边界证据最多保留 2000 条，明确截断。进度来自实际检查帧/episode 数量，回调取消/租约异常向上传播。
6. `split_episodes` 使用独立 `random.Random(seed)` 对完整 episode 列表排序后洗牌。`ratio` 表示训练占比，至少保留一条训练和一条验证；输出版本指纹、seed、比例、双方 episode 列表。不会切相邻帧来宣称无泄漏。
7. `statistics_report` 对 state/action 每维使用 Welford 算法流式求均值与总体标准差；非有限值明确计数后排除，不将它们替换为 0。统计不会混合不同数据集或重新解释同维动作的物理含义。
8. 内容指纹按相对文件名、大小和每个文件 SHA256 汇总；包括 `SOURCE.json`，忽略下载工具 `.cache`。平台报告应写入数据快照以外的产物目录，避免改变输入版本指纹。

## 当前限制

只支持带完整元信息和 MP4 视频的 v3.0 常见格式；拒绝 v2.1、嵌入 image 列、未声明分片与缺失 offset。state/action 必须是一维向量，维度从 schema 读取；不假定 6/7 维机械臂。不包含 ROS bag、深度图专用解码、音频、action 空间转换、训练 transform 或在线机器人连接。真实视频解码在 CPU worker 的质量任务执行，不会在每次 API 初始化时加载训练框架。

适配器测试：

```sh
.venv/bin/pytest -q tests/test_adapter.py
```
