import ast
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path("plugins.v2/metadata115sync/__init__.py")
META = Path("package.v2.json")


def _load_plugin():
    app = types.ModuleType("app")
    schemas = types.ModuleType("app.schemas")
    class Response:
        def __init__(self, success=True, message="", data=None):
            self.success, self.message, self.data = success, message, data
    schemas.Response = Response
    app.schemas = schemas

    chain_mod = types.ModuleType("app.chain.storage")
    chain_mod.StorageChain = object
    core = types.ModuleType("app.core.config")
    core.settings = types.SimpleNamespace(API_TOKEN="token")
    log = types.ModuleType("app.log")
    log.logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        exception=lambda *a, **k: None,
        debug=lambda *a, **k: None,
    )
    plugins = types.ModuleType("app.plugins")
    class Base:
        def __init__(self):
            self._data = {}
        def get_data(self, key=None, plugin_id=None):
            return self._data.get(key)
        def save_data(self, key, value, plugin_id=None):
            self._data[key] = value
        def del_data(self, key, plugin_id=None):
            self._data.pop(key, None)
        def update_config(self, config, plugin_id=None):
            self._config = config
            return True
    plugins._PluginBase = Base

    sys.modules.update({
        "app": app,
        "app.schemas": schemas,
        "app.chain": types.ModuleType("app.chain"),
        "app.chain.storage": chain_mod,
        "app.core": types.ModuleType("app.core"),
        "app.core.config": core,
        "app.log": log,
        "app.plugins": plugins,
    })
    namespace = {}
    exec(compile(PLUGIN.read_text(encoding="utf-8"), str(PLUGIN), "exec"), namespace)
    return namespace["Metadata115Sync"], chain_mod


def test_plugin_compiles_and_contract_matches():
    code = PLUGIN.read_text(encoding="utf-8")
    ast.parse(code)
    assert 'plugin_version = "2.10.0"' in code
    assert 'def get_api(self)' in code
    for path in ('"path": "/scan"', '"path": "/stop"', '"path": "/sync"', '"path": "/status"'):
        assert path in code
    assert '"auth": "bear"' in code
    assert 'self.save_data("file_cache", cache)' in code
    assert 'self.save_data("remote_dir_cache", cache)' in code
    assert 'self.save_data("sync_plan"' in code
    assert 'exclude_dirs' in code
    assert '_is_excluded' in code
    assert 'chain.list_files' in code
    assert 'chain.get_file_item' in code
    assert 'chain.get_folder' in code
    assert 'chain.upload_file' in code
    meta = json.loads(META.read_text(encoding="utf-8"))
    assert meta["Metadata115Sync"]["version"] == "2.10.0"


def test_cache_requires_same_size_mtime_and_fresh_remote_check():
    Plugin, _ = _load_plugin()
    p = Plugin()
    p._cache_enabled = True
    p._remote_cache_ttl_hours = 1
    from types import SimpleNamespace
    import time
    stat = SimpleNamespace(st_size=10, st_mtime_ns=100)
    cache = {"k": {"size": 10, "mtime_ns": 100, "checked_at": time.time(), "status": "present"}}
    assert p._cache_hit(cache, "k", stat)
    stat.st_mtime_ns = 101
    assert not p._cache_hit(cache, "k", stat)
    cache["k"]["mtime_ns"] = 101
    cache["k"]["checked_at"] = time.time() - 7200
    assert p._cache_hit(cache, "k", stat)


def test_remote_directory_is_queried_once_and_then_cached():
    Plugin, chain_mod = _load_plugin()
    p = Plugin()
    p._cache_enabled = True
    p._remote_cache_ttl_hours = 6

    class Item:
        def __init__(self, name, typ="file"):
            self.name = name
            self.type = typ

    class Chain:
        def __init__(self):
            self.get_calls = 0
            self.list_calls = 0
        def get_file_item(self, storage, path):
            self.get_calls += 1
            return Item("dir", "dir")
        def list_files(self, folder, recursion=False):
            self.list_calls += 1
            return [Item("a.nfo"), Item("poster.jpg")]

    c = Chain()
    remote_cache = {}
    names1, _, cached1 = p._remote_names(c, "/影视库/电影/A", remote_cache)
    names2, _, cached2 = p._remote_names(c, "/影视库/电影/A", remote_cache)
    assert names1 == names2 == {"a.nfo", "poster.jpg"}
    assert cached1 is False
    assert cached2 is True
    assert c.get_calls == 1
    assert c.list_calls == 1


def test_sync_plan_is_reused_without_second_remote_scan():
    Plugin, _ = _load_plugin()
    p = Plugin()
    p._mappings = "/tmp=/影视库"
    p._extensions = ".nfo"
    p._max_size_mb = 20
    p.save_data("sync_plan", {
        "created_at": 1,
        "fingerprint": p._mapping_fingerprint(),
        "entries": [],
    })
    plan = p._load_plan()
    assert plan is not None
    assert plan["entries"] == []


def test_excluded_directory_is_pruned_from_scan(tmp_path):
    Plugin, _ = _load_plugin()
    p = Plugin()
    root = tmp_path / "strm"
    keep = root / "电影"
    excluded = root / "不同步"
    keep.mkdir(parents=True)
    excluded.mkdir(parents=True)
    (keep / "ok.nfo").write_text("ok", encoding="utf-8")
    (excluded / "skip.nfo").write_text("skip", encoding="utf-8")
    p._extensions = ".nfo"
    p._max_size_mb = 20
    p._exclude_dirs = str(excluded)
    totals = {"scanned": 0, "existing": 0, "pending": 0, "uploaded": 0, "skipped": 0, "failed": 0}
    candidates = list(p._iter_local(root, p._exclude_paths()))
    assert [x[0].name for x in candidates] == ["ok.nfo"]


