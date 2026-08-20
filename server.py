#!/usr/bin/env python3
"""Flask API process used behind the local TLS gateway."""

import argparse
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from process_guard import start_parent_guard

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "var" / "logs"
ENC_REQ_DIR = LOG_DIR / "enc_requests"
ENC_REQ_DIR.mkdir(parents=True, exist_ok=True)
DECRYPTED_REQ_LOG = LOG_DIR / "decrypted_requests.jsonl"

logger = logging.getLogger("nanaon.server")

# ═══════════════════════════════════════════════════════════
# 路径 → FuncId 推断 (保留旧逻辑)
# ═══════════════════════════════════════════════════════════

_PATH_FUNCID_MAP = {
    "/api/environment/server": 100,
    "/api/auth/login": 970,
    "/api/auth/start_user": 1040,
    "/api/start": 980,
    "/api/live_music": 16002,
    "/api/message": 31000,
    "/api/member_multi_live/period": 35000,
    "/api/point_exchange": 14200,
    "/api/lottery/draw": 7010,
    "/api/lottery/convert": 7020,
    "/api/lottery": 7000,
    "/api/talk/get": 11100,
    "/api/talk": 11100,
    "/api/musical_score/get_favorite_count": 38044,
    "/api/musical_score/material": 39010,
    "/api/musical_score": 38000,
    "/api/tutorial/skip": 43000,
    "/api/friend": 8000,
    "/api/device/login": 970,
    "/api/session/start": 980,
    "/api/user/get": 1000,
    "/api/user/info": 1002,
    "/api/user/initialize": 1010,
    "/api/user/detail": 1020,
    "/api/user/migration": 1030,
    "/api/user/update/main_deck_slot": 1110,
    "/api/user/update/favorite_card_id": 1120,
    "/api/user/update/profile_settings": 1130,
    "/api/user/update/title_id": 1140,
    "/api/user/update/birth_date": 1142,
    "/api/user/update": 1100,
    "/api/home": 1200,
    "/api/gift/receive": 2010,
    "/api/gift": 2000,
    "/api/live/start": 3000,
    "/api/live/end": 3010,
    "/api/live/retire": 3020,
    "/api/live/continue": 3030,
    "/api/live/live_viewing": 3040,
    "/api/live/viewing": 3040,
    "/api/live/setup": 3060,
    "/api/live": 3050,
    "/api/card/reinforce": 4000,
    "/api/card/skill_reinforce": 4010,
    "/api/card/evolve": 4020,
    "/api/card/episode_unlock": 4030,
    "/api/card/episode_read": 4040,
    "/api/card/illust_change": 4050,
    "/api/card/sub_exchange": 4100,
    "/api/deck": 5000,
    "/api/story/end_card": 13200,
    "/api/mission": 9000,
    "/api/login_bonus": 12000,
    "/api/title": 15000,
    "/api/shop": 22000,
    "/api/error": 150,
}


def _infer_func_id_from_path(path: str, method: str = "GET") -> Optional[int]:
    # The released client uses the same /api/lottery path for both listing
    # (GET) and drawing (POST).  Treating POST as LOTTERY_GET returns the wrong
    # protocol object and crashes MngGachaData.SetLotteryResult.
    if path == "/api/lottery" and method.upper() == "POST":
        return 7010
    if path == "/api/point_exchange" and method.upper() == "POST":
        return 14300
    if path == "/api/talk" and method.upper() == "POST":
        return 11000
    if path in _PATH_FUNCID_MAP:
        return _PATH_FUNCID_MAP[path]

    # Complete fallback generated from the recovered FuncId naming scheme.
    # Examples: character_vote/get_all -> CHARACTER_VOTE_GET_ALL and
    # subscription/should_restore -> SUBSCRIPTION_SHOULD_RESTORE.
    from crypto.funcid import FuncId

    if path.startswith("/api/"):
        normalized = path[5:].strip("/").replace("-", "_").replace("/", "_").upper()
        candidates = [normalized, f"{normalized}_GET"]
        for candidate in candidates:
            member = FuncId.__members__.get(candidate)
            if member is not None and not member.name.endswith("_R"):
                return int(member)

    # Last resort for endpoints carrying a numeric/detail suffix.  Longest
    # prefix wins so /friend/request/cancel can never collapse to /friend.
    for url_pattern in sorted(_PATH_FUNCID_MAP, key=len, reverse=True):
        if path.startswith(url_pattern + "/"):
            return _PATH_FUNCID_MAP[url_pattern]
    return None


# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8888,
    "debug": False,
    "log_level": "DEBUG",
    "crypto": {
        "aes_key": "",   # hex, 32 字节
        "aes_iv": "",    # hex, 16 字节
    },
    "environment": "private",
}


