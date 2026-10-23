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
  type Experiment,
  type Job,
  type Dataset,
  type Checkpoint,
} from '../lib/api';
import StateBox from '../components/StateBox.vue';
import StatusBadge from '../components/StatusBadge.vue';
import ArtifactList from '../components/ArtifactList.vue';
type Detail = Experiment & { dataset: Dataset; checkpoint?: Checkpoint; results: any[] };
const route = useRoute(),
  router = useRouter(),
  id = String(route.params.id),
  experiment = ref<Detail>(),
  loading = ref(true),
  error = ref(''),
  modal = ref(false),
  formError = ref(''),
  saving = ref(false),
  bundle = ref(''),
  selectedFile = ref(''),
  capabilities = ref<any>(),
  jobModal = ref(false),
  jobType = ref('train'),
  jobParams = ref('{}');
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [e, c] = await Promise.all([api<Detail>(`/experiments/${id}`), api('/capabilities')]);
    experiment.value = e;
    capabilities.value = c;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
async function readFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  formError.value = '';
  if (file.size > 2 * 1024 * 1024) {
    formError.value = '结果文件不能超过 2 MB';
    input.value = '';
    return;
  }
  selectedFile.value = file.name;
  bundle.value = await file.text();
}
async function importResult() {
  saving.value = true;
  formError.value = '';
  try {
    const parsed = JSON.parse(bundle.value);
    if (!['imported', 'synthetic_demo'].includes(parsed.source_kind))
      throw new Error('source_kind 必须为 imported 或 synthetic_demo');
    await post(`/experiments/${id}/results`, parsed);
    modal.value = false;
    ElMessage.success('结果已归档');
    await load();
  } catch (e) {
    formError.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
async function createJob() {
  saving.value = true;
  formError.value = '';
  try {
    const params = JSON.parse(jobParams.value);
    const j = await post<Job>('/jobs', {
      type: jobType.value,
      dataset_id: experiment.value!.dataset_id,
      experiment_id: id,
      params,
      idempotency_key: newKey(),
    });
    jobModal.value = false;
    router.push(`/jobs/${j.id}`);
  } catch (e) {
    formError.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
function openRunner(type: string) {
  jobType.value = type;
  formError.value = '';
  jobModal.value = true;
}
const exampleSchema = {
  source_kind: 'imported',
  task_set: ['任务名称'],
  seed: 42,
  metric_definition: '环境终止时 success == true',
  trials: [{ task: '任务名称', success: true, steps: 120, latency_ms: 45.2 }],
  notes: '记录来源与实际运行环境；此处仅展示文件结构',
};
onMounted(load);
</script>
<template>
  <RouterLink to="/experiments" class="back">← 返回实验管理</RouterLink
  ><StateBox :loading="loading" :error="error" @retry="load"
    ><template v-if="experiment"
      ><div class="page-heading">
        <div>
          <div class="eyebrow">EXPERIMENT DETAIL</div>
          <h1>{{ experiment.name }}</h1>
          <p>{{ experiment.purpose }} · 创建于 {{ date(experiment.created_at) }}</p>
        </div>
        <div class="actions">
          <button class="btn" @click="openRunner('train')">创建训练任务</button
          ><button class="btn" @click="openRunner('evaluate')">创建评测任务</button
          ><button
            class="btn primary"
            @click="
              formError = '';
              modal = true;
            "
          >
            ＋ 导入结果文件
          </button>
        </div>
      </div>
      <div class="panel">
        <div class="panel-body meta-grid">
          <div class="meta-item">
            <span>数据集版本</span
            ><strong
              ><RouterLink :to="`/datasets/${experiment.dataset_id}`"
                >{{ experiment.dataset.name }} ↗</RouterLink
              ></strong
            >
          </div>
          <div class="meta-item">
            <span>模型检查点</span><strong>{{ experiment.checkpoint?.name || '未关联' }}</strong>
          </div>
          <div class="meta-item">
            <span>代码 / 工具版本</span><strong class="mono">{{ experiment.code_version }}</strong>
          </div>
          <div class="meta-item">
            <span>数据指纹</span><strong class="mono">{{ experiment.dataset.fingerprint }}</strong>
          </div>
          <div class="meta-item">
            <span>检查点 REVISION</span
            ><strong class="mono">{{ experiment.checkpoint?.revision || '未登记' }}</strong>
          </div>
          <div class="meta-item">
            <span>实验备注</span><strong>{{ experiment.notes || '—' }}</strong>
          </div>
        </div>
      </div>
      <div
        v-if="!capabilities?.train?.available || !capabilities?.evaluate?.available"
        class="info-banner warning"
      >
        <span>ⓘ</span>
        <div>
          <strong>运行环境：</strong>训练
          {{
            capabilities?.train?.available ? '已配置' : capabilities?.train?.reason || '未配置'
          }}；评测
          {{
            capabilities?.evaluate?.available
              ? '已配置'
              : capabilities?.evaluate?.reason || '未配置'
          }}。可先导入已有结果。
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>
            评测结果 <span class="count">{{ experiment.results?.length || 0 }}</span>
          </h2>
          <span class="subtle">成功率由逐条 trial 计数得到</span>
        </div>
        <StateBox :empty="!experiment.results?.length" title="还没有评测结果"
          ><template #hint>导入真实评测记录或明确标识的合成示例，保留原始结果。</template>
          <div class="panel-body results-grid">
            <div v-for="result in experiment.results" :key="result.id" class="result-card">
              <div class="actions" style="justify-content: space-between">
                <span class="source" :class="result.source_kind">{{
                  sourceLabel(result.source_kind)
                }}</span
                ><span class="subtle">SEED {{ result.seed }}</span>
              </div>
              <div class="metric">
                {{ (result.success_rate * 100).toFixed(1) }}%<small>成功率</small>
              </div>
              <p>
                {{ result.successes }} / {{ result.count }} 条成功 · 平均
                {{ result.mean_steps.toFixed(1) }} 步
              </p>
              <p>平均推理延迟 {{ result.mean_latency_ms.toFixed(1) }} ms</p>
              <div class="card-divider"></div>
              <p><strong>任务：</strong>{{ result.task_set?.join(' / ') }}</p>
              <p><strong>指标口径：</strong>{{ result.metric_definition }}</p>
              <p style="margin-top: 10px">{{ date(result.created_at) }}</p>
            </div>
          </div></StateBox
        >
      </div>
      <div class="two-col">
        <div>
          <div class="panel">
            <div class="panel-header">
              <h2>
                关联任务 <span class="count">{{ experiment.jobs?.length || 0 }}</span>
              </h2>
            </div>
            <div v-if="!experiment.jobs?.length" class="pad muted">暂未创建关联任务。</div>
            <div v-else class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>任务</th>
                    <th>状态</th>
                    <th>创建时间</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="j in experiment.jobs" :key="j.id">
                    <td>{{ jobLabel(j.type) }}</td>
                    <td><StatusBadge :status="j.status" /></td>
                    <td>{{ date(j.created_at) }}</td>
                    <td>
                      <RouterLink class="btn ghost small" :to="`/jobs/${j.id}`">查看 ↗</RouterLink>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="panel">
            <div class="panel-header"><h2>实验产物</h2></div>
            <ArtifactList :items="experiment.artifacts || []" />
          </div>
        </div>
        <div class="panel">
          <div class="panel-header"><h2>配置快照</h2></div>
          <div class="panel-body">
            <pre class="code">{{ pretty(experiment.config) }}</pre>
          </div>
        </div>
      </div></template
    ></StateBox
  ><ElDialog v-model="modal" title="导入评测结果" width="660px"
    ><div v-if="formError" class="inline-error">{{ formError }}</div>
    <div class="form-grid">
      <label class="field"
        ><span>选择 JSON 文件（最大 2 MB）</span
        ><input type="file" accept="application/json,.json" @change="readFile" /><small
          v-if="selectedFile"
          >{{ selectedFile }}</small
        ></label
      ><label class="field"
        ><span>结果内容</span
        ><textarea
          v-model="bundle"
          style="min-height: 180px"
          placeholder="选择文件后在此核对内容，也可直接粘贴 JSON"
          spellcheck="false"
        />
      </label>
      <details>
        <summary class="subtle" style="cursor: pointer">查看文件结构示例（仅说明 schema）</summary>
        <pre class="code" style="margin-top: 10px; max-height: 230px">{{
          pretty(exampleSchema)
        }}</pre>
      </details>
      <small class="subtle" style="line-height: 1.7"
        >外部真实记录使用 imported，合成示例使用
        synthetic_demo。导入不代表平台执行过训练或仿真。</small
      >
    </div>
    <div class="dialog-footer">
      <button class="btn" @click="modal = false">取消</button
      ><button class="btn primary" :disabled="saving || !bundle.trim()" @click="importResult">
        {{ saving ? '导入中…' : '校验并归档' }}
      </button>
    </div></ElDialog
  ><ElDialog
    v-model="jobModal"
    :title="jobType === 'train' ? '创建训练任务' : '创建评测任务'"
    width="570px"
    ><div v-if="formError" class="inline-error">{{ formError }}</div>
    <div v-if="!capabilities?.[jobType]?.available" class="info-banner warning">
      {{ capabilities?.[jobType]?.reason || '运行环境未配置' }}。请先配置受控 runner，当前无法提交。
    </div>
    <div class="form-grid">
      <label class="field"
        ><span>任务参数 JSON</span><textarea v-model="jobParams" spellcheck="false" /></label
      ><label class="field"
        ><span>受控命令协议</span>
        <pre class="code">{{ pretty(capabilities?.[jobType]?.command_template || '未配置') }}</pre>
      </label>
    </div>
    <div class="dialog-footer">
      <button class="btn" @click="jobModal = false">关闭</button
      ><button
        class="btn primary"
        :disabled="saving || !capabilities?.[jobType]?.available"
        @click="createJob"
      >
        提交任务
      </button>
    </div></ElDialog
  >
</template>
