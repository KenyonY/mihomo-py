"""Real browser acceptance against a CLI Web service, local origin and bundled core."""

import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from mihomo_py.controller import Controller
from mihomo_py.manager import Manager


def main():
    content = {
        "yaml": (
            "proxies: []\nproxy-groups:\n"
            " - {name: browser-test, type: select, proxies: [DIRECT, REJECT]}\n"
            "rules: ['MATCH,DIRECT']\n"
        )
    }

    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(content["yaml"].encode())

        def log_message(self, *args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    worker = threading.Thread(target=origin.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="mihomo-portal-") as temporary:
            root = Path(temporary)
            manager = Manager(root / "state")
            ports = []
            for _ in range(3):
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    ports.append(sock.getsockname()[1])
            manager.configure({"proxy_port": ports[0], "controller_port": ports[1]})
            with (root / "web.log").open("w") as output:
                server = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "mihomo_py",
                        "--data-dir",
                        str(manager.store.root),
                        "web",
                        "serve",
                        "--port",
                        str(ports[2]),
                    ],
                    stdout=output,
                    stderr=output,
                )
                try:
                    for _ in range(100):
                        if server.poll() is not None:
                            raise RuntimeError((root / "web.log").read_text())
                        try:
                            connection = http.client.HTTPConnection(
                                "127.0.0.1", ports[2], timeout=1
                            )
                            connection.request("GET", "/")
                            response = connection.getresponse()
                            response.read()
                            connection.close()
                            if response.status == 200:
                                break
                        except OSError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("Web server did not become ready")
                    with sync_playwright() as playwright:
                        executable = os.environ.get("MIHOMO_PY_BROWSER") or (
                            shutil.which("google-chrome") or playwright.chromium.executable_path
                        )
                        browser = playwright.chromium.launch(
                            executable_path=executable,
                            headless=True,
                            args=["--no-sandbox", "--no-proxy-server"],
                        )
                        try:
                            context = browser.new_context(viewport={"width": 1280, "height": 800})
                            host = os.environ.get("MIHOMO_PY_WEB_TEST_HOST", "127.0.0.2")
                            authority = f"{host}:{ports[2]}"
                            external = []

                            def route(request):
                                if urlsplit(request.request.url).netloc != authority:
                                    external.append(request.request.url)
                                    request.abort()
                                else:
                                    request.continue_()

                            context.route("**/*", route)
                            page = context.new_page()
                            frames = []
                            page.on(
                                "websocket",
                                lambda ws: ws.on("framereceived", lambda _: frames.append(True)),
                            )
                            page.goto(f"http://{authority}/")
                            page.get_by_label("登录密钥").fill("incorrect")
                            page.get_by_role("button", name="登录", exact=True).click()
                            expect(page.get_by_role("alert")).to_contain_text("登录密钥错误")
                            page.get_by_label("登录密钥").fill(manager.engine.controller_secret())
                            page.get_by_role("button", name="登录", exact=True).click()
                            expect(page.get_by_role("heading", name="订阅管理")).to_be_visible()
                            source = (
                                f"http://127.0.0.1:{origin.server_port}/sub?token=PRIVATE-TOKEN"
                            )

                            def add(name):
                                page.get_by_role("button", name="添加订阅", exact=True).click()
                                page.get_by_label("订阅名称").fill(name)
                                page.get_by_label("订阅地址或服务器 YAML 路径").fill(source)
                                page.get_by_role("button", name="保存", exact=True).click()
                                expect(page.get_by_role("dialog")).to_have_count(0)
                                return page.get_by_role("article", name=f"订阅 {name}")

                            first = add("first")
                            first.get_by_role("button", name="切换", exact=True).click()
                            page.get_by_role("button", name="启动内核", exact=True).click()
                            expect(page.get_by_role("button", name="停止内核")).to_be_enabled()
                            previous = manager.status()["pid"]
                            second = add("second")
                            second.get_by_role("button", name="切换", exact=True).click()
                            expect(second.get_by_text("已选中", exact=True)).to_be_visible()
                            assert (
                                manager.status()["healthy"] and manager.status()["pid"] != previous
                            )
                            assert manager.store.read()["selected"] == "second"
                            second.get_by_role("button", name="更新", exact=True).click()
                            expect(
                                page.get_by_text("「second」已更新。", exact=True)
                            ).to_be_visible()
                            second.get_by_role("button", name="修改来源", exact=True).click()
                            page.get_by_label("订阅地址或服务器 YAML 路径").fill(source + "-EDIT")
                            page.get_by_role("button", name="保存", exact=True).click()
                            expect(page.get_by_role("dialog")).to_have_count(0)
                            assert manager.store.read()["subs"]["second"]["source"].endswith(
                                "-EDIT"
                            )
                            content["yaml"] = "invalid Clash YAML"
                            second.get_by_role("button", name="更新", exact=True).click()
                            expect(page.get_by_role("alert")).to_be_visible()
                            assert manager.status()["healthy"]
                            content["yaml"] = "proxies: []\nrules: ['MATCH,DIRECT']\n"
                            first.get_by_role("button", name="删除", exact=True).click()
                            page.get_by_role("button", name="确认删除", exact=True).click()
                            expect(first).to_have_count(0)
                            assert "PRIVATE-TOKEN" not in page.locator("body").inner_text()
                            page.get_by_role("button", name="节点面板", exact=True).click()
                            nodes = page.frame_locator('iframe[title="节点面板"]')
                            expect(nodes.get_by_text("browser-test", exact=True)).to_be_visible()
                            nodes.get_by_text("browser-test", exact=True).click()
                            nodes.get_by_text("REJECT", exact=True).click()
                            expect(nodes.get_by_text("REJECT", exact=True).first).to_be_visible()
                            assert (
                                Controller(manager.engine).proxies()["browser-test"]["now"]
                                == "REJECT"
                            )
                            nodes.locator('a[href="#/logs"]').click()
                            page.wait_for_timeout(1200)
                            assert frames, "No authenticated gateway WebSocket frames"
                            page.get_by_role("button", name="订阅管理", exact=True).click()
                            other_context = browser.new_context()
                            other_context.route("**/*", route)
                            other = other_context.new_page()
                            other.goto(f"http://{authority}/")
                            old_secret = manager.engine.controller_secret()
                            other.get_by_label("登录密钥").fill(old_secret)
                            other.get_by_role("button", name="登录", exact=True).click()
                            expect(other.get_by_role("heading", name="订阅管理")).to_be_visible()
                            custom_secret = 'yao+&?#"\\'
                            page.get_by_role("button", name="设置", exact=True).click()
                            page.get_by_label("新登录密钥", exact=True).fill(custom_secret)
                            page.get_by_label("确认新密钥", exact=True).fill("different")
                            page.get_by_role("button", name="保存密钥", exact=True).click()
                            expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text(
                                "两次输入的密钥不一致"
                            )
                            page.get_by_label("确认新密钥", exact=True).fill(custom_secret)
                            page.get_by_role("button", name="保存密钥", exact=True).click()
                            expect(page.get_by_role("dialog")).to_have_count(0)
                            expect(page.get_by_text(
                                "登录密钥已修改。其他浏览器需使用新密钥重新登录。", exact=True
                            )).to_be_visible()
                            assert manager.engine.controller_secret() == custom_secret
                            assert manager.status()["healthy"]
                            assert (
                                Controller(manager.engine).proxies()["browser-test"]["now"]
                                == "REJECT"
                            )
                            page.reload()
                            expect(page.get_by_role("heading", name="订阅管理")).to_be_visible()
                            page.get_by_role("button", name="节点面板", exact=True).click()
                            expect(nodes.get_by_text("browser-test", exact=True)).to_be_visible()
                            received = len(frames)
                            nodes.locator('a[href="#/logs"]').click()
                            page.wait_for_timeout(1200)
                            assert len(frames) > received, (
                                "No WebSocket frames after secret rotation"
                            )
                            page.get_by_role("button", name="订阅管理", exact=True).click()
                            other.reload()
                            expect(other.get_by_label("登录密钥")).to_be_visible()
                            other.get_by_label("登录密钥").fill(old_secret)
                            other.get_by_role("button", name="登录", exact=True).click()
                            expect(other.get_by_role("alert")).to_contain_text("登录密钥错误")
                            other.get_by_label("登录密钥").fill(custom_secret)
                            other.get_by_role("button", name="登录", exact=True).click()
                            expect(other.get_by_role("heading", name="订阅管理")).to_be_visible()
                            other_context.close()
                            page.get_by_role("button", name="停止内核", exact=True).click()
                            expect(page.get_by_role("button", name="启动内核")).to_be_enabled()
                            page.set_viewport_size({"width": 390, "height": 844})
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            )
                            page.reload()
                            expect(page.get_by_role("heading", name="订阅管理")).to_be_visible()
                            assert not manager.status()["running"]
                            assert not external, external
                            print(
                                "Chromium: subscription CRUD/switch/failure rollback/"
                                "custom secret/session revocation/"
                                "core lifecycle/"
                                "embedded nodes/WebSocket/saved login/mobile width passed; "
                                "external requests=0"
                            )
                        finally:
                            browser.close()
                finally:
                    server.terminate()
                    server.wait(timeout=15)
                    manager.engine.stop()
    finally:
        origin.shutdown()
        origin.server_close()
        worker.join()


if __name__ == "__main__":
    main()
