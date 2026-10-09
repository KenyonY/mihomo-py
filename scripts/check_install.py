"""Exercise an installed wheel with empty user state and a local HTTP origin.

Run with the fresh environment's Python, optionally under strace to audit networking.
No preinstalled core, subscription service, or external destination is used.
"""

import asyncio
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mihomo_py.bundle import ASSET_ROOT, dashboard_root


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


def check_subscription_service(directory, source):
    from aiohttp import ClientSession, WSMsgType, web

    from mihomo_py.manager import Manager
    from mihomo_py.web_server import API, create_app

    async def verify():
        manager = Manager(directory)
        runner = web.AppRunner(create_app(manager), access_log=None)
        await runner.setup()
        port = free_port()
        try:
            await web.TCPSite(runner, "0.0.0.0", port).start()
            origin = f"http://127.0.0.2:{port}"
            headers = {"Authorization": "Bearer " + manager.engine.controller_secret()}
            async with ClientSession() as client:
                async def call(method, path, body=None, expected=200):
                    async with client.request(
                        method, origin + API + path, json=body, headers=headers
                    ) as response:
                        value = await response.json()
                        assert response.status == expected, value
                        return value

                async with client.get(origin + "/") as response:
                    assert response.status == 200
                    index = await response.text()
                for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', index):
                    async with client.get(origin + asset) as response:
                        assert response.status == 200
                        await response.read()
                async with client.get(origin + API + "/subscriptions") as response:
                    assert response.status == 401
                await call("POST", "/subscriptions", {"name": "portal", "source": str(source)})
                await call("POST", "/subscriptions/portal/use")
                await call("POST", "/core/start")
                async with client.get(origin + "/proxies", headers=headers) as response:
                    assert response.status == 200
                    assert "DIRECT" in (await response.json())["proxies"]
                secret = manager.engine.controller_secret()
                async with client.ws_connect(origin + "/traffic?token=" + secret) as stream:
                    assert (await stream.receive(timeout=10)).type == WSMsgType.TEXT
                await call("POST", "/core/stop")
                await call("DELETE", "/subscriptions/portal")
                assert not (await call("GET", "/subscriptions"))["status"]["running"]
            print("Web subscription service: "
                  "static/auth/add/switch/core/proxy/WebSocket/delete passed")
        finally:
            await runner.cleanup()
            manager.engine.stop()

    asyncio.run(verify())


def main():
    manifest = json.loads((ASSET_ROOT / "manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix="mihomo-install-") as temporary:
        root = Path(temporary)
        env = dict(os.environ, HOME=temporary, PATH=str(Path(sys.executable).parent))
        timeout = os.environ.get("MIHOMO_PY_CHECK_TIMEOUT", "30")
        env["MIHOMO_PY_HEALTHY_TIMEOUT"] = os.environ.get("MIHOMO_PY_HEALTHY_TIMEOUT", "5")
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
                        [
                            sys.executable,
                            "-m",
                            "mihomo_py",
                            "--timeout",
                            timeout,
                            "--data-dir",
                            str(state),
                            *args,
                        ],
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
                    "--controller-host",
                    "0.0.0.0",
                    "--proxy-port",
                    str(free_port()),
                    "--controller-port",
                    str(free_port()),
                )
                cli("sub", "add", "offline", str(source))
                cli("sub", "use", "offline")
                try:
                    started = cli("core", "start")
                    if not started["healthy"]:
                        raise RuntimeError(
                            "mihomo-py core start returned unhealthy:\n"
                            + (state / "core.log").read_text()
                        )
                    assert cli("core", "status")["healthy"]
                    has_web = dashboard_root() is not None
                    web = cli("core", "web") if has_web else {
                        "port": started["settings"]["controller_port"],
                        "secret": json.loads((state / "process.json").read_text())["secret"],
                    }
                    api = http.client.HTTPConnection("127.0.0.2", web["port"], timeout=5)
                    try:
                        api.request("GET", "/version")
                        response = api.getresponse()
                        assert response.status == 401
                        response.read()
                        headers = {} if has_web else {"Authorization": f"Bearer {web['secret']}"}
                        api.request("GET", "/ui/", headers=headers)
                        response = api.getresponse()
                        assert response.status == (200 if has_web else 404)
                        index = response.read().decode()
                        assert web["secret"] not in index
                        if has_web:
                            assert "zashboard" in index
                        else:
                            assert not list(ASSET_ROOT.rglob("*zashboard*"))
                        for asset in re.findall(r'(?:src|href)="(\./[^"?]+)"', index):
                            api.request("GET", "/ui/" + asset[2:])
                            response = api.getresponse()
                            assert response.status == 200, asset
                            response.read()
                    finally:
                        api.close()
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
                    log = (state / "core.log").read_text()
                    if has_web:
                        assert "UI already exists, skip downloading" in log
                    assert "download" not in log.replace(
                        "UI already exists, skip downloading", ""
                    ).lower()
                finally:
                    cli("core", "stop")
                assert not cli("core", "status")["running"]
                print(f"geodata-mode={mode}: validate/start/web/auth/proxy/stop passed")
                if has_web and mode == "false":
                    check_subscription_service(state, source)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(f"Installed core {manifest['core_version']} at {ASSET_ROOT}")


if __name__ == "__main__":
    main()
