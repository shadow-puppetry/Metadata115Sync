import ast

def test_contract():
    s=open('plugins.v2/metadata115sync/__init__.py',encoding='utf-8').read()
    ast.parse(s)
    assert 'plugin_version = "2.2.0"' in s
    assert 'VDialogCloseBtn' in s
    assert 'plugin/Metadata115Sync/sync' in s
    assert 'plugin/Metadata115Sync/scan' in s
