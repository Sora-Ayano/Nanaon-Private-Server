import hashlib
import json
from pathlib import Path

import pytest
from flask import Flask

from lan.network import validate_ip
from lan.resources import ResourceCatalog
from lan.web import make_blueprint


@pytest.fixture
def catalog(tmp_path):
    root=tmp_path/'resources'
    files={'main.5465.com.aniplex.nananiji.obb':b'OBB0123456789',
           'com.aniplex.nananiji/files/TTSCriProject.acf':b'ACF',
           'com.aniplex.nananiji/files/DownloadCache/Android/bundle/example/data':b'0123456789',
           'com.aniplex.nananiji/files/private-save.json':b'PRIVATE'}
    for name, data in files.items():
        path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
    result=ResourceCatalog(root,tmp_path/'state/index.json'); result.build()
    return result


@pytest.fixture
def client(catalog,tmp_path):
    app=Flask(__name__)
    app.register_blueprint(make_blueprint(catalog,tmp_path/'dist','http://192.168.1.2:18080'))
    return app.test_client()


def test_inventory_excludes_saves_and_stable_revision(catalog):
    manifest=catalog.manifest
    assert len(manifest['files'])==3
    assert manifest['total_bytes']==26
    assert all('private-save' not in x['path'] for x in manifest['files'])
    assert catalog.build()==manifest
    path=next(p for p,_,_ in catalog.paths.values() if p.suffix=='.acf')
    path.write_bytes(b'ACF UPDATED')
    assert catalog.build()['revision']!=manifest['revision']


def test_download_range_and_digest(client,catalog):
    item=next(x for x in catalog.manifest['files'] if x['kind']=='cache')
    response=client.get(item['url'],headers={'Range':'bytes=3-','If-Range':'"'+item['sha256']+'"'})
    assert response.status_code==206
    assert response.headers['Content-Range']=='bytes 3-9/10'
    assert response.data==b'3456789'
    assert response.headers['ETag']=='"'+item['sha256']+'"'
    assert client.get(item['url'],headers={'Range':'bytes=3-','If-Range':'"stale"'}).data==b'0123456789'
    assert hashlib.sha256(client.get(item['url']).data).hexdigest()==item['sha256']


def test_download_changed_resource_fails_closed(client,catalog):
    item=catalog.manifest['files'][0]
    catalog.paths[item['id']][0].write_bytes(b'changed')
    assert client.get(item['url']).status_code==409


@pytest.mark.parametrize('path',['/bootstrap/files/'+('0'*64),'/bootstrap/files/../index.json',
                                '/client/../../config.yaml','/client/password.txt'])
def test_download_does_not_expose_arbitrary_files(client,path):
    assert client.get(path).status_code==404


@pytest.mark.parametrize('value',['127.0.0.1','169.254.1.1','8.8.8.8','192.0.2.1','0.0.0.0','224.0.0.1','172.32.0.1','192.168.1.256'])
def test_reject_non_lan_ip(value):
    with pytest.raises(ValueError): validate_ip(value)


def test_rfc1918_addresses():
    for value in ['10.1.2.3','172.16.0.2','172.31.255.1','192.168.1.2']:
        assert validate_ip(value)==value


def test_cdn_confinement(tmp_path):
    from cdn.asset_server import AssetServer
    assets=tmp_path/'assets'; assets.mkdir()
    (assets/'ok.txt').write_text('ok')
    (tmp_path/'secret.txt').write_text('secret')
    server=AssetServer(assets)
    assert server.resolve_path('/assets/ok.txt')==assets/'ok.txt'
    for bad in ['/../secret.txt','/assets/../secret.txt','/assets/..\\secret.txt','/C:/secret.txt']:
        assert server.resolve_path(bad) is None
    assert server.list_available_assets('../')==[]


def test_api_lan_url_and_no_diagnostics(tmp_path,monkeypatch):
    monkeypatch.setenv('NANAON_DB_PATH',str(tmp_path/'users.sqlite3'))
    from server import create_app,load_config,BASE_DIR
    from crypto import NanaPacker
    from api import handlers
    from api.storage import UserStore
    monkeypatch.setattr(handlers,'USER_STORE',UserStore(tmp_path/'users.sqlite3'))
    config=load_config(str(BASE_DIR/'config.yaml'))
    config.update(diagnostics=False,capture_traffic=False,cors=False)
    config['game'].update(api_url='http://192.168.1.2:18080',asset_url='http://192.168.1.2:18080')
    app=create_app(config); client=app.test_client()
    for path in ['/admin/last_requests','/admin/enc_requests']:
        assert client.get(path).status_code==404
        assert client.get(path,environ_base={'REMOTE_ADDR':'192.168.1.3'}).status_code==404
    result=client.get('/api/environment/server')
    assert result.status_code==200
    assert 'Access-Control-Allow-Origin' not in result.headers
    decoded=json.loads(NanaPacker(key_hex=config['crypto']['aes_key']).decode_string(result.get_data(as_text=True)))
    assert 'http://192.168.1.2:18080' in json.dumps(decoded)
    with handlers.USER_STORE._connect() as db:
        assert db.execute('select count(*) from users').fetchone()[0]==0


def test_no_usb_install_scripts():
    base=Path(__file__).resolve().parents[1]
    for name in ['Start-Server.cmd','lan_launcher.py']:
        text=(base/name).read_text(encoding='utf-8').lower()
        assert 'adb.exe' not in text and 'adb push' not in text and 'adb reverse' not in text
    assert not (base/'Push-All-Resources.cmd').exists()


@pytest.mark.parametrize('answer,expected',[('', 'ja-JP'),('1','ja-JP'),(' 2 ','zh-Hans')])
def test_language_menu(monkeypatch,answer,expected):
    from lan_launcher import choose_locale
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    monkeypatch.setattr('builtins.input',lambda _:answer)
    assert choose_locale()==expected


def test_language_menu_retries_invalid_and_defaults_unattended(monkeypatch):
    from lan_launcher import choose_locale
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    answers=iter(['invalid',''])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    assert choose_locale()=='ja-JP'
    monkeypatch.setattr('sys.stdin.isatty',lambda:False)
    assert choose_locale()=='ja-JP'
    assert choose_locale('zh-Hans')=='zh-Hans'


def test_patch_catalog_and_cdn_share_bytes(catalog,tmp_path):
    from cdn.asset_server import AssetServer
    from lan.patches import load_patch
    folder=tmp_path/'patches/zh-Hans'
    name='Android/bundle/example/data.unity3d'
    original=catalog.root/'com.aniplex.nananiji/files/DownloadCache'/name
    original.write_bytes(b'original')
    path=folder/name; path.parent.mkdir(parents=True); path.write_bytes(b'UnityFS patched')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    (folder/'manifest.json').write_text(json.dumps(dict(schema=1,client_version_code=5465,
        files=[dict(path=name,size=15,sha256=digest)])))
    overlay,_=load_patch(folder)
    patched=ResourceCatalog(catalog.root,tmp_path/'patched-index.json',overlay,folder,'zh-Hans')
    manifest=patched.build()
    item=next(x for x in manifest['files'] if x['path']==name)
    assert patched.resolve(item['id'])==path
    assert item['sha256']==digest and manifest['locale']=='zh-Hans'
    assert item['startup_check'] is True
    assert original.read_bytes()==b'original'
    cdn=AssetServer(tmp_path/'assets'); cdn.LOCALE_ROOT=folder
    cdn.LOCALE_OVERRIDES={'android/assetbundle/data/example.unity3d':path}
    assert cdn.resolve_path('/android/assetbundle/data/example.unity3d')==path
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash mismatch'): load_patch(folder)
