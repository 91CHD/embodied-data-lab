<script setup lang="ts">
import { computed, onMounted, ref, onUnmounted } from 'vue';
import { useRoute } from 'vue-router';
import { api } from './lib/api';
const route = useRoute();
const healthy = ref<boolean | null>(null);
const checking = ref(false);
const section = computed(() =>
  route.path.startsWith('/jobs')
    ? '任务中心'
    : route.path.startsWith('/experiments')
      ? '实验管理'
      : '数据工作台',
);
async function health() {
  checking.value = true;
  try {
    await api('/health');
    healthy.value = true;
  } catch {
    healthy.value = false;
  } finally {
    checking.value = false;
  }
}
let timer: ReturnType<typeof setInterval>;
onMounted(() => {
  health();
  timer = setInterval(health, 30000);
});
onUnmounted(() => clearInterval(timer));
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/datasets"
        ><span class="brand-mark">e<span>·</span></span>
        <div>Embodied<span>DATA LAB</span></div></RouterLink
      >
      <div class="workspace-label">个人研发空间 <span>LOCAL</span></div>
      <nav>
        <RouterLink to="/datasets" :class="{ active: section === '数据工作台' }"
          ><span class="nav-icon">▦</span>数据工作台<span class="nav-arrow">↗</span></RouterLink
        ><RouterLink to="/jobs" :class="{ active: section === '任务中心' }"
          ><span class="nav-icon">☷</span>任务中心<span class="nav-arrow">↗</span></RouterLink
        ><RouterLink to="/experiments" :class="{ active: section === '实验管理' }"
          ><span class="nav-icon">◇</span>实验管理<span class="nav-arrow">↗</span></RouterLink
        >
      </nav>
      <div class="sidebar-bottom">
        <div class="local-chip"><i></i>本地单用户工作台</div>
        <p>让每一次实验<br />都有迹可循。</p>
        <div class="sidebar-user">
          <div class="avatar">连</div>
          <div>我的工作空间<small>Embodied research</small></div>
        </div>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <div>
          <span class="muted">工作空间</span><span class="breadcrumb-separator">/</span
          ><strong>{{ section }}</strong>
        </div>
        <button
          class="connection"
          @click="health"
          :title="healthy ? 'API 与数据库可访问' : '请确认 API 和 PostgreSQL 已启动'"
        >
          <i :class="{ online: healthy, offline: healthy === false }"></i
          >{{
            checking
              ? '连接检查中'
              : healthy
                ? '服务已连接'
                : healthy === false
                  ? '服务未连接'
                  : '连接中'
          }}<span>↻</span>
        </button>
      </header>
      <main><RouterView /></main>
      <footer>Embodied Data Lab<span>数据 · 轨迹 · 实验</span></footer>
    </div>
  </div>
</template>
