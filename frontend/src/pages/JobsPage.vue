<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { api, date, jobLabel, progressPercent, type Job, type List } from '../lib/api';
import StatusBadge from '../components/StatusBadge.vue';
import StateBox from '../components/StateBox.vue';
const jobs = ref<Job[]>([]),
  total = ref(0),
  loading = ref(true),
  error = ref(''),
  filter = ref('all'),
  offset = ref(0);
const shown = computed(() =>
  filter.value === 'all' ? jobs.value : jobs.value.filter((j) => j.status === filter.value),
);
async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    const d = await api<List<Job>>(`/jobs?offset=${offset.value}&limit=50`);
    jobs.value = d.items;
    total.value = d.total;
    error.value = '';
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
let timer: ReturnType<typeof setInterval>;
onMounted(() => {
  load();
  timer = setInterval(() => load(true), 4000);
});
onUnmounted(() => clearInterval(timer));
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">JOB CENTER</div>
      <h1>任务中心</h1>
      <p>查看后台任务的执行进度、持久化日志和输出产物。</p>
    </div>
    <button class="btn" @click="load()">↻ 刷新任务</button>
  </div>
  <div class="stats">
    <div class="stat-card">
      <div class="stat-label">全部任务<span>☷</span></div>
      <div class="stat-value">{{ total }}<small>条记录</small></div>
      <div class="stat-note">独立 worker 执行</div>
    </div>
    <div
      class="stat-card"
      v-for="[status, label] in [
        ['running', '运行中'],
        ['queued', '排队中'],
        ['failed', '执行失败'],
      ]"
      :key="status"
    >
      <div class="stat-label">{{ label }}</div>
      <div class="stat-value">
        {{ jobs.filter((j) => j.status === status).length }}<small>当前页</small>
      </div>
      <div class="stat-note">自动同步最新状态</div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-header">
      <h2>
        任务记录 <span class="count">{{ total }}</span>
      </h2>
      <select
        v-model="filter"
        class="input"
        style="width: 130px; min-height: 30px; padding: 5px 8px; font-size: 11px"
      >
        <option value="all">全部状态</option>
        <option value="running">运行中</option>
        <option value="queued">排队中</option>
        <option value="succeeded">已完成</option>
        <option value="failed">失败</option>
        <option value="cancelled">已取消</option>
      </select>
    </div>
    <StateBox
      :loading="loading"
      :error="error"
      :empty="!shown.length"
      title="没有符合条件的任务"
      @retry="load()"
      ><div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>任务 / ID</th>
              <th>状态</th>
              <th>执行进度</th>
              <th>尝试次数</th>
              <th>创建时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="j in shown" :key="j.id">
              <td>
                <RouterLink :to="`/jobs/${j.id}`" class="data-name"
                  ><span class="folder-icon">☷</span>
                  <div>
                    {{ jobLabel(j.type) }}<small class="mono">{{ j.id.slice(0, 12) }}</small>
                  </div></RouterLink
                >
              </td>
              <td><StatusBadge :status="j.status" /></td>
              <td>
                <div class="job-progress">
                  <div>
                    <span>{{ j.status === 'queued' ? '等待 worker' : '处理进度' }}</span
                    ><span>{{ progressPercent(j.progress) }}%</span>
                  </div>
                  <div class="progress">
                    <span :style="{ width: progressPercent(j.progress) + '%' }"></span>
                  </div>
                </div>
              </td>
              <td class="number">{{ j.attempt }} / {{ j.max_attempts }}</td>
              <td class="subtle">{{ date(j.created_at) }}</td>
              <td>
                <RouterLink class="btn ghost small" :to="`/jobs/${j.id}`">查看 ↗</RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-bottom">
        <span>当前页 {{ jobs.length }} / {{ total }} 条记录</span>
        <div class="actions">
          <button
            class="btn small"
            :disabled="offset === 0"
            @click="
              offset -= 50;
              load();
            "
          >
            ← 上一页</button
          ><button
            class="btn small"
            :disabled="offset + 50 >= total"
            @click="
              offset += 50;
              load();
            "
          >
            下一页 →
          </button>
        </div>
      </div></StateBox
    >
  </div>
</template>
