<script setup lang="ts">
import type { Artifact } from '../lib/api';
import { sourceLabel } from '../lib/api';
defineProps<{ items: Artifact[] }>();
</script>
<template>
  <div v-if="!items?.length" class="muted pad">暂无产物，任务完成后在此查看。</div>
  <a
    v-for="a in items"
    :key="a.id"
    :href="a.url || `/api/artifacts/${a.id}/download`"
    class="artifact-row"
    download
    ><div class="file-icon">↓</div>
    <div>
      <strong>{{ a.filename }}</strong
      ><small
        >{{ a.kind }} · {{ sourceLabel(a.source_kind) }} ·
        {{ (a.size / 1024).toFixed(1) }} KB</small
      >
    </div>
    <span class="mono muted" :title="a.sha256">{{ a.sha256?.slice(0, 12) }}</span
    ><span>下载 ↗</span></a
  >
</template>
