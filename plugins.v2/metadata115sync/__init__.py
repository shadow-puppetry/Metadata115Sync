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
    """本地元数据单向补齐到 MoviePilot 已配置的 115。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "只把本地已有、115没有的元数据上传到已配置的115，不使用TMDB。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/u115.png"
    plugin_version = "2.1.0"
    plugin_author = "shadow-puppetry"
    plugin_label = "元数据同步到115"
    plugin_config_prefix = "metadata115sync_"
    plugin_order = 50
    auth_level = 1

    _enabled = False
    _mappings = ""
    _extensions = ".nfo,.jpg,.jpeg,.png,.webp,.xml"
    _max_size_mb = 20
    _interval_minutes = 60

    _lock = threading.Lock()
    _running = False
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
        if not self._enabled:
            return {"success": False, "message": "插件未启用"}
        return {"success": True, "data": self.sync()}

    def api_scan(self):
        return {"success": True, "data": self.scan_preview()}

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用元数据同步"}},
                    {
                        "component": "VTextarea",
                        "props": {
                            "model": "mappings",
                            "label": "本地目录 → 115目录映射",
                            "rows": 4,
                            "placeholder": "/strm=/影视库/媒体目录",
                            "hint": "每行一个映射；左侧必须是MoviePilot容器内可访问的实际路径。",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "extensions",
                            "label": "元数据扩展名",
                            "hint": "默认：.nfo,.jpg,.jpeg,.png,.webp,.xml",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "max_size_mb",
                            "label": "单文件大小上限（MB）",
                            "type": "number",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "interval_minutes",
                            "label": "自动同步间隔（分钟）",
                            "type": "number",
                        },
                    },
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
        s = self._stats
        lines = [
            f"状态：{self._status}",
            f"扫描：{s['scanned']}    上传：{s['uploaded']}    115已存在：{s['skipped']}",
            f"失败：{s['failed']}    忽略：{s['ignored']}",
        ]
        log_text = "\n".join(self._logs[-100:]) if self._logs else "暂无执行日志。"
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "\n".join(lines),
                },
            },
            {
                "component": "VBtn",
                "props": {
                    "color": "primary",
                    "variant": "elevated",
                    "text": "立即同步",
                },
                "events": {
                    "click": {
                        "api": "plugin/Metadata115Sync/sync",
                        "method": "post",
                    }
                },
            },
            {
                "component": "VBtn",
                "props": {
                    "color": "secondary",
                    "variant": "outlined",
                    "text": "扫描预览",
                },
                "events": {
                    "click": {
                        "api": "plugin/Metadata115Sync/scan",
                        "method": "get",
                    }
                },
            },
            {
                "component": "VTextarea",
                "props": {
                    "label": "最近执行日志",
                    "rows": 18,
                    "readonly": True,
                    "model": "log_text",
                    "model-value": log_text,
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
        out = set()
        for x in self._extensions.split(","):
            x = x.strip().lower()
            if x:
                out.add(x if x.startswith(".") else "." + x)
        return out

    def _mappings_list(self) -> list[tuple[Path, str]]:
        result = []
        for line in self._mappings.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            local, remote = line.split("=", 1)
            local = local.strip()
            remote = remote.strip()
            if local and remote:
                result.append((Path(local).expanduser().resolve(), self._remote(remote)))
        return result

    def _iter_files(self, root: Path):
        if not root.is_dir():
            return
        limit = self._max_size_mb * 1024 * 1024
        exts = self._exts()
        for current, _, files in os.walk(root):
            for filename in sorted(files):
                path = Path(current) / filename
                if path.suffix.lower() not in exts:
                    continue
                try:
                    size = path.stat().st_size
                except OSError as e:
                    self._stats["failed"] += 1
                    self._log(f"[失败] 无法读取：{path}：{e}", "error")
                    continue
                if size > limit:
                    self._stats["ignored"] += 1
                    self._log(f"[忽略] 超过大小限制：{path} ({size / 1024 / 1024:.1f}MB)")
                    continue
                yield path

    def scan_preview(self) -> dict:
        self._stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._logs = []
        mappings = self._mappings_list()
        self._log("开始扫描预览：不会访问或修改115")
        if not mappings:
            self._status = "未配置有效映射"
            self._log("请配置，例如：/strm=/影视库/媒体目录", "error")
            return self._stats

        for root, remote in mappings:
            self._log(f"扫描：{root} → {remote}")
            if not root.is_dir():
                self._stats["failed"] += 1
                self._log(f"[失败] 本地目录不存在：{root}", "error")
                continue
            for path in self._iter_files(root):
                self._stats["scanned"] += 1
                self._log(f"[发现] {path}")
        self._status = "扫描完成"
        self._log(
            f"扫描完成：共发现 {self._stats['scanned']} 个元数据文件，"
            f"忽略 {self._stats['ignored']}，失败 {self._stats['failed']}"
        )
        return self._stats

    def _upload_one(self, chain: StorageChain, path: Path, root: Path, remote_root: str) -> str:
        relative = path.relative_to(root).as_posix()
        remote_path = self._remote(f"{remote_root}/{relative}")

        if chain.get_file_item(storage="u115", path=Path(remote_path)):
            self._log(f"[跳过] 115已存在：{remote_path}")
            return "skipped"

        parent = chain.get_folder(storage="u115", path=Path(remote_path).parent)
        if not parent:
            raise RuntimeError(f"115目标目录不存在或无法访问：{Path(remote_path).parent}")

        result = chain.upload_file(fileitem=parent, path=path, new_name=path.name)
        if not result:
            raise RuntimeError("MoviePilot 115 上传接口返回失败")

        self._log(f"[上传] {path} → {remote_path}")
        return "uploaded"

    def sync(self) -> dict:
        if not self._enabled:
            self._status = "插件未启用"
            return self._stats

        if not self._lock.acquire(blocking=False):
            self._status = "已有任务正在运行"
            return self._stats

        self._running = True
        self._stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._logs = []
        try:
            mappings = self._mappings_list()
            self._log("开始同步：本地 → 115")
            if not mappings:
                self._status = "未配置有效映射"
                self._log("请配置，例如：/strm=/影视库/媒体目录", "error")
                return self._stats

            chain = StorageChain()
            for root, remote in mappings:
                self._log(f"处理：{root} → {remote}")
                if not root.is_dir():
                    self._stats["failed"] += 1
                    self._log(f"[失败] 本地目录不存在：{root}", "error")
                    continue
                for path in self._iter_files(root):
                    self._stats["scanned"] += 1
                    try:
                        result = self._upload_one(chain, path, root, remote)
                        self._stats[result] += 1
                    except Exception as e:
                        self._stats["failed"] += 1
                        self._log(f"[失败] {path}：{e}", "error")

            self._status = (
                f"同步完成：扫描 {self._stats['scanned']}，"
                f"上传 {self._stats['uploaded']}，"
                f"跳过 {self._stats['skipped']}，"
                f"失败 {self._stats['failed']}，"
                f"忽略 {self._stats['ignored']}"
            )
            self._log(self._status)
            return self._stats
        finally:
            self._running = False
            self._lock.release()
