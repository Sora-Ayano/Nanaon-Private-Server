import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from cloud_launcher import create_cloud_app, validate_public_url
from api import handlers
from crypto import NanaPacker
from server import load_config


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(handlers,'USER_STORE',handlers.USER_STORE)
    monkeypatch.setattr(handlers,'USER_DB',{})
    monkeypatch.setattr(handlers,'UUID_MAP',{})
    monkeypatch.setattr(handlers,'TOKEN_MAP',{})
    monkeypatch.setattr(handlers,'_GAME_CONFIG',dict(handlers._GAME_CONFIG))
    monkeypatch.setenv('NANAON_DB_PATH',str(tmp_path/'unused.sqlite3'))
    app = create_cloud_app('https://game.example.org',tmp_path/'state')
    config = load_config(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'config.yaml'))['crypto']
    packer = NanaPacker(key_hex=config['aes_key'],iv_hex=config.get('aes_iv'))
    def api(key, path='/api/auth/start_user', body=None):
        response = app.test_client().post(path,json=body or {'uuid':'same-game-uuid'},
                        headers={'X-Nanaon-Client-Key':key} if key else {})
        return response, json.loads(packer.decode_string(response.text)) if response.status_code==200 else None
    return app,api


def test_players_isolated_and_auth_required(cloud):
    app,api = cloud
    a = api('a'*64)[1]['data']['user_id']; b = api('b'*64)[1]['data']['user_id']
    assert a != b
    assert api(None,'/api/user/get')[0].status_code == 401
    assert api('c'*64,'/api/user/get')[0].status_code == 401
    assert api('a'*64,'/api/user/get',{'user_id':b})[0].status_code == 403
    assert api('b'*64,'/api/user/update',{'name':'second-player'})[0].status_code==200
    assert api('a'*64,'/api/user/get')[1]['data']['user']['name'] != 'second-player'
    assert api('b'*64,'/api/user/get')[1]['data']['user']['name'] == 'second-player'
    from api.identity import CloudIdentity
    from api.storage import UserStore
    restarted = CloudIdentity(UserStore(handlers.USER_STORE.path))
    assert restarted.resolve('a'*64) == a
    assert restarted.resolve('b'*64) == b
    assert app.test_client().get('/bootstrap/manifest.json').status_code==404
    assert app.test_client().get('/bootstrap/updates.json').status_code==204
    assert app.test_client().get('/bootstrap/server.json').json['full_resources'] is False
    assert app.test_client().get('/admin/last_requests').status_code==404


def test_parallel_first_logins_are_atomic(cloud):
    app,_ = cloud
    identity = app.config['CLOUD_IDENTITY']
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: identity.resolve('d'*64,create=True),range(16)))
    assert len(set(ids)) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda n: identity.resolve(f'{n:064x}',create=True),range(1,9)))
    assert len(set(ids)) == 8


def test_settings_cannot_redirect_or_modify_another_save(cloud):
    _, api = cloud
    a = api('a'*64)[1]['data']['user_id']
    b = api('b'*64)[1]['data']['user_id']
    before_a = handlers.USER_STORE.load_user(a).to_dict()
    before_b = handlers.USER_STORE.load_user(b).to_dict()
    for path, body in (
        ('/api/user/update_main_deck_slot', {'main_deck_slot': 1, 'id': b}),
        ('/api/user/update_profile_settings', {'profile_settings': [1], 'id': b}),
        ('/api/user/update_birth_date', {'birth_date': '2000-01-01', 'id': b}),
        ('/api/user/update_profile_settings', {'profile_settings': [1], 'exp': 999999}),
        ('/api/user/update_main_deck_slot', {'main_deck_slot': 999}),
        ('/api/user/update_profile_settings', {'profile_settings': 'invalid'}),
    ):
        response, result = api('a'*64, path, body)
        assert response.status_code == 200 and result['code'] != 0
        assert handlers.USER_STORE.load_user(a).to_dict() == before_a
        assert handlers.USER_STORE.load_user(b).to_dict() == before_b
    for path, body in (
        ('/api/user/update_main_deck_slot', {'main_deck_slot': 1}),
        ('/api/user/update_profile_settings', {'profile_settings': [0, 1, 1]}),
        ('/api/user/update_birth_date', {'birth_date': '2000-01-01'}),
    ):
        response, result = api('a'*64, path, body)
        assert response.status_code == 200 and result['code'] == 0
    assert handlers.USER_STORE.load_user(a).user.profile_settings == [0, 1, 1]
    assert handlers.USER_STORE.load_user(a).user.birth_date == '2000-01-01'
    assert handlers.USER_STORE.load_user(b).to_dict() == before_b


