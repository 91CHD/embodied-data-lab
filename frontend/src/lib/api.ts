export type List<T> = { items: T[]; total: number };
export type Dataset = {
  id: string;
  name: string;
  source_kind: string;
  source_uri?: string;
  revision?: string;
  fingerprint?: string;
  format_version?: string;
  status: string;
  summary: Record<string, unknown>;
  features: Record<string, unknown>;
  total_episodes: number;
  created_at: string;
};
export type Episode = {
  id: string;
  episode_index: number;
  task: string;
  length: number;
  duration: number;
  cameras: string[];
  metadata: Record<string, unknown>;
};
export type Artifact = {
  id: string;
  kind: string;
  filename: string;
  sha256: string;
  size: number;
  source_kind: string;
  url: string;
};
export type Job = {
  id: string;
  type: string;
  dataset_id?: string;
  experiment_id?: string;
  params: Record<string, unknown>;
  status: string;
  attempt: number;
  max_attempts: number;
  worker_id?: string;
  heartbeat_at?: string;
  lease_expires_at?: string;
  progress: number;
  error?: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  artifacts: Artifact[];
};
export type JobEvent = {
  id: number;
  job_id: string;
  level: string;
  message: string;
  progress?: number;
  created_at: string;
};
export type Frames = {
  offset: number;
  total: number;
  fps: number;
  timestamps: number[];
  frame_indices: number[];
  series: Record<string, (number | null)[][]>;
  cameras: { key: string; url: string; start_time: number; end_time: number; fps: number }[];
};
export type Checkpoint = {
  id: string;
  name: string;
  source: string;
  revision?: string;
  checksum?: string;
};
export type Experiment = {
  id: string;
  name: string;
  purpose: string;
  dataset_id: string;
  checkpoint_id?: string;
  config: Record<string, unknown>;
  code_version: string;
  notes?: string;
  created_at: string;
  results?: Record<string, unknown>[];
  artifacts?: Artifact[];
  jobs?: Job[];
};
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const post = <T>(path: string, body: unknown = {}) =>
  api<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const newKey = () => crypto.randomUUID();
export const date = (v?: string) =>
  v
    ? new Date(v).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '—';
export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
export const short = (v?: string) => (v ? v.slice(0, 12) : '—');
export const sourceLabel = (kind: string) =>
  ({
    public: '公开数据',
    local: '本地数据',
    synthetic_demo: '合成演示',
    imported: '外部导入',
    executed: '平台执行',
  })[kind] ?? kind;
export const jobLabel = (kind: string) =>
  ({
    import: '数据导入',
    import_dataset: '数据导入',
    validate: '质量检查',
    split: '数据划分',
    statistics: '数据统计',
    train: '策略训练',
    evaluate: '策略评测',
  })[kind] ?? kind;
export const terminal = (status: string) => ['succeeded', 'failed', 'cancelled'].includes(status);
export const progressPercent = (v: number) =>
  Math.max(0, Math.min(100, Math.round(v <= 1 ? v * 100 : v)));
