"""Build an incremental package from two public full resource manifests."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from lan.resources import safe_relative, sha256


def build(old, new, source_root, output, *, source_catalog=None):
    if output.exists(): raise FileExistsError('Choose a new output folder')
    if old['locale'] != new['locale'] or old['version_code'] != new['version_code']:
        raise ValueError('locale/version changes require a local full resource package')
    for manifest in (old,new):
        if manifest['schema']!=1 or not re.fullmatch(r'[a-f0-9]{64}',manifest['revision']):raise ValueError('invalid manifest')
        targets=set()
        for row in manifest['files']:
            safe_relative(row['path'])
            key=(row['kind'],row['path'])
            if key in targets:raise ValueError('duplicate resource target')
            targets.add(key)
            if (row['kind']=='cache' and not row['path'].startswith('Android/')) or row['kind'] not in ('cache','obb','acf'):
                raise ValueError('invalid resource kind')
            if row['kind']=='obb' and row['path']!='main.5465.obb' or row['kind']=='acf' and row['path']!='TTSCriProject.acf':raise ValueError('invalid resource target')
            if not all(re.fullmatch(r'[a-f0-9]{64}',row[field]) for field in ('id','sha256')) or row['url']!='/bootstrap/files/'+row['id']:raise ValueError('invalid resource digest')
    before = {(x['kind'],x['path']):x for x in old['files']}
    after = {(x['kind'],x['path']):x for x in new['files']}
    if not before.keys() <= after.keys(): raise ValueError('removing files is not supported')
    rows = [x for key,x in after.items() if key not in before or x['sha256']!=before[key]['sha256']]
    if not rows: raise ValueError('no changed files')
    checked = []
    if source_catalog is None:source_root = source_root.resolve()
    for row in rows:
        safe_relative(row['path'])
        kind = row['kind']
        # Source tree is an exported client file tree: cache/, obb/, acf/.
        path = source_catalog.resolve(row['id']) if source_catalog else source_root/kind/row['path']
        if path is None or path.is_symlink() or (source_catalog is None and not path.resolve().is_relative_to(source_root)): raise ValueError('invalid source')
        if path.stat().st_size!=row['size'] or sha256(path)!=row['sha256']: raise ValueError('file mismatch')
        checked.append((row,path))
    (output/'files').mkdir(parents=True)
    for row,path in checked: shutil.copy2(path,output/'files'/row['id'])
    package = dict(schema=1,version_code=new['version_code'],locale=new['locale'],
                   base_revision=old['revision'],revision=new['revision'],
                   total_bytes=sum(x['size'] for x in rows),files=rows)
    (output/'manifest.json').write_text(json.dumps(package,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return package


def catalog_from_archive(root,locale,state):
    """Read this release's resources directly; no running server or player DB."""
    from cdn.asset_server import asset_server
    from cdn.bootstrap_sources import compatibility_sources
    from lan.patches import load_patch
    from lan.resources import ResourceCatalog
    root=root.resolve();patch_root=BASE/'patches'/locale
    if (patch_root/'manifest.json').is_file():overrides,_=load_patch(patch_root)
    elif locale=='ja-JP':overrides={}
    else:raise FileNotFoundError('Chinese patch is missing')
    asset_server.ASSET_BASE=root/'main.5465.com.aniplex.nananiji/assets'
    asset_server.CACHE_BASE=root/'com.aniplex.nananiji'
    asset_server.DOWNLOAD_CACHE=asset_server.CACHE_BASE/'files/DownloadCache'
    asset_server.FALLBACK_ASSETS=dict(asset_server.FALLBACK_ASSETS)
    asset_server.FALLBACK_ASSETS[asset_server.NOTICE_STORY_FALLBACK_KEY]=asset_server.DOWNLOAD_CACHE/'Android/sound/118b868d886c15a8383d0ea1064b1d58/Voice/PART_10600000_000.acb'
    compatibility=compatibility_sources(asset_server,state/'generated-resources')
    catalog=ResourceCatalog(root,state/'resource-index.json',overrides,patch_root,locale,compatibility)
    catalog.build()
    return catalog


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old-manifest',type=Path,required=True)
    p.add_argument('--new-manifest',type=Path,help='Required with --files; full public manifest')
    sources=p.add_mutually_exclusive_group(required=True)
    sources.add_argument('--files',type=Path,help='Exported cache/, obb/, acf/ file tree')
    sources.add_argument('--resource-root',type=Path,help='Build directly from this release archive')
    p.add_argument('--locale',choices=['ja-JP','zh-Hans'],default='ja-JP')
    p.add_argument('--state-dir',type=Path,default=BASE/'var/update-build')
    p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    if a.files and not a.new_manifest:p.error('--files requires --new-manifest')
    catalog=catalog_from_archive(a.resource_root,a.locale,a.state_dir) if a.resource_root else None
    new=catalog.manifest if catalog else json.loads(a.new_manifest.read_text(encoding='utf-8'))
    result = build(json.loads(a.old_manifest.read_text(encoding='utf-8-sig')),new,a.files,a.output,source_catalog=catalog)
    print(f"Incremental package: {len(result['files'])} files, {result['total_bytes']} bytes")
