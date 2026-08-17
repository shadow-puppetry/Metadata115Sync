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
    """把本地已有元数据单向补齐到 MoviePilot 已配置的 115。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "本地已有、115没有的元数据，按目录映射单向上传到115。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/refs/heads/v2/src/assets/images/misc/u115.png"
    plugin_version = "1.3.0"
    plugin_author = "shadow-puppetry"
    plugin_label = "115元数据同步"
    plugin_config_prefix = "metadata115sync_"
    plugin_order = 50
    auth_level = 1

    _enabled = False
    _mappings = ""
    _extensions = ".nfo,.jpg,.jpeg,.png,.webp,.xml"
    _max_size_mb = 20
    _parent_metadata = False
    _interval_minutes = 60

    _running = False
    _lock = threading.Lock()
    _stats: dict[str, int] = {}
    _logs: list[str] = []
    _last_status = "尚未执行"
    _last_error = ""

    def init_plugin(self, config: dict | None = None) -> None:
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._mappings = str(config.get("mappings") or "").strip()
        self._extensions = str(config.get("extensions") or ".nfo,.jpg,.jpeg,.png,.webp,.xml")
        try:
            self._max_size_mb = max(1, int(config.get("max_size_mb") or 20))
        except (TypeError, ValueError):
            self._max_size_mb = 20
        self._parent_metadata = bool(config.get("parent_metadata", False))
        try:
            self._interval_minutes = max(5, int(config.get("interval_minutes") or 60))
        except (TypeError, ValueError):
            self._interval_minutes = 60

        self._logs = []
        self._stats = {}
        self._last_status = "配置已加载"
        self._last_error = ""

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
                "summary": "立即同步",
                "description": "立即执行一次本地元数据到115的单向补齐。",
            },
            {
                "path": "/preview",
                "endpoint": self.preview_api,
                "methods": ["GET"],
                "summary": "扫描预览",
                "description": "只扫描本地文件，不访问或修改115。",
            },
        ]

    def sync_api(self):
        if not self._enabled:
            return {"success": False, "message": "插件未启用，请先打开“启用元数据同步”。"}
        result = self.sync()
        return {"success": True, "result": result}

    def preview_api(self):
        result = self.preview()
        return {"success": True, "result": result}

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
                            "text": "只做本地 → 115 单向补齐：本地有、115没有才上传；115已有则跳过。不使用TMDB，不上传视频，不删除或覆盖115文件。",
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
                            "rows": 4,
                            "placeholder": "/strm=/影视库/媒体目录",
                            "hint": "每行一个映射。例：/strm=/影视库/媒体目录；左侧必须是MP容器内实际可访问的本地目录。",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "extensions",
                            "label": "元数据扩展名",
                            "hint": "逗号分隔，例如 .nfo,.jpg,.jpeg,.png,.webp,.xml",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "max_size_mb",
                            "label": "单个元数据大小上限（MB）",
                            "type": "number",
                            "min": 1,
                            "max": 1024,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "parent_metadata",
                            "label": "同步父目录元数据",
                        },
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
        stats = self._stats or {
            "scanned": 0,
            "uploaded": 0,
            "skipped": 0,
            "failed": 0,
            "ignored": 0,
        }
        status = (
            f"状态：{'运行中' if self._running else self._last_status}\n"
            f"扫描：{stats.get('scanned', 0)}  |  "
            f"上传：{stats.get('uploaded', 0)}  |  "
            f"已存在跳过：{stats.get('skipped', 0)}  |  "
            f"失败：{stats.get('failed', 0)}  |  "
            f"忽略：{stats.get('ignored', 0)}"
        )
        logs = "\n".join(self._logs[-80:]) or "暂无执行日志。"

        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info" if not self._running else "warning",
                    "variant": "tonal",
                    "text": status,
                },
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 6},
                        "content": [
                            {
                                "component": "VBtn",
                                "props": {
                                    "color": "primary",
                                    "block": True,
                                    "disabled": self._running,
                                    "text": "立即同步",
                                },
                                "events": {
                                    "click": {
                                        "api": "plugin/Metadata115Sync/sync",
                                        "method": "post",
                                    }
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 6},
                        "content": [
                            {
                                "component": "VBtn",
                                "props": {
                                    "variant": "outlined",
                                    "block": True,
                                    "disabled": self._running,
                                    "text": "扫描预览（不上传）",
                                },
                                "events": {
                                    "click": {
                                        "api": "plugin/Metadata115Sync/preview",
                                        "method": "get",
                                    }
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VTextarea",
                "props": {
                    "model": "logs",
                    "label": "最近执行日志",
                    "rows": 18,
                    "readonly": True,
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

    @staticmethod
    def _normalize_remote(path: str) -> str:
        path = path.replace("\\", "/").strip()
        if not path:
            return "/"
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path.rstrip("/") or "/"

    def _extensions_set(self) -> set[str]:
        result = set()
        for item in self._extensions.split(","):
            item = item.strip().lower()
            if item:
                result.add(item if item.startswith(".") else "." + item)
        return result

    def _parse_mappings(self) -> list[tuple[Path, str]]:
        result = []
        for raw in self._mappings.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            local, remote = line.split("=", 1)
            local = local.strip()
            remote = remote.strip()
            if not local or not remote:
                continue
            result.append((Path(local).expanduser().resolve(), self._normalize_remote(remote)))
        return result

    def _log(self, message: str, level: str = "info") -> None:
        line = message
        self._logs.append(line)
        self._logs = self._logs[-200:]
        if level == "error":
            logger.error(f"Metadata115Sync：{message}")
        elif level == "warning":
            logger.warning(f"Metadata115Sync：{message}")
        else:
            logger.info(f"Metadata115Sync：{message}")

    def _iter_metadata(self, root: Path):
        if not root.is_dir():
            return
        extensions = self._extensions_set()
        limit = self._max_size_mb * 1024 * 1024
        for current, _, files in os.walk(root):
            for name in sorted(files):
                path = Path(current) / name
                try:
                    if path.suffix.lower() not in extensions:
                        continue
                    size = path.stat().st_size
                    if size > limit:
                        self._stats["ignored"] += 1
                        self._log(f"忽略超大文件：{path} ({size / 1024 / 1024:.1f} MB)")
                        continue
                    yield path
                except OSError as err:
                    self._stats["failed"] += 1
                    self._log(f"读取文件失败：{path}：{err}", "error")

    @staticmethod
    def _remote_item(chain: StorageChain, path: str):
        return chain.get_file_item(storage="u115", path=Path(path))

    def _upload_one(
        self,
        chain: StorageChain,
        local_file: Path,
        local_root: Path,
        remote_root: str,
        folder_cache: dict[str, Any],
    ) -> str:
        relative = local_file.relative_to(local_root).as_posix()
        remote_path = self._normalize_remote(f"{remote_root}/{relative}")

        existing = self._remote_item(chain, remote_path)
        if existing:
            self._log(f"跳过（115已有）：{remote_path}")
            return "skipped"

        parent_path = self._normalize_remote(str(Path(remote_path).parent))
        parent = folder_cache.get(parent_path)
        if parent is None:
            parent = chain.get_folder(storage="u115", path=Path(parent_path))
            if not parent:
                raise RuntimeError(f"无法获取/创建115目录：{parent_path}")
            folder_cache[parent_path] = parent

        uploaded = chain.upload_file(
            fileitem=parent,
            path=local_file,
            new_name=local_file.name,
        )
        if not uploaded:
            raise RuntimeError("MoviePilot返回上传失败")

        self._log(f"上传成功：{local_file} → {remote_path}")
        return "uploaded"

    def preview(self) -> dict[str, Any]:
        self._stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._logs = []
        mappings = self._parse_mappings()
        self._log("开始扫描预览（不会访问或修改115）")

        if not mappings:
            self._last_status = "配置错误：没有有效的目录映射"
            self._log("没有有效映射，请填写例如：/strm=/影视库/媒体目录", "error")
            return {"ok": False, **self._stats}

        for local_root, remote_root in mappings:
            self._log(f"检查映射：{local_root} → {remote_root}")
            if not local_root.is_dir():
                self._stats["failed"] += 1
                self._log(f"本地目录不存在：{local_root}", "error")
                continue
            for path in self._iter_metadata(local_root):
                self._stats["scanned"] += 1
                self._log(f"发现元数据：{path}")

        self._last_status = "扫描预览完成"
        self._log(
            f"扫描完成：发现 {self._stats['scanned']} 个元数据文件，"
            f"忽略 {self._stats['ignored']} 个，失败 {self._stats['failed']} 个"
        )
        return {"ok": True, **self._stats}

    def sync(self) -> dict[str, int]:
        if not self._enabled:
            self._last_status = "插件未启用"
            self._log("插件未启用，未执行同步", "warning")
            return {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}

        if not self._lock.acquire(blocking=False):
            self._log("已有同步任务正在运行，本次跳过", "warning")
            return self._stats

        self._running = True
        self._stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._logs = []
        try:
            mappings = self._parse_mappings()
            self._log("开始执行本地 → 115 元数据同步")
            self._log(f"扩展名：{', '.join(sorted(self._extensions_set()))}")
            self._log(f"单文件大小上限：{self._max_size_mb} MB")

            if not mappings:
                self._last_status = "配置错误：没有有效的目录映射"
                self._log("没有有效映射，请填写例如：/strm=/影视库/媒体目录", "error")
                return self._stats

            chain = StorageChain()
            folder_cache: dict[str, Any] = {}

            for local_root, remote_root in mappings:
                self._log(f"处理映射：{local_root} → {remote_root}")
                if not local_root.is_dir():
                    self._stats["failed"] += 1
                    self._log(f"本地目录不存在：{local_root}", "error")
                    continue

                for local_file in self._iter_metadata(local_root):
                    self._stats["scanned"] += 1
                    try:
                        result = self._upload_one(
                            chain, local_file, local_root, remote_root, folder_cache
                        )
                        self._stats[result] += 1
                    except Exception as err:
                        self._stats["failed"] += 1
                        self._log(f"失败：{local_file}：{err}", "error")

            self._last_status = (
                f"同步完成：扫描 {self._stats['scanned']}，"
                f"上传 {self._stats['uploaded']}，"
                f"跳过 {self._stats['skipped']}，"
                f"失败 {self._stats['failed']}，"
                f"忽略 {self._stats['ignored']}"
            )
            self._log(self._last_status)
            return self._stats
        except Exception as err:
            self._last_error = str(err)
            self._last_status = "同步异常"
            self._log(f"同步异常：{err}", "error")
            return self._stats
        finally:
            self._running = False
            self._lock.release()
