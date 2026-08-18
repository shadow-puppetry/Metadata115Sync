import ast, json

def test_contract():
    p='plugins.v2/metadata115sync/__init__.py'
    s=open(p,encoding='utf-8').read()
    ast.parse(s)
    assert 'plugin_version = "2.3.0"' in s
    assert '"model": "onlyonce"' in s
    assert 'def init_plugin' in s and 'self._onlyonce' in s
    assert 'def get_form' in s and 'def get_page' in s
    meta=json.load(open('package.v2.json',encoding='utf-8'))
    assert meta['Metadata115Sync']['version']=='2.3.0'
