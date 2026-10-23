<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElDialog, ElMessage } from 'element-plus';
import {
  api,
  post,
  newKey,
  date,
  pretty,
  sourceLabel,
  jobLabel,
  type Dataset,
  type Episode,
  type Job,
  type List,
} from '../lib/api';
import StateBox from '../components/StateBox.vue';
import StatusBadge from '../components/StatusBadge.vue';
const route = useRoute(),
  router = useRouter(),
  data = ref<Dataset>(),
  episodes = ref<Episode[]>([]),
  total = ref(0),
  jobs = ref<Job[]>([]),
  loading = ref(true),
  error = ref(''),
  tab = ref('episodes'),
  query = ref(''),
  offset = ref(0),
  split = ref(false),
  ratio = ref(0.8),
  seed = ref(42),
  saving = ref(false),
  actionError = ref('');
const id = String(route.params.id);
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [d, e, j] = await Promise.all([
      api<Dataset>(`/datasets/${id}`),
      api<List<Episode>>(
        `/datasets/${id}/episodes?q=${encodeURIComponent(query.value)}&offset=${offset.value}&limit=20`,
      ),
      api<List<Job>>(`/jobs?dataset_id=${id}&limit=50`),
    ]);
    data.value = d;
    episodes.value = e.items;
    total.value = e.total;
    jobs.value = j.items;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
