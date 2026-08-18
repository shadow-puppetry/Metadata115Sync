from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger

from app.chain.storage import StorageChain
from app.log import logger
from app.plugins import _PluginBase


class Metadata115Sync(_PluginBase):
    """将本地已有、115不存在的元数据单向补齐到115。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "只上传本地已有且115不存在的元数据，不使用TMDB。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/u115.png"
    plugin_version = "2.2.0"
    plugin_author = "shadow-puppetry"
    plugin_label = "115元数据同步"
    plugin_config_prefix = "metadata115sync_"
    plugin_order = 50
    auth_level = 1

    _enabled = False
    _mappings = ""
    _extensions = ".nfo,.jpg,.jpeg,.png,.webp,.xml"
    _max_size_mb = 20
    _interval_minutes = 60
    _running = False
    _lock = threading.Lock()
    _stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
    _logs: list[str] = []
    _status = "尚未执行"

    def init_plugin(self, config: dict | None = None) -> None:
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._mappings = str(config.get("mappings") or "").strip()
        self._extensions = str(config.get("extensions") or self._extensions)
        try:
            self._max_size_mb = max(1, int(config.get("max_size_mb") or 20))
        except (TypeError, ValueError):
            self._max_size_mb = 20
        try:
            self._interval_minutes = max(5, int(config.get("interval_minutes") or 60))
        except (TypeError, ValueError):
            self._interval_minutes = 60

    def get_state(self) -> bool:
        return self._enabled

    def get_api(self) -> list[dict]:
        return [
            {
                "path": "/sync",
                "endpoint": self.api_sync,
                "methods": ["POST"],
                "summary": "立即同步",
                "description": "执行一次本地元数据到115的单向补齐",
            },
            {
                "path": "/scan",
                "endpoint": self.api_scan,
                "methods": ["GET"],
                "summary": "扫描预览",
                "description": "只扫描本地，不上传115",
            },
        ]

    def api_sync(self):
        return {"success": True, "data": self.sync()}

    def api_scan(self):
        return {"success": True, "data": self.scan_preview()}

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用元数据同步"}},
                    {"component": "VTextarea", "props": {
                        "model": "mappings", "label": "本地目录 → 115目录映射",
                        "rows": 4, "placeholder": "/strm=/影视库/媒体目录"
                    }},
                    {"component": "VTextField", "props": {
                        "model": "extensions", "label": "元数据扩展名"
                    }},
                    {"component": "VTextField", "props": {
                        "model": "max_size_mb", "label": "单文件大小上限（MB）", "type": "number"
                    }},
                    {"component": "VTextField", "props": {
                        "model": "interval_minutes", "label": "自动同步间隔（分钟）", "type": "number"
                    }},
                ],
            }
        ], {
            "enabled": False,
            "mappings": "",
            "extensions": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
            "max_size_mb": 20,
            "interval_minutes": 60,
        }

    def get_page(self) -> list[dict]:
        stats = self._stats
        status = (
            f"状态：{self._status}\n"
            f"扫描：{stats['scanned']}    上传：{stats['uploaded']}    "
            f"115已存在：{stats['skipped']}    失败：{stats['failed']}    "
            f"忽略：{stats['ignored']}"
        )
        logs = "\n".join(self._logs[-100:]) if self._logs else "暂无执行日志。"

        return [
            {
                "component": "VAlert",
                "props": {"type": "info", "variant": "tonal", "text": status},
            },
            {
                "component": "VDialogCloseBtn",
                "props": {"text": "立即同步"},
                "events": {
                    "click": {
                        "api": "plugin/Metadata115Sync/sync",
                        "method": "post",
                        "params": {},
                    }
                },
            },
            {
                "component": "VDialogCloseBtn",
                "props": {"text": "扫描预览"},
                "events": {
                    "click": {
                        "api": "plugin/Metadata115Sync/scan",
                        "method": "get",
                        "params": {},
                    }
                },
            },
            {
                "component": "VTextarea",
                "props": {
                    "label": "最近执行日志",
                    "rows": 18,
                    "readonly": True,
                    "model": "logs",
                    "model-value": logs,
                },
            },
        ]

    def get_service(self) -> list[dict]:
        if not self._enabled:
            return []
        return [{
            "id": "Metadata115Sync.Sync",
            "name": "Metadata115Sync 自动同步",
            "trigger": IntervalTrigger(minutes=self._interval_minutes),
            "func": self.sync,
            "kwargs": {},
        }]

    def stop_service(self) -> None:
        self._enabled = False

    def _log(self, text: str, level: str = "info") -> None:
        self._logs.append(text)
        self._logs = self._logs[-200:]
        if level == "error":
            logger.error(f"Metadata115Sync：{text}")
        elif level == "warning":
            logger.warning(f"Metadata115Sync：{text}")
        else:
            logger.info(f"Metadata115Sync：{text}")

    @staticmethod
    def _remote(path: str) -> str:
        path = path.replace("\\", "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path.rstrip("/") or "/"

    def _exts(self) -> set[str]:
        result = set()
        for item in self._extensions.split(","):
            item = item.strip().lower()
            if item:
                result.add(item if item.startswith(".") else "." + item)
        return result

    def _mappings_list(self) -> list[tuple[Path, str]]:
        result = []
        for raw in self._mappings.splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            local, remote = raw.split("=", 1)
            if local.strip() and remote.strip():
                result.append((
                    Path(local.strip()).expanduser().resolve(),
                    self._remote(remote.strip()),
                ))
        return result

    def _iter_metadata(self, root: Path):
        if not root.is_dir():
            return
        limit = self._max_size_mb * 1024 * 1024
        for current, _, files in os.walk(root):
            for name in sorted(files):
                path = Path(current) / name
                if path.suffix.lower() not in self._exts():
                    continue
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    self._stats["failed"] += 1
                    self._log(f"[失败] 无法读取：{path}：{exc}", "error")
                    continue
                if size > limit:
                    self._stats["ignored"] += 1
                    self._log(f"[忽略] 超过大小限制：{path}")
                    continue
                yield path

    def scan_preview(self) -> dict[str, int]:
        self._stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._logs = []
        self._status = "扫描中"
        mappings = self._mappings_list()
        self._log("开始扫描预览（不会访问或修改115）")
        if not mappings:
            self._status = "未配置有效映射"
            self._log("请配置，例如：/strm=/影视库/媒体目录", "error")
            return self._stats

        for root, remote in mappings:
            self._log(f"[目录] {root} → {remote}")
            if not root.is_dir():
                self._stats["failed"] += 1
                self._log(f"[失败] 本地目录不存在：{root}", "error")
                continue
            for path in self._iter_metadata(root):
                self._stats["scanned"] += 1
                self._log(f"[发现] {path}")

        self._status = "扫描完成"
        self._log(
            f"扫描完成：发现 {self._stats['scanned']} 个文件，"
            f"忽略 {self._stats['ignored']}，失败 {self._stats['failed']}"
        )
        return self._stats

    def _upload_one(self, chain: StorageChain, local_file: Path,
                    local_root: Path, remote_root: str) -> str:
        relative = local_file.relative_to(local_root).as_posix()
        remote_path = self._remote(f"{remote_root}/{relative}")

        if chain.get_file_item(storage="u115", path=Path(remote_path)):
            self._log(f"[跳过] 115已有：{remote_path}")
            return "skipped"

        parent = chain.get_folder(storage="u115", path=Path(remote_path).parent)
        if not parent:
            raise RuntimeError(f"115目标目录不存在或无法访问：{Path(remote_path).parent}")

        if not chain.upload_file(fileitem=parent, path=local_file, new_name=local_file.name):
            raise RuntimeError("115上传接口返回失败")

        self._log(f"[上传] {local_file} → {remote_path}")
        return "uploaded"

    def sync(self) -> dict[str, int]:
        if not self._enabled:
            self._status = "插件未启用"
            self._log("插件未启用", "warning")
            return self._stats

        if not self._lock.acquire(blocking=False):
            self._status = "已有同步任务正在运行"
            self._log("已有同步任务正在运行，本次跳过", "warning")
            return self._stats

        self._running = True
        self._stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._logs = []
        try:
            mappings = self._mappings_list()
            self._status = "同步中"
            self._log("开始执行：本地 → 115")
            if not mappings:
                self._status = "未配置有效映射"
                self._log("请配置，例如：/strm=/影视库/媒体目录", "error")
                return self._stats

            chain = StorageChain()
            for root, remote in mappings:
                self._log(f"[目录] {root} → {remote}")
                if not root.is_dir():
                    self._stats["failed"] += 1
                    self._log(f"[失败] 本地目录不存在：{root}", "error")
                    continue
                for path in self._iter_metadata(root):
                    self._stats["scanned"] += 1
                    try:
                        result = self._upload_one(chain, path, root, remote)
                        self._stats[result] += 1
                    except Exception as exc:
                        self._stats["failed"] += 1
                        self._log(f"[失败] {path}：{exc}", "error")

            self._status = (
                f"同步完成：扫描 {self._stats['scanned']}，上传 {self._stats['uploaded']}，"
                f"跳过 {self._stats['skipped']}，失败 {self._stats['failed']}，"
                f"忽略 {self._stats['ignored']}"
            )
            self._log(self._status)
            return self._stats
        finally:
            self._running = False
            self._lock.release()
