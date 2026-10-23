<script setup lang="ts">
import { computed } from 'vue';
import { pretty } from '../lib/api';
const props = defineProps<{ report: unknown }>();
const data = computed(() => props.report as Record<string, any>);
const quality = computed(() => data.value.report_type === 'quality');
const issues = computed(() => (data.value.issues || []).slice(0, 50));
</script>
<template>
  <template v-if="quality">
    <div class="report-summary">
      <div>
        <span>检查轨迹</span><strong>{{ data.summary.episodes_checked }}</strong>
      </div>
      <div>
        <span>检查帧数</span><strong>{{ data.summary.frames_checked }}</strong>
      </div>
      <div>
        <span>错误</span
        ><strong :class="{ 'danger-text': data.summary.errors }">{{ data.summary.errors }}</strong>
      </div>
      <div>
        <span>警告</span><strong>{{ data.summary.warnings }}</strong>
      </div>
    </div>
    <div class="info-banner" :class="{ warning: !data.summary.valid }">
      {{
        data.summary.valid
          ? '检查通过，未发现规则定义的错误。'
          : '发现数据异常，请根据下方定位和原始报告核查。'
      }}
      <span v-if="data.data_source_kind === 'synthetic_demo'">本次检查的数据为合成演示。</span>
    </div>
    <div v-if="issues.length" class="table-wrap" style="margin-bottom: 18px">
      <table>
        <thead>
          <tr>
            <th>级别 / 规则</th>
            <th>Episode / 帧</th>
            <th>字段与原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(issue, i) in issues" :key="i">
            <td>
              <span class="source">{{ issue.severity }}</span
              ><small class="mono muted" style="display: block; margin-top: 5px">{{
                issue.rule
              }}</small>
            </td>
            <td class="mono">{{ issue.episode_index ?? '—' }} / {{ issue.frame_index ?? '—' }}</td>
            <td style="white-space: normal">
              <strong class="mono">{{ issue.field }}</strong>
              <p style="font-size: 11px">{{ issue.message }}</p>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="subtle" style="margin-top: 8px">
        页面展示前 50 条异常；完整计数、记录及截断说明见下载报告。
      </div>
    </div>
    <details>
      <summary class="subtle" style="cursor: pointer">查看完整 JSON 报告与视频边界证据</summary>
      <pre class="code" style="margin-top: 12px">{{ pretty(report) }}</pre>
    </details>
  </template>
  <pre v-else class="code">{{ pretty(report) }}</pre>
</template>
