#!/usr/bin/env python3
"""Run the local resource CDN used by the TLS gateway."""

import argparse
import logging
import os
import sys
from pathlib import Path

from process_guard import start_parent_guard

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))


def main():
    start_parent_guard()
    if pid_file := os.environ.get("NANAON_PID_FILE"):
        Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    parser = argparse.ArgumentParser(description="ナナオン CDN 资源服务器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    from flask import Flask
    from cdn.asset_server import asset_bp

    app = Flask(__name__)
    app.register_blueprint(asset_bp)

    print(f"CDN server: http://{args.host}:{args.port}/assets")
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
