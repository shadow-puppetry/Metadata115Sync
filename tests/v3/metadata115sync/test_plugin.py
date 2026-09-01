from pathlib import Path


def test_v3_layout():
    repo = Path(__file__).parents[3]
    assert (repo / "plugins.v3" / "metadata115sync" / "__init__.py").exists()
    assert (repo / "package.v3.json").exists()
