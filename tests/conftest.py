import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mihomo_py.manager import Manager

MINIMAL = "proxies: []\nrules:\n  - MATCH,DIRECT\n"


def free_ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


@pytest.fixture
def command(tmp_path):
    def run(*args, input=None, expected=0):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mihomo_py",
                "--data-dir",
                str(tmp_path / "home"),
                "--core-binary",
                os.environ.get("MIHOMO_TEST_BINARY", "mihomo"),
                *args,
            ],
            input=input,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == expected, (result.stdout, result.stderr, result.returncode)
        return result

    return run


@pytest.fixture
def real_core(tmp_path):
    binary = os.environ.get("MIHOMO_TEST_BINARY") or shutil.which("mihomo")
    if not binary:
        pytest.skip("真实内核验证未运行：未找到 mihomo")
    manager = Manager(tmp_path / "home", binary, timeout=5)
    proxy_port, controller_port = free_ports()
    with manager.store.lock():
        manager.configure({"proxy_port": proxy_port, "controller_port": controller_port})
    yield manager
    with manager.store.lock():
        manager.engine.stop()


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.yaml"
    path.write_text(MINIMAL)
    return path


@pytest.fixture
def http_source():
    state = {"content": MINIMAL.encode(), "status": 200, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append((self.path, self.headers.get("User-Agent")))
            self.send_response(state["status"])
            self.end_headers()
            self.wfile.write(state["content"])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/subscribe?token=PRIVATE-TOKEN"
    yield state
    server.shutdown()
    server.server_close()
    thread.join()
