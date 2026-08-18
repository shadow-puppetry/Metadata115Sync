from __future__ import annotations

import datetime
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from app import schemas
from app.chain.storage import StorageChain
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase

lock = Lock()


class Metadata115Sync(_PluginBase):
    """只把本地已有、115不存在的元数据补齐到115，不使用TMDB。"""

    plugin_name = "Metadata115Sync"
    plugin_desc = "本地元数据单向同步到已配置的115网盘。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/u115.png"
    plugin_version = "2.3.0"
    plugin_author = "shadow-puppetry"
    author_url = ""
    plugin_config_prefix = "metadata115sync_"
    plugin_order = 50
    auth_level = 1

    _enabled: bool = False
    _onlyonce: bool = False
    _mappings: str = ""
    _extensions: str = ".nfo,.jpg,.jpeg,.png,.webp,.xml"
    _max_size_mb: int = 20
    _interval: int = 60
    _scheduler = None
    _stats: Dict[str, int] = {}
    _logs: List[str] = []
    _status: str = "暂无执行记录"

    def init_plugin(self, config: dict = None):
        self.stop_service()
        config = config or {}

        self._enabled = bool(config.get("enabled", False))
        self._onlyonce = bool(config.get("onlyonce", False))
        self._mappings = str(config.get("mappings") or "")
        self._extensions = str(config.get("extensions") or ".nfo,.jpg,.jpeg,.png,.webp,.xml")
        try:
            self._max_size_mb = max(1, int(config.get("max_size_mb") or 20))
        except (TypeError, ValueError):
            self._max_size_mb = 20
        try:
            self._interval = max(5, int(config.get("interval") or 60))
        except (TypeError, ValueError):
            self._interval = 60

        # 与官方 V2 插件的 onlyonce 模式一致：
        # 保存配置后，后台立即安排一次 date 任务，然后自动关闭开关。
        if self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info("Metadata115Sync 服务启动，立即运行一次")
            self._scheduler.add_job(
                func=self.sync,
                trigger="date",
                run_date=datetime.datetime.now(
                    tz=pytz.timezone(settings.TZ)
                ) + datetime.timedelta(seconds=3),
            )
            self._scheduler.start()
            self._onlyonce = False
            self._update_config()
        elif self._enabled:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.sync,
                trigger="interval",
                minutes=self._interval,
            )
            self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

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
        ]

    def sync_api(self):
        return schemas.Response(success=True, data=self.sync(), message=self._status)

    def scan_api(self):
        return schemas.Response(success=True, data=self.scan_preview(), message=self._status)

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 这里严格采用官方 V2 DoubanSync 已实际使用的 VSwitch/onlyonce 方案。
        return [
            {
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
                                    "props": {
                                        "model": "enabled",
                                        "label": "启用元数据同步",
                                    },
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "onlyonce",
                                        "label": "立即运行一次",
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "content": [{
                                "component": "VTextarea",
                                "props": {
                                    "model": "mappings",
                                    "label": "本地目录 → 115目录映射",
                                    "rows": 4,
                                    "placeholder": "/strm=/影视库/媒体目录",
                                },
                            }],
                        }],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "content": [{
                                "component": "VTextField",
                                "props": {
                                    "model": "extensions",
                                    "label": "元数据扩展名",
                                    "placeholder": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
                                },
                            }],
                        }],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [{
                                "component": "VTextField",
                                "props": {
                                    "model": "max_size_mb",
                                    "label": "元数据大小上限（MB）",
                                    "type": "number",
                                },
                            }],
                        }, {
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
                        }],
                    },
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "mappings": "",
            "extensions": ".nfo,.jpg,.jpeg,.png,.webp,.xml",
            "max_size_mb": 20,
            "interval": 60,
        }

    def get_page(self) -> List[dict]:
        stats = self._stats or {
            "scanned": 0, "uploaded": 0, "skipped": 0,
            "failed": 0, "ignored": 0,
        }
        text = (
            f"状态：{self._status}\n"
            f"扫描：{stats['scanned']}  | 上传：{stats['uploaded']}  | "
            f"已存在：{stats['skipped']}  | 失败：{stats['failed']} | "
            f"忽略：{stats['ignored']}"
        )
        logs = "\n".join(self._logs[-100:]) if self._logs else "暂无执行日志。"
        return [{
            "component": "div",
            "props": {"class": "d-flex flex-column gap-3"},
            "content": [
                {"component": "VAlert", "props": {
                    "type": "info", "variant": "tonal", "text": text
                }},
                {"component": "VTextarea", "props": {
                    "label": "最近执行日志",
                    "rows": 18,
                    "readonly": True,
                    "model": "logs",
                    "model-value": logs,
                }},
            ],
        }]

    def get_service(self) -> List[Dict[str, Any]]:
        # 持续服务由 init_plugin 中的 scheduler 管理，避免重复注册。
        return []

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
            self._scheduler = None
        except Exception as e:
            logger.error("Metadata115Sync 停止服务失败：%s", str(e))

    def _update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "mappings": self._mappings,
            "extensions": self._extensions,
            "max_size_mb": self._max_size_mb,
            "interval": self._interval,
        })

    def _log(self, text: str, level: str = "info"):
        self._logs.append(text)
        self._logs = self._logs[-200:]
        if level == "error":
            logger.error("Metadata115Sync：%s", text)
        elif level == "warning":
            logger.warning("Metadata115Sync：%s", text)
        else:
            logger.info("Metadata115Sync：%s", text)

    @staticmethod
    def _remote(path: str) -> str:
        path = path.replace("\\", "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path.rstrip("/") or "/"

    def _extensions_set(self) -> set[str]:
        result = set()
        for value in self._extensions.split(","):
            value = value.strip().lower()
            if value:
                result.add(value if value.startswith(".") else "." + value)
        return result

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

    def _iter_metadata(self, root: Path):
        if not root.is_dir():
            return
        limit = self._max_size_mb * 1024 * 1024
        for current, _, files in os.walk(root):
            for name in sorted(files):
                path = Path(current) / name
                if path.suffix.lower() not in self._extensions_set():
                    continue
                try:
                    if path.stat().st_size > limit:
                        self._stats["ignored"] += 1
                        self._log(f"[忽略] 超过大小限制：{path}")
                        continue
                except OSError as e:
                    self._stats["failed"] += 1
                    self._log(f"[失败] 无法读取：{path}：{e}", "error")
                    continue
                yield path

    def scan_preview(self):
        self._stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._logs = []
        self._status = "扫描中"
        mappings = self._mappings_list()
        self._log("开始扫描预览（不会上传或修改115）")
        if not mappings:
            self._status = "未配置有效映射"
            self._log("请填写，例如：/strm=/影视库/媒体目录", "error")
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
            f"扫描完成：发现 {self._stats['scanned']}，"
            f"忽略 {self._stats['ignored']}，失败 {self._stats['failed']}"
        )
        return self._stats

    def _upload_one(self, chain: StorageChain, local: Path,
                    root: Path, remote_root: str) -> str:
        relative = local.relative_to(root).as_posix()
        remote = self._remote(f"{remote_root}/{relative}")

        if chain.get_file_item(storage="u115", path=Path(remote)):
            self._log(f"[跳过] 115已有：{remote}")
            return "skipped"

        parent = chain.get_folder(storage="u115", path=Path(remote).parent)
        if not parent:
            raise RuntimeError(f"115目标目录不存在或无法访问：{Path(remote).parent}")

        ok = chain.upload_file(fileitem=parent, path=local, new_name=local.name)
        if not ok:
            raise RuntimeError("115上传接口返回失败")
        self._log(f"[上传] {local} → {remote}")
        return "uploaded"

    def sync(self):
        if not self._enabled and not self._onlyonce:
            self._status = "插件未启用"
            self._log("插件未启用", "warning")
            return self._stats

        if not lock.acquire(blocking=False):
            self._status = "已有同步任务正在运行"
            self._log("已有同步任务正在运行，本次跳过", "warning")
            return self._stats

        self._stats = {"scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._logs = []
        self._status = "同步中"
        try:
            mappings = self._mappings_list()
            self._log("开始执行：本地 → 115")
            if not mappings:
                self._status = "未配置有效映射"
                self._log("请填写，例如：/strm=/影视库/媒体目录", "error")
                return self._stats

            chain = StorageChain()
            for root, remote in mappings:
                self._log(f"[目录] {root} → {remote}")
                if not root.is_dir():
                    self._stats["failed"] += 1
                    self._log(f"[失败] 本地目录不存在：{root}", "error")
                    continue
                for local in self._iter_metadata(root):
                    self._stats["scanned"] += 1
                    try:
                        result = self._upload_one(chain, local, root, remote)
                        self._stats[result] += 1
                    except Exception as e:
                        self._stats["failed"] += 1
                        self._log(f"[失败] {local}：{e}", "error")

            self._status = (
                f"同步完成：扫描 {self._stats['scanned']}，"
                f"上传 {self._stats['uploaded']}，已存在 {self._stats['skipped']}，"
                f"失败 {self._stats['failed']}，忽略 {self._stats['ignored']}"
            )
            self._log(self._status)
            return self._stats
        finally:
            lock.release()

    def get_command(self):
        return []
