def test_basic_import_contract():
    text = open("plugins.v2/metadata115sync/__init__.py", encoding="utf-8").read()
    assert 'plugin_version = "1.2.0"' in text
    assert 'class Metadata115Sync' in text
    assert 'def get_api' in text
    assert 'def get_page' in text
