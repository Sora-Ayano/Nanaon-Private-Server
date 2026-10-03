"""Atomic gem-shop purchases backed by the recovered master rewards."""
import json
import time
from functools import lru_cache
from pathlib import Path
from api.lottery import DrawLimitReached


@lru_cache(maxsize=1)
def catalog():
    return json.loads((Path(__file__).resolve().parents[1]/'data/shop_catalog.json').read_text(encoding='utf-8'))


def count(db, user_id, item_id):
    return db.execute('''SELECT COUNT(*) FROM state_transactions
        WHERE user_id=? AND reason='shop_buy'
        AND json_extract(request_json,'$.master_shop_item_id')=?''', (user_id, item_id)).fetchone()[0]


def inventory(store, user_id):
    with store._connect() as db:
        return [dict(master_shop_item_id=row['id'], count=count(db, user_id, row['id']))
                for row in catalog()['items']]


def purchase(store, data, item_id):
    item = next((row for row in catalog()['items'] if row['id'] == item_id), None)
    if item is None:
        raise ValueError('unknown shop item')
    user_id = data.user.id
    now = int(time.time())
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        uuid_row = db.execute('SELECT uuid FROM users WHERE user_id=?', (user_id,)).fetchone()
        if uuid_row is None:
            raise ValueError('unknown account')
        bought = count(db, user_id, item_id)
        if item['buy_limit'] and bought >= item['buy_limit']:
            raise DrawLimitReached('purchase limit reached')
        updated = store.load_user(user_id)
        cost = item['price']
        if updated.gem.total < cost:
            raise OverflowError('insufficient gem')
        free = min(cost, updated.gem.free)
        updated.gem.total -= cost
        updated.gem.free -= free
        updated.gem.charge -= cost-free
        reward_changes = []
        for reward in item['rewards']:
            if reward['type'] == 'STAMINA':
                before = updated.stamina.stamina
                if updated.stamina.stamina >= 99:
                    raise DrawLimitReached('boost points are full')
                updated.stamina.stamina = min(99, updated.stamina.stamina + reward['amount'])
                updated.stamina.last_updated_time = now
                after = updated.stamina.stamina
            elif reward['type'] == 'LIVE_BATTLE_POINT':
                point = dict(updated.live_battle_point or {})
                before = int(point.get('live_battle_point', 0))
                point['live_battle_point'] = before + reward['amount']
                point['last_updated_time'] = now
                updated.live_battle_point = point
                after = point['live_battle_point']
            else:
                raise ValueError('unsupported shop reward')
            reward_changes.append((reward['type'].lower(), after-before, after))
        store.save_user(uuid_row['uuid'], updated, connection=db)
        tid = db.execute('''INSERT INTO state_transactions
            (user_id,func_id,reason,request_json,created_at)
            VALUES (?,22100,'shop_buy',?,?)''',
            (user_id, json.dumps({'master_shop_item_id': item_id}), now)).lastrowid
        db.execute('''INSERT INTO state_ledger_entries
            (transaction_id,resource_type,resource_id,delta,balance_after)
            VALUES (?,'gem',0,?,?)''', (tid, -cost, updated.gem.total))
        for kind, delta, balance in reward_changes:
            db.execute('''INSERT INTO state_ledger_entries
                (transaction_id,resource_type,resource_id,delta,balance_after)
                VALUES (?,?,1,?,?)''', (tid, kind, delta, balance))
    # Never leave a failed purchase in the cached account state.
    data.gem, data.stamina, data.live_battle_point = updated.gem, updated.stamina, updated.live_battle_point
    return dict(master_shop_item_id=item_id, count=bought+1)
