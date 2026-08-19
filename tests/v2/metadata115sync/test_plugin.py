import ast
import json
from pathlib import Path

PLUGIN = Path("plugins.v2/metadata115sync/__init__.py")
META = Path("package.v2.json")

def test_plugin_compiles_and_contract_matches():
    code = PLUGIN.read_text(encoding="utf-8")
    ast.parse(code)
    assert 'plugin_version = "2.6.0"' in code
    assert 'def get_api(self)' in code
    assert '"path": "/scan"' in code
    assert '"path": "/stop"' in code
    assert '"path": "/sync"' in code
    assert '"api": "plugin/Metadata115Sync/scan"' in code
    assert '"api": "plugin/Metadata115Sync/stop"' in code
    assert '"api": "plugin/Metadata115Sync/sync"' in code
    assert 'self.save_data("status", status)' in code
    assert 'chain.list_files' in code
    assert 'chain.get_file_item' in code
    assert 'chain.get_folder' in code
    assert 'chain.upload_file' in code
    meta = json.loads(META.read_text(encoding="utf-8"))
    assert meta["Metadata115Sync"]["version"] == "2.6.0"
