def test_contract():
    p = open('plugins.v2/metadata115sync/__init__.py', encoding='utf-8').read()
    assert 'plugin_version = "2.1.0"' in p
    assert 'def get_page' in p
    assert 'def get_api' in p
    assert 'plugin/Metadata115Sync/sync' in p
