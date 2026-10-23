<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElDialog, ElMessage } from 'element-plus';
import {
  api,
  post,
  date,
  pretty,
  type List,
  type Dataset,
  type Experiment,
  type Checkpoint,
} from '../lib/api';
import StateBox from '../components/StateBox.vue';
const router = useRouter(),
  experiments = ref<Experiment[]>([]),
  datasets = ref<Dataset[]>([]),
  checkpoints = ref<Checkpoint[]>([]),
  loading = ref(true),
  error = ref(''),
  modal = ref(false),
  checkpointModal = ref(false),
  formError = ref(''),
  saving = ref(false),
  tab = ref('experiments'),
  selected = ref<string[]>([]),
  comparison = ref<{ items: any[]; comparable: boolean; reasons: string[] }>(),
  comparing = ref(false);
const form = ref({
    name: '',
    purpose: '',
    dataset_id: '',
    checkpoint_id: '',
    config: '{}',
    code_version: '',
    notes: '',
  }),
  checkpointForm = ref({ name: '', source: '', revision: '', checksum: '' });
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [e, d, c] = await Promise.all([
      api<List<Experiment>>('/experiments'),
      api<List<Dataset>>('/datasets?limit=100'),
      api<List<Checkpoint>>('/checkpoints'),
    ]);
    experiments.value = e.items;
    datasets.value = d.items;
    checkpoints.value = c.items;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
