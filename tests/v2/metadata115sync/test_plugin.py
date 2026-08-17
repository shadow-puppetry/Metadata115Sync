from pathlib import Path

from plugins.v2.metadata115sync import Metadata115Sync


def test_v2_layout_and_metadata():
    repo = Path(__file__).parents[3]
    assert (repo / "plugins.v2" / "metadata115sync" / "__init__.py").exists()
    assert Metadata115Sync.plugin_version == "2.0.0"


def test_remote_path_normalization():
    assert Metadata115Sync._normalize_remote_path("电影//Test/") == "/电影/Test"
    assert Metadata115Sync._normalize_remote_path("/电影/Test") == "/电影/Test"


def test_extension_filtering():
    plugin = Metadata115Sync()
    plugin.init_plugin({"extensions": "nfo,.jpg, PNG"})
    assert plugin._extensions_set() == {".nfo", ".jpg", ".png"}
