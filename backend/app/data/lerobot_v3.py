"""Read-only LeRobot v3.0 adapter, checked against upstream v0.4.3.

The official training reader imports torch/datasets/torchvision at import time.
This isolated CPU adapter uses the same metadata addressing, without importing
the training stack into the HTTP API. It does not implement training transforms.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Callable

import av
import pyarrow.dataset as ds
import pyarrow.parquet as pq

ADAPTER_VERSION = "eld-lerobot-v3-2"
UPSTREAM_RELEASE = "v0.4.3"
MAX_ISSUES = 1000
VIDEO_TIME_TOLERANCE_FRAMES = 0.5
REQUIRED_COLUMNS = ("index", "episode_index", "frame_index", "timestamp", "observation.state", "action", "task_index")


def _path(root: Path, relative: str) -> Path:
    """Never let format templates or symlinks address outside the dataset."""
    root = Path(root).resolve()
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"数据路径越界: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"数据路径越界: {relative}")
    if not path.is_file():
        raise ValueError(f"数据文件不存在: {relative}")
    return path


def _json(path: Path) -> dict:
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError(f"元信息超过32MB限制: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"无效JSON元信息: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"元信息应为对象: {path.name}")
    return value


def dataset_fingerprint(root: Path) -> str:
    """Content manifest hash: relative filename + size + each file SHA256."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    for file in sorted(p for p in root.rglob("*") if p.is_file() and ".cache" not in p.relative_to(root).parts):
        relative = file.relative_to(root).as_posix()
        _path(root, relative)
        content = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content.update(chunk)
        digest.update(f"{relative}\0{file.stat().st_size}\0{content.hexdigest()}\n".encode())
    return digest.hexdigest()


def _metadata(root: Path) -> tuple[dict, list[dict]]:
    root = Path(root).resolve()
    info = _json(_path(root, "meta/info.json"))
    if info.get("codebase_version") != "v3.0":
        raise ValueError(f"仅支持LeRobot v3.0，收到: {info.get('codebase_version')!r}")
    for name in ("features", "fps", "data_path", "total_episodes", "total_frames"):
        if name not in info:
            raise ValueError(f"meta/info.json 缺少字段: {name}")
    if not isinstance(info["features"], dict) or not info["features"]:
        raise ValueError("features必须为非空对象")
    try:
        fps = float(info["fps"])
    except (ValueError, TypeError) as exc:
        raise ValueError("fps必须为正数") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps必须为正数")
    for key in ("action", "observation.state"):
        feature = info["features"].get(key)
        if not feature or not isinstance(feature.get("shape"), list) or len(feature["shape"]) != 1 or feature["shape"][0] <= 0:
            raise ValueError(f"缺少或不支持的一维向量schema: {key}")
    for key, feature in info["features"].items():
        if feature.get("dtype") == "image":
            raise ValueError(f"首版仅支持MP4 video相机，不支持嵌入image: {key}")
    episode_files = sorted((root / "meta/episodes").glob("chunk-*/file-*.parquet"))
    if not episode_files:
        raise ValueError("缺少meta/episodes分片元信息")
    episodes: list[dict] = []
    for file in episode_files:
        _path(root, file.relative_to(root).as_posix())
        try:
            episodes.extend(pq.read_table(file).to_pylist())
        except Exception as exc:
            raise ValueError(f"无法解析episode元信息: {file.relative_to(root)}: {exc}") from exc
    if len(episodes) != int(info["total_episodes"]):
        raise ValueError(f"episode元信息数量{len(episodes)}与info.total_episodes={info['total_episodes']}不符；不接受缺分片的隐式子集")
    seen = set()
    for ep in episodes:
        required = ("episode_index", "length", "dataset_from_index", "dataset_to_index", "data/chunk_index", "data/file_index", "tasks")
        for name in required:
            if name not in ep or ep[name] is None:
                raise ValueError(f"episode元信息缺少字段: {name}")
        idx = int(ep["episode_index"])
        if idx in seen:
            raise ValueError(f"重复episode_index: {idx}")
        seen.add(idx)
        if int(ep["length"]) < 1 or int(ep["dataset_from_index"]) < 0 or int(ep["dataset_to_index"]) <= int(ep["dataset_from_index"]):
            raise ValueError(f"episode {idx} 的length/index范围无效")
        # Metadata offsets are global exclusive ranges, not positions in a shard.
        _path(root, _data_path(info, ep))
        for camera in _cameras(info, ep):
            _path(root, camera["path"])
    episodes.sort(key=lambda item: int(item["episode_index"]))
    return info, episodes


