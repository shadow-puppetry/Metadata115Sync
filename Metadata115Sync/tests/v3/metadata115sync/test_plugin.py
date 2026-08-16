from pathlib import Path

from plugins.v3.metadata115sync import Metadata115Sync


def test_v3_layout_and_metadata():
    """验证 V3 插件目录与版本元数据。"""
    repo = Path(__file__).parents[3]
    assert (repo / "plugins.v3" / "metadata115sync" / "__init__.py").exists()
    assert Metadata115Sync.plugin_version == "1.0.0"


def test_remote_path_normalization():
    """验证远程路径规范化逻辑。"""
    assert Metadata115Sync._normalize_remote_path("电影//Test/") == "/电影/Test"
    assert Metadata115Sync._normalize_remote_path("/电影/Test") == "/电影/Test"


def test_extension_filtering():
    """验证元数据扩展名解析。"""
    plugin = Metadata115Sync()
    plugin.init_plugin({"extensions": "nfo,.jpg, PNG"})
    assert plugin._extensions_set() == {".nfo", ".jpg", ".png"}
