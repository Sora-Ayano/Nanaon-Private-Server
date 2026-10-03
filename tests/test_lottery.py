import sqlite3
import pytest

from api.models import UserGetData, User, Gem, Item
from api.storage import UserStore
from api.lottery import InsufficientTicket, DrawLimitReached, select_items


@pytest.fixture
def account(tmp_path):
    store = UserStore(tmp_path/'users.sqlite3')
    data = UserGetData(user=User(id=42), gem=Gem(total=10000, free=9900, charge=100),
                       item_list=[Item(id=1, master_item_id=11090101, amount=2)])
    store.save_user('test-install', data)
    return store, data


def draw(store, data, price=4):
    return store.complete_lottery_draw('test-install', data, 10100001, price, {})


def test_draw_grants_cards_and_survives_restart(account):
    store, data = account
    results, cost = draw(store, data)
    assert len(results) == 10 and cost == 2500
    assert data.gem.total == 7500 and data.gem.free == 7400
    saved = UserStore(store.path).load_user(42)
    assert len(saved.card_list) + sum(s.breakthrough_level for s in saved.card_list) == 10
    assert saved.card_sub_list == []
    assert saved.gem == data.gem
    assert results[-1]['master_lottery_item_id'] in (12900002, 12900003)
    assert sum(r['is_new'] for r in results) == len(saved.card_list)
    counts = store.lottery_list(42)
    assert len({x['master_lottery_id'] for x in counts}) == 144
    assert next(x for x in counts if x['master_lottery_id']==10100001 and x['master_lottery_price_number'] == 4)['count'] == 1


def test_single_ticket_and_paid_limit(account):
    store, data = account
    assert len(draw(store, data, 3)[0]) == 1
    balance = data.gem.total
    assert len(draw(store, data, 2)[0]) == 1
    assert data.gem.total == balance and data.item_list[0].amount == 1
    draw(store, data, 1)
    assert data.gem.charge == 40
    with pytest.raises(DrawLimitReached):
        draw(store, data, 1)
    with pytest.raises(InsufficientTicket):
        draw(store, data, 5)


def test_failed_transaction_does_not_charge_or_grant(account):
    store, data = account
    before = data.to_dict()
    with store._connect() as db:
        db.execute("""CREATE TRIGGER reject_draw BEFORE INSERT ON lottery_draws
            BEGIN SELECT RAISE(ABORT, 'test rollback'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        draw(store, data)
    assert data.to_dict() == before
    saved = store.load_user(42)
    assert saved.gem.total == 10000 and saved.card_list == []
    with store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM state_transactions').fetchone()[0] == 0


def test_two_stale_snapshots_cannot_overspend(account):
    store, data = account
    data.gem = Gem(total=2500, free=2500)
    store.save_user('test-install', data)
    stale = store.load_user(42)
    draw(store, data)
    with pytest.raises(OverflowError):
        draw(store, stale)
    assert store.load_user(42).gem.total == 0


def test_rarity_roll_is_independent_of_group_size():
    class FixedRoll:
        def randrange(self, stop):
            assert stop == 10000
            return 8000
        def choice(self, rows):
            return rows[0]
    rows = [{'rarity':1}]*11 + [{'rarity':2}]*22 + [{'rarity':3}]*25
    weights = [{'rarity':1,'weight':8000},{'rarity':2,'weight':1400},{'rarity':3,'weight':600}]
    assert select_items(rows,1,weights,[],FixedRoll())[0][0]['rarity'] == 2


def test_daily_limit_resets_at_four_jst(account):
    from api.lottery import draw_counts
    from datetime import datetime,timezone
    store,data=account;draw(store,data,1)
    midnight_utc=int(datetime(2026,10,2,19,tzinfo=timezone.utc).timestamp())
    with store._connect() as db:
        db.execute('UPDATE state_transactions SET created_at=?',(midnight_utc-1,))
        assert draw_counts(db,42,10100001,1,midnight_utc-1)==(1,1)
        assert draw_counts(db,42,10100001,1,midnight_utc)==(1,0)
