from __future__ import annotations

import os
import threading
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Tuple

from app import schemas
from app.chain.storage import StorageChain
from app.log import logger
from app.plugins import _PluginBase

lock = Lock()


class Metadata115Sync(_PluginBase):
    plugin_name = "Metadata115Sync"
    plugin_desc = "本地元数据单向同步到115，不使用TMDB。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/u115.png"
    plugin_version = "2.4.0"
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
    _stop_event = Event()
    _running = False
    _status = "空闲"

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._onlyonce = bool(config.get("onlyonce", False))
        self._mappings = str(config.get("mappings") or "")
        self._extensions = str(config.get("extensions") or ".nfo,.jpg,.jpeg,.png,.webp,.xml")
        self._max_size_mb = self._integer(config.get("max_size_mb"), 20, 1, 10000)
        self._interval = self._integer(config.get("interval"), 60, 5, 10080)
        self._concurrency = self._integer(config.get("concurrency"), 2, 1, 4)
        self._cache_enabled = bool(config.get("cache_enabled", True))
        self._stop_event.clear()
        if self._onlyonce:
            self._save_config(False)
            threading.Thread(target=self.sync, name="Metadata115Sync-once", daemon=True).start()

    @staticmethod
    def _integer(value, default, minimum, maximum):
        try:
            return min(maximum, max(minimum, int(value)))
        except (TypeError, ValueError):
            return default

    def _save_config(self, onlyonce=None):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce if onlyonce is None else onlyonce,
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
        return [{"id": "Metadata115Sync", "name": "115元数据同步服务", "trigger": "interval", "func": self.sync, "kwargs": {"minutes": self._interval}}]

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {"path": "/sync", "endpoint": self.sync_api, "methods": ["GET", "POST"], "summary": "立即同步"},
            {"path": "/scan", "endpoint": self.scan_api, "methods": ["GET"], "summary": "扫描预览"},
            {"path": "/stop", "endpoint": self.stop_api, "methods": ["GET", "POST"], "summary": "停止同步"},
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [{"component": "VForm", "content": [
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用元数据同步"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即运行一次"}}]},
            ]},
            {"component": "VTextarea", "props": {"model": "mappings", "label": "本地目录 → 115目录映射", "rows": 4, "placeholder": "/strm=/影视库/媒体目录"}},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "extensions", "label": "元数据扩展名"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "max_size_mb", "label": "单文件大小上限（MB）", "type": "number"}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "interval", "label": "自动同步间隔（分钟）", "type": "number"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "concurrency", "label": "上传并发（建议1-2）", "type": "number"}}]},
            ]},
            {"component": "VSwitch", "props": {"model": "cache_enabled", "label": "启用本地同步缓存"}},
        ]}], {"enabled": False, "onlyonce": False, "mappings": "", "extensions": ".nfo,.jpg,.jpeg,.png,.webp,.xml", "max_size_mb": 20, "interval": 60, "concurrency": 2, "cache_enabled": True}

    def get_page(self) -> List[dict]:
        # 不重复实现日志；日志全部进入 MoviePilot 原生日志。
        return [{"component": "div", "props": {"class": "d-flex flex-column gap-3"}, "content": [
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": f"状态：{self._status}"}},
            {"component": "div", "props": {"class": "d-flex flex-wrap gap-2"}, "content": [
                {"component": "VBtn", "props": {"color": "primary"}, "text": "立即同步", "events": {"click": {"api": "plugin/Metadata115Sync/sync", "method": "post"}}},
                {"component": "VBtn", "props": {"variant": "tonal"}, "text": "扫描预览", "events": {"click": {"api": "plugin/Metadata115Sync/scan", "method": "get"}}},
                {"component": "VBtn", "props": {"color": "error", "variant": "tonal"}, "text": "停止同步", "events": {"click": {"api": "plugin/Metadata115Sync/stop", "method": "post"}}},
            ]},
        ]}]

    def stop_service(self):
        self._stop_event.set()

    def sync_api(self):
        return schemas.Response(success=True, data=self.sync(), message=self._status)

    def scan_api(self):
        return schemas.Response(success=True, data=self.scan_preview(), message=self._status)

    def stop_api(self):
        self._stop_event.set()
        logger.info("Metadata115Sync：收到停止请求，当前文件操作结束后停止后续任务")
        return schemas.Response(success=True, message="已发送停止请求")

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
            if local.strip() and remote.strip():
                result.append((Path(local.strip()).resolve(), self._remote(remote.strip())))
        return result

    def _extensions_set(self):
        return {e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower() for e in self._extensions.split(",") if e.strip()}

    def _iter_local(self, root: Path):
        if not root.is_dir():
            logger.error("Metadata115Sync：本地目录不存在：%s", root)
            return
        limit = self._max_size_mb * 1024 * 1024
        exts = self._extensions_set()
        for current, _, files in os.walk(root):
            if self._stop_event.is_set():
                return
            for name in files:
                if self._stop_event.is_set():
                    return
                path = Path(current) / name
                if path.suffix.lower() not in exts:
                    continue
                try:
                    stat = path.stat()
                except OSError as e:
                    logger.warning("Metadata115Sync：读取失败 %s：%s", path, e)
                    continue
                if stat.st_size <= limit:
                    yield path, stat

    def _cache(self):
        return self.get_data("file_cache") or {}

    def _cache_key(self, local, root, remote):
        return f"{remote}|{local.relative_to(root).as_posix()}"

    def _cache_hit(self, cache, key, stat):
        item = cache.get(key)
        return self._cache_enabled and item and item.get("size") == stat.st_size and item.get("mtime") == int(stat.st_mtime)

    def _mark_cache(self, cache, key, stat):
        if self._cache_enabled:
            cache[key] = {"size": stat.st_size, "mtime": int(stat.st_mtime)}

    def _prepare(self, chain, root, remote_root, local_files):
        dirs = {p.parent for p, _ in local_files}
        dirs.add(root)
        folders = {}
        for local_dir in sorted(dirs, key=lambda p: len(p.parts)):
            if self._stop_event.is_set():
                return folders, {}
            rel = local_dir.relative_to(root).as_posix()
            remote = self._remote(remote_root if rel == "." else f"{remote_root}/{rel}")
            folder = chain.get_folder(storage="u115", path=Path(remote))
            if folder:
                folders[remote] = folder
        index = {}
        for remote, folder in folders.items():
            if self._stop_event.is_set():
                break
            index[remote] = {x.name: x for x in (chain.list_files(folder, recursion=False) or []) if x.type == "file"}
        return folders, index

    def _process(self, chain, root, remote_root, cache):
        local_files = list(self._iter_local(root) or [])
        folders, remote_index = self._prepare(chain, root, remote_root, local_files)
        pending = []
        for local, stat in local_files:
            if self._stop_event.is_set():
                return
            rel = local.parent.relative_to(root).as_posix()
            remote_dir = self._remote(remote_root if rel == "." else f"{remote_root}/{rel}")
            key = self._cache_key(local, root, remote_root)
            if self._cache_hit(cache, key, stat):
                continue
            if local.name in remote_index.get(remote_dir, {}):
                self._mark_cache(cache, key, stat)
                logger.info("Metadata115Sync：115已有，跳过 %s", local)
                continue
            pending.append((local, stat, remote_dir, key))
        logger.info("Metadata115Sync：%s 扫描 %d 个文件，待上传 %d 个", root, len(local_files), len(pending))

        import concurrent.futures
        def upload(item):
            local, stat, remote_dir, key = item
            if self._stop_event.is_set():
                return False, key, stat
            folder = folders.get(remote_dir)
            if not folder:
                logger.error("Metadata115Sync：目标目录不存在：%s", remote_dir)
                return False, key, stat
            try:
                ok = chain.upload_file(fileitem=folder, path=local, new_name=local.name)
                if ok:
                    logger.info("Metadata115Sync：上传成功 %s", local)
                    return True, key, stat
            except Exception as e:
                logger.error("Metadata115Sync：上传失败 %s：%s", local, e)
            return False, key, stat

        with concurrent.futures.ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            futures = [pool.submit(upload, item) for item in pending]
            for future in concurrent.futures.as_completed(futures):
                ok, key, stat = future.result()
                if ok:
                    self._mark_cache(cache, key, stat)
                if self._stop_event.is_set():
                    logger.info("Metadata115Sync：停止请求已生效")
                    break

    def _run(self, preview=False):
        if not lock.acquire(blocking=False):
            logger.warning("Metadata115Sync：已有任务正在运行")
            return {"status": "already_running"}
        self._running = True
        self._stop_event.clear()
        self._status = "扫描中" if preview else "同步中"
        try:
            mappings = self._mappings_list()
            if not mappings:
                self._status = "未配置有效映射"
                logger.error("Metadata115Sync：未配置有效映射")
                return {"status": self._status}
            chain = StorageChain()
            cache = self._cache()
            total = 0
            for root, remote in mappings:
                if self._stop_event.is_set():
                    break
                if preview:
                    count = sum(1 for _ in self._iter_local(root) or [])
                    total += count
                    logger.info("Metadata115Sync：扫描 %s，发现 %d 个文件", root, count)
                else:
                    self._process(chain, root, remote, cache)
            if not preview and self._cache_enabled:
                self.save_data("file_cache", cache)
            if self._stop_event.is_set():
                self._status = "已停止"
                logger.info("Metadata115Sync：任务已停止")
                return {"status": "stopped"}
            self._status = "扫描完成" if preview else "同步完成"
            logger.info("Metadata115Sync：%s", self._status)
            return {"status": self._status, "scanned": total} if preview else {"status": self._status}
        finally:
            self._running = False
            lock.release()

    def sync(self):
        return self._run(False)

    def scan_preview(self):
        return self._run(True)
