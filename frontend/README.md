# 前端工作台

本目录是浏览器端界面。数据、状态、日志和结果均来自 `/api`；没有预置成功任务或模型指标。

## 启动和验证

在项目根目录优先执行 `bash scripts/dev.sh`，会同时准备 API、worker 和前端。单独开发前端：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
# http://127.0.0.1:5173，/api 由 Vite 代理至 http://127.0.0.1:8000
pnpm build
pnpm test
```

默认 Node 24、pnpm 11.25.0。请确认它们已加入 PATH；`scripts/dev.sh` 也会探测当前用户目录下可用的 bundled runtime，不依赖某个开发者的绝对路径。依赖锁位于 `pnpm-lock.yaml`；安装脚本仅允许 esbuild 和 vue-demi 必需的构建。

## 固定的直接依赖

| 依赖 | 版本 | 在本项目中的用途 |
| --- | --- | --- |
| Vue | 3.5.44 | 页面组件、响应式请求状态和时间游标 |
| Vue Router | 4.6.3 | 数据集、轨迹、任务、实验的独立地址 |
| Element Plus | 2.11.8 | 统一对话框和操作反馈消息；其余布局采用本地 CSS |
| ECharts | 6.0.0 | 按当前片段绘制状态/动作曲线，按需导入折线图及必要组件 |
| Vite | 7.2.2 | 本地服务器、API 代理和静态资源构建 |
| TypeScript | 5.9.3 | API 数据及页面行为类型检查 |
| @vitejs/plugin-vue | 6.0.2 | Vue SFC 编译 |
| vue-tsc | 3.1.4 | 构建前检查组件脚本与模板类型 |
| Vitest | 3.2.4 | 时间偏移、共享视频末帧边界及事件去重的行为测试 |
| Prettier | 3.6.2 | 保持源码可阅读；Vue 多语句事件 handler 保留分号 |
| @types/node | 24.10.1 | Vite 配置中的 Node 类型 |

## 从哪里开始读

1. `src/main.ts`：路由声明、页面按需加载和样式入口。
2. `src/lib/api.ts`：真实 API 请求封装、错误提取和业务类型。
3. `src/pages/DatasetsPage.vue`：`openImport → selectSource → submit` 如何选择受控目录、填写数据来源并创建导入任务。
4. `src/pages/EpisodePage.vue`：`fetchChunk → setTime → alignVideos → draw` 如何把一条 episode 的局部时间映射到多相机共享视频。
5. `src/pages/JobPage.vue`：`history → append → connect` 如何合并历史与 SSE，并在断线时通过普通查询补偿。

其他页面：`DatasetPage.vue` 展示 schema、轨迹检索和质量/划分提交；`ExperimentsPage.vue` 创建实验/检查点并调用条件对比；`ExperimentPage.vue` 读取 JSON 文件、显示实际逐条 trial 汇总和训练能力检查。

## 关键行为和边界

- 当前片段最多读取 1,000 帧，长轨迹游标跨片段时再请求下一片；不会一次载入全部数据。
- `Frames.timestamps` 是相对 episode 首帧的时间。每个相机用自身 `start_time + cursor` 定位；v3 的 `end_time` 为排他边界，所以定位上限是 `end_time − 1/fps`，避免展示下一条 episode。
- 浏览器视频定位受编码关键帧与解码误差影响；数据帧索引是准确口径。浏览器仅从 API 提供的受控 URL 读取视频。
- `mergeEvents` 根据数据库 event ID 去重排序；定时读取状态与补取事件，补偿 SSE 连接丢失。终态后关闭 SSE，任务和事件不会因页面关闭而丢失。
- 结果导入最多 2 MB；必须选择 `imported` 或 `synthetic_demo`。客户端不能提交平台实际执行 `executed`。数字指标由后端逐条计数计算。
- 比较结果中的 `comparable` 和 `reasons` 由后端根据版本、任务、seed、指标口径和来源计算；条件不一致会直接显示原因。
- 未配置的训练/仿真 runner 显示具体原因并禁用提交，不把按钮当成模型实验完成证据。
- 首版提供小 JSON 结果上传和受控本地数据目录导入；不提供大型任意文件上传、登录或公网发布。

## 验证范围

`src/lib/timeline.test.ts` 覆盖共享视频偏移/末帧、非整数时间定位和重叠 SSE 日志去重。完整 API/worker/PostgreSQL 集成由根目录测试验证，浏览器操作和截图记录见 `docs/ACCEPTANCE.md` 与 `docs/screenshots/`。
