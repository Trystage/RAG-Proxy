"""安全解压工具：zip / tar.gz / tgz / tar。

防护：
- 逐 entry 校验路径，拒绝绝对路径、`..` 跳转、符号链接/硬链接
- 解压前估算总大小（防 zip bomb），解压过程中实时计数，超限立即中止
- 全部解压到目标目录内，不覆盖已存在文件之外的内容
"""
from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path


class ArchiveError(RuntimeError):
    pass


def _safe_rel_path(entry_name: str, entry_kind: str = "file") -> str:
    """校验归档条目路径，返回安全相对路径。"""
    name = entry_name.replace("\\", "/")
    if name.startswith("/") or re_abs_windows(name):
        raise ArchiveError(f"拒绝绝对路径条目：{entry_name!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ArchiveError(f"拒绝路径跳转条目：{entry_name!r}")
    if not parts:
        raise ArchiveError(f"非法空路径条目：{entry_name!r}")
    return "/".join(parts)


def re_abs_windows(name: str) -> bool:
    return len(name) >= 2 and name[1] == ":" and name[0].isalpha()


def _check_total(sizes: list[int], max_extract_bytes: int, what: str) -> None:
    total = sum(sizes)
    if total > max_extract_bytes:
        raise ArchiveError(
            f"{what} 解压后预估总大小 {total / 1024 / 1024:.1f} MB "
            f"超过上限 {max_extract_bytes / 1024 / 1024:.1f} MB"
        )


def extract_zip(path: Path, dest: Path, max_extract_bytes: int) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            _check_total([i.file_size for i in infos], max_extract_bytes, "压缩包")
            written: list[Path] = []
            total = 0
            for info in infos:
                rel = _safe_rel_path(info.filename)
                target = (dest / rel).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ArchiveError(f"路径越界条目：{info.filename!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_extract_bytes:
                            raise ArchiveError("解压过程中超出大小上限（疑似 zip bomb）")
                        out.write(chunk)
                written.append(target)
            return written
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"无效的 zip 文件：{e}") from e


def extract_tar(path: Path, dest: Path, max_extract_bytes: int) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(path, "r:*") as tf:
            members = [m for m in tf.getmembers() if m.isfile()]
            _check_total([m.size for m in members], max_extract_bytes, "压缩包")
            written: list[Path] = []
            total = 0
            for m in members:
                if m.issym() or m.islnk():
                    raise ArchiveError(f"拒绝链接条目：{m.name!r}")
                rel = _safe_rel_path(m.name)
                target = (dest / rel).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ArchiveError(f"路径越界条目：{m.name!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                f = tf.extractfile(m)
                if f is None:
                    continue
                with f as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_extract_bytes:
                            raise ArchiveError("解压过程中超出大小上限（疑似 zip bomb）")
                        out.write(chunk)
                written.append(target)
            return written
    except tarfile.TarError as e:
        raise ArchiveError(f"无效的 tar 文件：{e}") from e


def extract_archive(path: Path, dest: Path, max_extract_bytes: int) -> list[Path]:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return extract_zip(path, dest, max_extract_bytes)
    if suffix in (".tar", ".gz", ".tgz", ".tar.gz"):
        return extract_tar(path, dest, max_extract_bytes)
    raise ArchiveError(f"不支持的压缩格式：{suffix}")


def archive_is_supported(name: str) -> bool:
    n = name.lower()
    return n.endswith((".zip", ".tar", ".tar.gz", ".tgz"))


# re-export for convenience
io = io