def test_original_client_mutation_paths_save_changes(cloud):
    _, api = cloud
    user_id = api('a'*64)[1]['data']['user_id']
    data = handlers.USER_STORE.load_user(user_id)
    group = next(row for row in data.costume_list if len(row.master_costume_ids) > 1)
    target = next(item for item in group.master_costume_ids if item != group.master_costume_id)
    response, result = api('a'*64, '/api/costume', {
        'master_character_id': group.master_character_id, 'master_costume_id': target,
    })
    assert response.status_code == 200 and result['code'] == 0
    from api.storage import UserStore
    saved = UserStore(handlers.USER_STORE.path).load_user(user_id)
    assert next(row for row in saved.costume_list
                if row.master_character_id == group.master_character_id).master_costume_id == target
    card = saved.card_list[-1]
    assert api('a'*64, '/api/user', {'favorite_master_card_id': card.master_card_id,
                                   'favorite_card_evolve': 0})[1]['code'] == 0
    assert api('a'*64, '/api/user', {'profile_settings': [1, 0]})[1]['code'] == 0
    assert api('a'*64, '/api/user', {'birth_date': '2000-01-01'})[1]['code'] == 0
    assert api('a'*64, '/api/user', {'profile_settings': [1], 'id': user_id + 1})[1]['code'] != 0
    saved = UserStore(handlers.USER_STORE.path).load_user(user_id)
    assert saved.user.favorite_master_card_id == card.master_card_id
    assert saved.user.profile_settings == [1, 0] and saved.user.birth_date == '2000-01-01'
    other_id = api('b'*64)[1]['data']['user_id']
    other_before = handlers.USER_STORE.load_user(other_id).to_dict()
    before_gem, before_stamina = saved.gem.total, saved.stamina.stamina
    response, result = api('a'*64, '/api/shop/buy', {'master_shop_item_id': 1})
    assert response.status_code == 200 and result['code'] == 0
    assert result['data']['shop_list']['master_shop_item_id'] == 1
    assert result['data']['updated_value_list']['stamina']['stamina'] == before_stamina + 3
    saved = UserStore(handlers.USER_STORE.path).load_user(user_id)
    assert saved.gem.total == before_gem - 50
    assert handlers.USER_STORE.load_user(other_id).to_dict() == other_before


def test_cloud_url():
    assert validate_public_url('https://game.example.org/')=='https://game.example.org'
    assert validate_public_url('http://203.0.113.10:18081')=='http://203.0.113.10:18081'
    for value in ('ftp://example.org','https://user:pass@example.org','https://example.org/api','https://example.org?token=x','http://example.org:99999'):
        with pytest.raises(ValueError): validate_public_url(value)


def test_draw_and_migrated_save_stay_with_the_authenticated_player(cloud):
    app,api=cloud
    a=api('a'*64)[1]['data']['user_id'];b=api('b'*64)[1]['data']['user_id']
    before=handlers.USER_STORE.load_user(b).to_dict()
    response,result=api('a'*64,'/api/lottery/draw',{'master_lottery_id':10100001,'master_lottery_price_number':4})
    assert response.status_code==200 and result['code']==0
    assert handlers.USER_STORE.load_user(b).to_dict()==before
    assert len(handlers.USER_STORE.lottery_list(a))==383
    from api.models import UserGetData
    from api.identity import CloudIdentity
    handlers.USER_STORE.save_user('legacy',UserGetData.create_default(888))
    app.config['CLOUD_IDENTITY'].bind_existing('c'*64,888)
    assert api('c'*64)[1]['data']['user_id']==888
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):app.config['CLOUD_IDENTITY'].bind_existing('c'*64,a)
    assert CloudIdentity(handlers.USER_STORE).resolve('c'*64)==888
