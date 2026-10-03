"""Restore missing room structure without changing an existing arrangement."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def definitions():
    return json.loads((Path(__file__).resolve().parents[1]/'data/area_layout.json').read_text(encoding='utf-8'))


def max_level(master_id):
    return int(definitions()['max_levels'].get(str(master_id),0))


def default_settings(owned_ids):
    from api.models import AreaItemSetting
    instances={int(master):index for index,master in enumerate(owned_ids,1)}
    return [AreaItemSetting(row['position_id'],instances[row['default_item_id']])
            for row in definitions()['positions'] if row['default_item_id'] in instances]


def ensure_room_layout(user_data):
    changed=False
    for item in user_data.area_item_list:
        limit=max_level(item.master_area_item_id)
        if limit>0 and not 1<=item.level<=limit:
            item.level=max(1,min(item.level,limit));changed=True
    if user_data.area_item_setting_list:return changed
    from api.models import AreaItemSetting
    owned={x.master_area_item_id:x.id for x in user_data.area_item_list}
    user_data.area_item_setting_list=[AreaItemSetting(row['position_id'],owned[row['default_item_id']])
        for row in definitions()['positions'] if row['default_item_id'] in owned]
    return changed or bool(user_data.area_item_setting_list)
