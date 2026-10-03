"""Optional curated incremental downloads; never exports the full archive."""
import hashlib
import json
import re
from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file
from lan.resources import safe_relative, sha256


def make_updates_blueprint(directory=None):
    bp = Blueprint('updates', __name__)
    package = None
    paths = {}
    if directory is not None:
        root = Path(directory).resolve()
        source = root/'manifest.json'
        if source.is_symlink(): raise ValueError('update manifest must be a real file')
        package = json.loads(source.read_text(encoding='utf-8'))
        if (package.get('schema') != 1 or package.get('version_code') != 5465
                or package.get('locale') not in ('ja-JP','zh-Hans')):
            raise ValueError('unsupported update package')
        for key in ('base_revision','revision'):
            if not re.fullmatch(r'[a-f0-9]{64}', package.get(key,'')):
                raise ValueError('invalid revision')
        if package['base_revision'] == package['revision']: raise ValueError('unchanged update')
        records = package['files']
        if not isinstance(records,list) or not 1 <= len(records) <= 50000: raise ValueError('invalid files')
        targets = set()
        for row in records:
            name = row['path']; safe_relative(name)
            kind = row['kind']
            if not (kind == 'cache' and name.startswith('Android/') or kind == 'obb' and name == 'main.5465.obb'
                    or kind == 'acf' and name == 'TTSCriProject.acf'):
                raise ValueError('invalid update target')
            if (kind,name) in targets: raise ValueError('duplicate target')
            targets.add((kind,name))
            identity = row['id']
            if (not re.fullmatch(r'[a-f0-9]{64}', identity)
                    or not re.fullmatch(r'[a-f0-9]{64}',row['sha256'])
                    or row['url'] != '/bootstrap/files/'+identity):
                raise ValueError('invalid file identity')
            path = root/'files'/identity
            if (path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file()
                    or path.stat().st_size != row['size'] or sha256(path) != row['sha256']):
                raise ValueError('update file missing or changed')
            paths[identity] = (path, path.stat().st_size, path.stat().st_mtime_ns)
        if sum(x['size'] for x in records) != package['total_bytes']: raise ValueError('invalid total')

    @bp.get('/bootstrap/updates.json')
    def updates():
        if package is None: return '',204
        revision = request.args.get('revision','')
        if revision == package['revision']: return '',204
        if revision != package['base_revision'] or request.args.get('locale') != package['locale']:
            return jsonify(error='update_base_mismatch'),409
        response = jsonify(package)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/bootstrap/files/<identity>')
    def update_file(identity):
        item = paths.get(identity)
        if item is None: abort(404)
        path,size,stamp = item
        if path.is_symlink() or not path.is_file() or (path.stat().st_size,path.stat().st_mtime_ns)!=(size,stamp): abort(409)
        return send_file(path, conditional=True, etag=identity, max_age=0)
    return bp
