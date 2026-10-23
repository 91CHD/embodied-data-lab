# 浏览器实际验收记录

时间：2026-10-22，Asia/Shanghai，约 20:16–20:23。使用 Codex in-app Chromium 浏览器，通过真实页面操作，API 指向本机 PostgreSQL 和独立 worker。未使用浏览器网络 mock、硬编码响应或模拟任务成功。主入口 `http://127.0.0.1:5173`。

## 数据导入、回放和质量

| 操作 | 真实记录 / 观察 | 结论 |
| --- | --- | --- |
| 打开空工作台 | 初始 0 数据集、0 任务，界面呈现空状态 | 通过 |
| 选择 `public-small` | 来源类型自动变为公开数据，source_uri 为 `https://huggingface.co/datasets/lerobot/pusht`，revision 自动回填 `7628202a2180972f291ba1bc6723834921e72c19` | 通过 |
| 导入公开数据 | 任务 `dc2d716a-1931-4d4f-861c-dc4c0dd339a8`；数据集 `16a6012e-32ad-439b-9cb6-1b0491f21e0b`，名称 `PushT · 公开数据`；206 episode，25,650 帧 | 实际完成 |
| 公开 episode 1 回放 | AV1 视频在浏览器正常显示；118 帧、10 fps，视频片段 `[16.1,27.9)` 秒。拖动游标到 End 后显示 episode 时间 11.7、数据帧 117；读取 DOM video.currentTime 为 27.8，无 video.error | 通过，未越入下一条 episode |
| 按帧、维度、播放 | 点击前后帧、维度 0→1、Home 后播放，看到按钮变“暂停”、游标前进至 0.233 秒 | 通过 |
| 质量检查 | 任务 `d0dc5c95-26c7-4c5e-a764-f331161e90de`，worker 实际检查 206 ep/25,650 帧；0 errors、0 warnings；报告有每条轨迹视频首末帧解码时间 | 实际完成 |
| 页面重开 | 刷新质量任务页面后恢复 45 条已保存事件、最终状态、报告和两个产物链接 | 通过 |
| 报告下载 | 点击 `quality_report.json`，浏览器下载到本机 Downloads 目录；页面产物显示 60.5 KB，SHA256 前缀 `f20a9e8cd9eb` | 下载成功 |
| 双相机合成数据 | 任务 `57b89e72-fcc0-46a3-889d-52242ad4d25f`；数据集 `fcde969a-62b9-4828-9ee6-4be766f99d33`，名称 `双相机轨迹 · 合成演示`；来源 `synthetic_demo` | 实际导入 |
| 双相机偏移 | episode 1：front start=1.2、wrist start=1.4 秒；按两次下一帧后游标=0.2。读取 DOM 两 video.currentTime=1.4 / 1.6，readyState 都为 4，error 均为 null | 通过，两个相机分别用自身偏移 |
| Schema | 公开数据详情中打开“特征 Schema”，展示实际 observation.state/action 及 dtype、shape | 通过 |
| Episode 划分 | 从公开数据详情提交 ratio=0.8、seed=42；任务 `e42fc864-88b6-4aa1-959b-03d53c1fd3a6` 实际完成，报告 train=164、validation=42、unit=episode，保留参数和源指纹 | 通过；重复性和无交叉由后端行为测试另外验证 |

公开数据内容指纹 `9b0297b095a3413db79b882e7f3b19625357304c1a39f20d833bc7b349dbd561`。合成数据内容指纹 `6afd5d26744197743d92ef8afea20edb9da74420a488a585f34907c258282280`。报告为平台实际执行的数据检查；这不表示执行了策略训练或仿真。

## 检查点、实验和结果文件

