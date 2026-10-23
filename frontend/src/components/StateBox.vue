<script setup lang="ts">
defineProps<{ loading?: boolean; error?: string; empty?: boolean; title?: string }>();
defineEmits<{ retry: [] }>();
</script>
<template>
  <div v-if="loading" class="statebox">
    <span class="spinner"></span>
    <h3>正在读取工作台数据</h3>
    <p>请稍候</p>
  </div>
  <div v-else-if="error" class="statebox error-state">
    <span class="state-symbol">!</span>
    <h3>暂时无法读取</h3>
    <p>{{ error }}</p>
    <button class="btn" @click="$emit('retry')">重新加载</button>
  </div>
  <div v-else-if="empty" class="statebox">
    <span class="state-symbol">＋</span>
    <h3>{{ title || '这里还没有记录' }}</h3>
    <p><slot name="hint">创建第一条记录，开始你的实验。</slot></p>
  </div>
  <slot v-else />
</template>
