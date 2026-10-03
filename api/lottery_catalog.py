"""Master-backed group probabilities and durable draw rewards for 144 pools."""
import copy
import json
import random
import time
from functools import lru_cache
from pathlib import Path

from api.lottery import draw_counts, DrawLimitReached, InsufficientTicket
from api.models import Card, Item, Point


@lru_cache(maxsize=1)
def catalog():
    return json.loads((Path(__file__).resolve().parents[1]/'data/preservation_lottery.json').read_text(encoding='utf-8'))


def install(db, data):
    for pool in data['lotteries']:
        pid=pool['master_lottery_id'];first=pool['prices'][0]
        db.execute('DELETE FROM master_lottery_items WHERE master_lottery_id=?',(pid,))
        db.execute('''INSERT OR REPLACE INTO master_lotteries
            (master_lottery_id,name,cost_resource_type,cost_resource_id,cost_amount,draw_count,
             data_json,source,verification_status) VALUES (?,?,?,?,?,?,?,'decrypted_master','verified')''',
            (pid,pool['name'],'item' if first['consume_type']=='ITEM' else 'gem',first['master_item_id'],
             first['price'],first['count'],json.dumps(pool,ensure_ascii=False)))
        eligible={x['item_group'] for x in pool['groups']}
        sequence=0
        for gid in sorted(eligible):
            for item in data['item_groups'][str(gid)]:
                sequence+=1;r=item['reward'];definitions=data['cards'] if r['type']=='CARD' else data['ability_cards']
                db.execute('INSERT INTO master_lottery_items VALUES (?,?,?,?,?,?,?,?)',
                    (pid,sequence,r['type'].lower(),r['value'],r['amount'],1,definitions[str(r['value'])]['rarity'],json.dumps(item)))


def select(pool,count,data,rng=None):
    rng=rng or random.SystemRandom();results=[]
    for index in range(count):
        ensured=int(count==10 and index==9 and any(x['ensured'] for x in pool['groups']))
        groups=[x for x in pool['groups'] if x['ensured']==ensured]
        roll=rng.randrange(sum(x['weight'] for x in groups));position=roll
        for group in groups:
            if position < group['weight']:
                items=[x for x in data['item_groups'][str(group['item_group'])]
                       if x['pickup']==int(group['pickup_ratio']==10000)]
                if not items:raise ValueError('empty reward group')
                results.append((rng.choice(items),roll));break
            position-=group['weight']
    return results


