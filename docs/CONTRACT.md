# Implementation contract

Local single-user application, API prefix `/api`. All IDs are strings (UUID except episode_index integer). UTC ISO timestamps. Errors `{detail: string|object}`. Lists `{items: [], total: int}`. API owns PostgreSQL and immutable dataset snapshots below STORAGE_ROOT. No uploaded code is executed.

## HTTP
- GET `/health`: status + database
- GET `/sources`: available directories under STORAGE_ROOT/incoming, server approved paths only.
- GET `/datasets?q=&offset=0&limit=20`; POST `/datasets` `{name,path,source_kind:'local'|'public'|'synthetic_demo',source_uri?,revision?,idempotency_key}` returns Job. Path is relative to incoming.
- Dataset `{id,name,source_kind,source_uri,revision,fingerprint,format_version,status,summary,features,total_episodes,created_at}`.
- GET `/datasets/{id}`; GET `/datasets/{id}/episodes?q=&offset=&limit=`; episodes `{id,episode_index,task,length,duration,cameras,metadata}`.
- GET `/datasets/{id}/episodes/{index}/frames?offset=0&limit=500`: `{offset,total,fps,timestamps,frame_indices,series:{'observation.state':number[][],action:number[][]},cameras:[{key,url,start_time,end_time,fps}]}`. A camera URL is `/api/datasets/{id}/media?path=...` and streams original shared MP4, browser cursor = start_time + episode-local time. Null series values represent nonfinite values.
- GET `/jobs?dataset_id=&offset=&limit=`; POST `/jobs` `{type:'validate'|'split'|'statistics'|'train'|'evaluate',dataset_id,experiment_id?,params:{},idempotency_key}` returns Job.
- Job `{id,type,dataset_id,experiment_id,params,status,attempt,max_attempts,worker_id,heartbeat_at,lease_expires_at,progress,error,created_at,started_at,finished_at,artifacts:[]}`.
- GET `/jobs/{id}`; POST `/jobs/{id}/cancel`; POST `/jobs/{id}/retry`; GET `/jobs/{id}/events?after=0` returns `{items:events,total}`; GET `/jobs/{id}/stream?after=0` SSE id+event=log data JSON. Event `{id,job_id,level,message,progress,created_at}`. Terminal stream closes after last event. Frontend EventSource handles Last-Event-ID and deduplicates.
- Artifact `{id,kind,filename,sha256,size,source_kind,job_id,experiment_id,created_at,url}`; GET `/artifacts/{id}/download` serves controlled path. GET `/jobs/{id}/report` returns parsed JSON report if exists.
- GET `/capabilities`: `{cpu:true,train:{available:false,reason,command_template},evaluate:{available:false,reason,command_template}}`. Unconfigured train/evaluate returns 409.
- GET/POST `/checkpoints`: `{name,source,revision,checksum}`; GET/POST `/experiments`: `{name,purpose,dataset_id,checkpoint_id?,config:{},code_version,notes}`.
- GET `/experiments/{id}` returns experiment + results + artifacts + jobs.
- POST `/experiments/{id}/results` JSON evaluation bundle `{source_kind:'imported'|'synthetic_demo',task_set:string[],seed:int,metric_definition:string,trials:[{task:string,success:bool,steps:int,latency_ms:number}],notes?:string}`. Platform computes successes/count, never trusts supplied rate. Uploaded JSON max 2MB. Preserves raw bundle as artifact. No claimed `executed` from client.
- GET `/experiments/compare?ids=id1,id2` returns `{items:[{experiment,result}],comparable:bool,reasons:[]}`. Must declare mismatch in dataset fingerprint/tasks/seed/metric_definition.

## Adapter interface owned by data agent
`app/data/lerobot_v3.py`:
- `inspect_dataset(root: Path) -> dict`: `{info,features,format_version,total_episodes,episodes:[{episode_index,task,length,duration,cameras:[],metadata:{}}],fingerprint,summary}`. Raises ValueError for unsupported/missing metadata. Full fingerprint stable files.
- `read_episode(root: Path, episode_index:int, offset:int=0, limit:int=500) -> dict`: frames response above except camera `{key,path,start_time,end_time,fps}` uses root-relative POSIX path.
- `quality_report(root:Path, progress:Callable[[float,str],None], cancelled:Callable[[],bool]) -> dict` with issues `{severity,rule,episode_index,frame_index,field,message}` and summary; bounded report size while exact counts.
- `split_episodes(root:Path, ratio:float, seed:int) -> dict` deterministic train/validation lists and parameters; reject less than two episodes or invalid ratio.
- `statistics_report(root:Path, progress, cancelled) -> dict`.
- Exceptions from callbacks propagate for cancellation/lease.

Data agent owns `backend/app/data/**`, `scripts/generate_fixture.py`, `scripts/fetch_public_sample.py`, `tests/test_adapter.py`, `docs/DATA_FORMAT.md`, may add sample manifest to docs; root owns other backend. Frontend agent owns `frontend/**`. Infra agent owns Docker and scripts/dev/test and docs setup. All coordinate breaking contract edits.
