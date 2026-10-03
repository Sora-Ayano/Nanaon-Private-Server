"""Run copied server code where no legacy server or client build kit exists."""
from pathlib import Path
import os
import shutil
import subprocess
import sys


def test_standalone_copy_without_legacy_or_client(tmp_path):
    source=Path(__file__).resolve().parents[1]
    target=tmp_path/'fresh-server';target.mkdir()
    for folder in ('api','cdn','crypto','lan','data','compat_assets'):
        shutil.copytree(source/folder,target/folder,ignore=shutil.ignore_patterns('__pycache__','*.sqlite3'))
    for file in ('server.py','lan_launcher.py','config.yaml'):
        shutil.copy2(source/file,target/file)
    for name,content in {
        'resources/main.5465.com.aniplex.nananiji.obb':b'test-obb',
        'resources/com.aniplex.nananiji/files/TTSCriProject.acf':b'test-acf',
        'resources/com.aniplex.nananiji/files/DownloadCache/Android/bundle/test/data':b'test-bundle',
    }.items():
        path=target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    script='''from pathlib import Path
from lan_launcher import create_lan_app
from cdn.asset_server import asset_server
from api import handlers
app=create_lan_app(Path('resources'),'http://192.168.1.2:18080')
c=app.test_client()
assert c.get('/api/environment/server').status_code==200
from urllib.parse import urlsplit
notice=handlers._GAME_CONFIG['api_url']+'web/announcement?category=1'
assert urlsplit(notice).port==18080
assert urlsplit(notice).path=='/web/announcement'
assert c.get(urlsplit(notice).path).status_code==200
manifest=c.get('/bootstrap/manifest.json').get_json()
assert manifest['locale']=='ja-JP' and len(manifest['files'])>=3
assert c.get(manifest['files'][0]['url']).status_code==200
assert c.get('/play').status_code==200
assert handlers.USER_STORE.path.resolve().is_relative_to(Path.cwd())
assert asset_server.ASSET_BASE.resolve().is_relative_to(Path.cwd())
assert asset_server.CACHE_BASE.resolve().is_relative_to(Path.cwd())
assert not Path('client').exists() and not Path('patches').exists()
from api.models import UserGetData
handlers.USER_STORE.save_user('old-install',UserGetData.create_default(777))
with handlers.USER_STORE._connect() as db:
    db.execute("UPDATE users SET exp=1234 WHERE user_id=777")
    db.execute("INSERT OR REPLACE INTO server_settings VALUES ('local_user_id','777')")
app=create_lan_app(Path('resources'),'http://192.168.1.2:18080')
assert handlers._get_or_create_user('new-install')==777
assert handlers.USER_STORE.load_user(777).user.exp==1234
'''
    env=os.environ.copy();env.pop('NANAON_DB_PATH',None);env.pop('PYTHONPATH',None)
    subprocess.run([sys.executable,'-B','-c',script],cwd=target,env=env,check=True,capture_output=True)


def test_cloud_copy_has_no_full_resource_or_legacy_dependency(tmp_path):
    source=Path(__file__).resolve().parents[1]
    target=tmp_path/'cloud-server';target.mkdir()
    for folder in ('api','cdn','crypto','lan','data','compat_assets'):
        shutil.copytree(source/folder,target/folder,ignore=shutil.ignore_patterns('__pycache__','*.sqlite3'))
    for name in ('server.py','cloud_launcher.py','config.yaml'):
        shutil.copy2(source/name,target/name)
    script='''from pathlib import Path
from cloud_launcher import create_cloud_app
from api import handlers
app=create_cloud_app('https://game.example.org',Path('state'))
c=app.test_client()
assert c.get('/bootstrap/server.json').json['multi_player']
assert c.get('/bootstrap/manifest.json').status_code==404
assert c.get('/bootstrap/updates.json').status_code==204
assert c.get('/web/announcement').status_code==200
identity=app.config['CLOUD_IDENTITY'];player=identity.resolve('a'*64,create=True)
assert handlers.USER_STORE.load_user(player) is not None
assert handlers.USER_STORE.path.resolve().is_relative_to(Path.cwd())
assert not Path('resources').exists() and not Path('client').exists()
'''
    env=os.environ.copy();env.pop('NANAON_DB_PATH',None);env.pop('PYTHONPATH',None)
    subprocess.run([sys.executable,'-B','-c',script],cwd=target,env=env,check=True,capture_output=True)
