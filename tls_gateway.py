#!/usr/bin/env python3
"""Terminate game TLS and route asset hosts to CDN, all others to the API."""

import argparse
import logging
import os
import socket
from process_guard import start_parent_guard
import ssl
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
CERT_FILE = BASE_DIR / "var" / "certs" / "gateway_trust_cert.pem"
KEY_FILE = BASE_DIR / "var" / "certs" / "game_key.pem"
LOG_DIR = BASE_DIR / "var" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 后端路由
DEFAULT_API_HOST, DEFAULT_API_PORT = "127.0.0.1", 8888   # Flask
DEFAULT_CDN_HOST, DEFAULT_CDN_PORT = "127.0.0.1", 8000   # CDN
CDN_HOST_KEYWORDS = ("prd-asset",)  # Host 含这些关键词走 CDN

BUFFER_SIZE = 65536
RECV_TIMEOUT = 30
BACKEND_TIMEOUT = 120

logger = logging.getLogger("nanaon.gateway")


# ═══════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════

def setup_logging(log_file: Path = LOG_DIR / "gateway.log", level: str = "DEBUG"):
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.DEBUG),
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[console_handler, file_handler],
    )


def _preview(data: bytes, n: int = 256) -> str:
    """生成 body 预览：可见字符直显 + 末尾 hex"""
    if not data:
        return "<empty>"
    head = data[:n]
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
    return f"{printable}  |hex:{head[:64].hex()}"


# ═══════════════════════════════════════════════════════════
# HTTP 解析
# ═══════════════════════════════════════════════════════════

def recv_until_headers(sock: socket.socket) -> bytes:
    """读取直到收到 \r\n\r\n（请求头结束）"""
    data = b""
    sock.settimeout(RECV_TIMEOUT)
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(BUFFER_SIZE)
        except socket.timeout:
            break
        if not chunk:
            break
        data += chunk
        if len(data) > 1 * 1024 * 1024:  # 头部上限 1MB 防恶意
            break
    return data


def parse_headers(raw: bytes):
    """从原始字节切出 headers 区与 body 已读部分，返回 (headers_str, host, content_length, is_chunked, body_so_far)"""
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        return "", None, 0, False, b""
    headers_blob = raw[:sep].decode("latin-1", errors="replace")
    body_so_far = raw[sep + 4:]
    host = None
    content_length = 0
    is_chunked = False
    for line in headers_blob.split("\r\n")[1:]:  # 跳过请求行
        if not line:
            continue
        k, _, v = line.partition(":")
        k = k.strip().lower()
        v = v.strip()
        if k == "host":
            host = v
        elif k == "content-length":
            try:
                content_length = int(v)
            except ValueError:
                content_length = 0
        elif k == "transfer-encoding" and "chunked" in v.lower():
            is_chunked = True
    return headers_blob, host, content_length, is_chunked, body_so_far


def read_full_body(sock: socket.socket, body_so_far: bytes, content_length: int, is_chunked: bool) -> bytes:
    """按 Content-Length 或 chunked 读完请求体"""
    if content_length > 0:
        while len(body_so_far) < content_length:
            try:
                chunk = sock.recv(BUFFER_SIZE)
            except socket.timeout:
                break
            if not chunk:
                break
            body_so_far += chunk
        return body_so_far[:content_length]
    if is_chunked:
        # 读到 0\r\n\r\n 结束
        while b"\r\n0\r\n\r\n" not in body_so_far and not body_so_far.endswith(b"\r\n0\r\n"):
            try:
                chunk = sock.recv(BUFFER_SIZE)
            except socket.timeout:
                break
            if not chunk:
                break
            body_so_far += chunk
            if len(body_so_far) > 50 * 1024 * 1024:
                break
        return body_so_far
    # 无 Content-Length 且非 chunked：GET 类请求，body_so_far 即为已读（通常为空）
    return body_so_far


# ═══════════════════════════════════════════════════════════
# 后端选择
# ═══════════════════════════════════════════════════════════

def select_backend(host: str, api_port: int, cdn_port: int):
    """根据 Host 头选后端 (host, port)"""
    if host:
        h = host.lower()
        for kw in CDN_HOST_KEYWORDS:
            if kw in h:
                return ("127.0.0.1", cdn_port)
    return ("127.0.0.1", api_port)


# ═══════════════════════════════════════════════════════════
# 转发
# ═══════════════════════════════════════════════════════════

def forward_to_backend(
    backend_host: str,
    backend_port: int,
    raw_request: bytes,
    client_sock: ssl.SSLSocket,
) -> tuple[int, bytes]:
    """把后端响应边读边发给客户端，仅保留少量预览用于日志。"""
    be = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    be.settimeout(BACKEND_TIMEOUT)
    try:
        be.connect((backend_host, backend_port))
        be.sendall(raw_request)
        # 关闭写半边，让后端能 flush 响应（对 Flask 的 dev server 友好）
        try:
            be.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        total = 0
        preview = bytearray()
        while True:
            try:
                chunk = be.recv(BUFFER_SIZE)
            except socket.timeout:
                break
            if not chunk:
                break
            client_sock.sendall(chunk)
            total += len(chunk)
            if len(preview) < 512:
                preview.extend(chunk[: 512 - len(preview)])
        return total, bytes(preview)
    finally:
        try:
            be.close()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════