def load_config(config_path: str) -> dict:
    """加载 YAML 或 JSON 配置"""
    path = Path(config_path)
    if not path.exists():
        logger.warning(f"Config not found: {path}, using defaults")
        return dict(DEFAULT_CONFIG)
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            cfg = yaml.safe_load(text) or {}
        except ImportError:
            logger.error("PyYAML 未安装，无法读 yaml 配置；用默认值")
            return dict(DEFAULT_CONFIG)
    else:
        cfg = json.loads(text)
    # 合并默认值
    merged = dict(DEFAULT_CONFIG)
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


# ═══════════════════════════════════════════════════════════
# 最近请求缓存 (调试端点用)
# ═══════════════════════════════════════════════════════════

_LAST_REQUESTS = []  # [(ts, host, path, func_id, body_preview, resp_preview)]
_LAST_MAX = 50


def _record_request(host, path, func_id, body_preview, resp_preview):
    _LAST_REQUESTS.append((int(time.time()), host, path, func_id, body_preview, resp_preview))
    if len(_LAST_REQUESTS) > _LAST_MAX:
        _LAST_REQUESTS.pop(0)


# ═══════════════════════════════════════════════════════════
# Flask 应用工厂
# ═══════════════════════════════════════════════════════════

def create_app(config: dict):
    from flask import Flask, request, jsonify, Response
    from crypto.funcid import FuncId
    from crypto import NanaPacker
    from api.router import router
    from api.models import ParsedRequest
    from api import handlers
    from cdn.asset_server import asset_bp

    handlers.configure_game(config.get("game", {}))

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = config.get("max_request_size", 10 * 1024 * 1024)

    crypto_cfg = config.get("crypto", {})
    packer = None
    if crypto_cfg.get("aes_key"):
        packer = NanaPacker(
            key_hex=crypto_cfg["aes_key"],
            iv_hex=crypto_cfg.get("aes_iv") or None,  # 可选固定 IV; 默认随机前置
        )
        logger.info(f"Encryption: ENABLED (key={crypto_cfg['aes_key'][:16]}...)")
    else:
        logger.info("Encryption: DISABLED (no key yet — 落盘密文 + 明文回退)")

    # ── 健康检查 ───────────────────────────────────────────
    @app.route("/health")
    @app.route("/")
    def health():
        return jsonify({
            "status": "ok",
            "server": "NanaOn Private Server",
            "version": "2.4.0",
            "environment": config.get("environment", "private"),
            "encryption": packer is not None and packer.has_key,
            "timestamp": int(time.time()),
        })

    # ── 主 API 端点 ────────────────────────────────────────
    @app.route("/api", methods=["POST", "GET", "OPTIONS"])
    @app.route("/api/", methods=["POST", "GET", "OPTIONS"])
    @app.route("/Prod", methods=["POST", "GET", "OPTIONS"])
    @app.route("/Prod/", methods=["POST", "GET", "OPTIONS"])
    @app.route("/api/<path:subpath>", methods=["POST", "GET", "OPTIONS"])
    def handle_api(subpath: str = ""):
        raw_body = request.get_data()
        body_text = raw_body.decode("utf-8", errors="replace") if raw_body else ""
        host = request.headers.get("Host", "")

        json_data = None
        func_id = None
        decrypted_ok = False

        # 1) 若有密钥，尝试解密
        if packer and packer.has_key and body_text:
            try:
                decrypted = packer.decode_string(body_text.strip())
                logger.debug(f"DECRYPT OK: {decrypted[:200]}")
                json_data = json.loads(decrypted)
                if isinstance(json_data, dict) and "func_id" in json_data:
                    func_id = json_data["func_id"]
                decrypted_ok = True
            except Exception as e:
                logger.warning(f"DECRYPT FAIL: {type(e).__name__}: {e}")

        # 2) 解密失败或无密钥：尝试当明文 JSON
        if json_data is None and body_text:
            try:
                json_data = json.loads(body_text)
                if isinstance(json_data, dict) and "func_id" in json_data:
                    func_id = json_data["func_id"]
            except (json.JSONDecodeError, ValueError):
                pass

        # 3) Header / query / path 推断
        if func_id is None:
            fid_str = (request.headers.get("X-Func-Id")
                       or request.headers.get("X-FuncId")
                       or request.args.get("func_id"))
            if fid_str:
                try:
                    func_id = int(fid_str)
                except ValueError:
                    pass
        if func_id is None:
            func_id = _infer_func_id_from_path(request.path, request.method)

        # 4) 落盘未解密的密文 (抓密文主力，无论是否有密钥都落)
        if body_text and not decrypted_ok:
            try:
                fid_tag = func_id if func_id is not None else "unk"
                ts = int(time.time())
                fp = ENC_REQ_DIR / f"fid{fid_tag}_{ts}.b64"
                fp.write_text(body_text, encoding="utf-8")
                logger.info(f"Saved encrypted body → {fp.name} ({len(body_text)}B)")
            except OSError as e:
                logger.warning(f"Save enc body fail: {e}")

        if func_id is None:
            func_id = 0

        if decrypted_ok:
            try:
                capture = {
                    "timestamp": int(time.time()),
                    "host": host,
                    "method": request.method,
                    "path": request.path,
                    "func_id": int(func_id),
                    "request": json_data,
                }
                with DECRYPTED_REQ_LOG.open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(capture, ensure_ascii=False) + "\n")
            except (OSError, TypeError, ValueError) as e:
                logger.warning(f"Save decrypted request fail: {e}")

        parsed = ParsedRequest(
            func_id=func_id,
            method=request.method,
            uri=request.path,
            json_data=json_data,
            raw_body=raw_body if not decrypted_ok else None,
            headers=dict(request.headers),
            access_token=request.headers.get("Authorization", "").replace("Bearer ", ""),
            user_id=json_data.get("user_id") if isinstance(json_data, dict) else None,
            uuid_param=json_data.get("uuid") if isinstance(json_data, dict) else None,
            appsflyer_id=json_data.get("appsflyer_id") if isinstance(json_data, dict) else None,
            timestamp=json_data.get("time_stamp") if isinstance(json_data, dict) else None,
            client_version=json_data.get("client_version") if isinstance(json_data, dict) else None,
        )

        response = router.dispatch(parsed)

        # 构建响应体
        response_json = json.dumps(response, ensure_ascii=False)
        if packer and packer.has_key:
            response_body = packer.encode_string(response_json)
            logger.debug(f"Encrypted response ({len(response_body)} chars)")
        else:
            # 无密钥：Base64 编码明文 JSON (游戏会报错，但能观察流程)
            response_body = base64.b64encode(response_json.encode("utf-8")).decode("ascii")
            logger.debug(f"Plain+Base64 response ({len(response_body)} chars)")

        body_preview = body_text[:120] if body_text else ""
        _record_request(host, request.path, func_id, body_preview, response_body[:120])

        return Response(
            response_body,
            status=200,
            mimetype="application/json",
            headers={
                "X-Server": "NanaOn-Private-Server",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Methods": "*",
            },
        )

    # ── CDN blueprint (gateway 通常把 CDN 流量直转 :8000，这里也挂一份兜底) ──
    try:
        app.register_blueprint(asset_bp)
    except Exception:
        pass

    # ── 调试端点 ───────────────────────────────────────────
    @app.route("/admin/last_requests")
    def admin_last_requests():
        return jsonify([
            {"ts": t, "host": h, "path": p, "func_id": f,
             "req_preview": rb, "resp_preview": rp}
            for (t, h, p, f, rb, rp) in _LAST_REQUESTS
        ])

    @app.route("/admin/replay", methods=["POST"])
    def admin_replay():
        """用指定密钥重放一个落盘的密文请求，验证密钥正确性"""
        data = request.get_json(silent=True) or {}
        fname = data.get("file")
        key = data.get("aes_key", crypto_cfg.get("aes_key"))
        iv = data.get("aes_iv", crypto_cfg.get("aes_iv"))
        if not (fname and key and iv):
            return jsonify({"error": "need file, aes_key, aes_iv"}), 400
        fp = ENC_REQ_DIR / fname
        if not fp.exists():
            return jsonify({"error": f"file not found: {fname}"}), 404
        body = fp.read_text(encoding="utf-8").strip()
        try:
            pk = NanaPacker(key_hex=key, iv_hex=iv)
            decrypted = pk.decode_string(body)
            return jsonify({"ok": True, "decrypted": decrypted[:2000]})
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 200

    @app.route("/admin/enc_requests")
    def admin_enc_requests():
        files = sorted(ENC_REQ_DIR.glob("*.b64"), reverse=True)
        return jsonify({"files": [f.name for f in files[:100]]})

    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    return app


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    start_parent_guard()
    if pid_file := os.environ.get("NANAON_PID_FILE"):
        Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    parser = argparse.ArgumentParser(description="ナナオン Private Server (Flask API)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--debug", action="store_true", default=None)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)

    # 命令行覆盖
    if args.host is not None:
        config["host"] = args.host
    if args.port is not None:
        config["port"] = args.port
    if args.debug is not None:
        config["debug"] = args.debug

    # 日志
    logging.basicConfig(
        level=getattr(logging, config.get("log_level", "DEBUG").upper(), logging.DEBUG),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(LOG_DIR / "server.log"), encoding="utf-8"),
        ],
    )

    print(f"""
╔══════════════════════════════════════════════╗
║  ナナオン Private Server v2.4.0 (API)        ║
╠══════════════════════════════════════════════╣
║  Listen:  {config['host']}:{config['port']:<24}║
║  Enc:    {'ENABLED' if config.get('crypto',{}).get('aes_key') else 'DISABLED (capture mode)':<24}║
║  EncReq: {str(ENC_REQ_DIR):<24}║
║  Debug:  {'ON' if config.get('debug') else 'OFF':<24}║
╚══════════════════════════════════════════════╝
""")

    app = create_app(config)
    app.run(
        host=config["host"],
        port=config["port"],
        debug=config.get("debug", False),
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
