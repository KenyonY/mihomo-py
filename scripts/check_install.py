"""Exercise an installed wheel with empty user state and a local HTTP origin.

Run with the fresh environment's Python, optionally under strace to audit networking.
No preinstalled core, subscription service, or external destination is used.
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mihomo_py.bundle import ASSET_ROOT


class Origin(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"offline-install-ok")

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    manifest = json.loads((ASSET_ROOT / "manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix="mihomo-install-") as temporary:
        root = Path(temporary)
        env = dict(os.environ, HOME=temporary, PATH=str(Path(sys.executable).parent))
        for name in ("MIHOMO_PY_BINARY", "MIHOMO_PY_GEODATA_DIR", "XDG_CONFIG_HOME"):
            env.pop(name, None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for mode in ("false", "true"):
                state = root / mode

                def cli(*args):
                    result = subprocess.run(
                        [sys.executable, "-m", "mihomo_py", "--data-dir", str(state), *args],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    if result.returncode:
                        raise RuntimeError(
                            f"mihomo-py {' '.join(args)} failed ({result.returncode}):\n"
                            f"{result.stdout}\n{result.stderr}"
                        )
                    return json.loads(result.stdout)

                source = root / "source.yaml"
                source.write_text(
                    f"geodata-mode: {mode}\nproxies: []\nrules:\n"
                    "  - GEOSITE,cn,DIRECT\n  - GEOIP,CN,DIRECT\n"
                    "  - IP-ASN,13335,DIRECT\n  - MATCH,DIRECT\n"
                )
                cli(
                    "config",
                    "set",
                    "--proxy-port",
                    str(free_port()),
                    "--controller-port",
                    str(free_port()),
                )
                cli("sub", "add", "offline", str(source))
                cli("sub", "use", "offline")
                try:
                    started = cli("core", "start")
                    assert started["healthy"]
                    assert cli("core", "status")["healthy"]
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        started["settings"]["proxy_port"],
                        timeout=5,
                    )
                    try:
                        connection.request("GET", f"http://127.0.0.1:{server.server_port}/")
                        response = connection.getresponse()
                        assert response.status == 200
                        assert response.read() == b"offline-install-ok"
                    finally:
                        connection.close()
                    assert "download" not in (state / "core.log").read_text().lower()
                finally:
                    cli("core", "stop")
                assert not cli("core", "status")["running"]
                print(f"geodata-mode={mode}: validate/start/proxy/stop passed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(f"Installed core {manifest['core_version']} at {ASSET_ROOT}")


if __name__ == "__main__":
    main()
