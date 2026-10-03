"""API-only multi-player entry point; no APK or full resource archive required."""
import argparse
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent


def validate_public_url(value):
    u = urlsplit(value.strip())
    if (u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password
            or u.path not in ('', '/') or u.query or u.fragment):
        raise ValueError('Use http(s)://host[:port], without a path or credentials')
    # Force validation of malformed/out-of-range ports.
    u.port
    return value.strip().rstrip('/')


def create_cloud_app(public_url, state_dir=None, updates_dir=None):
    public_url = validate_public_url(public_url)
    state = Path(state_dir or BASE/'var').resolve()
    state.mkdir(parents=True, exist_ok=True)
    os.environ['NANAON_DB_PATH'] = str(state/'data/users.sqlite3')
    from server import create_app, load_config
    from api import handlers
    from api.storage import UserStore
    from api.identity import CloudIdentity
    store = UserStore(state/'data/users.sqlite3')
    handlers.USER_STORE = store
    handlers.USER_DB.clear()
    handlers.UUID_MAP.clear()
    handlers.TOKEN_MAP.clear()
    config = load_config(str(BASE/'config.yaml'))
    config['game'] = {**config.get('game', {}), 'api_url':public_url+'/',
                      'asset_url':public_url, 'multi_user':True}
    config.update(capture_traffic=False, diagnostics=False, cors=False, serve_assets=False)
    app = create_app(config)
    app.config['CLOUD_IDENTITY'] = CloudIdentity(store)
    from lan.updates import make_updates_blueprint
    app.register_blueprint(make_updates_blueprint(updates_dir))

    @app.get('/bootstrap/server.json')
    def server_info():
        return {'schema':1, 'mode':'cloud', 'multi_player':True, 'full_resources':False}

    @app.get('/web/announcement')
    @app.get('/web/tos/detail')
    def information():
        return ('<!doctype html><meta charset="utf-8"><h1>Nanaon 服务</h1>'
                '<p>游玩进度保存在当前服务器的独立账号中。首次完整资源需通过本地资源服务下载。</p>')
    return app


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--public-url', required=True, help='External HTTPS origin; HTTP also accepted for testing')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=18081)
    p.add_argument('--state-dir', type=Path, default=BASE/'var')
    p.add_argument('--updates-dir', type=Path, help='Optional incremental resource package')
    a = p.parse_args()
    if not 1 <= a.port <= 65535: p.error('invalid port')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    from lan.state_lock import StateLock
    with StateLock(a.state_dir.resolve()):
        app = create_cloud_app(a.public_url, a.state_dir, a.updates_dir)
        from waitress import serve
        serve(app, host=a.host, port=a.port, threads=16, max_request_body_size=1024*1024)


if __name__ == '__main__': main()