async function create() {
  saving.value = true;
  formError.value = '';
  try {
    const config = JSON.parse(form.value.config);
    if (!config || Array.isArray(config) || typeof config !== 'object')
      throw new Error('配置必须为 JSON 对象');
    const e = await post<Experiment>('/experiments', {
      ...form.value,
      checkpoint_id: form.value.checkpoint_id || null,
      config,
    });
    modal.value = false;
    ElMessage.success('实验已创建');
    router.push(`/experiments/${e.id}`);
  } catch (e) {
    formError.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
async function checkpoint() {
  saving.value = true;
  formError.value = '';
  try {
    await post('/checkpoints', {
      ...checkpointForm.value,
      revision: checkpointForm.value.revision || null,
      checksum: checkpointForm.value.checksum || null,
    });
    checkpointModal.value = false;
    ElMessage.success('检查点已登记');
    await load();
  } catch (e) {
    formError.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
async function compare() {
  comparing.value = true;
  try {
    comparison.value = await api(`/experiments/compare?ids=${selected.value.join(',')}`);
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    comparing.value = false;
  }
}
function datasetName(id: string) {
  return datasets.value.find((d) => d.id === id)?.name || id.slice(0, 12);
}
function checkpointName(id?: string) {
  return checkpoints.value.find((c) => c.id === id)?.name || '未关联';
}
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">EXPERIMENT REGISTRY</div>
      <h1>实验管理</h1>
      <p>保存配置和结果来源，让数据、检查点与评测记录相互关联。</p>
    </div>
    <div class="actions">
      <button
        class="btn"
        @click="
          formError = '';
          checkpointModal = true;
        "
      >
        登记检查点</button
      ><button
        class="btn primary"
        @click="
          formError = '';
          modal = true;
        "
      >
        ＋ 创建实验
      </button>
    </div>
  </div>
  <div class="stats">
    <div class="stat-card">
      <div class="stat-label">实验记录<span>◇</span></div>
      <div class="stat-value">{{ experiments.length }}<small>个实验</small></div>
      <div class="stat-note">独立保存配置快照</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">策略检查点<span>◈</span></div>
      <div class="stat-value">{{ checkpoints.length }}<small>条登记</small></div>
      <div class="stat-note">记录来源、版本和校验</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">可用数据版本<span>▦</span></div>
      <div class="stat-value">{{ datasets.length }}<small>个版本</small></div>
      <div class="stat-note">当前已读取的数据版本</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">已选实验<span>↔</span></div>
      <div class="stat-value">{{ selected.length }}<small>个实验</small></div>
      <div class="stat-note">选择 2–6 个进行条件对比</div>
    </div>
  </div>
  <div class="panel">
    <div class="tabs">
      <button :class="{ active: tab === 'experiments' }" @click="tab = 'experiments'">
        实验记录 <span class="count">{{ experiments.length }}</span></button
      ><button :class="{ active: tab === 'checkpoints' }" @click="tab = 'checkpoints'">
        模型检查点 <span class="count">{{ checkpoints.length }}</span>
      </button>
    </div>
    <StateBox
      :loading="loading"
      :error="error"
      :empty="tab === 'experiments' ? !experiments.length : !checkpoints.length"
      :title="tab === 'experiments' ? '还没有实验记录' : '还没有模型检查点'"
      @retry="load"
      ><template v-if="tab === 'experiments'"
        ><div class="toolbar">
          <span class="subtle">选择实验后，核对数据版本、任务和指标口径。</span
          ><button
            class="btn small"
            :disabled="selected.length < 2 || selected.length > 6 || comparing"
            @click="compare"
          >
            {{ comparing ? '读取中…' : '对比所选实验 ↔' }}
          </button>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th></th>
                <th>实验名称 / 用途</th>
                <th>数据集版本</th>
                <th>模型检查点</th>
                <th>创建时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="e in experiments" :key="e.id">
                <td>
                  <input
                    type="checkbox"
                    class="checkbox"
                    v-model="selected"
                    :value="e.id"
                    :aria-label="`选择 ${e.name}`"
                  />
                </td>
                <td>
                  <RouterLink :to="`/experiments/${e.id}`" class="data-name"
                    ><span class="folder-icon">◇</span>
                    <div>
                      {{ e.name }}<small>{{ e.purpose }}</small>
                    </div></RouterLink
                  >
                </td>
                <td>{{ datasetName(e.dataset_id) }}</td>
                <td>{{ checkpointName(e.checkpoint_id) }}</td>
                <td class="subtle">{{ date(e.created_at) }}</td>
                <td>
                  <RouterLink class="btn ghost small" :to="`/experiments/${e.id}`"
                    >查看 ↗</RouterLink
                  >
                </td>
              </tr>
            </tbody>
          </table>
        </div></template
      >
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>检查点名称</th>
              <th>来源</th>
              <th>REVISION</th>
              <th>SHA256</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in checkpoints" :key="c.id">
              <td>
                <strong>{{ c.name }}</strong>
              </td>
              <td class="mono">{{ c.source }}</td>
              <td class="mono">{{ c.revision || '—' }}</td>
              <td class="mono" :title="c.checksum">{{ c.checksum?.slice(0, 16) || '未登记' }}</td>
            </tr>
          </tbody>
        </table>
      </div></StateBox
    >
  </div>
  <div v-if="comparison" class="panel" data-testid="comparison">
    <div class="panel-header">
      <h2>实验对比</h2>
      <span class="source">{{
        comparison.comparable ? '条件一致，可直接比较' : '条件不同，谨慎解释'
      }}</span>
    </div>
    <div v-if="!comparison.comparable" class="panel-body">
      <div class="info-banner warning" style="margin: 0">{{ comparison.reasons.join('；') }}</div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>实验</th>
            <th>数据指纹</th>
            <th>模型 / REVISION</th>
            <th>来源 / SEED</th>
            <th>任务与指标</th>
            <th>成功 / 样本数</th>
            <th>成功率</th>
            <th>平均步数 / 延迟</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in comparison.items" :key="item.experiment.id">
            <td>{{ item.experiment.name }}</td>
            <td class="mono">{{ item.dataset?.fingerprint?.slice(0, 12) }}</td>
            <td>
              {{ item.checkpoint?.name || '未关联'
              }}<small class="mono muted" style="display: block">{{
                item.checkpoint?.revision || '—'
              }}</small>
            </td>
            <td>{{ item.result?.source_kind || '无结果' }} / {{ item.result?.seed ?? '—' }}</td>
            <td style="max-width: 300px; white-space: normal">
              {{ item.result?.task_set?.join(', ')
              }}<small class="muted" style="display: block; margin-top: 5px">{{
                item.result?.metric_definition
              }}</small>
            </td>
            <td>{{ item.result ? `${item.result.successes} / ${item.result.count}` : '—' }}</td>
            <td>{{ item.result ? `${(item.result.success_rate * 100).toFixed(1)}%` : '—' }}</td>
            <td>
              {{
                item.result
                  ? `${item.result.mean_steps.toFixed(1)} / ${item.result.mean_latency_ms.toFixed(1)} ms`
                  : '—'
              }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  <ElDialog v-model="modal" title="创建实验" width="600px"
    ><div v-if="formError" class="inline-error">{{ formError }}</div>
    <div class="form-grid">
      <div class="grid-2">
        <label class="field"
          ><span>实验名称</span><input v-model="form.name" placeholder="实验的可识别名称" /></label
        ><label class="field"
          ><span>用途</span><input v-model="form.purpose" placeholder="例如：动作块长度对比"
        /></label>
      </div>
      <label class="field"
        ><span>数据集版本</span
        ><select v-model="form.dataset_id">
          <option value="">选择数据集</option>
          <option v-for="d in datasets" :key="d.id" :value="d.id">
            {{ d.name }} · {{ d.fingerprint?.slice(0, 8) }}
          </option>
        </select></label
      >
      <div class="grid-2">
        <label class="field"
          ><span>关联检查点</span
          ><select v-model="form.checkpoint_id">
            <option value="">暂不关联</option>
            <option v-for="c in checkpoints" :key="c.id" :value="c.id">{{ c.name }}</option>
          </select></label
        ><label class="field"
          ><span>代码 / 工具版本</span
          ><input v-model="form.code_version" placeholder="Git commit 或明确版本"
        /></label>
      </div>
      <label class="field"
        ><span>参数快照 JSON</span><textarea v-model="form.config" spellcheck="false" /></label
      ><label class="field"
        ><span>备注</span><input v-model="form.notes" placeholder="可选，补充实验条件"
      /></label>
    </div>
    <div class="dialog-footer">
      <button class="btn" @click="modal = false">取消</button
      ><button
        class="btn primary"
        :disabled="saving || !form.name || !form.purpose || !form.dataset_id || !form.code_version"
        @click="create"
      >
        {{ saving ? '保存中…' : '创建实验' }}
      </button>
    </div></ElDialog
  ><ElDialog v-model="checkpointModal" title="登记策略检查点" width="520px"
    ><div v-if="formError" class="inline-error">{{ formError }}</div>
    <div class="form-grid">
      <label class="field"><span>检查点名称</span><input v-model="checkpointForm.name" /></label
      ><label class="field"
        ><span>来源</span
        ><input v-model="checkpointForm.source" placeholder="模型 repo_id 或本地检查点路径" /><small
          >仅登记位置，不会自动下载模型权重。</small
        ></label
      ><label class="field"
        ><span>Revision</span
        ><input v-model="checkpointForm.revision" placeholder="提交或版本，可选" /></label
      ><label class="field"
        ><span>SHA256</span
        ><input v-model="checkpointForm.checksum" placeholder="64 位十六进制校验值，可选"
      /></label>
    </div>
    <div class="dialog-footer">
      <button class="btn" @click="checkpointModal = false">取消</button
      ><button
        class="btn primary"
        :disabled="saving || !checkpointForm.name || !checkpointForm.source"
        @click="checkpoint"
      >
        登记检查点
      </button>
    </div></ElDialog
  >
</template>
