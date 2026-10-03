"""Export initial room positions from the recovered master tables."""
import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def build(source):
    hashes={}
    def table(name):
        raw=(source/(name+'.bytes')).read_bytes();hashes[name]=hashlib.sha256(raw).hexdigest()
        return list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))[2:]
    positions=table('area_position');items=defaultdict(list);limits={}
    for item in table('area_item'):
        items[int(item['master_area_position_id'])].append(int(item['id']));limits[item['id']]=int(item['max_level'])
    return dict(schema=1,sources=hashes,max_levels=limits,positions=[dict(position_id=int(p['id']),area_id=int(p['master_area_id']),
        default_item_id=min(items[int(p['id'])])) for p in positions if items[int(p['id'])]])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--master-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(build(a.master_dir),indent=2)+'\n',encoding='utf-8')