def test_local_cache_does_not_expire_with_remote_ttl():
    Plugin, _ = _load_plugin()
    p = Plugin()
    p._cache_enabled = True
    p._remote_cache_ttl_hours = 1
    from types import SimpleNamespace
    import time
    stat = SimpleNamespace(st_size=10, st_mtime_ns=100)
    cache = {"k": {"size": 10, "mtime_ns": 100, "checked_at": time.time() - 7200, "status": "present"}}
    assert p._cache_hit(cache, "k", stat)


def test_remote_cache_miss_forces_refresh_and_finds_new_file():
    Plugin, _ = _load_plugin()
    p = Plugin()
    p._cache_enabled = True
    p._remote_cache_ttl_hours = 6

    class Item:
        def __init__(self, name, typ="file"):
            self.name = name
            self.type = typ

    class Chain:
        def __init__(self):
            self.calls = 0
        def get_file_item(self, storage, path):
            return Item("dir", "dir")
        def list_files(self, folder, recursion=False):
            self.calls += 1
            return [Item("海\u0301.nfo")]

    c = Chain()
    remote_cache = {"/影视库/A": {"checked_at": __import__("time").time(), "files": [], "missing": False}}
    names, _, cached = p._remote_names(c, "/影视库/A", remote_cache)
    assert cached is True
    assert names == set()
    refreshed, _, refreshed_cached = p._remote_names(c, "/影视库/A", remote_cache, refresh=True)
    assert refreshed_cached is False
    assert p._normalize_name("海\u0301.nfo") in refreshed
    assert c.calls == 1


def test_modified_local_file_overwrites_existing_remote(tmp_path):
    Plugin, chain_mod = _load_plugin()
    class Item:
        def __init__(self, name, typ="file", fileid="123"):
            self.name = name
            self.type = typ
            self.fileid = fileid
            self.path = "/影视库/A/" + name if typ == "file" else "/影视库/A/"
    class Chain:
        def __init__(self):
            self.deleted = []
            self.uploaded = []
        def get_folder(self, storage, path):
            return Item("A", "dir", "99")
        def get_file_item(self, storage, path):
            return Item(Path(path).name, "file", "123")
        def delete_file(self, item):
            self.deleted.append(item.name)
            return True
        def upload_file(self, fileitem, path, new_name=None):
            self.uploaded.append(path.name)
            return Item(path.name)
    fake = Chain()
    Plugin.sync.__globals__["StorageChain"] = lambda: fake
    p = Plugin()
    p._cache_enabled = True
    p._mappings = f"{tmp_path}=/影视库/A"
    p._extensions = ".nfo"
    p._max_size_mb = 20
    local = tmp_path / "a.nfo"
    local.write_text("new", encoding="utf-8")
    stat = local.stat()
    key = p._cache_key(local, tmp_path, "/影视库/A")
    p.save_data("sync_plan", {
        "created_at": 1,
        "fingerprint": p._mapping_fingerprint(),
        "entries": [{"path": str(local), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                     "remote_dir": "/影视库/A", "cache_key": key, "action": "overwrite"}],
    })
    result = p.sync()
    assert result["status"] == "同步完成"
    assert result["updated"] == 1
    assert fake.deleted == ["a.nfo"]
    assert fake.uploaded == ["a.nfo"]


def test_modified_file_plan_is_overwrite_but_first_discovery_skips(tmp_path):
    Plugin, _ = _load_plugin()
    class Item:
        def __init__(self, name, typ="file"):
            self.name = name
            self.type = typ
    class Chain:
        def get_file_item(self, storage, path):
            return Item("A", "dir")
        def list_files(self, folder, recursion=False):
            return [Item("a.nfo")]
    Plugin.scan_preview.__globals__["StorageChain"] = Chain
    p = Plugin()
    p._cache_enabled = True
    p._mappings = f"{tmp_path}=/影视库/A"
    p._extensions = ".nfo"
    p._max_size_mb = 20
    local = tmp_path / "a.nfo"
    local.write_text("new", encoding="utf-8")
    stat = local.stat()
    key = p._cache_key(local, tmp_path, "/影视库/A")
    p.save_data("file_cache", {key: {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns - 1, "checked_at": 1, "status": "uploaded"}})
    result = p.scan_preview()
    assert result["pending"] == 1
    plan = p.get_data("sync_plan")
    assert plan["entries"][0]["action"] == "overwrite"

    p2 = Plugin()
    p2._cache_enabled = True
    p2._mappings = f"{tmp_path}=/影视库/A"
    p2._extensions = ".nfo"
    p2._max_size_mb = 20
    p2.scan_preview()
    plan2 = p2.get_data("sync_plan")
    assert plan2["entries"] == []


def test_remote_missing_directory_is_not_cached_as_empty():
    Plugin, _ = _load_plugin()
    p = Plugin()
    p._cache_enabled = True

    class Chain:
        def get_file_item(self, storage, path):
            return None

    remote_cache = {}
    names, _, cached = p._remote_names(Chain(), "/不存在", remote_cache)
    assert names == set()
    assert cached is False
    assert "/不存在" not in remote_cache
