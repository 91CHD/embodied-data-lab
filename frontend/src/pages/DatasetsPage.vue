<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElDialog, ElMessage } from 'element-plus';
import {
  api,
  post,
  newKey,
  date,
  short,
  sourceLabel,
  type Dataset,
  type Job,
  type List,
} from '../lib/api';
import StatusBadge from '../components/StatusBadge.vue';
import StateBox from '../components/StateBox.vue';
const router = useRouter(),
  datasets = ref<Dataset[]>([]),
  total = ref(0),
  jobs = ref<Job[]>([]),
  loading = ref(true),
  error = ref(''),
  query = ref(''),
  offset = ref(0),
  modal = ref(false),
  saving = ref(false),
  formError = ref(''),
  sources = ref<unknown[]>([]),
  sourcesError = ref('');
const form = ref({ name: '', path: '', source_kind: 'local', source_uri: '', revision: '' });
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [data, jobData] = await Promise.all([
      api<List<Dataset>>(
        `/datasets?q=${encodeURIComponent(query.value)}&offset=${offset.value}&limit=20`,
      ),
      api<List<Job>>('/jobs?limit=100'),
    ]);
    datasets.value = data.items;
    total.value = data.total;
    jobs.value = jobData.items;
  } catch (e) {
    error.value = String((e as Error).message);
  } finally {
    loading.value = false;
  }
}
async function openImport() {
  modal.value = true;
  formError.value = '';
  sourcesError.value = '';
  try {
    const data = await api<unknown>('/sources');
    sources.value = Array.isArray(data) ? data : (data as List<unknown>).items || [];
  } catch (e) {
    sourcesError.value = (e as Error).message;
  }
}
function sourcePath(s: unknown) {
  return typeof s === 'string'
    ? s
    : String((s as Record<string, unknown>).path ?? (s as Record<string, unknown>).name ?? '');
}
function selectSource() {
  const s = sources.value.find((s) => sourcePath(s) === form.value.path);
  if (typeof s === 'object' && s) {
    const v = s as Record<string, unknown>;
    if (v.source_kind) form.value.source_kind = String(v.source_kind);
    if (v.source_uri) form.value.source_uri = String(v.source_uri);
    if (v.revision) form.value.revision = String(v.revision);
  }
  if (!form.value.name) form.value.name = form.value.path;
}
async function submit() {
  saving.value = true;
  formError.value = '';
  try {
    const job = await post<Job>('/datasets', { ...form.value, idempotency_key: newKey() });
    modal.value = false;
    ElMessage.success('已提交导入任务');
    router.push(`/jobs/${job.id}`);
  } catch (e) {
    formError.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
function search() {
  offset.value = 0;
  load();
}
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">DATA WORKSPACE</div>
      <h1>数据工作台</h1>
      <p>管理轨迹数据，查看每条 episode，追溯每一次实验。</p>
    </div>
    <button class="btn primary" @click="openImport"><span>＋</span> 导入数据集</button>
  </div>
  <div class="stats">
    <div class="stat-card">
      <div class="stat-label">数据集总量<span>▦</span></div>
      <div class="stat-value">{{ loading ? '—' : total }}<small>个数据集</small></div>
      <div class="stat-note">当前查询下的版本记录</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">已索引轨迹<span>⌁</span></div>
      <div class="stat-value">
        {{ loading ? '—' : datasets.reduce((n, d) => n + d.total_episodes, 0)
        }}<small>episodes</small>
      </div>
      <div class="stat-note">当前页数据集的轨迹总数</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">进行中任务<span>↗</span></div>
      <div class="stat-value">
        {{
          loading
            ? '—'
            : jobs.filter((j) => ['queued', 'running', 'cancel_requested'].includes(j.status))
                .length
        }}<small>个任务</small>
      </div>
      <div class="stat-note">最近 {{ jobs.length }} 条任务记录</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">公开数据版本<span>◇</span></div>
      <div class="stat-value">
        {{ loading ? '—' : datasets.filter((d) => d.source_kind === 'public').length
        }}<small>个版本</small>
      </div>
      <div class="stat-note">当前页中已登记的公开来源</div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-header">
      <h2>
        我的数据集 <span class="count">{{ total }}</span>
      </h2>
      <button class="btn ghost small" @click="load">↻ 刷新</button>
    </div>
    <form class="toolbar" @submit.prevent="search">
      <label class="search"
        ><span>⌕</span
        ><input aria-label="搜索数据集" v-model="query" placeholder="搜索数据集名称或任务…"
      /></label>
      <div class="actions">
        <button class="btn small" type="submit">搜索</button
        ><span class="subtle">每个导入版本独立保存</span>
      </div>
    </form>
    <StateBox
      :loading="loading"
      :error="error"
      :empty="!datasets.length"
      title="还没有导入数据集"
      @retry="load"
      ><template #hint>先将数据放入 incoming 目录，再点击右上角导入。</template>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>数据集名称 / 版本</th>
              <th>来源</th>
              <th>轨迹数</th>
              <th>格式</th>
              <th>状态</th>
              <th>创建时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in datasets" :key="d.id">
              <td>
                <RouterLink :to="`/datasets/${d.id}`" class="data-name"
                  ><span class="folder-icon">▤</span>
                  <div>
                    {{ d.name }}<small class="mono">{{ short(d.fingerprint || d.id) }}</small>
                  </div></RouterLink
                >
              </td>
              <td>
                <span class="source" :class="d.source_kind">{{ sourceLabel(d.source_kind) }}</span>
              </td>
              <td class="number">{{ d.total_episodes }}</td>
              <td>{{ d.format_version || '—' }}</td>
              <td><StatusBadge :status="d.status" /></td>
              <td class="subtle">{{ date(d.created_at) }}</td>
              <td>
                <RouterLink class="btn ghost small" :to="`/datasets/${d.id}`">查看 ↗</RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-bottom">
        <span
          >共 {{ total }} 个数据集 · 当前 {{ offset + 1 }}–{{ Math.min(offset + 20, total) }}</span
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
  <div class="info-banner">
    <span class="symbol">◇</span>
    <div>数据原始文件保持不变。检查报告、划分和实验均关联到具体数据版本，便于复现和回溯。</div>
  </div>
  <ElDialog v-model="modal" title="导入数据集" width="540px"
    ><div v-if="formError" class="inline-error">{{ formError }}</div>
    <div class="form-grid">
      <label class="field"
        ><span>数据集名称</span
        ><input v-model="form.name" placeholder="例如：抓取演示数据" required /></label
      ><label class="field"
        ><span>数据目录</span
        ><select v-model="form.path" @change="selectSource">
          <option value="">选择已准备的 incoming 子目录</option>
          <option v-for="s in sources" :key="sourcePath(s)" :value="sourcePath(s)">
            {{ sourcePath(s) }}
          </option></select
        ><small v-if="sourcesError" class="danger-text">{{ sourcesError }}</small
        ><small>仅导入服务器批准目录中的 LeRobot 数据，不会执行数据包内代码。</small></label
      ><label class="field"
        ><span>来源类型</span
        ><select v-model="form.source_kind">
          <option value="local">本地数据</option>
          <option value="public">公开数据</option>
          <option value="synthetic_demo">合成演示</option>
        </select></label
      >
      <div class="grid-2">
        <label class="field"
          ><span>来源地址 / 标识</span
          ><input v-model="form.source_uri" placeholder="可选，数据 repo_id 或来源" /></label
        ><label class="field"
          ><span>Revision</span><input v-model="form.revision" placeholder="可选，固定提交或版本"
        /></label>
      </div>
    </div>
    <div class="dialog-footer">
      <button class="btn" @click="modal = false">取消</button
      ><button
        class="btn primary"
        :disabled="saving || !form.path || !form.name.trim()"
        @click="submit"
      >
        {{ saving ? '提交中…' : '创建导入任务' }}
      </button>
    </div></ElDialog
  >
</template>