def _data_path(info: dict, ep: dict) -> str:
    try:
        return info["data_path"].format(chunk_index=int(ep["data/chunk_index"]), file_index=int(ep["data/file_index"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("无效的data_path模板或分片索引") from exc


def _cameras(info: dict, ep: dict) -> list[dict]:
    cameras = []
    for key, feature in info["features"].items():
        if feature.get("dtype") != "video":
            continue
        prefix = f"videos/{key}/"
        for field in ("chunk_index", "file_index", "from_timestamp", "to_timestamp"):
            if prefix + field not in ep:
                raise ValueError(f"episode {ep['episode_index']} 缺少相机偏移: {prefix + field}")
        try:
            path = info["video_path"].format(video_key=key, chunk_index=int(ep[prefix + "chunk_index"]), file_index=int(ep[prefix + "file_index"]))
            start, end = float(ep[prefix + "from_timestamp"]), float(ep[prefix + "to_timestamp"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"无效的相机路径或时间范围: {key}") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"相机时间范围无效: {key}, episode {ep['episode_index']}")
        video_info = feature.get("info", feature.get("video_info", {}))
        camera_fps = float(video_info.get("video.fps", info["fps"]))
        if not math.isfinite(camera_fps) or camera_fps <= 0:
            raise ValueError(f"相机fps必须为正数: {key}")
        cameras.append({"key": key, "path": path, "start_time": start, "end_time": end,
                        "fps": camera_fps})
    return cameras


def inspect_dataset(root: Path) -> dict:
    info, episodes = _metadata(root)
    views = []
    for ep in episodes:
        views.append({"episode_index": int(ep["episode_index"]), "task": "; ".join(ep["tasks"] or []),
                      "length": int(ep["length"]), "duration": int(ep["length"]) / float(info["fps"]),
                      "cameras": _cameras(info, ep), "metadata": ep})
    return {"info": info, "features": info["features"], "format_version": "v3.0",
            "total_episodes": len(episodes), "episodes": views, "fingerprint": dataset_fingerprint(root),
            "summary": {"total_frames": int(info["total_frames"]), "fps": float(info["fps"]),
                        "robot_type": info.get("robot_type"), "adapter_version": ADAPTER_VERSION,
                        "state_dimensions": info["features"]["observation.state"]["shape"],
                        "action_dimensions": info["features"]["action"]["shape"],
                        "cameras": [key for key, feature in info["features"].items() if feature.get("dtype") == "video"],
                        "upstream_release": UPSTREAM_RELEASE}}


def _clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    return value


def _inside_camera_segment(local_time: float, camera: dict) -> bool:
    """MP4 metadata end is exclusive; permit at most half a declared video frame.

    A last trajectory timestamp beyond the segment must never seek into the next
    episode just because that episode shares the same physical video file.
    """
    period = 1 / camera["fps"]
    tolerance = VIDEO_TIME_TOLERANCE_FRAMES * period
    last_frame = camera["end_time"] - camera["start_time"] - period
    return -tolerance - 1e-6 <= local_time <= last_frame + tolerance + 1e-6


def read_episode(root: Path, episode_index: int, offset: int = 0, limit: int = 500) -> dict:
    if offset < 0 or limit < 1 or limit > 2000:
        raise ValueError("offset必须非负，limit必须在1到2000之间")
    info, episodes = _metadata(root)
    ep = next((item for item in episodes if int(item["episode_index"]) == episode_index), None)
    if ep is None:
        raise ValueError(f"episode不存在: {episode_index}")
    columns = list(REQUIRED_COLUMNS)
    dataset = ds.dataset(_path(root, _data_path(info, ep)), format="parquet")
    missing = set(columns) - set(dataset.schema.names)
    if missing:
        raise ValueError(f"数据分片缺少字段: {', '.join(sorted(missing))}")
    start, end = int(ep["dataset_from_index"]), int(ep["dataset_to_index"])
    lower, upper = min(start + offset, end), min(start + offset + limit, end)
    selected = dataset.to_table(columns=columns, filter=(ds.field("index") >= lower) & (ds.field("index") < upper)).to_pylist()
    if len(selected) != upper - lower:
        raise ValueError(f"episode {episode_index} 数据边界缺失或重复: 期望{upper-lower}帧，实际{len(selected)}帧")
    for position, row in enumerate(selected, offset):
        if int(row["episode_index"]) != episode_index or int(row["frame_index"]) != position or int(row["index"]) != start + position:
            raise ValueError(f"episode {episode_index} 帧索引/共享分片边界不一致: frame {position}")
    first = dataset.to_table(columns=["timestamp"], filter=ds.field("index") == start).to_pylist()
    if len(first) != 1:
        raise ValueError(f"episode {episode_index} 缺少唯一首帧，无法映射视频时间")
    origin = first[0]["timestamp"]
    if not isinstance(origin, (int, float)) or not math.isfinite(origin):
        raise ValueError(f"episode {episode_index} 首帧时间戳无效，无法映射视频时间")
    # The browser API is local to the episode; quality checks still use raw timestamps.
    timestamps = [row["timestamp"] - origin if isinstance(row["timestamp"], (int, float)) else None for row in selected]
    cameras = _cameras(info, ep)
    previous = None
    if selected and offset > 0:
        preceding = dataset.to_table(columns=["timestamp"], filter=ds.field("index") == start + offset - 1).to_pylist()
        if len(preceding) != 1 or not isinstance(preceding[0]["timestamp"], (int, float)) or not math.isfinite(preceding[0]["timestamp"]):
            raise ValueError(f"episode {episode_index} 前页边界时间无效，拒绝回放")
        previous = preceding[0]["timestamp"] - origin
    for frame, timestamp in zip((row["frame_index"] for row in selected), timestamps):
        if timestamp is None or not math.isfinite(timestamp) or (previous is not None and timestamp <= previous):
            raise ValueError(f"episode {episode_index} frame {frame} 时间戳无效或未严格递增，拒绝回放")
        for camera in cameras:
            if not _inside_camera_segment(timestamp, camera):
                raise ValueError(f"episode {episode_index} frame {frame} 时间戳{timestamp:.6f}s超出相机{camera['key']}的episode区间，拒绝跨片段回放（容差半帧）")
        previous = timestamp
    return _clean({"offset": offset, "total": int(ep["length"]), "fps": float(info["fps"]),
                   "timestamps": timestamps,
                   "frame_indices": [row["frame_index"] for row in selected],
                   "series": {key: [row[key] for row in selected] for key in ("observation.state", "action")},
                   "cameras": cameras})


def _probe_video(path: Path, times: list[float]) -> dict:
    """Decode near each requested boundary; PTS/keyframes avoid linear full-video decode."""
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("文件没有视频轨")
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else (float(container.duration / av.time_base) if container.duration else None)
        rate = float(stream.average_rate) if stream.average_rate else None
        found = []
        for time in times:
            container.seek(max(0, int(time / float(stream.time_base))), stream=stream, backward=True)
            timestamp = None
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                current = float(frame.pts * frame.time_base)
                if current + (VIDEO_TIME_TOLERANCE_FRAMES / rate if rate else 0.03) + 1e-6 >= time:
                    timestamp = current
                    break
            if timestamp is None:
                raise ValueError(f"在{time:.6f}s附近无法解码视频帧")
            found.append(timestamp)
        return {"duration": duration, "fps": rate, "codec": stream.codec_context.name, "decoded_timestamps": found}


def _noop_progress(progress: float, message: str) -> None:
    return None


def _not_cancelled() -> bool:
    return False


def quality_report(root: Path, progress: Callable[[float, str], None] = _noop_progress,
                   cancelled: Callable[[], bool] = _not_cancelled) -> dict:
    """Stream each physical Parquet shard once, preserving exact issue counts."""
    info, episodes = _metadata(root)
    issues, counts, rules = [], Counter(), Counter()
    def issue(rule, message, ep=None, frame=None, field=None, severity="error"):
        counts[severity] += 1
        rules[rule] += 1
        if len(issues) < MAX_ISSUES:
            issues.append({"severity": severity, "rule": rule, "episode_index": ep,
                           "frame_index": frame, "field": field, "message": message})
    def check_cancel():
        if cancelled():
            raise RuntimeError("任务已取消")
    by_index = {int(ep["episode_index"]): ep for ep in episodes}
    cameras_by_episode = {idx: _cameras(info, ep) for idx, ep in by_index.items()}
    states = {idx: {"count": 0, "last_timestamp": None, "first_timestamp": None} for idx in by_index}
    paths = sorted({_data_path(info, ep) for ep in episodes})
    processed = 0
    for relative in paths:
        check_cancel()
        parquet = pq.ParquetFile(_path(root, relative))
        missing = set(REQUIRED_COLUMNS) - set(parquet.schema_arrow.names)
        for field in sorted(missing):
            issue("missing_field", f"分片{relative}缺少必需字段{field}", field=field)
        columns = [key for key in REQUIRED_COLUMNS if key in parquet.schema_arrow.names]
        for batch in parquet.iter_batches(batch_size=1024, columns=columns):
            check_cancel()
            for row in batch.to_pylist():
                processed += 1
                idx = row.get("episode_index")
                frame = row.get("frame_index")
                if idx not in by_index:
                    issue("unknown_episode", f"未登记的episode_index={idx}", idx, frame, "episode_index")
                    continue
                ep, state = by_index[idx], states[idx]
                position = state["count"]
                state["count"] += 1
                start, end = int(ep["dataset_from_index"]), int(ep["dataset_to_index"])
                if _data_path(info, ep) != relative:
                    issue("wrong_shard", "轨迹行位于错误的共享分片", idx, frame, "data/file_index")
                if "index" in row and (row["index"] is None or row["index"] != start + position or not start <= row["index"] < end):
                    issue("index_boundary", f"全局index应为{start + position}且属于[{start},{end})，实际{row['index']}", idx, frame, "index")
                if "frame_index" in row and frame != position:
                    issue("frame_index", f"episode内frame_index应为{position}，实际{frame}", idx, frame, "frame_index")
                if "timestamp" in row:
                    timestamp = row["timestamp"]
                    if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                        issue("nonfinite", "时间戳不是有限数", idx, frame, "timestamp")
                    else:
                        if timestamp < 0:
                            issue("timestamp_negative", "episode局部时间戳为负", idx, frame, "timestamp")
                        if state["first_timestamp"] is None:
                            state["first_timestamp"] = timestamp
                            if abs(timestamp) > 0.51 / info["fps"]:
                                issue("timestamp_origin", "episode局部时间没有从0附近开始", idx, frame, "timestamp")
                        if state["last_timestamp"] is not None:
                            delta = timestamp - state["last_timestamp"]
                            if delta <= 0:
                                issue("timestamp_order", "episode内时间戳未严格递增", idx, frame, "timestamp")
                            elif abs(delta - 1 / info["fps"]) > max(0.002, 0.1 / info["fps"]):
                                issue("timestamp_gap", "时间步长偏离schema帧率", idx, frame, "timestamp", "warning")
                        state["last_timestamp"] = timestamp
                        local_time = timestamp - state["first_timestamp"]
                        for camera in cameras_by_episode[idx]:
                            if not _inside_camera_segment(local_time, camera):
                                issue("video_frame_range", f"实际轨迹局部时间{local_time:.6f}s超出相机episode区间，不能跨共享视频中的下一条轨迹（容差半帧）", idx, frame, camera["key"])
                for field in ("observation.state", "action"):
                    if field not in row:
                        continue
                    value, dimension = row[field], info["features"][field]["shape"][0]
                    if not isinstance(value, list) or len(value) != dimension:
                        issue("dimension", f"期望向量维度{dimension}，实际{len(value) if isinstance(value, list) else type(value).__name__}", idx, frame, field)
                    elif any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
                        issue("nonfinite", "向量包含NaN、Inf或null", idx, frame, field)
            progress(min(0.65, processed / max(1, info["total_frames"]) * 0.65), f"已检查{processed}个轨迹帧")
    ranges = sorted((int(ep["dataset_from_index"]), int(ep["dataset_to_index"]), int(ep["episode_index"])) for ep in episodes)
    for number, (start, end, idx) in enumerate(ranges):
        ep = by_index[idx]
        if end - start != int(ep["length"]) or states[idx]["count"] != int(ep["length"]):
            issue("episode_length", f"元信息length={ep['length']}，index范围={end-start}，实际帧数={states[idx]['count']}", idx, field="length")
        if number and start < ranges[number - 1][1]:
            issue("episode_overlap", "全局episode索引范围交叉", idx, field="dataset_from_index")
        if number and start > ranges[number - 1][1]:
            issue("episode_gap", "全局episode索引范围存在缺口", idx, field="dataset_from_index")
    if ranges and (ranges[0][0] != 0 or ranges[-1][1] != int(info["total_frames"])):
        issue("dataset_range", "完整数据集全局索引范围与[0,total_frames)不一致", field="dataset_from_index")
    camera_ranges: dict[tuple[str, str], list[tuple[float, float, int]]] = {}
    for ep in episodes:
        for camera in cameras_by_episode[int(ep["episode_index"])]:
            camera_ranges.setdefault((camera["key"], camera["path"]), []).append((camera["start_time"], camera["end_time"], int(ep["episode_index"])))
    for (key, _), intervals in camera_ranges.items():
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1] - 1e-5:
                issue("video_episode_overlap", "同一相机共享视频中的episode时间区间交叉", current[2], field=key)
    if processed != int(info["total_frames"]):
        issue("total_frames", f"实际帧数{processed}与info.total_frames={info['total_frames']}不符", field="total_frames")
    video_checks = []
    for number, ep in enumerate(episodes):
        check_cancel()
        idx = int(ep["episode_index"])
        for camera in cameras_by_episode[idx]:
            check_cancel()
            expected_duration = ep["length"] / info["fps"]
            if abs(camera["end_time"] - camera["start_time"] - expected_duration) > 1.1 / camera["fps"]:
                issue("video_episode_duration", "相机区间长度与轨迹帧数不匹配", idx, field=camera["key"])
            state = states[idx]
            local_last = (state["last_timestamp"] - state["first_timestamp"]) if state["last_timestamp"] is not None and state["first_timestamp"] is not None else None
            target_last = camera["start_time"] + local_last if local_last is not None else None
            # Do not decode a neighbouring episode to manufacture "successful" evidence.
            targets = [camera["start_time"]]
            last_in_range = local_last is not None and _inside_camera_segment(local_last, camera)
            if last_in_range:
                targets.append(target_last)
            try:
                probe = _probe_video(_path(root, camera["path"]), targets)
                tolerance = VIDEO_TIME_TOLERANCE_FRAMES / (probe["fps"] or camera["fps"]) + 1e-6
                if probe["duration"] is not None and camera["end_time"] > probe["duration"] + tolerance:
                    issue("video_boundary", f"区间结束{camera['end_time']}超过视频时长{probe['duration']}", idx, field=camera["key"])
                for target, actual in zip(targets, probe["decoded_timestamps"]):
                    if abs(target - actual) > tolerance:
                        issue("video_timestamp", f"请求PTS={target}，解码PTS={actual}，超过半帧容差", idx, field=camera["key"])
                video_checks.append({"episode_index": idx, "camera": camera["key"], "first_target": camera["start_time"], "last_target": target_last,
                                     "last_target_in_segment": last_in_range, "tolerance_frames": VIDEO_TIME_TOLERANCE_FRAMES, **probe})
            except Exception as exc:
                issue("video_decode", f"视频不可访问或边界无法解码: {exc}", idx, field=camera["key"])
        progress(0.65 + (number + 1) / len(episodes) * 0.35, f"已核对{number + 1}/{len(episodes)}条轨迹的视频边界")
    return {"report_type": "quality", "adapter_version": ADAPTER_VERSION, "dataset_fingerprint": dataset_fingerprint(root),
            "summary": {"episodes_checked": len(episodes), "frames_checked": processed, "errors": counts["error"], "warnings": counts["warning"],
                        "valid": counts["error"] == 0, "issue_counts_by_rule": dict(rules), "issues_recorded": len(issues),
                        "issues_truncated": sum(counts.values()) > len(issues)}, "issues": issues,
            "video_checks": video_checks[:2000], "video_checks_truncated": len(video_checks) > 2000,
            "rules": ["required_columns", "dimension", "finite_values", "episode_local_timestamp_order", "global_index_boundary", "episode_length", "video_frame_range_half_frame_tolerance", "video_decode_boundaries"]}


def split_episodes(root: Path, ratio: float, seed: int) -> dict:
    if not isinstance(ratio, (float, int)) or not math.isfinite(ratio) or not 0 < ratio < 1:
        raise ValueError("训练比例ratio必须在0和1之间")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed必须为整数")
    _, episodes = _metadata(root)
    if len(episodes) < 2:
        raise ValueError("至少需要两条完整episode才能划分")
    indices = [int(ep["episode_index"]) for ep in episodes]
    random.Random(seed).shuffle(indices)
    count = max(1, min(len(indices) - 1, int(len(indices) * ratio)))
    return {"report_type": "split", "dataset_fingerprint": dataset_fingerprint(root), "unit": "episode", "ratio": ratio, "seed": seed,
            "train": sorted(indices[:count]), "validation": sorted(indices[count:]),
            "train_count": count, "validation_count": len(indices) - count, "source_episodes": len(indices)}


def statistics_report(root: Path, progress: Callable[[float, str], None] = _noop_progress,
                      cancelled: Callable[[], bool] = _not_cancelled) -> dict:
    info, episodes = _metadata(root)
    stats = {}
    for field in ("observation.state", "action"):
        count = info["features"][field]["shape"][0]
        stats[field] = {"count": [0] * count, "mean": [0.0] * count, "m2": [0.0] * count,
                        "min": [None] * count, "max": [None] * count, "invalid_values": 0}
    processed = 0
    for path in sorted({_data_path(info, ep) for ep in episodes}):
        parquet = pq.ParquetFile(_path(root, path))
        missing = set(stats) - set(parquet.schema_arrow.names)
        if missing:
            raise ValueError(f"统计缺少字段: {sorted(missing)}")
        for batch in parquet.iter_batches(batch_size=1024, columns=list(stats)):
            if cancelled():
                raise RuntimeError("任务已取消")
            for row in batch.to_pylist():
                processed += 1
                for field, item in stats.items():
                    values = row[field]
                    if not isinstance(values, list) or len(values) != len(item["count"]):
                        raise ValueError(f"统计遇到维度不一致: {field}")
                    for index, value in enumerate(values):
                        if not isinstance(value, (int, float)) or not math.isfinite(value):
                            item["invalid_values"] += 1
                            continue
                        item["count"][index] += 1
                        delta = value - item["mean"][index]
                        item["mean"][index] += delta / item["count"][index]
                        item["m2"][index] += delta * (value - item["mean"][index])
                        item["min"][index] = value if item["min"][index] is None else min(value, item["min"][index])
                        item["max"][index] = value if item["max"][index] is None else max(value, item["max"][index])
            progress(min(1.0, processed / max(1, info["total_frames"])), f"已统计{processed}帧")
    for item in stats.values():
        m2 = item.pop("m2")
        item["std"] = [math.sqrt(max(0.0, variance) / count) if count else None for variance, count in zip(m2, item["count"])]
        item["mean"] = [value if count else None for value, count in zip(item["mean"], item["count"])]
    return {"report_type": "statistics", "dataset_fingerprint": dataset_fingerprint(root), "frames_processed": processed,
            "episodes": len(episodes), "features": stats, "std_definition": "population; finite values only"}
