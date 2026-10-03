import copy
from api import handlers
from api.models import UserGetData, ParsedRequest, AreaItemSetting
from api.storage import UserStore
from api.area_layout import ensure_room_layout, max_level
from api.card_costume_catalog import ensure_available_costumes, available_costume_inventory


def test_available_costumes_and_profile_survive_restart(tmp_path,monkeypatch):
    store=UserStore(tmp_path/'users.sqlite3');data=UserGetData.create_default(42)
    store.save_user('player',data)
    monkeypatch.setattr(handlers,'USER_STORE',store)
    monkeypatch.setattr(handlers,'USER_DB',{})
    monkeypatch.setattr(handlers,'_GAME_CONFIG',{**handlers._GAME_CONFIG,'multi_user':False,'playable_minimal_cards':False})
    group=next(x for x in available_costume_inventory() if len(x['master_costume_ids'])>1)
    target=group['master_costume_ids'][-1]
    request=ParsedRequest(20000,'POST','/api/costume',{'master_character_id':group['master_character_id'],'master_costume_id':target},None,{},user_id=42)
    assert handlers.handle_costume_update(request)['code']==0
    data=store.load_user(42)
    assert next(x for x in data.costume_list if x.master_character_id==group['master_character_id']).master_costume_id==target
    card=data.card_list[-1];card.evolve=1
    assert handlers._apply_user_profile_update(data,{'favorite_master_card_id':card.master_card_id,'favorite_card_evolve':1})
    handlers._persist_user(data)
    saved=UserStore(store.path).load_user(42)
    assert saved.user.favorite_master_card_id==card.master_card_id and saved.user.favorite_card_evolve==1
    before=saved.to_dict()
    assert not handlers._apply_user_profile_update(saved,{'name':'invalid edit','favorite_master_card_id':99999999})
    assert saved.to_dict()==before
    assert sum(len(x.master_costume_ids) for x in saved.costume_list)>=359
    ensure_available_costumes(saved)
    assert next(x for x in saved.costume_list if x.master_character_id==group['master_character_id']).master_costume_id==target


def test_empty_room_repaired_but_custom_arrangement_retained():
    data=UserGetData.create_default(42)
    assert data.area_item_setting_list
    assert all(1<=item.level<=max_level(item.master_area_item_id) for item in data.area_item_list)
    data.area_item_setting_list=[];data.area_item_list[0].level=10
    assert ensure_room_layout(data)
    assert data.area_item_list[0].level==max_level(data.area_item_list[0].master_area_item_id)
    custom=[AreaItemSetting(data.area_item_setting_list[0].master_area_position_id,0)]
    data.area_item_setting_list=copy.deepcopy(custom)
    assert not ensure_room_layout(data) and data.area_item_setting_list==custom
