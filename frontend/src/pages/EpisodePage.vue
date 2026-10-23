<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { useRoute } from 'vue-router';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { api, type Dataset, type Episode, type Frames, type List } from '../lib/api';
import { mediaTime, frameAtTime } from '../lib/timeline';
import StateBox from '../components/StateBox.vue';
echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);
const route = useRoute(),
  id = String(route.params.id),
  index = Number(route.params.index),
  dataset = ref<Dataset>(),
  episode = ref<Episode>(),
  frames = ref<Frames>(),
  loading = ref(true),
  error = ref(''),
  playing = ref(false),
  cursor = ref(0),
  chunkLoading = ref(false),
  chunkError = ref(''),
  dim = ref(0),
  stateEl = ref<HTMLElement>(),
  actionEl = ref<HTMLElement>(),
  videoErrors = ref<Record<string, string>>({});
let videos: Record<string, HTMLVideoElement> = {},
  stateChart: echarts.ECharts | undefined,
  actionChart: echarts.ECharts | undefined,
  raf = 0,
  previous = 0,
  requestSerial = 0,
  disposed = false;
const duration = computed(
  () => Math.max(0, (episode.value?.length ?? 0) - 1) / (frames.value?.fps || 1),
);
const currentFrame = computed(() =>
  Math.min(
    Math.max(0, (episode.value?.length ?? 1) - 1),
    Math.round(cursor.value * (frames.value?.fps || 1)),
  ),
);
const maxDims = computed(() =>
  Math.max(1, ...Object.values(frames.value?.series ?? {}).map((v) => v[0]?.length ?? 0)),
);
function videoRef(el: unknown, key: string) {
  if (el) videos[key] = el as HTMLVideoElement;
  else delete videos[key];
}
function alignVideos(force = false) {
  for (const camera of frames.value?.cameras ?? []) {
    const video = videos[camera.key];
    if (!video || video.readyState === 0) continue;
    const target = mediaTime(cursor.value, camera.start_time, camera.end_time, camera.fps);
    if (force || Math.abs(video.currentTime - target) > 0.12)
      try {
        video.currentTime = target;
      } catch {}
    if (playing.value) {
      void video.play().catch(() => {
        videoErrors.value[camera.key] = '浏览器暂时无法播放，可暂停后再次播放或检查视频编码。';
      });
    } else video.pause();
  }
}
function syncCharts() {
  for (const chart of [stateChart, actionChart]) {
    if (!chart) continue;
    chart.setOption({
      series: [
        {
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#caa768', type: 'solid', width: 1.3 },
            label: { show: false },
            data: [{ xAxis: cursor.value }],
          },
        },
      ],
    });
  }
}
function setTime(value: number) {
  cursor.value = Math.max(0, Math.min(duration.value, value));
  alignVideos(true);
  syncCharts();
  void ensureChunk();
}
function chartOption(key: string, title: string, color: string) {
  const values = frames.value?.series[key] ?? [];
  const times = frames.value?.timestamps ?? [];
  return {
    animation: false,
    grid: { left: 48, right: 20, top: 25, bottom: 38 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(5) : '缺失'),
    },
    xAxis: {
      type: 'value',
      name: '秒',
      nameGap: 5,
      min: times[0] ?? 0,
      max: times.at(-1) ?? duration.value,
      axisLine: { lineStyle: { color: '#dfe7d6' } },
      axisLabel: { color: '#8a9a7c', fontSize: 10 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#8a9a7c', fontSize: 10 },
      splitLine: { lineStyle: { color: '#eff3e9' } },
      name: title,
      nameTextStyle: { color: '#859875', fontSize: 10 },
    },
    series: [
      {
        name: `${key}[${dim.value}]`,
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.6, color },
        data: times.map((t, i) => [t, values[i]?.[dim.value] ?? null]),
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#caa768', type: 'solid' },
          label: { show: false },
          data: [{ xAxis: cursor.value }],
        },
      },
    ],
  };
}
function draw() {
  if (!stateEl.value || !actionEl.value) return;
  stateChart ??= echarts.init(stateEl.value);
  actionChart ??= echarts.init(actionEl.value);
  stateChart.setOption(chartOption('observation.state', '状态', '#5a8b61'), true);
  actionChart.setOption(chartOption('action', '动作', '#94a357'), true);
  for (const chart of [stateChart, actionChart]) {
    chart.off('click');
    chart.getZr().off('click');
    chart.getZr().on('click', (e) => {
      const point = chart.convertFromPixel({ gridIndex: 0 }, [e.offsetX, e.offsetY]);
      if (Array.isArray(point) && typeof point[0] === 'number') setTime(point[0]);
    });
  }
}
async function fetchChunk(offset: number) {
  const serial = ++requestSerial;
  chunkLoading.value = true;
  chunkError.value = '';
  try {
    const f = await api<Frames>(
      `/datasets/${id}/episodes/${index}/frames?offset=${Math.max(0, offset)}&limit=1000`,
    );
    if (serial !== requestSerial || disposed) return;
    frames.value = f;
    await nextTick();
    draw();
    alignVideos(true);
  } catch (e) {
    chunkError.value = (e as Error).message;
  } finally {
    if (serial === requestSerial) chunkLoading.value = false;
  }
}
async function ensureChunk() {
  if (!frames.value || chunkLoading.value) return;
  const f = currentFrame.value;
  if (f < frames.value.offset || f >= frames.value.offset + frames.value.timestamps.length)
    await fetchChunk(Math.floor(f / 1000) * 1000);
}
function tick(time: number) {
  if (!playing.value) return;
  if (previous) {
    cursor.value = Math.min(duration.value, cursor.value + (time - previous) / 1000);
    alignVideos();
    syncCharts();
    void ensureChunk();
  }
  previous = time;
  if (cursor.value >= duration.value) {
    pause();
    setTime(duration.value);
  } else raf = requestAnimationFrame(tick);
}
function pause() {
  playing.value = false;
  previous = 0;
  cancelAnimationFrame(raf);
  for (const v of Object.values(videos)) v.pause();
}
function toggle() {
  if (playing.value) pause();
  else {
    if (cursor.value >= duration.value) setTime(0);
    playing.value = true;
    previous = 0;
    alignVideos(true);
    raf = requestAnimationFrame(tick);
  }
}
function step(by: number) {
  pause();
  const f = frames.value;
  if (!f) return;
  const local = frameAtTime(f.timestamps, cursor.value);
  const next = f.timestamps[local + by];
  setTime(next ?? cursor.value + by / f.fps);
}
async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [d, e] = await Promise.all([
      api<Dataset>(`/datasets/${id}`),
      api<List<Episode>>(`/datasets/${id}/episodes?q=${index}&limit=100`),
    ]);
    dataset.value = d;
    episode.value = e.items.find((v) => v.episode_index === index);
    if (!episode.value) throw new Error('未找到指定轨迹');
    await fetchChunk(0);
    if (!frames.value) throw new Error(chunkError.value || '轨迹数据读取失败');
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
    await nextTick();
    draw();
  }
}
function resize() {
  stateChart?.resize();
  actionChart?.resize();
}
onMounted(() => {
  load();
  window.addEventListener('resize', resize);
});
onUnmounted(() => {
  disposed = true;
  pause();
  stateChart?.dispose();
  actionChart?.dispose();
  window.removeEventListener('resize', resize);
});
</script>
<template>
  <RouterLink :to="`/datasets/${id}`" class="back"
    >← 返回 {{ dataset?.name || '数据集详情' }}</RouterLink
  ><StateBox :loading="loading" :error="error" @retry="load"
    ><template v-if="frames && episode"
      ><div class="page-heading">
        <div>
          <div class="eyebrow">EPISODE PLAYBACK</div>
          <div class="detail-title">
            <h1>Episode #{{ String(index).padStart(4, '0') }}</h1>
            <span class="pill">{{ frames.total }} 帧 · {{ frames.fps }} FPS</span>
          </div>
          <p>{{ episode.task || '未标注任务' }} · {{ dataset?.name }}</p>
        </div>
        <span class="pill">{{ frames.cameras.length }} 个相机视角</span>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>轨迹回放</h2>
          <span class="subtle">共用 episode 时间游标</span>
        </div>
        <div class="panel-body">
          <div class="episode-grid">
            <div v-for="camera in frames.cameras" :key="camera.key" class="video-card">
              <div class="video-label">
                <span class="mono">{{ camera.key }}</span
                ><span>{{ camera.fps }} FPS</span>
              </div>
              <video
                :ref="(el) => videoRef(el, camera.key)"
                :src="camera.url"
                muted
                playsinline
                preload="metadata"
                @loadedmetadata="alignVideos(true)"
                @error="videoErrors[camera.key] = '视频无法读取。请检查文件、编码和数据质量报告。'"
              />
              <div v-if="videoErrors[camera.key]" class="video-error">
                {{ videoErrors[camera.key] }}
              </div>
            </div>
          </div>
          <div v-if="!frames.cameras.length" class="info-banner warning">
            本条轨迹没有可用的相机视频，仍可查看状态和动作曲线。
          </div>
          <div class="playback-controls">
            <button class="btn small" :disabled="cursor <= 0" @click="step(-1)">← 帧</button
            ><button class="btn primary small" @click="toggle">
              {{ playing ? 'Ⅱ 暂停' : '▷ 播放' }}</button
            ><button class="btn small" :disabled="cursor >= duration" @click="step(1)">帧 →</button
            ><input
              aria-label="轨迹时间游标"
              type="range"
              min="0"
              :max="duration"
              :step="1 / frames.fps"
              :value="cursor"
              @input="
                pause();
                setTime(Number(($event.target as HTMLInputElement).value));
              "
            /><span class="time-display">{{ cursor.toFixed(3) }} / {{ duration.toFixed(3) }} s</span
            ><span class="pill">帧 {{ currentFrame }}</span>
          </div>
          <div class="subtle">
            视频按各相机分片起点定位，浏览器解码存在帧级定位误差。精确帧号以数据索引为准。
          </div>
        </div>
      </div>
      <div v-if="chunkError" class="inline-error">
        {{ chunkError }}
        <button class="btn small" @click="fetchChunk(Math.floor(currentFrame / 1000) * 1000)">
          重试片段
        </button>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>状态与动作</h2>
          <div class="chart-controls">
            <span>{{
              chunkLoading
                ? '读取片段中…'
                : `当前片段 ${frames.offset}–${frames.offset + frames.timestamps.length - 1}`
            }}</span
            ><label
              >维度
              <select v-model.number="dim" aria-label="曲线维度" @change="draw">
                <option v-for="n in maxDims" :key="n" :value="n - 1">{{ n - 1 }}</option>
              </select></label
            >
          </div>
        </div>
        <div class="grid-2">
          <div ref="stateEl" class="chart"></div>
          <div ref="actionEl" class="chart"></div>
        </div>
        <div class="table-bottom">
          <span>点击曲线区域或拖动时间条定位。缺失和非有限值显示为断点。</span>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header"><h2>相机片段边界</h2></div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>相机</th>
                <th>分片起点</th>
                <th>分片终点</th>
                <th>当前视频时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in frames.cameras" :key="c.key">
                <td class="mono">{{ c.key }}</td>
                <td>{{ c.start_time.toFixed(3) }} s</td>
                <td>{{ c.end_time.toFixed(3) }} s</td>
                <td>{{ mediaTime(cursor, c.start_time, c.end_time, c.fps).toFixed(3) }} s</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div></template
    ></StateBox
  >
</template>
