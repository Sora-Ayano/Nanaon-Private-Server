"""Content-addressed inventory. Never publishes saves, logs or arbitrary paths."""
import hashlib
import json
from pathlib import Path
import threading


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_relative(value):
    if not value or '\\' in value or ':' in value or any(ord(c) < 32 for c in value) or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Unsafe resource path')
    return value


class ResourceCatalog:
    def __init__(self, root, index_path, overrides=None, patch_root=None, locale='ja-JP'):
        self.root = Path(root).resolve()
        self.index_path = Path(index_path)
        self.paths = {}
        self.records = {}
        self.manifest = None
        self.overrides = overrides or {}
        self.allowed_roots = [self.root] + ([Path(patch_root).resolve()] if patch_root else [])
        self.locale = locale
        self._lock = threading.Lock()

    def build(self):
        with self._lock:
            old = {}
            try:
                old = json.loads(self.index_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
            cache = self.root / 'com.aniplex.nananiji/files/DownloadCache'
            inputs = [('obb', 'main.5465.obb', self.root / 'main.5465.com.aniplex.nananiji.obb'),
                      ('acf', 'TTSCriProject.acf', self.root / 'com.aniplex.nananiji/files/TTSCriProject.acf')]
            if not cache.is_dir():
                raise FileNotFoundError(f'资源目录不存在：{cache}')
            for path in sorted(cache.rglob('*')):
                if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                    raise ValueError(f'资源中存在链接：{path}')
                if path.is_file():
                    name = safe_relative(path.relative_to(cache).as_posix())
                    inputs.append(('cache', name, self.overrides.get(name, path)))
            names = {name for kind, name, _ in inputs if kind == 'cache'}
            if not set(self.overrides).issubset(names):
                raise ValueError('Patch targets do not match this resource archive')
            records, stamps, paths = [], {}, {}
            for kind, name, path in inputs:
                if not path.is_file() or path.is_symlink() or not any(path.resolve().is_relative_to(r) for r in self.allowed_roots):
                    raise FileNotFoundError(f'资源缺失或超出指定目录：{path}')
                stat = path.stat()
                key = kind + ':' + name
                stamp = [str(path.resolve()), stat.st_size, stat.st_mtime_ns]
                prior = old.get(key, {})
                digest = prior.get('sha256') if prior.get('stamp') == stamp else None
                if not digest:
                    digest = sha256(path)
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                        raise RuntimeError('资源在生成索引时被修改：' + name)
                identity = hashlib.sha256((key + ':' + digest).encode()).hexdigest()
                records.append(dict(id=identity, kind=kind, path=name, size=stat.st_size, sha256=digest,
                                    url='/bootstrap/files/' + identity))
                if kind=='cache' and name in self.overrides:
                    records[-1]['startup_check']=True
                paths[identity] = (path, stat.st_size, stat.st_mtime_ns)
                stamps[key] = dict(stamp=stamp, sha256=digest)
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.index_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(stamps), encoding='utf-8')
            temporary.replace(self.index_path)
            revision = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.paths = paths
            self.records = {r['id']: r for r in records}
            self.manifest = dict(schema=1, revision=revision, version_code=5465,
                                 total_bytes=sum(r['size'] for r in records), files=records, locale=self.locale)
            return self.manifest

    def resolve(self, identity):
        record = self.paths.get(identity)
        if record is None:
            return None
        path, size, mtime = record
        if path.is_symlink() or not any(path.resolve().is_relative_to(r) for r in self.allowed_roots):
            return None
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (size, mtime):
            raise RuntimeError('Resource changed; rebuild the catalog')
        return path
