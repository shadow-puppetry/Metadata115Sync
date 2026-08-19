from __future__ import annotations

import concurrent.futures
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import schemas
from app.chain.storage import StorageChain
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase


class Metadata115Sync(_PluginBase):
    """本地元数据单向增量同步到 MoviePilot 已配置的 115 存储。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "本地元数据单向同步到115，不使用TMDB。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/u115.png"
    plugin_version = "2.7.0"
    plugin_author = "shadow-puppetry"
    author_url = ""
    plugin_config_prefix = "metadata115sync_"
    plugin_order = 50
    auth_level = 1

    _enabled = False
    _onlyonce = False
    _mappings = ""
    _extensions = ".nfo,.jpg,.jpeg,.png,.webp,.xml"
    _max_size_mb = 20
    _interval = 60
    _concurrency = 2
    _cache_enabled = True
    _remote_cache_ttl_hours = 6

    _running = False
    _stop_event = threading.Event()
    _worker: Optional[threading.Thread] = None
    _status_lock = threading.RLock()
    _status: Dict[str, Any] = {
        "state": "空闲",
        "current": 0,
        "total": 0,
        "percent": 0,
        "scanned": 0,
        "existing": 0,
        "pending": 0,
        "uploaded": 0,
        "skipped": 0,
        "failed": 0,
        "current_file": "",
        "current_dir": "",
        "message": "",
        "started_at": 0,
        "updated_at": 0,
    }
    _last_status_persist = 0.0

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._onlyonce = bool(config.get("onlyonce", False))
        self._mappings = str(config.get("mappings") or "")
        self._extensions = str(config.get("extensions") or self._extensions)
        self._max_size_mb = max(1, self._to_int(config.get("max_size_mb"), 20))
        self._interval = max(5, self._to_int(config.get("interval"), 60))
        self._concurrency = min(3, max(1, self._to_int(config.get("concurrency"), 2)))
        self._cache_enabled = bool(config.get("cache_enabled", True))
        self._remote_cache_ttl_hours = min(168, max(1, self._to_int(config.get("remote_cache_ttl_hours"), 6)))

        with self._status_lock:
            persisted = self.get_data("status") or {}
            if persisted:
                self._status.update(persisted)
                if self._status.get("state") in {"扫描中", "检查115", "同步中", "停止中"}:
                    self._status.update({"state": "空闲", "message": "MoviePilot 重载后任务已恢复为空闲", "current_file": "", "current_dir": ""})

        if self._onlyonce:
            self._onlyonce = False
            self._save_config()
            self._start_worker("sync")

    @staticmethod
    def _to_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _save_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "mappings": self._mappings,
            "extensions": self._extensions,
            "max_size_mb": self._max_size_mb,
            "interval": self._interval,
            "concurrency": self._concurrency,
            "cache_enabled": self._cache_enabled,
            "remote_cache_ttl_hours": self._remote_cache_ttl_hours,
        })

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        return [{
            "id": "Metadata115Sync",
            "name": "115元数据同步服务",
            "trigger": "interval",
            "func": self.sync,
            "kwargs": {"minutes": self._interval},
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {"path": "/sync", "endpoint": self.sync_api, "methods": ["GET"], "auth": "bear", "summary": "立即同步"},
            {"path": "/scan", "endpoint": self.scan_api, "methods": ["GET"], "auth": "bear", "summary": "扫描预览"},
            {"path": "/stop", "endpoint": self.stop_api, "methods": ["GET"], "auth": "bear", "summary": "停止任务"},
            {"path": "/status", "endpoint": self.status_api, "methods": ["GET"], "auth": "bear", "summary": "查询运行状态"},
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [{
            "component": "VForm",
            "content": [
                {"component": "VSwitch", "props": {"model": "enabled", "label": "启用元数据同步"}},
                {"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即运行一次"}},
                {"component": "VTextarea", "props": {"model": "mappings", "label": "本地目录 → 115目录映射", "rows": 4, "placeholder": "/strm=/影视库/媒体目录"}},
                {"component": "VTextField", "props": {"model": "extensions", "label": "元数据扩展名", "placeholder": ".nfo,.jpg,.jpeg,.png,.webp,.xml"}},
                {"component": "VTextField", "props": {"model": "max_size_mb", "label": "单文件大小上限（MB）", "type": "number"}},
                {"component": "VTextField", "props": {"model": "interval", "label": "自动同步间隔（分钟）", "type": "number"}},
                {"component": "VTextField", "props": {"model": "concurrency", "label": "上传并发（建议1-2）", "type": "number"}},
                {"component": "VTextField", "props": {"model": "remote_cache_ttl_hours", "label": "115目录缓存时间（小时）", "type": "number"}},
                {"component": "VSwitch", "props": {"model": "cache_enabled", "label": "启用本地同步缓存"}},
            ],
        }], {
            "enabled": False,
            "onlyonce": False,
            "mappings": "",
            "extensions": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
            "max_size_mb": 20,
            "interval": 60,
            "concurrency": 2,
            "remote_cache_ttl_hours": 6,
            "cache_enabled": True,
        }

    def get_page(self) -> List[dict]:
        status = self._get_status()
        summary = [
            f"状态：{status['state']}",
            f"进度：{status['current']}/{status['total']}（{status['percent']}%）" if status["total"] else "进度：—",
            f"扫描：{status['scanned']}",
            f"缓存跳过：{status['skipped']}",
            f"115已有：{status['existing']}",
            f"待同步：{status['pending']}",
            f"已上传：{status['uploaded']}",
            f"失败：{status['failed']}",
        ]
        if status.get("current_dir"):
            summary.append(f"目录：{status['current_dir']}")
        if status.get("current_file"):
            summary.append(f"当前：{status['current_file']}")
        if status.get("message"):
            summary.append(f"信息：{status['message']}")
        return [{
            "component": "div",
            "props": {"class": "d-flex flex-column gap-3"},
            "content": [
                {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "　".join(summary)}},
                {"component": "VProgressLinear", "props": {"model-value": status["percent"], "height": 8, "rounded": True}},
                {"component": "div", "props": {"class": "d-flex flex-wrap gap-2"}, "content": [
                    {"component": "VBtn", "props": {"color": "primary"}, "text": "立即同步", "events": {"click": {"api": "plugin/Metadata115Sync/sync", "method": "get"}}},
                    {"component": "VBtn", "props": {"variant": "tonal"}, "text": "扫描预览", "events": {"click": {"api": "plugin/Metadata115Sync/scan", "method": "get"}}},
                    {"component": "VBtn", "props": {"variant": "tonal"}, "text": "刷新状态", "events": {"click": {"api": "plugin/Metadata115Sync/status", "method": "get"}}},
                    {"component": "VBtn", "props": {"color": "error", "variant": "tonal"}, "text": "停止任务", "events": {"click": {"api": "plugin/Metadata115Sync/stop", "method": "get"}}},
                ]},
            ],
        }]

    def stop_service(self):
        self._stop_event.set()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=3)
        self._running = False
        self._set_status(state="空闲", message="插件已停用", current_file="", current_dir="", force=True)

    # ---------------- state ----------------
    def _get_status(self) -> Dict[str, Any]:
        with self._status_lock:
            return dict(self._status)

    def _set_status(self, force: bool = False, **kwargs):
        now = time.time()
        with self._status_lock:
            self._status.update(kwargs)
            self._status["updated_at"] = now
            snapshot = dict(self._status)
            should_persist = force or now - self._last_status_persist >= 1.0 or kwargs.get("state") in {"扫描完成", "同步完成", "已停止", "异常", "配置错误"}
            if should_persist:
                self._last_status_persist = now
        if should_persist:
            try:
                self.save_data("status", snapshot)
            except Exception as exc:
                logger.debug("Metadata115Sync：保存状态失败：%s", exc)

    def _set_progress(self, current: int, total: int, *, state: str, current_file: str = "", current_dir: str = "", force: bool = False, **kwargs):
        percent = min(100, int(current * 100 / total)) if total else (100 if state in {"扫描完成", "同步完成"} else 0)
        self._set_status(current=current, total=total, percent=percent, state=state, current_file=current_file, current_dir=current_dir, force=force, **kwargs)

    def _check_api_key(self, apikey: str) -> bool:
        return apikey == settings.API_TOKEN

    def status_api(self, apikey: str = ""):
        # bear 认证由宿主完成；兼容旧调用时仍允许 API_TOKEN。
        if apikey and not self._check_api_key(apikey):
            return schemas.Response(success=False, message="API密钥错误")
        return schemas.Response(success=True, data=self._get_status())

    def sync_api(self, apikey: str = ""):
        if apikey and not self._check_api_key(apikey):
            return schemas.Response(success=False, message="API密钥错误")
        if self._running:
            return schemas.Response(success=True, message="任务已经在运行")
        self._start_worker("sync")
        return schemas.Response(success=True, message="同步任务已启动")

    def scan_api(self, apikey: str = ""):
        if apikey and not self._check_api_key(apikey):
            return schemas.Response(success=False, message="API密钥错误")
        if self._running:
            return schemas.Response(success=False, message="当前已有任务运行，请先停止")
        self._start_worker("scan")
        return schemas.Response(success=True, message="扫描任务已启动")

    def stop_api(self, apikey: str = ""):
        if apikey and not self._check_api_key(apikey):
            return schemas.Response(success=False, message="API密钥错误")
        if not self._running:
            self._set_status(state="空闲", message="当前没有运行中的任务", force=True)
            return schemas.Response(success=True, message="当前没有运行中的任务")
        self._stop_event.set()
        self._set_status(state="停止中", message="停止请求已发送；当前115操作结束后停止", force=True)
        logger.info("Metadata115Sync：收到停止任务请求")
        return schemas.Response(success=True, message="已发送停止请求")

    def _start_worker(self, mode: str):
        if self._running:
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_main, args=(mode,), name="Metadata115Sync", daemon=True)
        self._worker.start()

    def _worker_main(self, mode: str):
        self._running = True
        try:
            if mode == "scan":
                self.scan_preview()
            else:
                self.sync()
        except Exception as exc:
            logger.exception("Metadata115Sync：后台任务异常：%s", exc)
            self._set_status(state="异常", message=str(exc), force=True)
        finally:
            self._running = False
            self._stop_event.clear()

    # ---------------- config / local scan ----------------
    @staticmethod
    def _remote(path: str) -> str:
        path = path.replace("\\", "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path.rstrip("/") or "/"

    def _mappings_list(self):
        result = []
        for line in self._mappings.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            local, remote = line.split("=", 1)
            local = local.strip()
            remote = remote.strip()
            if local and remote:
                result.append((Path(local).resolve(), self._remote(remote)))
        return result

    def _extensions_set(self) -> set[str]:
        result = set()
        for ext in self._extensions.split(","):
            ext = ext.strip().lower()
            if ext:
                result.add(ext if ext.startswith(".") else "." + ext)
        return result

    def _iter_local(self, root: Path):
        if not root.is_dir():
            return
        limit = self._max_size_mb * 1024 * 1024
        extensions = self._extensions_set()
        for current, dirs, files in os.walk(root):
            if self._stop_event.is_set():
                return
            dirs.sort()
            files.sort()
            for name in files:
                if self._stop_event.is_set():
                    return
                path = Path(current) / name
                if path.suffix.lower() not in extensions:
                    continue
                try:
                    stat = path.stat()
                except OSError as exc:
                    logger.warning("Metadata115Sync：读取文件失败 %s：%s", path, exc)
                    continue
                if stat.st_size > limit:
                    logger.info("Metadata115Sync：超过大小限制，跳过 %s", path)
                    continue
                yield path, stat

    def _load_cache(self) -> dict:
        return self.get_data("file_cache") or {}

    def _save_cache(self, cache: dict):
        if self._cache_enabled:
            self.save_data("file_cache", cache)

    @staticmethod
    def _cache_key(local: Path, root: Path, remote_root: str) -> str:
        return f"{remote_root}|{local.relative_to(root).as_posix()}"

    def _cache_hit(self, cache: dict, key: str, stat) -> bool:
        if not self._cache_enabled:
            return False
        item = cache.get(key)
        if not item or item.get("size") != stat.st_size or int(item.get("mtime_ns", 0)) != int(stat.st_mtime_ns):
            return False
        checked_at = float(item.get("checked_at", 0) or 0)
        return item.get("status") in {"present", "uploaded"} and (time.time() - checked_at) < self._remote_cache_ttl_hours * 3600

    @staticmethod
    def _cache_mark(cache: dict, key: str, stat, status: str = "present"):
        cache[key] = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "checked_at": time.time(), "status": status}

    def _load_remote_cache(self) -> dict:
        return self.get_data("remote_dir_cache") or {}

    def _save_remote_cache(self, cache: dict):
        if self._cache_enabled:
            self.save_data("remote_dir_cache", cache)

    def _remote_cache_fresh(self, item: dict) -> bool:
        return bool(item and time.time() - float(item.get("checked_at", 0) or 0) < self._remote_cache_ttl_hours * 3600)

    def _remote_dir_index(self, chain: StorageChain, folder: schemas.FileItem) -> Dict[str, schemas.FileItem]:
        items = chain.list_files(folder, recursion=False) or []
        return {item.name: item for item in items if item.type == "file" and item.name}

    def _remote_names(self, chain: StorageChain, remote_dir: str, remote_cache: dict, refresh: bool = False) -> Tuple[set[str], Optional[schemas.FileItem], bool]:
        cached = remote_cache.get(remote_dir)
        if self._cache_enabled and not refresh and self._remote_cache_fresh(cached):
            return set(cached.get("files", [])), None, True
        folder = chain.get_file_item("u115", Path(remote_dir))
        if not folder or folder.type != "dir":
            remote_cache[remote_dir] = {"checked_at": time.time(), "files": [], "missing": True}
            return set(), None, False
        index = self._remote_dir_index(chain, folder)
        remote_cache[remote_dir] = {"checked_at": time.time(), "files": sorted(index.keys()), "missing": False}
        return set(index.keys()), folder, False

    def _mapping_fingerprint(self) -> str:
        raw = "\n".join(f"{a}|{b}" for a, b in self._mappings_list())
        raw += f"|{self._extensions}|{self._max_size_mb}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    # ---------------- plan ----------------
    def _save_plan(self, entries: list[dict]):
        self.save_data("sync_plan", {"created_at": time.time(), "fingerprint": self._mapping_fingerprint(), "entries": entries})

    def _load_plan(self) -> Optional[dict]:
        plan = self.get_data("sync_plan") or None
        if not plan or plan.get("fingerprint") != self._mapping_fingerprint():
            return None
        return plan

    def _clear_plan(self):
        try:
            self.del_data("sync_plan")
        except Exception:
            pass

    def _collect_candidates(self, cache: dict, totals: dict, plan_only: bool = False):
        candidates = []
        seen = set()
        mappings = self._mappings_list()
        for root, remote_root in mappings:
            if self._stop_event.is_set():
                break
            if not root.is_dir():
                logger.error("Metadata115Sync：本地目录不存在：%s", root)
                continue
            for local, stat in self._iter_local(root) or []:
                totals["scanned"] += 1
                key = self._cache_key(local, root, remote_root)
                if self._cache_hit(cache, key, stat):
                    totals["skipped"] += 1
                    continue
                rel = local.parent.relative_to(root).as_posix()
                remote_dir = self._remote(remote_root if rel == "." else f"{remote_root}/{rel}")
                dedupe = (str(local), remote_dir)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                candidates.append((local, stat, remote_dir, key))
                if totals["scanned"] % 250 == 0:
                    self._set_progress(totals["scanned"], 0, state="扫描本地", current_file=str(local), current_dir=remote_dir, **totals, message="只扫描本地文件，尚未访问115")
        return candidates

    def scan_preview(self):
        mappings = self._mappings_list()
        if not mappings:
            self._set_status(state="配置错误", scanned=0, existing=0, pending=0, uploaded=0, skipped=0, failed=0, current=0, total=0, percent=0, message="没有配置有效的目录映射", force=True)
            logger.error("Metadata115Sync：没有配置有效的目录映射")
            return {"status": "invalid_mapping"}

        totals = {"scanned": 0, "existing": 0, "pending": 0, "uploaded": 0, "skipped": 0, "failed": 0}
        self._set_progress(0, 0, state="扫描本地", force=True, started_at=time.time(), **totals, message="开始扫描本地文件")
        logger.info("Metadata115Sync：开始扫描预览")
        cache = self._load_cache()
        candidates = self._collect_candidates(cache, totals)
        if self._stop_event.is_set():
            self._set_status(state="已停止", message="扫描在本地阶段被停止", force=True)
            return {"status": "stopped", **totals}

        total_work = len(candidates)
        self._set_progress(0, total_work, state="检查115", **totals, message=f"本地扫描完成；待检查115：{total_work}")
        logger.info("Metadata115Sync：本地扫描完成，共 %d 个文件，缓存命中 %d 个，需检查115 %d 个", totals["scanned"], totals["skipped"], total_work)

        chain = StorageChain()
        remote_cache = self._load_remote_cache()
        dir_groups: Dict[str, list] = {}
        for item in candidates:
            dir_groups.setdefault(item[2], []).append(item)

        plan_entries = []
        checked = 0
        remote_api_dirs = 0
        for remote_dir, items in dir_groups.items():
            if self._stop_event.is_set():
                break
            names, _, cache_used = self._remote_names(chain, remote_dir, remote_cache)
            if not cache_used:
                remote_api_dirs += 1
            for local, stat, _, key in items:
                checked += 1
                if local.name in names:
                    totals["existing"] += 1
                    self._cache_mark(cache, key, stat, "present")
                else:
                    totals["pending"] += 1
                    plan_entries.append({"path": str(local), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "remote_dir": remote_dir, "cache_key": key})
                if checked % 50 == 0 or checked == total_work:
                    self._set_progress(checked, total_work, state="检查115", current_file=str(local), current_dir=remote_dir, **totals, message=f"检查115：{checked}/{total_work}；远程目录查询：{remote_api_dirs}")

        if self._cache_enabled:
            self._save_cache(cache)
            self._save_remote_cache(remote_cache)
        self._save_plan(plan_entries)

        if self._stop_event.is_set():
            state, message = "已停止", "扫描预览已停止；已保存当前缓存"
        else:
            state, message = "扫描完成", f"扫描完成；待同步 {len(plan_entries)} 个"
        self._set_progress(checked, total_work, state=state, current_file="", current_dir="", force=True, **totals, message=message)
        logger.info("Metadata115Sync：扫描完成：本地 %d，缓存跳过 %d，115已有 %d，待同步 %d，远程目录实际查询 %d", totals["scanned"], totals["skipped"], totals["existing"], totals["pending"], remote_api_dirs)
        return {"status": state, **totals}

    # ---------------- sync ----------------
    def _plan_is_valid(self, plan: dict) -> bool:
        for item in plan.get("entries", []):
            path = Path(item.get("path", ""))
            try:
                stat = path.stat()
            except OSError:
                return False
            if int(stat.st_size) != int(item.get("size", -1)) or int(stat.st_mtime_ns) != int(item.get("mtime_ns", -1)):
                return False
        return True

    def _build_plan_for_sync(self, cache: dict, totals: dict):
        # 只在没有可用扫描计划时重新做一次完整规划。
        self._set_progress(0, 0, state="扫描本地", force=True, **totals, message="没有可复用扫描计划，重新建立同步计划")
        candidates = self._collect_candidates(cache, totals)
        chain = StorageChain()
        remote_cache = self._load_remote_cache()
        dir_groups: Dict[str, list] = {}
        for item in candidates:
            dir_groups.setdefault(item[2], []).append(item)
        entries = []
        checked = 0
        for remote_dir, items in dir_groups.items():
            if self._stop_event.is_set():
                break
            names, _, _ = self._remote_names(chain, remote_dir, remote_cache)
            for local, stat, _, key in items:
                checked += 1
                if local.name in names:
                    totals["existing"] += 1
                    self._cache_mark(cache, key, stat, "present")
                else:
                    totals["pending"] += 1
                    entries.append({"path": str(local), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "remote_dir": remote_dir, "cache_key": key})
                if checked % 50 == 0:
                    self._set_progress(checked, len(candidates), state="检查115", current_file=str(local), current_dir=remote_dir, **totals, message=f"建立同步计划：{checked}/{len(candidates)}")
        if self._cache_enabled:
            self._save_cache(cache)
            self._save_remote_cache(remote_cache)
        self._save_plan(entries)
        return entries

    def sync(self):
        if self._running and threading.current_thread() is not self._worker:
            return {"status": "already_running"}
        self._running = True
        self._stop_event.clear()
        totals = {"scanned": 0, "existing": 0, "pending": 0, "uploaded": 0, "skipped": 0, "failed": 0}
        cache = self._load_cache()
        plan = self._load_plan()
        try:
            if plan and self._plan_is_valid(plan):
                entries = list(plan.get("entries", []))
                logger.info("Metadata115Sync：复用最近扫描计划，待同步 %d 个", len(entries))
                # 计划复用时扫描统计不重复计数；当前任务只执行计划。
            else:
                entries = self._build_plan_for_sync(cache, totals)
            totals["pending"] = len(entries)
            if self._stop_event.is_set():
                self._set_status(state="已停止", message="同步在上传前被停止", force=True)
                return {"status": "stopped", **totals}
            if not entries:
                self._set_progress(0, 0, state="同步完成", force=True, **totals, message="没有需要同步的文件")
                self._clear_plan()
                return {"status": "completed", **totals}

            chain = StorageChain()
            folder_cache: Dict[str, schemas.FileItem] = {}
            for remote_dir in sorted({e["remote_dir"] for e in entries}):
                if self._stop_event.is_set():
                    break
                folder = chain.get_folder("u115", Path(remote_dir))
                if folder:
                    folder_cache[remote_dir] = folder
                else:
                    logger.error("Metadata115Sync：无法获取/创建115目录：%s", remote_dir)
                    for e in entries:
                        if e["remote_dir"] == remote_dir:
                            totals["failed"] += 1
                    entries = [e for e in entries if e["remote_dir"] in folder_cache]

            total_upload = len(entries)
            self._set_progress(0, total_upload, state="同步中", **totals, message=f"准备上传：{total_upload} 个")

            def upload_one(entry):
                if self._stop_event.is_set():
                    return entry, False, "stopped"
                folder = folder_cache.get(entry["remote_dir"])
                if not folder:
                    return entry, False, "folder"
                path = Path(entry["path"])
                try:
                    result = chain.upload_file(fileitem=folder, path=path, new_name=path.name)
                    return entry, bool(result), ""
                except Exception as exc:
                    return entry, False, str(exc)

            by_key = {e["cache_key"]: e for e in entries}
            with concurrent.futures.ThreadPoolExecutor(max_workers=self._concurrency, thread_name_prefix="Metadata115Sync") as executor:
                futures = [executor.submit(upload_one, e) for e in entries]
                done = 0
                for future in concurrent.futures.as_completed(futures):
                    entry, ok, reason = future.result()
                    done += 1
                    if ok:
                        totals["uploaded"] += 1
                        stat = Path(entry["path"]).stat()
                        self._cache_mark(cache, entry["cache_key"], stat, "uploaded")
                        logger.info("Metadata115Sync：上传成功 %s", entry["path"])
                    elif reason == "stopped":
                        logger.info("Metadata115Sync：收到停止请求，停止继续上传")
                        break
                    else:
                        totals["failed"] += 1
                        logger.error("Metadata115Sync：上传失败 %s：%s", entry["path"], reason)
                    self._set_progress(done, total_upload, state="同步中", current_file=entry["path"], current_dir=entry["remote_dir"], **totals, message=f"上传：{done}/{total_upload}")
                    if done % 25 == 0 and self._cache_enabled:
                        self._save_cache(cache)

            if self._cache_enabled:
                self._save_cache(cache)
            self._clear_plan()

            if self._stop_event.is_set():
                state, message = "已停止", f"同步已停止；成功上传 {totals['uploaded']} 个"
            else:
                state, message = "同步完成", f"同步完成；成功上传 {totals['uploaded']} 个，失败 {totals['failed']} 个"
            self._set_progress(min(totals["uploaded"] + totals["failed"], total_upload), total_upload, state=state, current_file="", current_dir="", force=True, **totals, message=message)
            logger.info("Metadata115Sync：%s", message)
            return {"status": state, **totals}
        except Exception as exc:
            logger.exception("Metadata115Sync：同步任务异常：%s", exc)
            self._set_status(state="异常", message=str(exc), **totals, force=True)
            return {"status": "error", **totals, "message": str(exc)}
        finally:
            self._running = False