async function run(type: string, params: Record<string, unknown> = {}) {
  saving.value = true;
  actionError.value = '';
  try {
    const job = await post<Job>('/jobs', {
      type,
      dataset_id: id,
      params,
      idempotency_key: newKey(),
    });
    split.value = false;
    ElMessage.success('任务已加入队列');
    router.push(`/jobs/${job.id}`);
  } catch (e) {
    actionError.value = (e as Error).message;
    ElMessage.error(actionError.value);
  } finally {
    saving.value = false;
  }
}
onMounted(load);
</script>
<template>
  <RouterLink to="/datasets" class="back">← 返回数据工作台</RouterLink
  ><StateBox :loading="loading" :error="error" @retry="load"
    ><template v-if="data"
      ><div class="page-heading">
        <div>
          <div class="eyebrow">DATASET OVERVIEW</div>
          <div class="detail-title">
            <h1>{{ data.name }}</h1>
            <StatusBadge :status="data.status" /><span class="source" :class="data.source_kind">{{
              sourceLabel(data.source_kind)
            }}</span>
          </div>
          <p>独立数据版本 · 创建于 {{ date(data.created_at) }}</p>
        </div>
        <div class="actions">
          <button class="btn" :disabled="saving" @click="run('statistics')">生成统计</button
          ><button class="btn" @click="split = true">划分数据集</button
          ><button class="btn primary" :disabled="saving" @click="run('validate')">
            ＋ 质量检查
          </button>
        </div>
      </div>
      <div v-if="actionError" class="inline-error">{{ actionError }}</div>
      <div class="panel">
        <div class="panel-body meta-grid">
          <div class="meta-item">
            <span>EPISODES</span><strong>{{ data.total_episodes }} 条轨迹</strong>
          </div>
          <div class="meta-item">
            <span>数据格式</span><strong>LeRobot {{ data.format_version }}</strong>
          </div>
          <div class="meta-item">
            <span>来源</span><strong>{{ data.source_uri || '本地受控目录' }}</strong>
          </div>
          <div class="meta-item">
            <span>版本指纹</span><strong class="mono">{{ data.fingerprint }}</strong>
          </div>
          <div class="meta-item">
            <span>REVISION</span><strong class="mono">{{ data.revision || '未登记' }}</strong>
          </div>
          <div class="meta-item">
            <span>数据集 ID</span><strong class="mono">{{ data.id }}</strong>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="tabs">
          <button :class="{ active: tab === 'episodes' }" @click="tab = 'episodes'">
            轨迹 Episodes <span class="count">{{ total }}</span></button
          ><button :class="{ active: tab === 'schema' }" @click="tab = 'schema'">特征 Schema</button
          ><button :class="{ active: tab === 'quality' }" @click="tab = 'quality'">
            检查与任务</button
          ><button :class="{ active: tab === 'summary' }" @click="tab = 'summary'">数据摘要</button>
        </div>
        <div v-if="tab === 'episodes'">
          <form
            class="toolbar"
            @submit.prevent="
              offset = 0;
              load();
            "
          >
            <label class="search"
              ><span>⌕</span
              ><input
                aria-label="搜索轨迹"
                v-model="query"
                placeholder="检索 episode / 任务描述…" /></label
            ><button class="btn small">搜索</button>
          </form>
          <StateBox :empty="!episodes.length" title="没有匹配的轨迹"
            ><div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>EPISODE</th>
                    <th>任务描述</th>
                    <th>帧数</th>
                    <th>时长</th>
                    <th>相机</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="e in episodes" :key="e.id">
                    <td class="mono">#{{ String(e.episode_index).padStart(4, '0') }}</td>
                    <td style="max-width: 350px; white-space: normal">
                      {{ e.task || '未标注任务' }}
                    </td>
                    <td>{{ e.length }}</td>
                    <td>{{ e.duration?.toFixed(2) }} s</td>
                    <td>
                      <span class="pill">{{ e.cameras.length }} 个视角</span>
                    </td>
                    <td>
                      <RouterLink
                        class="btn ghost small"
                        :to="`/datasets/${id}/episodes/${e.episode_index}`"
                        >回放 ▷</RouterLink
                      >
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="table-bottom">
              <span
                >共 {{ total }} 条 · 当前 {{ offset + 1 }}–{{ Math.min(offset + 20, total) }}</span
              >
              <div class="actions">
                <button
                  class="btn small"
                  :disabled="offset === 0"
                  @click="
                    offset -= 20;
                    load();
                  "
                >
                  ← 上一页</button
                ><button
                  class="btn small"
                  :disabled="offset + 20 >= total"
                  @click="
                    offset += 20;
                    load();
                  "
                >
                  下一页 →
                </button>
              </div>
            </div></StateBox
          >
        </div>
        <div v-else-if="tab === 'schema'" class="panel-body">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>字段</th>
                  <th>类型</th>
                  <th>SHAPE</th>
                  <th>完整定义</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(feature, key) in data.features" :key="key">
                  <td class="mono">{{ key }}</td>
                  <td>{{ (feature as any)?.dtype || '—' }}</td>
                  <td class="mono">{{ JSON.stringify((feature as any)?.shape || []) }}</td>
                  <td class="mono" style="max-width: 380px; white-space: normal">
                    {{ JSON.stringify(feature) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div v-else-if="tab === 'quality'">
          <StateBox :empty="!jobs.length" title="还没有数据处理任务"
            ><div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>任务</th>
                    <th>状态</th>
                    <th>进度</th>
                    <th>创建时间</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="j in jobs" :key="j.id">
                    <td>
                      {{ jobLabel(j.type)
                      }}<small class="mono muted"> · {{ j.id.slice(0, 8) }}</small>
                    </td>
                    <td><StatusBadge :status="j.status" /></td>
                    <td>{{ Math.round(j.progress <= 1 ? j.progress * 100 : j.progress) }}%</td>
                    <td>{{ date(j.created_at) }}</td>
                    <td>
                      <RouterLink :to="`/jobs/${j.id}`" class="btn ghost small"
                        >报告 / 日志 ↗</RouterLink
                      >
                    </td>
                  </tr>
                </tbody>
              </table>
            </div></StateBox
          >
        </div>
        <div v-else class="panel-body">
          <pre class="code">{{ pretty(data.summary) }}</pre>
        </div>
      </div></template
    ></StateBox
  ><ElDialog v-model="split" title="按完整 Episode 划分" width="470px"
    ><div class="info-banner">
      划分仅生成清单，保留原始数据。同一数据版本和 seed 会得到相同结果。
    </div>
    <div class="form-grid">
      <label class="field"
        ><span>训练集比例</span
        ><input v-model.number="ratio" type="number" min="0.01" max="0.99" step="0.05" /></label
      ><label class="field"
        ><span>随机种子 Seed</span><input v-model.number="seed" type="number" min="0" step="1"
      /></label>
    </div>
    <div class="dialog-footer">
      <button class="btn" @click="split = false">取消</button
      ><button
        class="btn primary"
        :disabled="saving || ratio <= 0 || ratio >= 1"
        @click="run('split', { ratio, seed })"
      >
        提交划分任务
      </button>
    </div></ElDialog
  >
</template>
