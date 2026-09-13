from pathlib import Path
import re
from flask import Blueprint, abort, jsonify, render_template_string, send_file


def make_blueprint(catalog, distribution, public_url):
    bp = Blueprint('lan', __name__)
    distribution = Path(distribution).resolve()

    @bp.get('/bootstrap/manifest.json')
    def manifest():
        response = jsonify(catalog.manifest)
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @bp.get('/bootstrap/files/<identity>')
    def file(identity):
        if not re.fullmatch('[a-f0-9]{64}', identity):
            abort(404)
        try:
            path = catalog.resolve(identity)
        except (OSError, RuntimeError):
            abort(409, 'Resource changed; restart the server to rebuild its index')
        if path is None:
            abort(404)
        return send_file(path, mimetype='application/octet-stream', conditional=True,
                         etag=catalog.records[identity]['sha256'])

    @bp.get('/client/<name>')
    def apk(name):
        if not re.fullmatch(r'nanaon-lan-[A-Za-z0-9_.-]+\.apk', name):
            abort(404)
        path = (distribution / name).resolve()
        if not path.is_relative_to(distribution) or not path.is_file():
            abort(404)
        return send_file(path, as_attachment=True, mimetype='application/vnd.android.package-archive', conditional=True)

    @bp.get('/play')
    def play():
        apks = sorted((p.name for p in distribution.glob('nanaon-lan-*.apk')), reverse=True)
        return render_template_string('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nanaon 局域网</title>
<style>body{font:17px/1.7 system-ui;background:#eff7fb;color:#16374c;margin:0;padding:28px}main{max-width:680px;margin:40px auto;background:white;padding:32px;border-radius:20px}a{display:block;padding:14px;background:#0769ad;color:white;border-radius:12px;margin:16px 0;text-decoration:none}code{word-break:break-all}small{color:#557080}</style>
<main><h1>Nanaon 局域网版</h1><p>手机与电脑连接同一个局域网。安装后打开游戏，资源会直接从这台电脑下载，不需要 root 或 USB 传输。</p>
<p>服务器：<code>{{ url }}</code></p>{% for name in apks %}<a href="/client/{{ name }}">下载 {{ name }}</a>{% else %}<p>这里仅运行服务端。可将配套构建包生成的 nanaon-lan-*.apk 放入 dist 目录，再刷新本页下载。</p>{% endfor %}
<p>首次下载约 {{ gib }} GiB，请保持服务器运行。下载中断后可继续，完整校验后才会启动游戏。</p>
<p>当前游戏语言：{{ locale }}。在电脑启动服务端时可选择语言，默认日语。</p>
{% if experimental %}<p>中文为实验版，当前存在主数据资源加载错误，尚未通过运行验证。日常游玩请选择日语。</p>{% endif %}
<small>独立包名 com.aniplex.nananiji.lan，保留原应用。IP 改变时可在启动器中更新服务器地址。</small></main></html>''', url=public_url, apks=apks, gib=round(catalog.manifest['total_bytes'] / 2**30, 2),locale='简体中文（实验版）' if catalog.locale=='zh-Hans' else '日本語（日语）',experimental=catalog.locale=='zh-Hans')

    return bp
