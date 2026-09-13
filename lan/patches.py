"""Verify a local language release and expose only its declared cache files."""
import json
from pathlib import Path
from lan.resources import safe_relative, sha256


def load_patch(directory):
    directory=Path(directory).resolve()
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!=1 or manifest.get('client_version_code')!=5465:
        raise ValueError('Unsupported language patch')
    result={}
    for item in manifest['files']:
        name=safe_relative(item['path'])
        bundle=name.startswith('Android/bundle/') and name.endswith('.unity3d')
        game_manifest=name.startswith('Android/manifest/manifest/') and name.endswith('/__data') and len(name.split('/'))==5
        cache_database=name=='Android/bundle/db'
        if not (bundle or game_manifest or cache_database) or name in result:
            raise ValueError('Invalid/duplicate patch target')
        path=directory/name
        if not path.is_file() or not path.resolve().is_relative_to(directory) or path.is_symlink():
            raise ValueError('Patch file missing or outside release')
        if path.stat().st_size!=item['size'] or sha256(path)!=item['sha256']:
            raise ValueError('Language patch hash mismatch: '+name)
        result[name]=path
    return result, manifest
