from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger

from app.chain.storage import StorageChain
from app.log import logger
from app.plugins import _PluginBase
from app.sdk.services import StorageHelper


class Metadata115Sync(_PluginBase):
    """把 NAS 本地已有的元数据增量同步到 MoviePilot 已配置的 115 存储。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "仅将本地已有、115 中不存在的元数据文件上传到 MP 已配置的 115，不使用 TMDB。"
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
        """初始化插件配置并重置本次运行状态。"""
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
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
        self._last_result = "已加载配置"

    def get_state(self) -> bool:
        """返回插件是否启用。"""
        return self._enabled

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        """返回插件命令注册信息。"""
        return []

    def get_api(self) -> list[dict[str, Any]]:
        """注册立即同步 API。"""
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
        """执行一次立即同步并返回统计结果。"""
        if not self._enabled:
            return {"success": False, "message": "插件未启用"}
        return {"success": True, "result": self.sync()}

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """返回 Vuetify JSON 配置表单。"""
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
                        "component": "VTextarea",
                        "props": {
                            "model": "mappings",
                            "label": "本地目录 → 115目录映射",
                            "rows": 5,
                            "placeholder": "/media/movies=/电影\n/media/tv=/电视剧",
                            "hint": "每行一个映射。左侧为 NAS 本地目录，右侧为 115 目标目录。使用 MP 已配置的 115 存储。",
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
            "mappings": "",
            "extensions": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
            "max_size_mb": 20,
            "parent_metadata": False,
            "interval_minutes": 60,
        }

    def get_page(self) -> list[dict]:
        """返回插件详情页状态信息。"""
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
                "component": "VAlert",
                "props": {
                    "type": "secondary",
                    "variant": "tonal",
                    "text": "本插件只执行 NAS → 115 单向补齐：115 已存在的文件跳过，不覆盖、不删除、不使用 TMDB。",
                },
            },
        ]

    def get_service(self) -> list[dict]:
        """注册按配置周期执行的自动同步服务。"""
        if not self._enabled:
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
        """停止插件服务并释放插件持有的运行状态。"""
        self._enabled = False

    @staticmethod
    def _normalize_remote_path(path: str) -> str:
        """规范化 115 远程路径。"""
        path = path.replace("\\", "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path.rstrip("/") or "/"

    def _extensions_set(self) -> set[str]:
        """解析允许同步的元数据扩展名集合。"""
        result: set[str] = set()
        for item in self._extensions.split(","):
            item = item.strip().lower()
            if item:
                result.add(item if item.startswith(".") else "." + item)
        return result

    def _parse_mappings(self) -> list[tuple[Path, str]]:
        """解析本地目录到 115 目录的映射配置。"""
        result: list[tuple[Path, str]] = []
        for raw in self._mappings.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            local, remote = line.split("=", 1)
            local = local.strip()
            remote = remote.strip()
            if local and remote:
                result.append(
                    (Path(local).expanduser().resolve(), self._normalize_remote_path(remote))
                )
        return result

    def _iter_metadata(self, root: Path):
        """遍历本地目录并筛选符合条件的元数据文件。"""
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
    def _storage_is_configured() -> bool:
        """检查 MoviePilot 是否已经配置 115 存储。"""
        return StorageHelper().get_storage("u115") is not None

    def _upload_one(self, local_file: Path, local_root: Path, remote_root: str) -> str:
        """检查远端文件并在缺失时通过 MoviePilot 存储链上传。"""
        relative = local_file.relative_to(local_root).as_posix()
        remote_path = self._normalize_remote_path(f"{remote_root}/{relative}")
        chain = StorageChain()

        # V3.0.0 的存储链提供按存储类型和路径查询文件项的公开操作。
        if chain.get_file_item(storage="u115", path=Path(remote_path)):
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
        """执行一次本地元数据到 115 的增量同步。"""
        stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0}
        if not self._enabled:
            return stats

        if not self._lock.acquire(blocking=False):
            self._last_result = "同步正在运行，本次跳过"
            return stats

        try:
            if not self._storage_is_configured():
                self._last_result = "MoviePilot 尚未配置 115 存储，请先在 MP 的存储设置中完成 115 授权。"
                return stats

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
                        if self._upload_one(local_file, local_root, remote_root) == "uploaded":
                            stats["uploaded"] += 1
                        else:
                            stats["skipped"] += 1
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

    def _sync_parent_metadata(
        self, local_root: Path, remote_root: str, stats: dict[str, int]
    ) -> None:
        """同步映射源目录的父目录中符合条件的元数据文件。"""
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
                if self._upload_one(path, parent, remote_parent) == "uploaded":
                    stats["uploaded"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as err:
                stats["failed"] += 1
                logger.exception(f"Metadata115Sync：父目录元数据失败 {path}: {err}")

