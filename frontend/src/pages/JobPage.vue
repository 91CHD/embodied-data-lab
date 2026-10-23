<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue';
import { useRoute } from 'vue-router';
import { ElMessage } from 'element-plus';
import {
  api,
  post,
  date,
  pretty,
  jobLabel,
  progressPercent,
  terminal,
  type Job,
  type JobEvent,
  type List,
} from '../lib/api';
import { mergeEvents } from '../lib/timeline';
import StatusBadge from '../components/StatusBadge.vue';
import StateBox from '../components/StateBox.vue';
import ArtifactList from '../components/ArtifactList.vue';
import ReportView from '../components/ReportView.vue';
const id = String(useRoute().params.id),
  job = ref<Job>(),
  events = ref<JobEvent[]>([]),
  report = ref<unknown>(),
  reportError = ref(''),
  loading = ref(true),
  error = ref(''),
  streamState = ref('连接中'),
  saving = ref(false),
  logBox = ref<HTMLElement>(),
  autoScroll = ref(true);
let stream: EventSource | undefined,
  timer: ReturnType<typeof setInterval> | undefined,
  lastCursor = 0,
  reportLoaded = false,
  stopped = false;
async function append(incoming: JobEvent[]) {
  events.value = mergeEvents(events.value, incoming);
  lastCursor = events.value.at(-1)?.id ?? 0;
  if (autoScroll.value) {
    await nextTick();
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight;
  }
}
async function history() {
  const data = await api<List<JobEvent>>(`/jobs/${id}/events?after=${lastCursor}`);
  await append(data.items);
}
function connect() {
  stream?.close();
  streamState.value = '连接中';
  stream = new EventSource(`/api/jobs/${id}/stream?after=${lastCursor}`);
  stream.onopen = () => (streamState.value = '实时连接');
  stream.addEventListener('log', (e) => {
    try {
      append([JSON.parse((e as MessageEvent).data)]);
    } catch {
      streamState.value = '日志解析失败';
    }
  });
  stream.onerror = () => {
    if (job.value && terminal(job.value.status)) {
      stream?.close();
      streamState.value = '历史已保存';
    } else streamState.value = '重连中 · 查询补偿';
  };
}
async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    job.value = await api<Job>(`/jobs/${id}`);
    await history();
    error.value = '';
    if (terminal(job.value.status)) {
      stream?.close();
      streamState.value = '历史已保存';
      if (!reportLoaded) {
        reportLoaded = true;
        try {
          report.value = await api(`/jobs/${id}/report`);
          reportError.value = '';
        } catch (e) {
          reportError.value = (e as Error).message;
        }
      }
    }
  } catch (e) {
    if (!silent) error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
async function action(type: 'cancel' | 'retry') {
  saving.value = true;
  try {
    await post(`/jobs/${id}/${type}`);
    ElMessage.success(type === 'cancel' ? '取消请求已发送' : '已重新加入队列');
    reportLoaded = false;
    await load(true);
    if (!terminal(job.value!.status)) connect();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    saving.value = false;
  }
}
onMounted(async () => {
  await load();
  if (!stopped && job.value && !terminal(job.value.status)) connect();
  timer = setInterval(() => load(true), 2500);
});
onUnmounted(() => {
  stopped = true;
  stream?.close();
  clearInterval(timer);
});
</script>
<template>
  <RouterLink to="/jobs" class="back">← 返回任务中心</RouterLink
  ><StateBox :loading="loading" :error="error" @retry="load()"
    ><template v-if="job"
      ><div class="page-heading">
        <div>
          <div class="eyebrow">JOB DETAIL</div>
          <div class="detail-title">
            <h1>{{ jobLabel(job.type) }}</h1>
            <StatusBadge :status="job.status" />
          </div>
          <p class="mono">{{ job.id }}</p>
        </div>
        <div class="actions">
          <RouterLink v-if="job.dataset_id" :to="`/datasets/${job.dataset_id}`" class="btn"
            >打开数据集 ↗</RouterLink
          ><button
            v-if="['failed', 'cancelled'].includes(job.status)"
            class="btn primary"
            :disabled="saving"
            @click="action('retry')"
          >
            重试任务</button
          ><button
            v-if="['queued', 'running'].includes(job.status)"
            class="btn danger"
            :disabled="saving"
            @click="action('cancel')"
          >
            取消任务
          </button>
        </div>
      </div>
      <div v-if="job.error" class="inline-error"><strong>执行信息：</strong>{{ job.error }}</div>
      <div class="panel">
        <div class="panel-body">
          <div
            style="
              display: flex;
              justify-content: space-between;
              font-size: 12px;
              margin-bottom: 13px;
            "
          >
            <strong>处理进度</strong
            ><span class="muted number">{{ progressPercent(job.progress) }}%</span>
          </div>
          <div class="progress" style="height: 6px">
            <span :style="{ width: progressPercent(job.progress) + '%' }"></span>
          </div>
          <div class="meta-grid" style="margin-top: 24px">
            <div class="meta-item">
              <span>创建时间</span><strong>{{ date(job.created_at) }}</strong>
            </div>
            <div class="meta-item">
              <span>开始 / 结束</span
              ><strong>{{ date(job.started_at) }} / {{ date(job.finished_at) }}</strong>
            </div>
            <div class="meta-item">
              <span>尝试次数</span><strong>{{ job.attempt }} / {{ job.max_attempts }}</strong>
            </div>
            <div class="meta-item">
              <span>WORKER</span><strong class="mono">{{ job.worker_id || '等待分配' }}</strong>
            </div>
            <div class="meta-item">
              <span>最近心跳</span><strong>{{ date(job.heartbeat_at) }}</strong>
            </div>
            <div class="meta-item">
              <span>租约到期</span><strong>{{ date(job.lease_expires_at) }}</strong>
            </div>
          </div>
        </div>
      </div>
      <div class="two-col">
        <div>
          <div class="panel">
            <div class="panel-header">
              <h2>
                执行日志 <span class="count">{{ events.length }}</span>
              </h2>
              <div class="actions">
                <label class="subtle"
                  ><input type="checkbox" v-model="autoScroll" class="checkbox" /> 跟随日志</label
                ><span class="pill">{{ streamState }}</span>
              </div>
            </div>
            <div class="log-console" ref="logBox">
              <div v-for="e in events" :key="e.id" class="log-row">
                <span class="log-time">{{ date(e.created_at) }}</span
                ><span class="log-level" :class="e.level">{{ e.level.toUpperCase() }}</span
                ><span>{{ e.message }}</span>
              </div>
              <div v-if="!events.length" class="muted">等待 worker 写入第一条事件…</div>
            </div>
          </div>
          <div class="panel">
            <div class="panel-header"><h2>结果报告</h2></div>
            <div class="panel-body">
              <ReportView v-if="report" :report="report" />
              <div v-else class="muted" style="font-size: 12px">
                {{
                  job.status === 'succeeded'
                    ? reportError || '此任务没有 JSON 报告，请查看产物列表。'
                    : '任务完成后在此查看报告；失败信息会保留在日志中。'
                }}
              </div>
            </div>
          </div>
        </div>
        <div>
          <div class="panel">
            <div class="panel-header">
              <h2>
                输出产物 <span class="count">{{ job.artifacts?.length || 0 }}</span>
              </h2>
            </div>
            <ArtifactList :items="job.artifacts || []" />
          </div>
          <div class="panel">
            <div class="panel-header"><h2>输入参数快照</h2></div>
            <div class="panel-body">
              <pre class="code">{{ pretty(job.params) }}</pre>
            </div>
          </div>
          <div class="info-banner">关闭页面不会停止任务。执行事件已保存，重新打开可继续查看。</div>
        </div>
      </div></template
    ></StateBox
  >
</template>
