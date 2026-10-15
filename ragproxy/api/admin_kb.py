"""管理 API：知识库 CRUD、批量上传（含压缩包自动解压）、索引任务、分块预览、重建。"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..utils.archive import ArchiveError, archive_is_supported, extract_archive
from .admin_common import get_state, require_admin, write_config

log = logging.getLogger("ragproxy.admin_kb")
router = APIRouter()

KB_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff-]{1,64}$")


def _check_kb_name(name: str) -> str:
    if not name or not KB_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="知识库名称仅允许中文/字母/数字/下划线/连字符，长度 1-64")
    return name


def _kb_provider_cfg(state, name: str) -> str | None:
    """从 config knowledge_bases 读取知识库指定的 embed_provider（None=全局默认）。"""
    for item in state.config.raw().get("knowledge_bases", []) or []:
        if isinstance(item, dict) and item.get("name") == name:
            pid = item.get("embed_provider")
            return pid if isinstance(pid, str) and pid else None
    return None


def _set_kb_provider_cfg(state, name: str, provider_id: str | None) -> None:
    cfg = state.config.raw()
    kbs = cfg.get("knowledge_bases", []) or []
    kbs = [k for k in kbs if not (isinstance(k, dict) and k.get("name") == name)]
    if provider_id:
        kbs.append({"name": name, "embed_provider": provider_id})
    cfg["knowledge_bases"] = kbs
    write_config(state, cfg)  # 写回 + 热重载


def _check_ext(ext: str, allowed: list[str]) -> bool:
    return ext.lower() in [e.lower() for e in allowed]


@router.get("/admin/kbs")
async def list_kbs(request: Request):
    require_admin(request)
    state = get_state(request)
    vs_items = await state.vs.list_kbs() if state.vs else []
    by_name = {i["name"]: i for i in vs_items}
    # 补充文档目录信息
    for rec in state.ingest.list_all_files() if state.ingest else []:
        name = rec.get("kb", "")
        if name not in by_name:
            by_name[name] = {"name": name, "documents": 0, "chunks": 0, "created_at": ""}
        by_name[name]["documents"] = by_name[name].get("documents", 0) + 1
    # 知识库级 Embedding Provider（未指定则为全局默认）
    for item in by_name.values():
        item["embed_provider"] = _kb_provider_cfg(state, item.get("name", ""))
    out = sorted(by_name.values(), key=lambda x: x.get("name", ""))
    return {"items": out}


class CreateKbBody(BaseModel):
    name: str
    embed_provider: str | None = None


@router.post("/admin/kbs")
async def create_kb(request: Request, body: CreateKbBody):
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(body.name)
    await state.vs.create_kb(name)
    state.ingest.kb_doc_dir(name)
    if body.embed_provider:
        if body.embed_provider not in state.embeds:
            raise HTTPException(status_code=400, detail=f"Embedding Provider 不存在：{body.embed_provider}")
        _set_kb_provider_cfg(state, name, body.embed_provider)
    return {"ok": True, "name": name}


class SetKbProviderBody(BaseModel):
    embed_provider: str | None = None


@router.put("/admin/kbs/{name}/provider")
async def set_kb_provider(request: Request, name: str, body: SetKbProviderBody):
    """设置知识库使用的 Embedding Provider（None=全局默认）。维度变化时提示重建索引。"""
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(name)
    if body.embed_provider and body.embed_provider not in state.embeds:
        raise HTTPException(status_code=400, detail=f"Embedding Provider 不存在：{body.embed_provider}")
    old_pid = _kb_provider_cfg(state, name)
    old_dim = await state.vs.dim(name) if state.vs else 0
    _set_kb_provider_cfg(state, name, body.embed_provider)
    # 维度变化检测：目标 provider 的维度与知识库已索引维度不同 → 需要重建索引
    need_reindex = False
    target_dim = 0
    target_embed = state.embeds.get(body.embed_provider) if body.embed_provider else state.embed
    if target_embed is not None:
        target_dim = target_embed.dimensions or 0
    if old_dim and target_dim and old_dim != target_dim:
        need_reindex = True
    return {
        "ok": True,
        "name": name,
        "embed_provider": body.embed_provider,
        "old_provider": old_pid,
        "old_dim": old_dim,
        "new_dim": target_dim,
        "need_reindex": need_reindex,
        "message": (
            f"向量维度由 {old_dim} 变为 {target_dim}，请重建该知识库索引以应用新维度"
            if need_reindex
            else "已更新，该知识库后续索引与检索将使用新 Provider"
        ),
    }


@router.delete("/admin/kbs/{name}")
async def delete_kb(request: Request, name: str):
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(name)
    await state.vs.delete_kb(name)
    doc_dir = state.ingest.kb_doc_dir(name)
    if doc_dir.exists():
        shutil.rmtree(doc_dir, ignore_errors=True)
    _set_kb_provider_cfg(state, name, None)  # 移除 knowledge_bases 配置条目
    return {"ok": True}


@router.get("/admin/kbs/{name}/files")
async def kb_files(request: Request, name: str):
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(name)
    files = state.ingest.list_files(name) if state.ingest else []
    return {"items": files, "total": len(files)}


@router.delete("/admin/kbs/{name}/files/{file_id}")
async def delete_file(request: Request, name: str, file_id: str):
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(name)
    ok = await state.ingest.delete_file(name, file_id)
    if not ok:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"ok": True}


@router.get("/admin/kbs/{name}/chunks")
async def file_chunks(request: Request, name: str, file_id: str, page: int = 1, size: int = 20):
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(name)
    data = await state.vs.chunks_of_file(name, file_id, page, size)
    return data


@router.post("/admin/kbs/{name}/test")
async def kb_test(request: Request, name: str, body: dict[str, Any]):
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(name)
    query = str(body.get("query", ""))
    top_k = int(body.get("top_k", 5))
    threshold = float(body.get("score_threshold", 0.0))
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    results = await state.ingest.test_query(name, query, top_k, threshold)
    return {"items": results, "total": len(results)}


@router.get("/admin/kbs/{name}/tasks/{task_id}")
async def task_status(request: Request, name: str, task_id: str):
    require_admin(request)
    state = get_state(request)
    task = state.ingest.get_task(task_id) if state.ingest else None
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


# ---------- 上传 ----------
async def _save_uploaded(
    state, kb: str, files: list[UploadFile]
) -> tuple[list[dict], list[dict], int]:
    """保存上传文件，返回 (已保存记录, 拒绝记录, 总字节)。"""
    cfg = state.config.raw().get("upload", {}) or {}
    max_file = int(cfg.get("max_file_size_mb", 100)) * 1024 * 1024
    max_batch = int(cfg.get("max_batch_size_mb", 2048)) * 1024 * 1024
    max_files = int(cfg.get("max_files_per_batch", 500))
    allowed = cfg.get("allowed_extensions", [".md", ".txt", ".pdf", ".html", ".docx"])
    arch_ext = cfg.get("archive_extensions", [".zip", ".tar.gz", ".tgz", ".tar"])

    if len(files) > max_files:
        raise HTTPException(
            status_code=400,
            detail=f"单次上传文件数 {len(files)} 超过上限 {max_files}",
        )

    saved: list[dict] = []
    rejected: list[dict] = []
    total_bytes = 0

    # 压缩包单独放临时目录
    tmp_root = state.settings.data_dir / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp_task = tmp_root / uuid.uuid4().hex[:12]
    tmp_task.mkdir(parents=True, exist_ok=True)

    for f in files:
        original = f.filename or "unnamed"
        suffix = Path(original).suffix.lower()
        is_archive = archive_is_supported(original)
        if not is_archive and not _check_ext(suffix, allowed):
            rejected.append({"name": original, "reason": f"扩展名 {suffix or '(无)'} 不在白名单"})
            continue
        size = 0
        chunks_bytes = bytearray()
        oversized = False
        while True:
            chunk = await f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_file:
                rejected.append({"name": original, "reason": f"超过单文件上限 {cfg.get('max_file_size_mb', 100)} MB"})
                chunks_bytes = bytearray()
                oversized = True
                break
            chunks_bytes.extend(chunk)
        if size == 0:
            rejected.append({"name": original, "reason": "空文件"})
            continue
        if oversized:
            continue  # 超限文件只记拒绝，不入库
        total_bytes += size
        if total_bytes > max_batch:
            raise HTTPException(
                status_code=400,
                detail=f"批量上传总大小超过上限 {cfg.get('max_batch_size_mb', 2048)} MB",
            )
        if is_archive:
            arch_path = tmp_task / original
            arch_path.write_bytes(bytes(chunks_bytes))
            saved.append(
                {"file_id": uuid.uuid4().hex, "original_name": original, "stored_name": original,
                 "size": size, "is_archive": True, "tmp_path": str(arch_path), "status": "uploaded"}
            )
        else:
            rec = await state.ingest.save_upload(kb, original, bytes(chunks_bytes))
            saved.append({**rec, "is_archive": False})

    return saved, rejected, total_bytes


async def _extract_archives(state, kb: str, saved: list[dict]) -> tuple[list[dict], list[dict]]:
    """解压压缩包并保存解压出的文件，返回 (新增文件记录, 失败记录)。"""
    cfg = state.config.raw().get("upload", {}) or {}
    max_extract = int(cfg.get("max_extract_size_mb", 4096)) * 1024 * 1024
    allowed = cfg.get("allowed_extensions", [".md", ".txt", ".pdf", ".html", ".docx"])
    tmp_root = state.settings.data_dir / "tmp"
    new_files: list[dict] = []
    failures: list[dict] = []
    for rec in saved:
        if not rec.get("is_archive"):
            new_files.append(rec)
            continue
        arch_path = Path(rec["tmp_path"])
        extract_dir = tmp_root / uuid.uuid4().hex[:12]
        try:
            extracted = await asyncio.to_thread(extract_archive, arch_path, extract_dir, max_extract)
        except ArchiveError as e:
            failures.append({"name": rec["original_name"], "reason": f"解压失败：{e}"})
            continue
        for p in extracted:
            suffix = p.suffix.lower()
            if not _check_ext(suffix, allowed):
                failures.append({"name": p.name, "reason": f"扩展名 {suffix} 不在白名单（已跳过）"})
                continue
            data = p.read_bytes()
            if len(data) > int(cfg.get("max_file_size_mb", 100)) * 1024 * 1024:
                failures.append({"name": p.name, "reason": "超过单文件上限（已跳过）"})
                continue
            r = await state.ingest.save_upload(kb, p.name, data)
            new_files.append({**r, "is_archive": False})
        shutil.rmtree(extract_dir, ignore_errors=True)
    # 清理压缩包临时文件
    for rec in saved:
        if rec.get("is_archive"):
            try:
                Path(rec["tmp_path"]).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    return new_files, failures


@router.post("/admin/kbs/{name}/upload")
async def upload_files(
    request: Request,
    name: str,
    files: list[UploadFile] = File(...),
):
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(name)
    await state.vs.create_kb(name)  # 确保知识库存在

    # 上传并发限制：保护文件保存阶段，防止并发大文件上传打满资源
    sem = state.upload_sem or asyncio.Semaphore(4)
    async with sem:
        saved, rejected, _ = await _save_uploaded(state, name, files)
    if not saved:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "没有可接受的文件", "rejected": rejected},
        )

    task_id = state.ingest.new_task(name, "upload", len(saved))

    async def run() -> None:
        try:
            new_files, extract_failures = await _extract_archives(state, name, saved)
            rejected.extend(extract_failures)
            if not new_files:
                reason = extract_failures[0]["reason"] if extract_failures else "无可用文件"
                state.ingest.finish_task(task_id, "failed", error=reason)
                return
            total = len(new_files)
            state.ingest.update_task(task_id, total=total, kind="upload+index")
            done = 0
            for rec in new_files:
                fid = rec["file_id"]
                state.ingest.update_task(task_id, current=rec.get("original_name", fid), done=done)
                try:
                    await state.ingest.index_file(name, fid)
                except Exception as e:  # noqa: BLE001
                    log.exception("上传后索引失败 %s", fid)
                    state.ingest.update_task(task_id, failed=state.ingest.tasks[task_id]["failed"] + 1)
                    state.ingest.record_file(name, fid, status="failed", error=str(e))
                done += 1
                state.ingest.update_task(task_id, done=done)
            status = "done" if state.ingest.tasks[task_id]["failed"] == 0 else "partial"
            state.ingest.finish_task(task_id, status)
        except Exception as e:  # noqa: BLE001
            log.exception("上传任务异常")
            state.ingest.finish_task(task_id, "failed", error=str(e))

    asyncio.create_task(run())
    return {
        "ok": True,
        "task_id": task_id,
        "accepted": len(saved),
        "rejected": rejected,
    }


@router.post("/admin/kbs/{name}/reindex")
async def reindex(request: Request, name: str):
    require_admin(request)
    state = get_state(request)
    name = _check_kb_name(name)
    task_id = await state.ingest.rebuild_kb(name)
    return {"ok": True, "task_id": task_id}
