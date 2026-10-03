import pytest
from api.lottery_catalog import catalog, select
from api.models import User, UserGetData, Gem, Item, Card
from api.storage import UserStore


def test_every_recovered_pool_draws_and_restarts(tmp_path):
    store=UserStore(tmp_path/'users.sqlite3');defs=catalog()
    ticket_ids={p['master_item_id'] for x in defs['lotteries'] for p in x['prices'] if p['consume_type']=='ITEM'}
    data=UserGetData(user=User(id=42),gem=Gem(total=2000000,free=1000000,charge=1000000),
                     item_list=[Item(i,t,10000) for i,t in enumerate(sorted(ticket_ids),1)])
    store.save_user('player',data)
    for pool in defs['lotteries']:
        price=next((x for x in pool['prices'] if x['consume_type']=='GEM' and x['count']==10),pool['prices'][0])
        results,cost=store.complete_lottery_draw('player',data,pool['master_lottery_id'],price['number'],{})
        assert len(results)==price['count'] and cost==price['price']
        assert all(r['master_lottery_item_id'] in {g['item_group'] for g in pool['groups']} for r in results)
    restarted=UserStore(store.path).load_user(42)
    assert restarted.gem==data.gem
    assert restarted.ability_card_list==data.ability_card_list and restarted.ability_card_list
    assert all(c.breakthrough_level<=5 for c in restarted.card_list)
    with store._connect() as db:
        assert db.execute('SELECT COUNT(DISTINCT master_lottery_id) FROM lottery_draws').fetchone()[0]==144


def test_pickup_group_boundaries():
    defs=catalog()
    class Fixed:
        def __init__(self,roll):self.roll=roll
        def randrange(self,stop):assert stop==10000;return self.roll
        def choice(self,items):return items[0]
    for pool in defs['lotteries']:
        boundary=0
        for group in [g for g in pool['groups'] if not g['ensured']]:
            item,_=select(pool,1,defs,Fixed(boundary))[0]
            assert item['master_lottery_item_id']==group['item_group']
            assert item['pickup']==int(group['pickup_ratio']==10000)
            boundary+=group['weight']


@pytest.mark.parametrize('before',[0,4])
def test_duplicate_breakthrough_cap_and_currency(tmp_path,monkeypatch,before):
    store=UserStore(tmp_path/'users.sqlite3');defs=catalog()
    item=next(x for rows in defs['item_groups'].values() for x in rows if x['reward']['type']=='CARD')
    cid=item['reward']['value']
    data=UserGetData(user=User(id=42),gem=Gem(total=10000,free=10000),card_list=[Card(1,cid,breakthrough_level=before)])
    store.save_user('player',data)
    monkeypatch.setattr('api.lottery_catalog.select',lambda *_:[(item,0)]*10)
    store.complete_lottery_draw('player',data,10100001,4,{})
    assert data.card_list[0].breakthrough_level==5 and data.card_sub_list==[]
    assert next(p.amount for p in data.point_list if p.type==2)==defs['cards'][str(cid)]['duplicate_seal']*10
    assert data._lottery_effects['card_breakthrough_list']==[dict(master_card_id=cid,before=before,after=5)]