def complete(store,uuid_param,data,pool_id,price_number,payload):
    definitions=catalog();pool=next((x for x in definitions['lotteries'] if x['master_lottery_id']==pool_id),None)
    if pool is None:raise ValueError('unknown lottery')
    price=next((x for x in pool['prices'] if x['number']==price_number),None)
    if price is None:raise ValueError('unknown price')
    user_id=data.user.id;now=int(time.time());cost=price['price'];effects={'card_breakthrough_list':[], 'ability_card_level_up_list':[], 'reward_list':[], 'model_costumes':[]}
    ledger={}
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT uuid FROM users WHERE user_id=?',(user_id,)).fetchone()
        if row is None or row['uuid']!=uuid_param:raise ValueError('unknown account')
        updated=store.load_user(user_id)
        count,daily=draw_counts(db,user_id,pool_id,price_number,now)
        if price['limit_count'] and count>=price['limit_count'] or price['daily_limit_count'] and daily>=price['daily_limit_count']:
            raise DrawLimitReached('draw limit reached')
        currency=price['consume_type']
        if currency=='ITEM':
            item=next((x for x in updated.item_list if x.master_item_id==price['master_item_id']),None)
            if item is None or item.amount<cost:raise InsufficientTicket('insufficient ticket')
            item.amount-=cost;ledger[('item',item.master_item_id)]=-cost
        elif currency in ('GEM','CHARGE_GEM'):
            if (updated.gem.charge if currency=='CHARGE_GEM' else updated.gem.total)<cost:raise OverflowError('insufficient gem')
            free=min(cost,updated.gem.free) if currency=='GEM' else 0
            updated.gem.free-=free;updated.gem.charge-=cost-free;updated.gem.total-=cost;ledger[('gem',0)]=-cost
        else:raise ValueError('unknown currency')
        def add_point(kind,amount):
            point=next((x for x in updated.point_list if x.type==kind),None)
            if point is None:point=Point(kind,0);updated.point_list.append(point)
            point.amount+=amount;ledger[('point',kind)]=ledger.get(('point',kind),0)+amount
        def level_effect(field,key,value,before,after):
            # The client constructs a dictionary by master ID; one ten-draw
            # can upgrade the same card several times but must notify it once.
            prior=next((x for x in effects[field] if x[key]==value),None)
            if prior is None:effects[field].append({key:value,'before':before,'after':after})
            else:prior['after']=after
        def grant(rewards):
            for reward in rewards:
                value=reward['value'];amount=reward['amount'];kind=reward['type']
                if kind=='ITEM':
                    item=next((x for x in updated.item_list if x.master_item_id==value),None)
                    if item is None:item=Item(max((x.id for x in updated.item_list),default=0)+1,value,0);updated.item_list.append(item)
                    item.amount+=amount;ledger[('item',value)]=ledger.get(('item',value),0)+amount
                elif kind=='TITLE':
                    if value not in updated.master_title_ids:updated.master_title_ids.append(value)
                elif kind=='MODEL_COSTUME':
                    group=definitions['model_rewards'].get(str(value))
                    if group is None:raise ValueError('unknown model costume reward')
                    changed=False
                    for character in group['character_ids']:
                        for costume in group['costume_ids']:
                            part=definitions['model_costume_types'][str(costume)]
                            row=next((x for x in updated.model_costume_list if x['master_character_id']==character and x['master_model_costume_type']==part),None)
                            if row is None:
                                row=dict(master_character_id=character,master_model_costume_type=part,master_model_costume_ids=[],master_model_costume_id=costume if part==1 else 0);updated.model_costume_list.append(row)
                            if costume not in row['master_model_costume_ids']:
                                row['master_model_costume_ids'].append(costume);changed=True
                    if changed:
                        effects['model_costumes'].append(dict(master_model_costume_ids=group['costume_ids'],master_character_ids=group['character_ids']))
                else:raise ValueError('unsupported reward '+kind)
                effects['reward_list'].append(dict(type={'ITEM':3,'MODEL_COSTUME':7,'TITLE':10}[kind],value=value,level=reward['level'],amount=amount))
        chosen=select(pool,price['count'],definitions);results=[]
        for item,roll in chosen:
            r=item['reward'];cid=r['value'];kind=r['type'];lookup=definitions['cards'] if kind=='CARD' else definitions['ability_cards'];definition=lookup[str(cid)]
            if kind=='CARD':
                owned=next((x for x in updated.card_list if x.master_card_id==cid),None);is_new=owned is None
                if is_new:
                    owned=Card(max((x.id for x in updated.card_list),default=0)+1,cid);updated.card_list.append(owned)
                    ledger[('card',cid)]=ledger.get(('card',cid),0)+1
                else:
                    before=owned.breakthrough_level;after=min(definitions['max_breakthrough'],before+1);owned.breakthrough_level=after
                    add_point(2,definition['duplicate_seal'])
                    if after>before:
                        level_effect('card_breakthrough_list','master_card_id',cid,before,after)
                        grant(definition['breakthrough_rewards'].get(str(after),[]))
            elif kind=='ABILITY_CARD':
                owned=next((x for x in updated.ability_card_list if x['master_ability_card_id']==cid),None);is_new=owned is None
                if is_new:
                    owned=dict(id=max((x['id'] for x in updated.ability_card_list),default=0)+1,master_ability_card_id=cid,level=max(1,r['level']));updated.ability_card_list.append(owned)
                    ledger[('ability_card',cid)]=ledger.get(('ability_card',cid),0)+1
                else:
                    before=owned['level'];after=min(definition['max_level'],before+1);owned['level']=after;add_point(2,definition['duplicate_seal'])
                    if after>before:
                        level_effect('ability_card_level_up_list','master_ability_card_id',cid,before,after);grant(definition['level_rewards'].get(str(after),[]))
            else:raise ValueError('unsupported draw reward')
            results.append(dict(master_lottery_item_id=item['master_lottery_item_id'],master_lottery_item_number=item['master_lottery_item_number'],is_new=int(is_new)))
        grant(price['bonus_rewards'])
        exchange=pool.get('exchange')
        if exchange:
            reward_count=price['count']*exchange['acquisition_number_per_once']
            grant([{**r,'amount':r['amount']*reward_count} for r in exchange['rewards']])
        store.save_user(uuid_param,updated,connection=db)
        tid=db.execute('''INSERT INTO state_transactions (user_id,func_id,reason,request_json,created_at)
            VALUES (?,7010,'lottery_draw',?,?)''',(user_id,json.dumps({**payload,'master_lottery_id':pool_id,'master_lottery_price_number':price_number}),now)).lastrowid
        for index,(item,roll) in enumerate(chosen):
            r=item['reward'];db.execute('''INSERT INTO lottery_draws
                (transaction_id,user_id,master_lottery_id,draw_index,reward_type,reward_id,reward_amount,random_value,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',(tid,user_id,pool_id,index,r['type'].lower(),r['value'],r['amount'],roll,now))
        def balance(kind,value):
            if kind=='gem':return updated.gem.total
            if kind=='point':return next(x.amount for x in updated.point_list if x.type==value)
            if kind=='item':return next(x.amount for x in updated.item_list if x.master_item_id==value)
            return 1
        for (kind,value),delta in ledger.items():
            db.execute('INSERT INTO state_ledger_entries (transaction_id,resource_type,resource_id,delta,balance_after) VALUES (?,?,?,?,?)',
                       (tid,kind,value,delta,balance(kind,value)))
    for field in ('gem','card_list','card_sub_list','item_list','point_list','ability_card_list','master_title_ids','model_costume_list'):
        setattr(data,field,getattr(updated,field))
    data._lottery_effects=effects
    return results,cost
