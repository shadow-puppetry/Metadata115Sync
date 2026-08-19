from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import schemas
from app.chain.storage import StorageChain
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase


class Metadata115Sync(_PluginBase):
    """将本地元数据单向同步到 MoviePilot 已配置的 115 存储。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "本地元数据单向同步到115，不使用TMDB。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/u115.png"
    plugin_version = "2.6.0"
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

    _running = False
    _stop_event = threading.Event()
    _worker: Optional[threading.Thread] = None

    def init_plugin(self, config: dict = None):
        config = config or {}

        self._enabled = bool(config.get("enabled", False))
        self._onlyonce = bool(config.get("onlyonce", False))
        self._mappings = str(config.get("mappings") or "")
        self._extensions = str(
            config.get("extensions") or ".nfo,.jpg,.jpeg,.png,.webp,.xml"
        )
        self._max_size_mb = max(1, self._to_int(config.get("max_size_mb"), 20))
        self._interval = max(5, self._to_int(config.get("interval"), 60))
        self._concurrency = min(4, max(1, self._to_int(config.get("concurrency"), 2)))
        self._cache_enabled = bool(config.get("cache_enabled", True))

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
            {
                "path": "/sync",
                "endpoint": self.sync_api,
                "methods": ["GET", "POST"],
                "summary": "立即同步",
            },
            {
                "path": "/scan",
                "endpoint": self.scan_api,
                "methods": ["GET"],
                "summary": "扫描预览",
            },
            {
                "path": "/stop",
                "endpoint": self.stop_api,
                "methods": ["GET", "POST"],
                "summary": "停止同步",
            },
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [{
            "component": "VForm",
            "content": [
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [{
                                "component": "VSwitch",
                                "props": {"model": "enabled", "label": "启用元数据同步"},
                            }],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [{
                                "component": "VSwitch",
                                "props": {"model": "onlyonce", "label": "立即运行一次"},
                            }],
                        },
                    ],
                },
                {
                    "component": "VTextarea",
                    "props": {
                        "model": "mappings",
                        "label": "本地目录 → 115目录映射",
                        "rows": 4,
                        "placeholder": "/strm=/影视库/媒体目录",
                    },
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [{
                                "component": "VTextField",
                                "props": {
                                    "model": "extensions",
                                    "label": "元数据扩展名",
                                    "placeholder": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
                                },
                            }],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [{
                                "component": "VTextField",
                                "props": {
                                    "model": "max_size_mb",
                                    "label": "单文件大小上限（MB）",
                                    "type": "number",
                                },
                            }],
                        },
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [{
                                "component": "VTextField",
                                "props": {
                                    "model": "interval",
                                    "label": "自动同步间隔（分钟）",
                                    "type": "number",
                                },
                            }],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [{
                                "component": "VTextField",
                                "props": {
                                    "model": "concurrency",
                                    "label": "上传并发（建议1-2）",
                                    "type": "number",
                                },
                            }],
                        },
                    ],
                },
                {
                    "component": "VSwitch",
                    "props": {"model": "cache_enabled", "label": "启用本地同步缓存"},
                },
            ],
        }], {
            "enabled": False,
            "onlyonce": False,
            "mappings": "",
            "extensions": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
            "max_size_mb": 20,
            "interval": 60,
            "concurrency": 2,
            "cache_enabled": True,
        }

    def get_page(self) -> List[dict]:
        status = self.get_data("status") or {}
        state = status.get("state", "空闲")
        current = int(status.get("current", 0) or 0)
        total = int(status.get("total", 0) or 0)
        percent = int(status.get("percent", 0) or 0)
        current_file = status.get("current_file", "")

        summary = [
            f"状态：{state}",
            f"进度：{current}/{total}（{percent}%）" if total else "进度：等待扫描",
            f"扫描文件：{status.get('scanned', 0)}",
            f"115已有：{status.get('existing', 0)}",
            f"待同步：{status.get('pending', 0)}",
            f"已上传：{status.get('uploaded', 0)}",
            f"缓存跳过：{status.get('skipped', 0)}",
            f"失败：{status.get('failed', 0)}",
        ]
        if current_file:
            summary.append(f"当前：{current_file}")
        if status.get("message"):
            summary.append(f"信息：{status['message']}")

        progress_props = {
            "model-value": percent,
            "height": 8,
            "rounded": True,
        }

        return [{
            "component": "div",
            "props": {"class": "d-flex flex-column gap-3"},
            "content": [
                {
                    "component": "VAlert",
                    "props": {"type": "info", "variant": "tonal"},
                    "text": "　".join(summary),
                },
                {"component": "VProgressLinear", "props": progress_props},
                {
                    "component": "div",
                    "props": {"class": "d-flex flex-wrap gap-2"},
                    "content": [
                        {
                            "component": "VBtn",
                            "props": {"color": "primary"},
                            "text": "立即同步",
                            "events": {"click": {
                                "api": "plugin/Metadata115Sync/sync",
                                "method": "post",
                                "params": {"apikey": settings.API_TOKEN},
                            }},
                        },
                        {
                            "component": "VBtn",
                            "props": {"variant": "tonal"},
                            "text": "扫描预览",
                            "events": {"click": {
                                "api": "plugin/Metadata115Sync/scan",
                                "method": "get",
                                "params": {"apikey": settings.API_TOKEN},
                            }},
                        },
                        {
                            "component": "VBtn",
                            "props": {"color": "error", "variant": "tonal"},
                            "text": "停止同步",
                            "events": {"click": {
                                "api": "plugin/Metadata115Sync/stop",
                                "method": "post",
                                "params": {"apikey": settings.API_TOKEN},
                            }},
                        },
                    ],
                },
            ],
        }]

    def stop_service(self):
        self.stop_api(settings.API_TOKEN)

    def _set_status(self, **kwargs):
        status = self.get_data("status") or {}
        status.update(kwargs)
        self.save_data("status", status)

    def _set_progress(self, current: int, total: int, *, state: str, current_file: str = "", **kwargs):
        percent = int(current * 100 / total) if total else 0
        self._set_status(
            current=current,
            total=total,
            percent=percent,
            current_file=current_file,
            state=state,
            **kwargs,
        )

    def _check_api_key(self, apikey: str) -> bool:
        return apikey == settings.API_TOKEN

    def sync_api(self, apikey: str = ""):
        if not self._check_api_key(apikey):
            return schemas.Response(success=False, message="API密钥错误")
        if self._running:
            return schemas.Response(success=True, message="同步任务已经在运行")
        self._start_worker("sync")
        return schemas.Response(success=True, message="同步任务已启动")

    def scan_api(self, apikey: str = ""):
        if not self._check_api_key(apikey):
            return schemas.Response(success=False, message="API密钥错误")
        if self._running:
            return schemas.Response(success=False, message="当前已有任务运行，请等待或停止")
        self._start_worker("scan")
        return schemas.Response(success=True, message="扫描任务已启动")

    def stop_api(self, apikey: str = ""):
        if apikey and not self._check_api_key(apikey):
            return schemas.Response(success=False, message="API密钥错误")
        if not self._running:
            self._set_status(state="空闲", message="当前没有运行中的同步任务")
            return schemas.Response(success=True, message="当前没有运行中的同步任务")
        self._stop_event.set()
        logger.info("Metadata115Sync：收到停止同步请求")
        self._set_status(state="停止中", message="停止请求已发送，当前上传操作结束后停止")
        return schemas.Response(success=True, message="已发送停止请求")

    def _start_worker(self, mode: str):
        if self._running:
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._worker_main,
            args=(mode,),
            name="Metadata115Sync",
            daemon=True,
        )
        self._worker.start()

    def _worker_main(self, mode: str):
        self._running = True
        try:
            if mode == "scan":
                self.scan_preview()
            else:
                self.sync()
        finally:
            self._running = False

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

        for current, _, files in os.walk(root):
            if self._stop_event.is_set():
                return
            for name in sorted(files):
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
                    logger.info("Metadata115Sync：文件超过大小限制，跳过 %s", path)
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
        return bool(
            item
            and item.get("size") == stat.st_size
            and int(item.get("mtime", 0)) == int(stat.st_mtime)
        )

    @staticmethod
    def _cache_mark(cache: dict, key: str, stat):
        cache[key] = {
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        }

    def _remote_dir_index(
        self, chain: StorageChain, folder: schemas.FileItem
    ) -> Dict[str, schemas.FileItem]:
        items = chain.list_files(folder, recursion=False) or []
        return {item.name: item for item in items if item.type == "file"}

    def _collect_local(self, root: Path):
        files = list(self._iter_local(root) or [])
        dirs = {root}
        for path, _ in files:
            dirs.add(path.parent)
        return files, sorted(dirs, key=lambda p: len(p.parts))

    def scan_preview(self):
        mappings = self._mappings_list()
        if not mappings:
            self._set_status(state="配置错误", scanned=0, existing=0, pending=0, uploaded=0, skipped=0, failed=0, current=0, total=0, percent=0, current_file="", message="没有配置有效的目录映射")
            logger.error("Metadata115Sync：没有配置有效的目录映射")
            return {"status": "invalid_mapping"}

        totals = {"scanned": 0, "existing": 0, "pending": 0, "uploaded": 0, "skipped": 0, "failed": 0}
        self._set_progress(0, 0, state="扫描中", **totals, message="正在扫描本地文件")
        logger.info("Metadata115Sync：开始扫描预览")

        # 第一阶段只扫描本地文件，建立候选集；这一阶段完全不访问115。
        candidates = []
        cache = self._load_cache()
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
                candidates.append((local, stat, root, remote_root, remote_dir, key))
                if totals["scanned"] % 100 == 0:
                    self._set_progress(totals["scanned"], 0, state="扫描本地文件", current_file=str(local), **totals, message="本地扫描中，尚未访问115")

        total_work = len(candidates)
        self._set_progress(0, total_work, state="检查115", **totals, message=f"本地扫描完成，待检查115：{total_work}")
        logger.info("Metadata115Sync：本地扫描完成，共 %d 个文件，缓存命中 %d 个，需检查115 %d 个", totals["scanned"], totals["skipped"], total_work)

        # 第二阶段只对缓存未命中的文件访问115，并按远程目录去重查询。
        chain = StorageChain()
        dir_groups = {}
        for item in candidates:
            dir_groups.setdefault(item[4], []).append(item)

        checked = 0
        remote_index = {}
        for remote_dir, items in dir_groups.items():
            if self._stop_event.is_set():
                break
            folder = chain.get_file_item("u115", Path(remote_dir))
            if folder and folder.type == "dir":
                remote_index[remote_dir] = self._remote_dir_index(chain, folder)
            else:
                remote_index[remote_dir] = {}
            for local, stat, root, remote_root, _, key in items:
                checked += 1
                if local.name in remote_index[remote_dir]:
                    totals["existing"] += 1
                    self._cache_mark(cache, key, stat)
                else:
                    totals["pending"] += 1
                if checked % 20 == 0 or checked == total_work:
                    self._set_progress(checked, total_work, state="检查115", current_file=str(local), **totals, message=f"正在检查115：{checked}/{total_work}")

        if self._cache_enabled:
            self._save_cache(cache)

        if self._stop_event.is_set():
            state, message = "已停止", "扫描预览已停止"
        else:
            state, message = "扫描完成", "扫描预览完成"
        self._set_progress(total_work if not self._stop_event.is_set() else checked, total_work, state=state, current_file="", **totals, message=message)
        logger.info("Metadata115Sync：扫描完成，文件 %d，缓存跳过 %d，115已有 %d，待同步 %d", totals["scanned"], totals["skipped"], totals["existing"], totals["pending"])
        return {"status": state, **totals}

    def _process_mapping(self, chain: StorageChain, root: Path, remote_root: str, cache: dict, totals: dict):
        if not root.is_dir():
            logger.error("Metadata115Sync：本地目录不存在：%s", root)
            return

        local_files, _ = self._collect_local(root)
        totals["scanned"] += len(local_files)

        # 关键优化：先用本地缓存过滤，缓存命中的文件不查询115。
        candidates = []
        for local, stat in local_files:
            if self._stop_event.is_set():
                return
            key = self._cache_key(local, root, remote_root)
            if self._cache_hit(cache, key, stat):
                totals["skipped"] += 1
                continue
            rel = local.parent.relative_to(root).as_posix()
            remote_dir = self._remote(remote_root if rel == "." else f"{remote_root}/{rel}")
            candidates.append((local, stat, remote_dir, key))

        # 只有真正需要检查的文件才触发115目录查询；每个目录一轮只查询一次。
        folder_cache = {}
        remote_index = {}
        dir_groups = {}
        for item in candidates:
            dir_groups.setdefault(item[2], []).append(item)

        for remote_dir in dir_groups:
            if self._stop_event.is_set():
                return
            folder = chain.get_file_item("u115", Path(remote_dir))
            if folder and folder.type == "dir":
                folder_cache[remote_dir] = folder
                remote_index[remote_dir] = self._remote_dir_index(chain, folder)
            else:
                remote_index[remote_dir] = {}

        pending = []
        for local, stat, remote_dir, key in candidates:
            if self._stop_event.is_set():
                return
            if local.name in remote_index.get(remote_dir, {}):
                totals["existing"] += 1
                self._cache_mark(cache, key, stat)
            else:
                pending.append((local, stat, remote_dir, key))

        totals["pending"] += len(pending)
        logger.info("Metadata115Sync：%s → %s：扫描 %d，缓存跳过 %d，115已有 %d，待同步 %d", root, remote_root, len(local_files), totals["skipped"], totals["existing"], len(pending))

        import concurrent.futures

        def upload_one(item):
            local, stat, remote_dir, key = item
            if self._stop_event.is_set():
                return False, key, "stopped"
            folder = folder_cache.get(remote_dir)
            if not folder:
                folder = chain.get_folder("u115", Path(remote_dir))
                if not folder:
                    return False, key, "folder"
            try:
                result = chain.upload_file(fileitem=folder, path=local, new_name=local.name)
                return bool(result), key, ""
            except Exception as exc:
                logger.error("Metadata115Sync：上传失败 %s：%s", local, exc)
                return False, key, str(exc)

        by_key = {item[3]: item for item in pending}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self._concurrency, thread_name_prefix="Metadata115Sync") as executor:
            futures = [executor.submit(upload_one, item) for item in pending]
            done = 0
            for future in concurrent.futures.as_completed(futures):
                ok, key, reason = future.result()
                item = by_key[key]
                done += 1
                if ok:
                    totals["uploaded"] += 1
                    self._cache_mark(cache, key, item[1])
                    logger.info("Metadata115Sync：上传成功 %s", item[0])
                elif reason == "stopped":
                    return
                else:
                    totals["failed"] += 1
                self._set_progress(done, len(pending), state="同步中", current_file=str(item[0]), **totals, message=f"正在同步：{done}/{len(pending)}")

    def sync(self):
        if self._running and threading.current_thread() is not self._worker:
            return {"status": "already_running"}

        if self._running is False:
            self._running = True
        self._stop_event.clear()

        mappings = self._mappings_list()
        if not mappings:
            self._running = False
            self._set_status(
                state="配置错误",
                scanned=0,
                existing=0,
                pending=0,
                uploaded=0,
                skipped=0,
                failed=0,
                message="没有配置有效的目录映射",
            )
            logger.error("Metadata115Sync：没有配置有效的目录映射")
            return {"status": "invalid_mapping"}

        totals = {
            "scanned": 0,
            "existing": 0,
            "pending": 0,
            "uploaded": 0,
            "skipped": 0,
            "failed": 0,
        }
        cache = self._load_cache()
        chain = StorageChain()

        self._set_progress(0, 0, state="同步中", current_file="", **totals, message="开始同步")
        logger.info("Metadata115Sync：开始同步")

        try:
            for root, remote_root in mappings:
                if self._stop_event.is_set():
                    break
                self._process_mapping(
                    chain, root, remote_root, cache, totals
                )

            if self._cache_enabled:
                self._save_cache(cache)

            if self._stop_event.is_set():
                state = "已停止"
                message = "同步已停止"
                logger.info("Metadata115Sync：同步已停止")
            else:
                state = "同步完成"
                message = "同步完成"
                logger.info(
                    "Metadata115Sync：同步完成：扫描 %d，已有 %d，待同步 %d，上传 %d，失败 %d",
                    totals["scanned"],
                    totals["existing"],
                    totals["pending"],
                    totals["uploaded"],
                    totals["failed"],
                )

            self._set_progress(totals.get("uploaded", 0), totals.get("pending", 0), state=state, current_file="", **totals, message=message)
            return {"status": state, **totals}
        except Exception as exc:
            logger.exception("Metadata115Sync：同步任务异常：%s", exc)
            self._set_status(state="异常", message=str(exc), **totals)
            return {"status": "error", **totals, "message": str(exc)}
        finally:
            self._running = False
            self._stop_event.clear()
