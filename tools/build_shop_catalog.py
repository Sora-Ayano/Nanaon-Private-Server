"""Export the three recovered gem shop items and their actual rewards."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def build(source):
    hashes = {}
    def table(name):
        raw = (source / (name + '.bytes')).read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        return list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))[2:]
    rewards = {}
    for row in table('common_reward'):
        rewards.setdefault(row['id'], []).append({
            'type': row['type'], 'amount': int(row['amount']),
        })
    items = []
    for row in table('shop_item'):
        reward = rewards[row['master_common_reward_id']]
        if row['consume_type'] != 'GEM' or any(x['type'] not in ('STAMINA', 'LIVE_BATTLE_POINT') for x in reward):
            raise ValueError('unsupported shop item')
        items.append(dict(id=int(row['id']), price=int(row['price']),
                          buy_limit=int(row['buy_limit']), rewards=reward))
    return dict(schema=1, sources=hashes, items=items)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--master-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.write_text(json.dumps(build(a.master_dir), indent=2)+'\n', encoding='utf-8')
