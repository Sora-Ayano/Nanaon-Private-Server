import sqlite3
import pytest
from api.models import UserGetData, User, Gem
from api.storage import UserStore
from api.shop import inventory, purchase


def test_shop_rewards_balances_and_counts_survive_restart(tmp_path):
    store = UserStore(tmp_path/'users.sqlite3')
    data = UserGetData(user=User(id=42), gem=Gem(total=300, free=100, charge=200))
    store.save_user('player', data)
    data = store.load_user(42)
    assert len(inventory(store, 42)) == 3
    assert purchase(store, data, 1)['count'] == 1
    assert data.stamina.stamina == 13 and data.gem.total == 250
    assert purchase(store, data, 2)['count'] == 1
    assert data.stamina.stamina == 23 and data.gem.free == 0 and data.gem.charge == 150
    assert purchase(store, data, 3)['count'] == 1
    assert data.live_battle_point['live_battle_point'] == 5 and data.gem.total == 50
    restarted = UserStore(store.path)
    assert restarted.load_user(42).to_dict() == data.to_dict()
    assert all(row['count'] == 1 for row in inventory(restarted, 42))
    before = data.to_dict()
    with pytest.raises(OverflowError): purchase(store, data, 2)
    with pytest.raises(ValueError): purchase(store, data, 9999)
    assert data.to_dict() == before and restarted.load_user(42).to_dict() == before


def test_shop_rolls_back_debit_reward_and_count(tmp_path):
    store = UserStore(tmp_path/'users.sqlite3')
    data = UserGetData(user=User(id=42))
    store.save_user('player', data)
    data = store.load_user(42)
    before = data.to_dict()
    with store._connect() as db:
        db.execute("CREATE TRIGGER reject_shop BEFORE INSERT ON state_transactions BEGIN SELECT RAISE(ABORT,'rollback'); END")
    with pytest.raises(sqlite3.IntegrityError): purchase(store, data, 1)
    assert data.to_dict() == before and store.load_user(42).to_dict() == before
    assert all(row['count'] == 0 for row in inventory(store, 42))


def test_full_boost_points_do_not_charge_again(tmp_path):
    from api.lottery import DrawLimitReached
    store = UserStore(tmp_path/'users.sqlite3')
    data = UserGetData(user=User(id=42))
    data.stamina.stamina = 97
    store.save_user('player', data)
    data = store.load_user(42)
    purchase(store, data, 1)
    assert data.stamina.stamina == 99
    before = data.to_dict()
    with pytest.raises(DrawLimitReached): purchase(store, data, 1)
    assert data.to_dict() == before and store.load_user(42).to_dict() == before
    assert inventory(store, 42)[0]['count'] == 1
    with store._connect() as db:
        assert db.execute("SELECT delta,balance_after FROM state_ledger_entries WHERE resource_type='stamina'").fetchone()[:] == (2, 99)
