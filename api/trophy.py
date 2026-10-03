"""Build the showroom response from owned inventory and recovered master rows."""
import json
from functools import lru_cache
from pathlib import Path
from api.master_catalog import load_master_catalog


@lru_cache(maxsize=1)
def catalog():
    path = Path(__file__).resolve().parents[1] / 'data/trophy_catalog.json'
    return json.loads(path.read_text(encoding='utf-8'))


def showroom_response(user):
    # Use content IDs, not instance counts: duplicate cards/furniture must not
    # grant extra collection trophies. Read-only projection preserves saves.
    music_by_live = {x['id']: x['music_id'] for x in load_master_catalog()['lives']}
    counts = {
        'CARD': len({x.master_card_id for x in user.card_list}),
        'MUSIC': len(set(user.master_music_ids)),
        'COSTUME': len({i for x in user.costume_list for i in x.master_costume_ids})
                   + len({int(i) for x in user.model_costume_list
                          for i in x.get('master_model_costume_ids', [])}),
        'AREA_ITEM': len({x.master_area_item_id for x in user.area_item_list}),
        'TITLE': len(set(user.master_title_ids)),
        'STAMP': len(set(user.master_stamp_ids)),
        'MOVIE': len({int(x.get('id', x.get('master_local_movie_id', 0)))
                      for x in user.local_movie_detail_list} - {0}),
        # EX illustrations are tracked separately from repeated AP plays.
        'ALL_PERFECT': len({music_by_live.get(int(x.get('master_live_id', 0)), 0)
                           for x in user.live_list if x.get('all_perfect_count', 0)
                           and int(x.get('level', 0)) in (4, 5)} - {0}),
    }
    trophies = []
    for item in catalog()['trophies']:
        count = counts[item['type']]
        grade = max((g for g in item['grades'] if g['achievement_condition_count'] <= count),
                    key=lambda g: g['achievement_condition_count'])
        trophies.append(dict(master_trophy_id=item['id'], count=count,
                             master_trophy_grade_id=grade['master_trophy_grade_id'],
                             master_trophy_detail_point=grade['point']))
    points = sum(x['master_trophy_detail_point'] for x in trophies)
    room = max((x for x in catalog()['showrooms'] if x['point'] <= points), key=lambda x: x['point'])
    return dict(trophy_list=trophies,
                user_data=dict(master_trophy_grade_id=room['master_trophy_grade_id'],
                               total_master_trophy_detail_point=points),
                upgraded_trophy_list=[], upgraded_showroom=None)
