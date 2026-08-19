import ast
import json
from pathlib import Path

def test_contract():
    code = Path("plugins.v2/metadata115sync/__init__.py").read_text(encoding="utf-8")
    ast.parse(code)
    assert 'plugin_version = "2.4.0"' in code
    assert '"model": "onlyonce"' in code
    assert 'def get_service' in code
    assert '"trigger": "interval"' in code
    assert '"path": "/stop"' in code
    assert 'chain.list_files' in code
    assert 'file_cache' in code
    meta = json.loads(Path("package.v2.json").read_text(encoding="utf-8"))
    assert meta["Metadata115Sync"]["version"] == "2.4.0"
