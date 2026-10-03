"""Build a distributable lottery catalog from decrypted v2.4.0 CSV tables.

Usage: python tools/build_lottery_catalog.py --master-dir PATH --output PATH
No local input paths, player data or network addresses enter the output.
"""
import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def build(master_dir):
    sources = {}

    def table(name):
        raw = (master_dir / (name + '.bytes')).read_bytes()
        sources[name + '.bytes'] = hashlib.sha256(raw).hexdigest()
        return list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))[2:]

    def group(rows, key='id'):
        result = defaultdict(list)
        for row in rows:
            result[int(row[key])].append(row)
        return result

    def array(raw):
        return [int(x) for x in raw.strip('[]').split('/') if x and x != '0']

    rewards = group(table('common_reward'))
    def reward_list(reward_id):
        if not reward_id:
            return []
        rows = rewards[reward_id]
        if not rows:
            raise ValueError(f'missing reward {reward_id}')
        return [dict(type=x['type'], value=int(x['value']),
                     level=int(x['level']), amount=int(x['amount'])) for x in rows]

    prices = group(table('lottery_price'))
    rates = group(table('lottery_rarity'))
    raw_items = group(table('lottery_item'))
    releases = {int(x['id']): x for x in table('release_label')}
    exchanges = {int(x['master_lottery_id']): x for x in table('lottery_exchange')}
    steps = table('lottery_stepup')
    if steps:
        raise ValueError('step-up rows require a separately verified implementation')
    # The original explanatory text establishes automatic duplicate upgrades
    # and the daily reset at 04:00 Japan time.
    table('lottery_info')
    lotteries = []
    used_groups = set()
    for raw in table('lottery'):
        lottery_id = int(raw['id'])
        rows = rates[int(raw['master_lottery_rarity_id'])]
        if not rows:
            raise ValueError(f'missing probabilities for {lottery_id}')
        groups = []
        for row in rows:
            item_group = int(row['master_lottery_item_id'])
            pickup_ratio = int(row['pickup_ratio'])
            if pickup_ratio not in (0, 10000):
                raise ValueError(f'unverified pickup semantics: {row}')
            eligible = [x for x in raw_items[item_group]
                        if int(x['pickup']) == int(pickup_ratio == 10000)]
            if not eligible or int(row['ratio']) <= 0:
                raise ValueError(f'empty probability group: {row}')
            used_groups.add(item_group)
            groups.append(dict(number=int(row['number']), item_group=item_group,
                               rarity=int(row['rarity'].removeprefix('RARE')),
                               weight=int(row['ratio']), pickup_ratio=pickup_ratio,
                               ensured=int(row['ensured'])))
        for ensured in (0, 1):
            weight = sum(x['weight'] for x in groups if x['ensured'] == ensured)
            if weight != 10000 and not (ensured == 1 and weight == 0):
                raise ValueError(f'{lottery_id}: probabilities sum to {weight} for {ensured}')
        price_list = []
        for row in prices[int(raw['master_lottery_price_id'])]:
            price = {key: int(row[key]) for key in
                     ('number', 'count', 'price', 'limit_count', 'daily_limit_count', 'master_item_id')}
            price['consume_type'] = row['consume_type']
            price['bonus_rewards'] = reward_list(int(row['master_common_reward_id']))
            if price['count'] not in (1, 10) or price['price'] < 0:
                raise ValueError(f'unsupported price: {row}')
            price_list.append(price)
        if not price_list:
            raise ValueError(f'missing price {lottery_id}')
        release = releases[int(raw['master_release_label_id'])]
        entry = dict(master_lottery_id=lottery_id, name=raw['name'],
                     master_lottery_image_id=int(raw['master_lottery_image_id']),
                     release_label_id=int(raw['master_release_label_id']),
                     original_opened_at=release['opened_at'], original_closed_at=release['closed_at'],
                     prices=price_list, groups=groups)
        if lottery_id in exchanges:
            exchange = exchanges[lottery_id]
            entry['exchange'] = {key: int(exchange[key]) for key in
                                 ('master_exchange_id', 'acquisition_number_per_once',
                                  'limit_seal_count', 'reason_id_for_convert_item')}
            entry['exchange']['rewards'] = reward_list(int(exchange['master_common_reward_id']))
        lotteries.append(entry)
    item_groups = {}
    for group_id in sorted(used_groups):
        group_items = []
        for row in raw_items[group_id]:
            reward = reward_list(int(row['master_common_reward_id']))
            if len(reward) != 1 or reward[0]['type'] not in ('CARD', 'ABILITY_CARD') or reward[0]['amount'] != 1:
                raise ValueError(f'unsupported draw reward: {row}')
            group_items.append(dict(master_lottery_item_id=group_id,
                                    master_lottery_item_number=int(row['number']),
                                    master_common_reward_id=int(row['master_common_reward_id']),
                                    pickup=int(row['pickup']), reward=reward[0]))
        item_groups[str(group_id)] = group_items
    card_rarity = {x['rarity']: int(x['exchange_seal_amount']) for x in table('card_rarity')}
    ability_rarity = {x['rarity']: int(x['exchange_ticket_amount']) for x in table('ability_card_rarity')}
    breakthroughs = group(table('card_breakthrough_reward'), 'master_card_id')
    ability_level_rewards = group(table('ability_card_level_up_reward'), 'master_ability_card_id')
    cards = {}
    for row in table('card'):
        cid = int(row['id'])
        cards[str(cid)] = dict(rarity=int(row['rarity'].removeprefix('RARE')),
                              duplicate_seal=card_rarity[row['rarity']],
                              costume_ids=array(row['master_costume_id_list']),
                              model_reward_id=int(row['master_model_reward_id']),
                              breakthrough_rewards={x['level']: [r for rid in array(x['master_common_reward_ids'])
                                                                  for r in reward_list(rid)]
                                                    for x in breakthroughs[cid]})
    abilities = {}
    for row in table('ability_card'):
        cid = int(row['id'])
        abilities[str(cid)] = dict(rarity=int(row['rarity'].removeprefix('RARE')),
                                  duplicate_seal=ability_rarity[row['rarity']],
                                  max_level=int(row['max_level']),
                                  level_rewards={x['level']: [r for rid in array(x['master_common_reward_ids'])
                                                             for r in reward_list(rid)]
                                                 for x in ability_level_rewards[cid]})
    for items in item_groups.values():
        for item in items:
            reward = item['reward']
            lookup = cards if reward['type'] == 'CARD' else abilities
            if str(reward['value']) not in lookup:
                raise ValueError(f'missing reward definition: {reward}')
    costumes = {x['id']: int(x['master_character_id']) for x in table('costume')}
    model_types = {'BODY': 1, 'HEAD': 2, 'FACE': 3, 'BACK': 4}
    model_definitions = {x['id']: model_types[x['model_costume_type']] for x in table('model_costume')}
    model_rewards = {x['id']: dict(costume_ids=array(x['master_model_costume_id_list']),
                                    character_ids=array(x['master_character_id_list']))
                     for x in table('model_costume_reward')}
    return dict(schema_version=2, mode='preservation_all_recovered_pools',
                daily_reset_jst_hour=4, max_breakthrough=max(int(x['level']) for x in table('card_breakthrough')),
                sources=sources, lotteries=lotteries, item_groups=item_groups,
                cards=cards, ability_cards=abilities, costume_characters=costumes,
                model_costume_types=model_definitions, model_rewards=model_rewards)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.master_dir)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Recovered {len(result['lotteries'])} pools, {len(result['item_groups'])} item groups")
