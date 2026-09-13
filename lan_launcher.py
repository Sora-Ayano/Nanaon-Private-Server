"""Choose a language, detect the LAN address and serve API/CDN/bootstrap."""
import argparse
import json
import logging
import os
from pathlib import Path
import socket
import sys
import webbrowser

BASE=Path(__file__).resolve().parent


def choose_locale(explicit=None):
    """Unattended starts and an empty answer always use Japanese."""
    if explicit is not None:
        return explicit
    if not sys.stdin.isatty():
        return 'ja-JP'
    print('选择游戏语言：\n  1. 日本語（日语，默认）\n  2. 简体中文（实验版，运行验证未通过）')
    while True:
        try:
            choice=input('输入 1 / 2，或直接回车使用日语：').strip()
        except EOFError:
            return 'ja-JP'
        if choice in ('', '1'):
            return 'ja-JP'
        if choice == '2':
            return 'zh-Hans'
        print('请输入 1 或 2。')


def create_lan_app(resource_root, public_url, state_dir=None, locale='ja-JP'):
    from lan.resources import ResourceCatalog
    from lan.web import make_blueprint
    state=Path(state_dir) if state_dir else BASE/'var'
    state.mkdir(parents=True,exist_ok=True)
    # Set before api.handlers imports its singleton store.
    os.environ['NANAON_DB_PATH']=str(state/'data/users.sqlite3')
    from server import create_app, load_config
    from cdn.asset_server import asset_server
    from api import handlers
    from api.storage import UserStore
    database=state/'data/users.sqlite3'
    if handlers.USER_STORE.path.resolve()!=database.resolve():
        handlers.USER_STORE=UserStore(database)
    root=Path(resource_root).resolve()
    asset_server.ASSET_BASE=root/'main.5465.com.aniplex.nananiji/assets'
    asset_server.CACHE_BASE=root/'com.aniplex.nananiji'
    asset_server.DOWNLOAD_CACHE=asset_server.CACHE_BASE/'files/DownloadCache'
    from lan.patches import load_patch
    patch_root=BASE/'patches'/locale
    if (patch_root/'manifest.json').is_file():
        overrides, patch=load_patch(patch_root)
    elif locale=='ja-JP':
        # A standalone server can use an unmodified Japanese resource archive.
        overrides, patch={},{}
    else:
        raise FileNotFoundError('缺少 patches/zh-Hans/manifest.json，请把独立中文资源包放入新服务目录。')
    asset_server.LOCALE_ROOT=patch_root
    asset_server.LOCALE_OVERRIDES={}
    for name, path in overrides.items():
        # Game transport reverses the two hash components compared to its cache.
        parts=name.split('/')
        wire=Path(parts[-1])
        asset_server.LOCALE_OVERRIDES[name.lower()]=path
        if parts[1]=='manifest':
            asset_server.LOCALE_OVERRIDES[f'android/manifest/{parts[-2]}/manifest.unity3d']=path
        elif wire.suffix=='.unity3d':
            asset_server.LOCALE_OVERRIDES[f'android/assetbundle/{wire.stem}/{parts[-2]}{wire.suffix}']=path
    asset_server.FALLBACK_ASSETS=dict(asset_server.FALLBACK_ASSETS)
    asset_server.FALLBACK_ASSETS[asset_server.NOTICE_STORY_FALLBACK_KEY]=asset_server.DOWNLOAD_CACHE/'Android/sound/118b868d886c15a8383d0ea1064b1d58/Voice/PART_10600000_000.acb'
    config=load_config(str(BASE/'config.yaml'))
    config['game']={**config.get('game',{}),'api_url':public_url,'asset_url':public_url}
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(state/'data/users.sqlite3')) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='server_settings'").fetchone():
            active=db.execute("SELECT value FROM server_settings WHERE key='local_user_id'").fetchone()
            if active:config['game']['local_user_id']=int(active[0])
    config.update(capture_traffic=False, diagnostics=False, cors=False)
    app=create_app(config)
    catalog=ResourceCatalog(root,state/'resource-index.json',overrides,patch_root,locale)
    catalog.build()
    app.register_blueprint(make_blueprint(catalog,BASE/'dist',public_url))
    app.config['LAN_CATALOG']=catalog
    return app


def run():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ip',help='Override automatic LAN IPv4 selection')
    p.add_argument('--port',type=int,default=18080)
    p.add_argument('--resource-root',type=Path,default=None)
    p.add_argument('--no-browser',action='store_true')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--locale',choices=['zh-Hans','ja-JP'],help='Skip the startup language chooser')
    a=p.parse_args()
    a.locale=choose_locale(a.locale)
    print('游戏语言：'+('日本語' if a.locale=='ja-JP' else '简体中文'),flush=True)
    if a.locale=='zh-Hans':
        print('中文为实验选项，当前存在主数据资源加载错误；测试已暂停。日常游玩请选择日语。',flush=True)
    from lan.network import detect_ip,validate_ip
    if not 1024<=a.port<=65535:p.error('Port must be 1024..65535')
    ip=validate_ip(a.ip) if a.ip else detect_ip()
    public_url=f'http://{ip}:{a.port}'
    root=a.resource_root or BASE/'resources'
    with socket.socket() as probe:
        if os.name=='nt':probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:probe.bind(('0.0.0.0',a.port))
        except OSError:raise SystemExit(f'端口 {a.port} 已占用，未终止其他程序。')
    logs=BASE/'var/logs'; logs.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(name)s %(message)s',
                        handlers=[logging.StreamHandler(),logging.FileHandler(logs/'lan.log',encoding='utf-8')])
    print('正在核对本地资源并生成清单…',flush=True)
    app=create_lan_app(root,public_url,locale=a.locale)
    info=dict(server_url=public_url,portal=public_url+'/play',resource_root=str(Path(root).resolve()),
              pid=os.getpid(),package='com.aniplex.nananiji.lan',locale=a.locale)
    (BASE/'var/lan-session.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    print('\n手机浏览器打开：'+info['portal']+'\n同一局域网 · 无 root · 无 USB 资源推送\n关闭本窗口可停止服务。',flush=True)
    if a.prepare_only:return
    if not a.no_browser:webbrowser.open(info['portal'])
    from waitress import serve
    serve(app,host='0.0.0.0',port=a.port,threads=16,channel_timeout=120,max_request_body_size=10*1024*1024)


def main():
    from lan.state_lock import StateLock
    with StateLock(BASE/'var'):
        run()


if __name__=='__main__':main()
