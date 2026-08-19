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
    assert 'plugin_version = "2.8.0"' in code
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
    assert meta["Metadata115Sync"]["version"] == "2.8.0"


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
    cache["k"]["mtime_ns"] = 100
    cache["k"]["checked_at"] = time.time() - 7200
    assert not p._cache_hit(cache, "k", stat)


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
