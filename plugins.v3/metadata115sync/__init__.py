from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger

from app import schemas
from app.chain.storage import StorageChain
from app.log import logger
from app.plugins import _PluginBase


class Metadata115Sync(_PluginBase):
    """本地元数据 -> MoviePilot 已配置 115 存储的增量同步插件。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "仅把本地存在、115不存在的元数据文件增量上传到MP已配置的115网盘。"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "1.0.0"
    plugin_author = "OpenAI"
    author_url = "https://github.com/jxxghp/MoviePilot-Plugins"
    plugin_config_prefix = "metadata115sync_"
    plugin_order = 50
    auth_level = 1

    _enabled = False
    _storage = "u115"
    _mappings = ""
    _extensions = ".nfo,.jpg,.jpeg,.png,.webp,.xml"
    _max_size_mb = 20
    _parent_metadata = False
    _interval_minutes = 60
    _running = False
    _last_result = "尚未执行同步"
    _lock = threading.Lock()

    def init_plugin(self, config: dict | None = None) -> None:
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._storage = str(config.get("storage") or "u115").strip()
        self._mappings = str(config.get("mappings") or "").strip()
        self._extensions = str(
            config.get("extensions") or ".nfo,.jpg,.jpeg,.png,.webp,.xml"
        )
        try:
            self._max_size_mb = max(1, int(config.get("max_size_mb") or 20))
        except (TypeError, ValueError):
            self._max_size_mb = 20
        self._parent_metadata = bool(config.get("parent_metadata", False))
        try:
            self._interval_minutes = max(5, int(config.get("interval_minutes") or 60))
        except (TypeError, ValueError):
            self._interval_minutes = 60
        self._running = False
        self._last_result = "已加载配置"

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        return []

    def get_api(self) -> list[dict[str, Any]]:
        return [
            {
                "path": "/sync",
                "endpoint": self.sync_api,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "立即同步本地元数据到115",
            }
        ]

    def sync_api(self):
        if not self._enabled:
            return {"success": False, "message": "插件未启用"}
        result = self.sync()
        return {"success": True, "result": result}

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "enabled",
                            "label": "启用元数据同步",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "storage",
                            "label": "115存储名称",
                            "hint": "使用MoviePilot里已经配置的115存储。默认是 u115。",
                        },
                    },
                    {
                        "component": "VTextarea",
                        "props": {
                            "model": "mappings",
                            "label": "本地目录 → 115目录映射",
                            "rows": 5,
                            "placeholder": "/media/movies=/电影\n/media/tv=/电视剧",
                            "hint": "每行一个映射，左边必须是NAS本地路径，右边是115目标目录。",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "extensions",
                            "label": "元数据扩展名",
                            "hint": "例如 .nfo,.jpg,.jpeg,.png,.webp,.xml",
                        },
                    },
                    {
                        "component": "VNumberInput",
                        "props": {
                            "model": "max_size_mb",
                            "label": "元数据大小上限（MB）",
                            "min": 1,
                            "max": 1024,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "parent_metadata",
                            "label": "父目录元数据同步",
                            "hint": "开启后同时检查映射源目录的上一级目录中的元数据文件。",
                        },
                    },
                    {
                        "component": "VNumberInput",
                        "props": {
                            "model": "interval_minutes",
                            "label": "自动同步间隔（分钟）",
                            "min": 5,
                            "max": 10080,
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "storage": "u115",
            "mappings": "",
            "extensions": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
            "max_size_mb": 20,
            "parent_metadata": False,
            "interval_minutes": 60,
        }

    def get_page(self) -> list[dict]:
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": self._last_result,
                },
            },
            {
                "component": "VBtn",
                "props": {
                    "text": "立即同步",
                    "color": "primary",
                    "href": f"/api/v1/plugin/{self.__class__.__name__}/sync",
                    "method": "POST",
                },
            },
        ]

    def get_service(self) -> list[dict]:
        if not self.get_state():
            return []
        return [
            {
                "id": "Metadata115Sync.Sync",
                "name": "Metadata115Sync 自动同步",
                "trigger": IntervalTrigger(minutes=self._interval_minutes),
                "func": self.sync,
                "kwargs": {},
            }
        ]

    def stop_service(self) -> None:
        self._enabled = False

    def _parse_mappings(self) -> list[tuple[Path, str]]:
        result: list[tuple[Path, str]] = []
        for raw in self._mappings.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            local, remote = line.split("=", 1)
            local = local.strip()
            remote = remote.strip()
            if not local or not remote:
                continue
            result.append((Path(local).expanduser().resolve(), self._norm_remote(remote)))
        return result

    @staticmethod
    def _norm_remote(path: str) -> str:
        path = path.replace("\\", "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path.rstrip("/") or "/"

    def _extensions_set(self) -> set[str]:
        return {
            e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower()
            for e in self._extensions.split(",")
            if e.strip()
        }

    def _iter_metadata(self, source: Path):
        if not source.exists() or not source.is_dir():
            return
        max_bytes = self._max_size_mb * 1024 * 1024
        exts = self._extensions_set()

        for current, dirs, files in os.walk(source):
            current_path = Path(current)
            for name in files:
                p = current_path / name
                if p.suffix.lower() not in exts:
                    continue
                try:
                    if p.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                yield p

    def _remote_path(self, local_file: Path, local_root: Path, remote_root: str) -> str:
        relative = local_file.relative_to(local_root).as_posix()
        return self._norm_remote(f"{remote_root}/{relative}")

    def _upload_one(self, local_file: Path, remote_path: str) -> tuple[str, str]:
        chain = StorageChain()
        remote_file = chain.get_file_item(
            storage=self._storage,
            path=Path(remote_path),
        )
        if remote_file:
            return "skip", remote_path

        remote_parent = Path(remote_path).parent
        folder = chain.get_folder(
            storage=self._storage,
            path=remote_parent,
        )
        if not folder:
            return "error", f"无法创建/获取115目录：{remote_parent}"

        uploaded = chain.upload_file(
            fileitem=folder,
            path=local_file,
            new_name=local_file.name,
        )
        if not uploaded:
            return "error", str(local_file)
        return "upload", remote_path

    def sync(self) -> dict[str, int]:
        if not self._enabled:
            return {"scanned": 0, "metadata": 0, "uploaded": 0, "skipped": 0, "failed": 0}

        if not self._lock.acquire(blocking=False):
            return {"scanned": 0, "metadata": 0, "uploaded": 0, "skipped": 0, "failed": 0}

        self._running = True
        stats = {"scanned": 0, "metadata": 0, "uploaded": 0, "skipped": 0, "failed": 0}
        try:
            mappings = self._parse_mappings()
            if not mappings:
                self._last_result = "没有配置目录映射：请填写“本地目录=/115目录”"
                return stats

            for local_root, remote_root in mappings:
                if not local_root.exists() or not local_root.is_dir():
                    logger.warning(f"Metadata115Sync：本地目录不存在，跳过：{local_root}")
                    continue

                files = list(self._iter_metadata(local_root))
                stats["scanned"] += len(files)

                for local_file in files:
                    stats["metadata"] += 1
                    try:
                        remote_path = self._remote_path(
                            local_file, local_root, remote_root
                        )
                        status, _ = self._upload_one(local_file, remote_path)
                        if status == "upload":
                            stats["uploaded"] += 1
                        elif status == "skip":
                            stats["skipped"] += 1
                        else:
                            stats["failed"] += 1
                    except Exception as err:
                        stats["failed"] += 1
                        logger.exception(
                            f"Metadata115Sync：上传失败 {local_file}: {err}"
                        )

                if self._parent_metadata:
                    parent = local_root.parent
                    for p in parent.iterdir():
                        if not p.is_file() or p.suffix.lower() not in self._extensions_set():
                            continue
                        try:
                            if p.stat().st_size > self._max_size_mb * 1024 * 1024:
                                continue
                            stats["metadata"] += 1
                            remote_path = self._norm_remote(
                                f"{Path(remote_root).parent}/{p.name}"
                            )
                            status, _ = self._upload_one(p, remote_path)
                            if status == "upload":
                                stats["uploaded"] += 1
                            elif status == "skip":
                                stats["skipped"] += 1
                            else:
                                stats["failed"] += 1
                        except Exception as err:
                            stats["failed"] += 1
                            logger.exception(
                                f"Metadata115Sync：父目录文件上传失败 {p}: {err}"
                            )

            self._last_result = (
                f"同步完成：扫描 {stats['scanned']} 个元数据文件，"
                f"上传 {stats['uploaded']}，已存在跳过 {stats['skipped']}，"
                f"失败 {stats['failed']}"
            )
            return stats
        finally:
            self._running = False
            self._lock.release()
