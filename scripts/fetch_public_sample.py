#!/usr/bin/env python3
"""Fetch a fixed, complete, public LeRobot v3 sample (about 7.7MB).

No Hugging Face login, remote Python, models, or training dependencies required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

import requests

REPO_ID = "lerobot/pusht"
REVISION = "7628202a2180972f291ba1bc6723834921e72c19"
REPO_URL = f"https://huggingface.co/datasets/{REPO_ID}"
MAX_BYTES = 200 * 1024 * 1024


def fetch(root: Path, max_bytes: int = MAX_BYTES) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    response = session.get(f"https://huggingface.co/api/datasets/{REPO_ID}/tree/{REVISION}",
                           params={"recursive": "true", "expand": "false"}, timeout=60)
    response.raise_for_status()
    entries = response.json()
    files = [entry for entry in entries if entry["type"] == "file" and entry["path"] != ".gitattributes"]
    total = sum(entry["size"] for entry in files)
    if total > min(MAX_BYTES, max_bytes):
        raise ValueError(f"公开样本预计{total} bytes，超过限制；未开始下载")
    manifest = {"name": "公开 PushT · LeRobot v3", "source_kind": "public", "source_uri": REPO_URL,
                "repo_id": REPO_ID, "revision": REVISION, "download_bytes": total,
                "scope": "complete fixed revision; no resampling or relabeling", "files": []}
    for entry in files:
        relative = entry["path"]
        destination = (root / relative).resolve()
        if Path(relative).is_absolute() or ".." in Path(relative).parts or not destination.is_relative_to(root):
            raise ValueError(f"远程文件路径不安全: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".part")
        digest, downloaded = hashlib.sha256(), 0
        url = f"{REPO_URL}/resolve/{REVISION}/{quote(relative)}"
        try:
            with session.get(url, stream=True, timeout=(30, 120), params={"download": "true"}) as response:
                response.raise_for_status()
                with temp.open("wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > entry["size"] or downloaded > max_bytes:
                            raise ValueError("下载长度超过公开清单，已终止")
                        digest.update(chunk)
                        stream.write(chunk)
            if downloaded != entry["size"]:
                raise ValueError(f"长度不一致: {relative}")
            # HF tree includes LFS oid where available; verify rather than merely record it.
            expected = entry.get("lfs", {}).get("oid")
            if expected and digest.hexdigest() != expected:
                raise ValueError(f"LFS SHA256校验不一致: {relative}")
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
        manifest["files"].append({"path": relative, "size": downloaded, "sha256": digest.hexdigest(), "url": url})
        print(f"{relative}: {downloaded:,} bytes SHA256 {digest.hexdigest()}", flush=True)
    (root / "SOURCE.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("storage/incoming/public-small"))
    parser.add_argument("--max-mb", type=float, default=200)
    options = parser.parse_args()
    fetch(options.output, int(options.max_mb * 1024 * 1024))
