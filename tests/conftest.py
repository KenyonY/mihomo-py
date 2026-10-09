import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mihomo_py.bundle import core_path
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
                os.environ.get("MIHOMO_TEST_BINARY", str(core_path())),
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
    binary = os.environ.get("MIHOMO_TEST_BINARY") or str(core_path())
    manager = Manager(tmp_path / "home", binary, timeout=5)
    proxy_port, controller_port = free_ports()
    with manager.store.lock():
        manager.configure({
            "proxy_port": proxy_port, "controller_port": controller_port,
            "controller_host": "127.0.0.1",
        })
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
        def do_HEAD(self):
            state["requests"].append((self.path, self.headers.get("User-Agent")))
            # mihomo treats sub-millisecond URL tests (delay == 0) as failures.
            time.sleep(0.01)
            self.send_response(state["status"])
            self.end_headers()

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


@pytest.fixture
def mmdb_bytes():
    # Empty MaxMind database, verified with maxminddb and real mihomo -t.
    return bytes.fromhex(
        "00000000000000000000000000000000abcdef4d61784d696e642e636f6de94a6e6f64"
        "655f636f756e74c04b7265636f72645f73697a65c1184a69705f76657273696f6ec104"
        "4d64617461626173655f747970654474657374496c616e67756167657300045b62696e"
        "6172795f666f726d61745f6d616a6f725f76657273696f6ec1025b62696e6172795f66"
        "6f726d61745f6d696e6f725f76657273696f6ec04b6275696c645f65706f6368c1014b"
        "6465736372697074696f6ee0"
    )
