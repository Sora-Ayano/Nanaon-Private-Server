#!/usr/bin/env python3
"""Single-instance launcher for NanaonPrivate Server."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.dont_write_bytecode = True

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "var" / "run"
STATE_PATH = RUNTIME_DIR / "processes.json"
LOCK_PATH = RUNTIME_DIR / "lifecycle.lock"
logger = logging.getLogger("nanaon.run")


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")


@contextmanager
def lifecycle_lock():
    """Serialize overlapping start and stop commands."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stream = LOCK_PATH.open("a+b")
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def taskkill_tree(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        # Every launcher and service PID is recorded separately. Use the
        # Win32 API directly so shutdown never depends on PATH or taskkill.exe.
        import ctypes

        process_terminate = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(process_terminate, False, pid)
        if handle:
            try:
                ctypes.windll.kernel32.TerminateProcess(handle, 0)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def write_state(children: list[subprocess.Popen], services: dict[str, int]) -> None:
    value = {"launcher_pid": os.getpid(), "children": [p.pid for p in children], "services": services, "started_at": int(time.time())}
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(STATE_PATH)


def stop_previous_instance() -> None:
    if not STATE_PATH.exists():
        return
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        STATE_PATH.unlink(missing_ok=True)
        return
    pids = [int(x) for x in state.get("children", [])]
    pids.extend(int(x) for x in state.get("services", {}).values())
    pids.append(int(state.get("launcher_pid", 0) or 0))
    pids = list(dict.fromkeys(x for x in pids if x > 0))
    running = [pid for pid in pids if process_is_running(pid)]
    if running:
        logger.info("stopping previous instance pids=%s", running)
    for pid in reversed(running):
        taskkill_tree(pid)
    STATE_PATH.unlink(missing_ok=True)
    time.sleep(1)


def assert_port_available(host: str, port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as error:
        raise SystemExit(f"required port {host}:{port} is occupied by an unregistered process ({error})") from error
    finally:
        probe.close()


def ensure_gateway_certificate(gateway: dict) -> None:
    """Generate the bundled gateway certificate on first direct launch."""
    cert_value = gateway.get("cert", "var/certs/gateway_trust_cert.pem")
    key_value = gateway.get("key", "var/certs/game_key.pem")
    cert_path = Path(cert_value)
    key_path = Path(key_value)
    if not cert_path.is_absolute():
        cert_path = BASE_DIR / cert_path
    if not key_path.is_absolute():
        key_path = BASE_DIR / key_path
    if cert_path.is_file() and key_path.is_file():
        return
    generator = BASE_DIR / "runtime" / "generate_gateway_certificate.py"
    if not generator.is_file():
        raise RuntimeError(f"TLS certificate generator is missing: {generator}")
    logger.info("generating local TLS certificate")
    subprocess.run([sys.executable, "-B", str(generator)], cwd=BASE_DIR, check=True)
    if not cert_path.is_file() or not key_path.is_file():
        raise RuntimeError("TLS certificate generation did not create the configured files")


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def log_tail(log_path: Path, line_count: int = 30) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "<service log unavailable>"
    return "\n".join(lines[-line_count:]) or "<service log is empty>"


def spawn(command: list[str], name: str, log_path: Path) -> tuple[subprocess.Popen, int]:
    logger.info("starting %s: %s", name, " ".join(command))
    pid_path = RUNTIME_DIR / f"{name.lower()}.pid"
    pid_path.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment["NANAON_PID_FILE"] = str(pid_path)
    environment["NANAON_PARENT_PID"] = str(os.getpid())
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    output = log_path.open("w", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        # Services are non-interactive children.  Keeping them out of a second
        # console prevents wrapper-window closure from looking like a service
        # crash; the parent guard still stops them with the main launcher.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, cwd=BASE_DIR, creationflags=flags, env=environment)
    finally:
        output.close()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if pid_path.is_file():
            try:
                return process, int(pid_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                pass
        if process.poll() is not None and not pid_path.is_file():
            raise RuntimeError(
                f"{name} failed to start (exit code {process.returncode}).\n"
                f"--- {log_path.name} ---\n{log_tail(log_path)}"
            )
        time.sleep(0.05)
    taskkill_tree(process.pid)
    raise RuntimeError(
        f"{name} did not publish its runtime PID.\n"
        f"--- {log_path.name} ---\n{log_tail(log_path)}"
    )


def stop_children(children: list[subprocess.Popen], services: dict[str, int]) -> None:
    for pid in reversed(list(services.values())):
        taskkill_tree(pid)
    for process in reversed(children):
        if process.poll() is None:
            taskkill_tree(process.pid)
    STATE_PATH.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--no-api", action="store_true")
    parser.add_argument("--no-cdn", action="store_true")
    parser.add_argument("--no-gateway", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    setup_logging()
    children: list[subprocess.Popen] = []
    services: dict[str, int] = {}

    exit_code = 0
    with lifecycle_lock():
        stop_previous_instance()
        if args.stop:
            logger.info("previous private-server instance stopped")
            return 0
        config = load_yaml(BASE_DIR / args.config)
        api, cdn, gateway = config.get("api", {}), config.get("cdn", {}), config.get("gateway", {})
        if not args.no_gateway:
            ensure_gateway_certificate(gateway)
        api_host, api_port = api.get("host", "127.0.0.1"), int(api.get("port", 8888))
        cdn_host, cdn_port = cdn.get("host", "127.0.0.1"), int(cdn.get("port", 8000))
        gateway_host, gateway_port = gateway.get("host", "127.0.0.1"), int(gateway.get("port", 8443))
        for enabled, host, port in ((not args.no_api, api_host, api_port), (not args.no_cdn, cdn_host, cdn_port), (not args.no_gateway, gateway_host, gateway_port)):
            if enabled:
                assert_port_available(host, port)
        log_dir = BASE_DIR / "var" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        definitions: list[tuple[str, list[str]]] = []
        if not args.no_api:
            definitions.append(("API", [sys.executable, str(BASE_DIR / "server.py"), "--config", args.config, "--host", api_host, "--port", str(api_port)]))
        if not args.no_cdn:
            definitions.append(("CDN", [sys.executable, str(BASE_DIR / "cdn_run.py"), "--host", cdn_host, "--port", str(cdn_port)]))
        if not args.no_gateway:
            command = [sys.executable, str(BASE_DIR / "tls_gateway.py"), "--host", gateway_host, "--port", str(gateway_port), "--api-port", str(api_port), "--cdn-port", str(cdn_port)]
            if gateway.get("cert"):
                command.extend(["--cert", gateway["cert"]])
            if gateway.get("key"):
                command.extend(["--key", gateway["key"]])
            definitions.append(("gateway", command))
        try:
            for name, command in definitions:
                process, actual_pid = spawn(command, name, log_dir / f"{name.lower()}.out.log")
                children.append(process)
                services[name.lower()] = actual_pid
                write_state(children, services)
                time.sleep(0.5)
        except Exception:
            stop_children(children, services)
            raise

    logger.info("private server is ready; Ctrl+C stops the full process tree")
    try:
        while True:
            for name, pid in services.items():
                if not process_is_running(pid):
                    log_path = BASE_DIR / "var" / "logs" / f"{name}.out.log"
                    raise RuntimeError(
                        f"{name} service {pid} exited unexpectedly.\n"
                        f"--- {log_path.name} ---\n{log_tail(log_path)}"
                    )
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("stopping private server")
    except Exception:
        logger.exception("private server stopped because a service failed")
        exit_code = 1
    finally:
        stop_children(children, services)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
