#!/usr/bin/env python3
"""Generate valid, deterministic v3 Parquet + shared multi-camera H264 MP4 fixtures."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

FPS = 10
LENGTHS = (12, 16, 14)
CAMERAS = ("observation.images.front", "observation.images.wrist")


def _video(path: Path, camera: int, leading_frames: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = 128, 96, "yuv420p"
        stream.options = {"crf": "20", "preset": "fast", "g": "10"}
        frame_number = 0
        for episode, length in [(-1, leading_frames)] + list(enumerate(LENGTHS)):
            for local in range(length):
                image = np.zeros((96, 128, 3), dtype=np.uint8)
                image[:, :] = [30 + max(0, episode) * 65, 40 + camera * 90, 65]
                left = 8 + int(local / max(1, length - 1) * 82)
                top = 22 + camera * 15
                image[top:top + 18, left:left + 18] = [245, 235, 200]
                image[4:8, 4:4 + (max(0, episode) + 1) * 25] = [255, 255, 255]
                frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                frame.pts = frame_number
                frame_number += 1
                for packet in stream.encode(frame):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def generate(root: Path, variant: str = "normal") -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta/episodes/chunk-000").mkdir(parents=True, exist_ok=True)
    (root / "data/chunk-000").mkdir(parents=True, exist_ok=True)
    features = {"observation.state": {"dtype": "float32", "shape": [3], "names": ["joint_1", "joint_2", "gripper"]},
                "action": {"dtype": "float32", "shape": [2], "names": ["delta_x", "delta_y"]}}
    for key in ("timestamp", "frame_index", "episode_index", "index", "task_index"):
        features[key] = {"dtype": "float32" if key == "timestamp" else "int64", "shape": [1], "names": None}
    for key in CAMERAS:
        features[key] = {"dtype": "video", "shape": [96, 128, 3], "names": ["height", "width", "channels"],
                         "info": {"video.fps": FPS, "video.codec": "h264", "video.pix_fmt": "yuv420p", "has_audio": False}}
    info = {"codebase_version": "v3.0", "robot_type": "synthetic_demo", "total_episodes": len(LENGTHS),
            "total_frames": sum(LENGTHS), "total_tasks": 1, "chunks_size": 1000, "fps": FPS,
            "data_files_size_in_mb": 100, "video_files_size_in_mb": 200,
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
            "features": features, "splits": {"train": "0:3"}}
    records, episodes, start = [], [], 0
    for episode, length in enumerate(LENGTHS):
        for frame in range(length):
            records.append({"timestamp": frame / FPS, "frame_index": frame, "episode_index": episode, "index": start + frame, "task_index": 0,
                            "observation.state": [math.sin(frame / 4), math.cos(frame / 4), float(episode)],
                            "action": [float(frame) / length, -float(frame) / length]})
        metadata = {"episode_index": episode, "length": length, "tasks": ["Move a synthetic marker; not robot evaluation"],
                    "dataset_from_index": start, "dataset_to_index": start + length, "data/chunk_index": 0, "data/file_index": 0,
                    "meta/episodes/chunk_index": 0, "meta/episodes/file_index": 0}
        for camera, key in enumerate(CAMERAS):
            # Wrist contains two leading frames: same episode, different camera offset.
            leading = camera * 2
            metadata.update({f"videos/{key}/chunk_index": 0, f"videos/{key}/file_index": 0,
                             f"videos/{key}/from_timestamp": (start + leading) / FPS,
                             f"videos/{key}/to_timestamp": (start + length + leading) / FPS})
        episodes.append(metadata)
        start += length
    if variant == "nan":
        records[15]["action"][0] = float("nan")
        records[16]["observation.state"][0] = float("inf")
    elif variant == "timestamp":
        records[16]["timestamp"] = records[15]["timestamp"] - 0.1
    elif variant == "dimension":
        records[15]["action"] = [1.0]
    elif variant == "boundary":
        episodes[1]["dataset_to_index"] += 5
        episodes[1][f"videos/{CAMERAS[0]}/to_timestamp"] += 10
    elif variant == "missing_field":
        for row in records:
            del row["action"]
    elif variant != "normal":
        raise ValueError(f"未知变体: {variant}")
    (root / "meta/info.json").write_text(json.dumps(info, indent=2) + "\n")
    table = pa.Table.from_pylist(records)
    for key in ("observation.state", "action"):
        if key in table.column_names:
            table = table.set_column(table.schema.get_field_index(key), key, pa.array([row[key] for row in records], type=pa.list_(pa.float32())))
    table = table.set_column(table.schema.get_field_index("timestamp"), "timestamp", pa.array([row["timestamp"] for row in records], type=pa.float32()))
    pq.write_table(table, root / "data/chunk-000/file-000.parquet", row_group_size=12)
    pq.write_table(pa.Table.from_pylist(episodes), root / "meta/episodes/chunk-000/file-000.parquet")
    # Pandas parquet-index metadata matches upstream tasks.parquet load_tasks contract.
    tasks = pa.table({"task_index": pa.array([0], type=pa.int64()), "__index_level_0__": ["Move a synthetic marker; not robot evaluation"]})
    pandas_metadata = {"index_columns": ["__index_level_0__"], "column_indexes": [], "columns": [
        {"name": "task_index", "field_name": "task_index", "pandas_type": "int64", "numpy_type": "int64", "metadata": None},
        {"name": None, "field_name": "__index_level_0__", "pandas_type": "unicode", "numpy_type": "object", "metadata": None}],
        "creator": {"library": "pyarrow", "version": pa.__version__}, "pandas_version": "2.2.3"}
    tasks = tasks.replace_schema_metadata({b"pandas": json.dumps(pandas_metadata).encode()})
    pq.write_table(tasks, root / "meta/tasks.parquet")
    (root / "meta/stats.json").write_text("{}\n")
    for camera, key in enumerate(CAMERAS):
        _video(root / f"videos/{key}/chunk-000/file-000.mp4", camera, camera * 2)
    source = {"name": "合成双相机共享分片演示" + (f" · {variant}" if variant != "normal" else ""), "source_kind": "synthetic_demo",
              "source_uri": "scripts/generate_fixture.py", "revision": "fixture-v1", "variant": variant,
              "note": "Generated pixels/trajectories for format verification; no real robot or policy evaluation."}
    (root / "SOURCE.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("storage/incoming/synthetic-small"))
    parser.add_argument("--variant", choices=["normal", "nan", "timestamp", "dimension", "boundary", "missing_field"], default="normal")
    parser.add_argument("--all-variants", action="store_true")
    options = parser.parse_args()
    print(generate(options.output, options.variant))
    if options.all_variants:
        for variant in ("nan", "timestamp", "dimension", "boundary", "missing_field"):
            print(generate(options.output.parent / f"synthetic-{variant}", variant))
