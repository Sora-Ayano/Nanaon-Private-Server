"""Include required CDN compatibility files in the initial local download."""
import re
import struct
from pathlib import Path


def compatibility_sources(asset_server, generated):
    generated=Path(generated).resolve()
    mappings={**asset_server.FALLBACK_ASSETS,**asset_server._sound_overrides,**asset_server._movie_overrides}
    for key in asset_server._event_result_catalog:
        path=asset_server._resolve_event_result_fallback(key)
        if path is not None:mappings[key]=path
    records={}
    for key,path in mappings.items():
        key=key.lower().lstrip('/')
        match=re.fullmatch(r'android/(assetbundle|sounds|movies)/([a-f0-9]{28,32})/([a-f0-9]{28,32})(\.[a-z0-9]+)',key)
        if not match:raise ValueError('invalid compatibility CDN key')
        category,name,folder,suffix=match.groups();category={'assetbundle':'bundle','sounds':'sound','movies':'movie'}[category]
        relative=f'Android/{category}/{folder}/{name}{suffix}'
        path=Path(path).resolve()
        if not path.is_file():continue
        if not asset_server._allowed(path):raise ValueError('compatibility file outside resource roots')
        padded=asset_server.FALLBACK_SIZES.get(key)
        if padded is not None:
            raw=path.read_bytes()
            if len(raw)>padded or raw[:4]!=b'@UTF':raise ValueError('invalid padded compatibility sound')
            content=raw[:4]+struct.pack('>I',padded-8)+raw[8:]+bytes(padded-len(raw))
            target=generated/relative;target.parent.mkdir(parents=True,exist_ok=True)
            if not target.is_file() or target.read_bytes()!=content:target.write_bytes(content)
            path=target
        records[relative]=path
    return records
