import hashlib
import json
import os
import shutil
from pathlib import Path

from .settings import settings


def controlled_path(base: Path, relative: str, *, must_exist=True) -> Path:
    """Resolve before access; symlinks and absolute paths cannot escape the approved root."""
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not relative or "\x00" in relative:
        raise ValueError("仅允许受控目录内的相对路径")
    resolved = (base / rel).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError("路径超出受控目录")
    if must_exist and not resolved.exists():
        raise ValueError("指定文件或目录不存在")
    return resolved


def sha_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def copy_snapshot(source: Path, staging: Path, check):
    """Copy files, never follow symlinks. Publish by atomic rename only after a complete copy."""
    if not source.is_dir():
        raise ValueError("数据源必须为目录")
    files = []
    size = 0
    for root, dirs, names in os.walk(source, followlinks=False):
        for d in dirs:
            if (Path(root) / d).is_symlink():
                raise ValueError("数据目录不能包含符号链接")
        for name in names:
            file = Path(root) / name
            if file.is_symlink() or not file.is_file():
                raise ValueError("数据包只能包含常规文件")
            if name.startswith(".") or ".cache" in file.relative_to(source).parts:
                continue
            size += file.stat().st_size
            if size > settings.max_import_bytes or len(files) >= 50000:
                raise ValueError("导入超过大小/文件数量限制")
            files.append(file)
    staging.mkdir(parents=True, exist_ok=False)
    for i, src in enumerate(files):
        check(i / max(len(files), 1) * 0.3, f"复制不可变快照 {i + 1}/{len(files)}")
        dest = staging / src.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Small bounded chunks permit cancellation while copying a large source file.
        with src.open("rb") as f, dest.open("wb") as out:
            while block := f.read(4 * 1024 * 1024):
                check(None, None)
                out.write(block)
    return size


def remove_tree(path: Path):
    if path.exists():
        shutil.rmtree(path)