# 连接处理
# ═══════════════════════════════════════════════════════════

def handle_client(tls_sock: ssl.SSLSocket, addr, api_port: int, cdn_port: int):
    peer = f"{addr[0]}:{addr[1]}"
    try:
        tls_sock.settimeout(RECV_TIMEOUT)
        raw = recv_until_headers(tls_sock)
        if not raw:
            return
        headers_blob, host, content_length, is_chunked, body_so_far = parse_headers(raw)
        body = read_full_body(tls_sock, body_so_far, content_length, is_chunked)

        # 重建完整请求字节（headers + \r\n\r\n + body）
        # The gateway has already received the body. Forwarding Expect makes
        # Werkzeug emit duplicate 100 Continue responses.
        backend_headers = [
            line for line in headers_blob.split("\r\n")
            if not line.lower().startswith("expect:")
        ]
        request_bytes = (
            "\r\n".join(backend_headers).encode("latin-1")
            + b"\r\n\r\n"
            + body
        )

        # 选后端
        backend = select_backend(host, api_port, cdn_port)
        req_line = headers_blob.split("\r\n", 1)[0] if headers_blob else "?"
        logger.info(f"REQ {peer} Host={host} → {backend[0]}:{backend[1]} | {req_line}")
        if body:
            logger.info(f"  body({len(body)}B): {_preview(body)}")

        # 落盘原始加密请求体（抓密文主力）
        if body and backend[1] == api_port:
            try:
                ts = int(time.time())
                with open(LOG_DIR / f"enc_requests_{ts}.b64", "wb") as f:
                    f.write(body)
            except OSError:
                pass

        # 转发
        response_size, response_preview = forward_to_backend(
            backend[0], backend[1], request_bytes, tls_sock
        )
        if response_size:
            logger.info(
                f"RESP {peer} {response_size}B: {_preview(response_preview)}"
            )
        else:
            logger.warning(f"RESP {peer} <empty from backend>")
    except ssl.SSLError as e:
        logger.warning(f"TLS error {peer}: {e}")
    except socket.timeout:
        logger.warning(f"Timeout {peer}")
    except Exception as e:
        logger.error(f"Handle error {peer}: {type(e).__name__}: {e}")
    finally:
        try:
            tls_sock.close()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def build_ssl_context(cert_file: Path, key_file: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1  # Android 7 兼容
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))

    # SNI 回调：当前单证书已覆盖全部 SAN；预留按 server_name 选证书
    def _sni_callback(ssock, server_name, sslctx):
        return None  # 用默认证书即可

    ctx.set_servername_callback(_sni_callback)
    return ctx


def main():
    start_parent_guard()
    if pid_file := os.environ.get("NANAON_PID_FILE"):
        Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    parser = argparse.ArgumentParser(description="ナナオン TLS 网关 (443→后端)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--cdn-port", type=int, default=DEFAULT_CDN_PORT)
    parser.add_argument("--cert", default=str(CERT_FILE))
    parser.add_argument("--key", default=str(KEY_FILE))
    parser.add_argument("--log-level", default="DEBUG")
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    logger.info(f"=== TLS Gateway ===")
    logger.info(f"listen : {args.host}:{args.port}")
    logger.info(f"API    → 127.0.0.1:{args.api_port}")
    logger.info(f"CDN    → 127.0.0.1:{args.cdn_port}")
    logger.info(f"cert   : {args.cert}")

    if not Path(args.cert).exists() or not Path(args.key).exists():
        logger.error("证书或密钥文件不存在，退出")
        sys.exit(1)

    ssl_ctx = build_ssl_context(Path(args.cert), Path(args.key))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((args.host, args.port))
    except PermissionError:
        logger.error(f"绑定 {args.port} 失败：权限不足（443 需管理员/root，或改 --port）")
        sys.exit(1)
    except OSError as e:
        logger.error(f"绑定 {args.port} 失败：{e}（端口被占用？）")
        sys.exit(1)
    srv.listen(50)
    logger.info("listening...")

    while True:
        try:
            client_sock, addr = srv.accept()
        except OSError:
            continue
        try:
            tls_sock = ssl_ctx.wrap_socket(client_sock, server_side=True)
        except ssl.SSLError as e:
            logger.warning(f"握手失败 {addr}: {e}")
            try:
                client_sock.close()
            except OSError:
                pass
            continue
        t = threading.Thread(
            target=handle_client,
            args=(tls_sock, addr, args.api_port, args.cdn_port),
            daemon=True,
        )
        t.start()


if __name__ == "__main__":
    main()
