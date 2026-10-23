from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_fixture import CAMERAS, LENGTHS, generate
from app.data.lerobot_v3 import inspect_dataset, quality_report, read_episode, split_episodes, statistics_report


@pytest.fixture(scope="module")
def normal(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("v3") / "normal")


def test_shared_parquet_and_camera_offsets_do_not_leak(normal):
    result = inspect_dataset(normal)
    assert result["total_episodes"] == 3
    assert result["summary"]["state_dimensions"] == [3]
    assert result["summary"]["action_dimensions"] == [2]
    second = read_episode(normal, 1)
    assert second["total"] == LENGTHS[1]
    assert second["frame_indices"] == list(range(LENGTHS[1]))
    assert second["timestamps"][0] == 0
    assert len(second["series"]["action"]) == LENGTHS[1]
    assert second["series"]["observation.state"][0][2] == 1
    cameras = {camera["key"]: camera for camera in second["cameras"]}
    assert cameras[CAMERAS[0]]["start_time"] == 1.2
    assert cameras[CAMERAS[1]]["start_time"] == 1.4
    last = read_episode(normal, 1, offset=15, limit=5)
    assert last["frame_indices"] == [15]
    assert last["series"]["observation.state"][0][2] == 1
    assert read_episode(normal, 1, offset=100)["timestamps"] == []


def test_quality_real_video_decode_with_episode_timestamp_reset(normal):
    progress = []
    result = quality_report(normal, lambda value, message: progress.append(value), lambda: False)
    assert result["summary"]["valid"], result
    assert result["summary"]["frames_checked"] == sum(LENGTHS)
    assert len(result["video_checks"]) == 6
    assert progress[-1] == 1
    for check in result["video_checks"]:
        assert check["decoded_timestamps"][0] == pytest.approx(check["first_target"], abs=0.001)
        assert check["decoded_timestamps"][1] == pytest.approx(check["last_target"], abs=0.001)


@pytest.mark.parametrize("variant,rule", [("nan", "nonfinite"), ("timestamp", "timestamp_order"),
                                       ("dimension", "dimension"), ("boundary", "episode_length"),
                                       ("missing_field", "missing_field")])
def test_corrupt_data_has_actionable_quality_issues(tmp_path, variant, rule):
    path = generate(tmp_path / variant, variant)
    result = quality_report(path)
    assert not result["summary"]["valid"]
    assert result["summary"]["issue_counts_by_rule"].get(rule, 0) >= 1
    assert all("field" in issue and "episode_index" in issue for issue in result["issues"])
    if variant == "nan":
        frames = read_episode(path, 1)
        assert frames["series"]["action"][3][0] is None
        assert frames["series"]["observation.state"][4][0] is None
        json.dumps(frames, allow_nan=False)


def test_split_reproducible_complete_disjoint_and_versioned(normal):
    first = split_episodes(normal, 0.7, 42)
    assert first == split_episodes(normal, 0.7, 42)
    assert not set(first["train"]) & set(first["validation"])
    assert sorted(first["train"] + first["validation"]) == [0, 1, 2]
    assert len(first["dataset_fingerprint"]) == 64
    with pytest.raises(ValueError):
        split_episodes(normal, 1, 1)
    with pytest.raises(ValueError):
        split_episodes(normal, float("nan"), 1)


def test_statistics_matches_streamed_frames(normal):
    result = statistics_report(normal)
    assert result["frames_processed"] == 42
    assert result["features"]["action"]["count"] == [42, 42]
    assert result["features"]["observation.state"]["min"][2] == 0
    assert result["features"]["observation.state"]["max"][2] == 2


def test_cancelled_callbacks_are_not_swallowed(normal):
    with pytest.raises(RuntimeError):
        quality_report(normal, cancelled=lambda: True)
    with pytest.raises(RuntimeError):
        statistics_report(normal, cancelled=lambda: True)
    def cancel_from_progress(value, message):
        raise RuntimeError("lease lost")
    with pytest.raises(RuntimeError, match="lease lost"):
        quality_report(normal, progress=cancel_from_progress)


def test_unknown_version_missing_offsets_and_path_escape_fail(tmp_path):
    path = generate(tmp_path / "schema")
    info_file = path / "meta/info.json"
    info = json.loads(info_file.read_text())
    info["codebase_version"] = "v2.1"
    info_file.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="v3.0"):
        inspect_dataset(path)
    info["codebase_version"] = "v3.0"
    info["data_path"] = "../../secret.parquet"
    info_file.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="越界"):
        inspect_dataset(path)


def test_read_page_limit_and_unknown_episode(normal):
    with pytest.raises(ValueError):
        read_episode(normal, 0, limit=3000)
    with pytest.raises(ValueError):
        read_episode(normal, 99)


