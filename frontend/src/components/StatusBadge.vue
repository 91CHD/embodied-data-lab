<script setup lang="ts">
defineProps<{ status: string }>();
const labels: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
  cancel_requested: '正在取消',
  cancelled: '已取消',
  ready: '可用',
  valid: '可用',
  indexed: '已索引',
  checked: '已检查',
  importing: '导入中',
};
</script>
<template>
  <span class="badge" :class="status"><i></i>{{ labels[status] || status }}</span>
</template>