- 通过页面登记检查点 `演示策略占位 · 无权重`，source=`synthetic://fixture-policy`，revision=`fixture-v1`。此登记不含任何实际权重。
- 创建实验 A：`85aa0f84-c7dd-4edc-b5e2-d26d1334e771`，名称 `平台联调演示 A · 合成结果`，关联合成数据与上述检查点，code_version=`fixture-v1`，config=`{"demo":true,"action_chunk":10}`。
- 创建实验 B：`3315ba21-a19e-4690-9928-391c70f8b581`，名称 `平台联调演示 B · 合成结果`，条件同上，config 中 action_chunk=20。
- 在 A 导入框粘贴 `{"source_kind":"executed"}`，页面明确拒绝，显示“source_kind 必须为 imported 或 synthetic_demo”。
- A 通过粘贴 JSON 导入明确标为 `synthetic_demo` 的 2 条人工 trial：一成功一失败，seed=42。页面实际汇总为 1/2=50%、平均 14 步、4 ms。
- B 通过浏览器 filechooser 选择 `frontend/e2e-fixtures/results-demo.json`，textarea 正常读取内容，再点击归档。2 条人工成功 trial、seed=42，页面汇总为 2/2=100%。
- 选择 A/B 比较，页面显示条件一致，保留模型、数据指纹、source_kind、seed、任务、指标口径及原始样本数。
- 向 B 追加同结构 seed=43 的人工记录，再比较，页面明确提示 `seed 不一致，不可直接比较`，不会把不同条件默认为可比。
- 点击创建训练任务，页面显示 Linux/GPU worker 未配置，“提交任务”按钮禁用。没有实际运行任何训练或仿真。

**以上 50% / 100% 仅是人工合成布尔结果的计数，用于验证导入、归档和比较链路，不是模型成绩。** 试验名称、数据来源、指标口径和结果来源均保留此说明。

## 截图

均为实际浏览器完整页面截图（JPEG），保存在 `docs/screenshots/`：

1. `01-data-workspace.jpg`：公开与合成数据列表。
2. `02-dataset-schema.jpg`：公开数据 schema。
3. `03-public-episode.jpg`：公开 episode 1 的视频和状态/动作曲线。
4. `04-quality-report.jpg`：质量检查任务、持久化日志、质量摘要与报告。
5. `05-dual-camera-playback.jpg`：两个相机不同起点的联动回放。
6. `06-experiment-result.jpg`：明确标识合成来源的实验结果。
7. `07-unconfigured-runner.jpg`：训练环境未配置、不可提交。
8. `08-comparable-experiments.jpg`：相同条件结果比较。
9. `09-incomparable-experiments.jpg`：seed 不同的警告。
10. `10-episode-split.jpg`：实际按 episode 划分的输出清单。
11. `11-job-center.jpg`：实际任务列表与终态。

## 前端构建与行为测试

在 `frontend/` 执行（本机 Node 24.19.0、pnpm 11.25.0）：

```bash
pnpm install --frozen-lockfile
pnpm build
pnpm test
```

最终 `vue-tsc -b && vite build` 通过；按需加载页面和 ECharts，最大 JS chunk 336.06 KB，无大 chunk 警告。Vitest 1 文件、4 行为测试通过：相机偏移、排他末帧边界（含单帧 episode）、真实时间序列定位、重叠历史/SSE 事件去重排序。后端集成、双 worker、失联恢复、重试和取消的更广验证见 `docs/ACCEPTANCE.md`；浏览器本次没有声称单独完成全部进程故障测试。

浏览器精确定位受视频编码/关键帧影响；应用在界面提示这一限制，数据索引与后端解码证据另行保留。

约 20:25 完整停止并重启 PostgreSQL/API/worker/前端、加载新版 adapter 后，再次从浏览器复查：两个数据集、209 条轨迹索引仍存在；公开 episode 1 末帧仍映射到 27.8 秒且 video.error=null；两条实验、A 的 1 条合成结果、原始产物仍可查看。重新保存 `01-data-workspace.jpg`，状态已显示中文“已检查”。新版 adapter 的再次公开质量执行证据由根目录验收补充。