def test_nonzero_source_timestamp_normalized_for_paging_but_reported(tmp_path):
    path = generate(tmp_path / "nonzero")
    file = path / "data/chunk-000/file-000.parquet"
    table = pq.read_table(file)
    rows = table.to_pylist()
    for row in rows:
        row["timestamp"] += 2.0
    pq.write_table(pa.Table.from_pylist(rows), file)
    frames = read_episode(path, 1, offset=3, limit=2)
    assert frames["timestamps"] == pytest.approx([0.3, 0.4], abs=1e-5)
    result = quality_report(path)
    assert result["summary"]["issue_counts_by_rule"]["timestamp_origin"] == 3


def test_missing_camera_offset_is_explicitly_rejected(tmp_path):
    path = generate(tmp_path / "missingoffset")
    file = path / "meta/episodes/chunk-000/file-000.parquet"
    table = pq.read_table(file)
    table = table.drop([f"videos/{CAMERAS[0]}/from_timestamp"])
    pq.write_table(table, file)
    with pytest.raises(ValueError, match="缺少相机偏移"):
        inspect_dataset(path)


def test_symlink_escape_is_rejected_before_read(tmp_path):
    path = generate(tmp_path / "symlink")
    source = path / "data/chunk-000/file-000.parquet"
    outside = tmp_path / "private.parquet"
    source.replace(outside)
    source.symlink_to(outside)
    with pytest.raises(ValueError, match="越界"):
        inspect_dataset(path)


def test_actual_timestamp_cannot_seek_into_next_shared_video_episode(tmp_path):
    path = generate(tmp_path / "actual-time-overflow")
    file = path / "data/chunk-000/file-000.parquet"
    rows = pq.read_table(file).to_pylist()
    rows[11]["timestamp"] = 5.0
    pq.write_table(pa.Table.from_pylist(rows), file)
    report = quality_report(path)
    assert report["summary"]["valid"] is False
    assert report["summary"]["errors"] >= 2
    issues = [issue for issue in report["issues"] if issue["rule"] == "video_frame_range"]
    assert {issue["field"] for issue in issues} == set(CAMERAS)
    assert all(issue["episode_index"] == 0 and issue["frame_index"] == 11 for issue in issues)
    checks = [check for check in report["video_checks"] if check["episode_index"] == 0]
    assert all(check["last_target_in_segment"] is False for check in checks)
    assert all(len(check["decoded_timestamps"]) == 1 for check in checks)
    with pytest.raises(ValueError, match="拒绝跨片段回放"):
        read_episode(path, 0)


@pytest.mark.parametrize("last_time,within_half_frame", [(1.14, True), (1.16, False)])
def test_video_mapping_tolerates_only_half_frame(tmp_path, last_time, within_half_frame):
    path = generate(tmp_path / "half-frame")
    file = path / "data/chunk-000/file-000.parquet"
    rows = pq.read_table(file).to_pylist()
    rows[11]["timestamp"] = last_time
    pq.write_table(pa.Table.from_pylist(rows), file)
    report = quality_report(path)
    assert report["summary"]["valid"] is within_half_frame
    if within_half_frame:
        assert read_episode(path, 0)["timestamps"][-1] == pytest.approx(last_time)
    else:
        with pytest.raises(ValueError, match="容差半帧"):
            read_episode(path, 0)


def test_read_rejects_unordered_timestamp_across_page_boundary(tmp_path):
    path = generate(tmp_path / "unordered-page")
    file = path / "data/chunk-000/file-000.parquet"
    rows = pq.read_table(file).to_pylist()
    rows[3]["timestamp"] = 0.15
    pq.write_table(pa.Table.from_pylist(rows), file)
    report = quality_report(path)
    assert not report["summary"]["valid"]
    with pytest.raises(ValueError, match="未严格递增"):
        read_episode(path, 0, offset=3, limit=1)


def test_public_sample_real_shared_boundaries_if_downloaded():
    path = ROOT / "storage/incoming/public-small"
    if not (path / "SOURCE.json").exists():
        pytest.skip("执行scripts/fetch_public_sample.py后运行公共样本验收")
    source = json.loads((path / "SOURCE.json").read_text())
    assert source["revision"] == "7628202a2180972f291ba1bc6723834921e72c19"
    meta = inspect_dataset(path)
    assert meta["total_episodes"] == 206
    for index in (0, 1, 205):
        episode = meta["episodes"][index]
        first = read_episode(path, index, limit=1)
        last = read_episode(path, index, offset=episode["length"] - 1, limit=1)
        assert first["frame_indices"] == [0]
        assert last["frame_indices"] == [episode["length"] - 1]
        assert first["cameras"][0]["start_time"] == episode["metadata"]["videos/observation.image/from_timestamp"]
