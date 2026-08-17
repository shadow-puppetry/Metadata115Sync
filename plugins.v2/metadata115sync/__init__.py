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
    """将 NAS 本地已有的元数据单向补齐到 MoviePilot 已配置的 115。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "仅把本地已有、115 中不存在的元数据上传到 MP 已配置的 115，不使用 TMDB。"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "1.0.0"
    plugin_author = "OpenAI"
    plugin_label = "元数据同步"
    plugin_config_prefix = "metadata115sync_"
    plugin_order = 50
    auth_level = 1

    _enabled = False
    _mappings = ""
    _extensions = ".nfo,.jpg,.jpeg,.png,.webp,.xml"
    _max_size_mb = 20
    _parent_metadata = False
    _interval_minutes = 60
    _last_result = "尚未执行同步"
    _lock = threading.Lock()

    def init_plugin(self, config: dict | None = None) -> None:
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._mappings = str(config.get("mappings") or "").strip()
        self._extensions = str(config.get("extensions") or self._extensions)
        try:
            self._max_size_mb = max(1, int(config.get("max_size_mb") or 20))
        except (TypeError, ValueError):
            self._max_size_mb = 20
        self._parent_metadata = bool(config.get("parent_metadata", False))
        try:
            self._interval_minutes = max(5, int(config.get("interval_minutes") or 60))
        except (TypeError, ValueError):
            self._interval_minutes = 60
        self._last_result = "已加载配置"

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        return []

    def get_api(self) -> list[dict[str, Any]]:
        return [{
            "path": "/sync",
            "endpoint": self.sync_api,
            "methods": ["POST"],
            "summary": "立即同步本地元数据到115",
            "description": "执行一次 NAS → 115 单向元数据补齐。",
        }]

    def sync_api(self):
        if not self._enabled:
            return {"success": False, "message": "插件未启用"}
        return {"success": True, "result": self.sync()}

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "使用 MoviePilot 已配置的 115 存储，仅执行本地 → 115 单向补齐；115 已存在的文件会跳过。",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {"model": "enabled", "label": "启用元数据同步"},
                    },
                    {
                        "component": "VTextarea",
                        "props": {
                            "model": "mappings",
                            "label": "本地目录 → 115目录映射",
                            "rows": 5,
                            "placeholder": "/media/movies=/电影\n/media/tv=/电视剧",
                            "hint": "每行一个映射；左侧必须是 MoviePilot 容器内可访问的本地路径。",
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
                            "label": "元数据大小上限（MB）",
                            "type": "number",
                            "min": 1,
                            "max": 1024,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {"model": "parent_metadata", "label": "父目录元数据同步"},
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "interval_minutes",
                            "label": "自动同步间隔（分钟）",
                            "type": "number",
                            "min": 5,
                            "max": 10080,
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "mappings": "",
            "extensions": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
            "max_size_mb": 20,
            "parent_metadata": False,
            "interval_minutes": 60,
        }

    def get_page(self) -> list[dict]:
        return [
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": self._last_result}},
            {"component": "VAlert", "props": {"type": "secondary", "variant": "tonal", "text": "不会使用 TMDB，不上传视频，不覆盖已有文件，不删除 115 文件。"}},
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

    @staticmethod
    def _normalize_remote_path(path: str) -> str:
        path = path.replace("\\", "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path.rstrip("/") or "/"

    def _extensions_set(self) -> set[str]:
        result: set[str] = set()
        for item in self._extensions.split(","):
            item = item.strip().lower()
            if item:
                result.add(item if item.startswith(".") else "." + item)
        return result

    def _parse_mappings(self) -> list[tuple[Path, str]]:
        result: list[tuple[Path, str]] = []
        for raw in self._mappings.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            local, remote = line.split("=", 1)
            local, remote = local.strip(), remote.strip()
            if local and remote:
                result.append((Path(local).expanduser().resolve(), self._normalize_remote_path(remote)))
        return result

    def _iter_metadata(self, root: Path):
        if not root.is_dir():
            return
        extensions = self._extensions_set()
        limit = self._max_size_mb * 1024 * 1024
        for current, _, files in os.walk(root):
            for name in files:
                path = Path(current) / name
                if path.suffix.lower() not in extensions:
                    continue
                try:
                    if path.stat().st_size <= limit:
                        yield path
                except OSError:
                    continue

    @staticmethod
    def _remote_item(chain: StorageChain, path: str):
        # V2 115 存储类型由宿主的 u115 模块提供。
        return chain.get_file_item(storage="u115", path=Path(path))

    def _upload_one(self, local_file: Path, local_root: Path, remote_root: str) -> str:
        relative = local_file.relative_to(local_root).as_posix()
        remote_path = self._normalize_remote_path(f"{remote_root}/{relative}")
        chain = StorageChain()

        if self._remote_item(chain, remote_path):
            return "skipped"

        parent = chain.get_folder(storage="u115", path=Path(remote_path).parent)
        if not parent:
            raise RuntimeError(f"无法获取或创建 115 目录：{Path(remote_path).parent}")

        uploaded = chain.upload_file(
            fileitem=parent,
            path=local_file,
            new_name=local_file.name,
        )
        if not uploaded:
            raise RuntimeError(f"115 上传失败：{local_file}")
        return "uploaded"

    def sync(self) -> dict[str, int]:
        stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0}
        if not self._enabled:
            return stats
        if not self._lock.acquire(blocking=False):
            self._last_result = "同步正在运行，本次跳过"
            return stats
        try:
            mappings = self._parse_mappings()
            if not mappings:
                self._last_result = "请先配置本地目录 → 115目录映射。"
                return stats

            for local_root, remote_root in mappings:
                if not local_root.is_dir():
                    logger.warning(f"Metadata115Sync：本地目录不存在，跳过：{local_root}")
                    continue

                for local_file in self._iter_metadata(local_root):
                    stats["scanned"] += 1
                    try:
                        result = self._upload_one(local_file, local_root, remote_root)
                        stats[result] += 1
                    except Exception as err:
                        stats["failed"] += 1
                        logger.exception(f"Metadata115Sync：处理失败 {local_file}: {err}")

                if self._parent_metadata:
                    self._sync_parent_metadata(local_root, remote_root, stats)

            self._last_result = (
                f"同步完成：扫描 {stats['scanned']}，上传 {stats['uploaded']}，"
                f"已存在跳过 {stats['skipped']}，失败 {stats['failed']}"
            )
            return stats
        finally:
            self._lock.release()

    def _sync_parent_metadata(self, local_root: Path, remote_root: str, stats: dict[str, int]) -> None:
        parent = local_root.parent
        if not parent.is_dir():
            return
        extensions = self._extensions_set()
        limit = self._max_size_mb * 1024 * 1024
        remote_parent = self._normalize_remote_path(str(Path(remote_root).parent))

        for path in parent.iterdir():
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            try:
                if path.stat().st_size > limit:
                    continue
                stats["scanned"] += 1
                result = self._upload_one(path, parent, remote_parent)
                stats[result] += 1
            except Exception as err:
                stats["failed"] += 1
                logger.exception(f"Metadata115Sync：父目录元数据失败 {path}: {err}")
