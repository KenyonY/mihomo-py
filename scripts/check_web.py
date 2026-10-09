"""Exercise the packaged dashboard in Chromium, with external requests blocked."""

import os
import shutil
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from mihomo_py.controller import Controller
from mihomo_py.manager import Manager


def main():
    with tempfile.TemporaryDirectory(prefix="mihomo-web-") as temporary:
        root = Path(temporary)
        source = root / "source.yaml"
        source.write_text(
            "proxies: []\nproxy-groups:\n"
            "  - {name: browser-test, type: select, proxies: [DIRECT, REJECT]}\n"
            "rules: ['MATCH,DIRECT']\n"
        )
        manager = Manager(root / "state")
        with socket.socket() as first, socket.socket() as second:
            first.bind(("127.0.0.1", 0))
            second.bind(("127.0.0.1", 0))
            proxy_port, controller_port = first.getsockname()[1], second.getsockname()[1]
        manager.configure({
            "proxy_port": proxy_port, "controller_port": controller_port,
            "controller_host": "0.0.0.0",
        })
        manager.put_sub("browser", str(source), create=True)
        manager.use("browser")
        try:
            manager.start()
            with sync_playwright() as playwright:
                executable = os.environ.get("MIHOMO_PY_BROWSER") or (
                    shutil.which("google-chrome") or shutil.which("chromium")
                    or shutil.which("chromium-browser") or playwright.chromium.executable_path
                )
                browser = playwright.chromium.launch(
                    executable_path=executable, headless=True,
                    args=["--no-sandbox", "--no-proxy-server"],
                )
                try:
                    context = browser.new_context(
                        locale="en-US", viewport={"width": 1280, "height": 800}
                    )
                    host = os.environ.get("MIHOMO_PY_WEB_TEST_HOST", "127.0.0.2")
                    authority = f"{host}:{controller_port}"
                    external = []

                    def route(request):
                        url = urlsplit(request.request.url)
                        if url.netloc != authority:
                            external.append(url.hostname)
                            request.abort()
                        else:
                            request.continue_()

                    context.route("**/*", route)
                    page = context.new_page()
                    sockets = []
                    page.on("websocket", lambda ws: ws.on(
                        "framereceived", lambda _: sockets.append(True)
                    ))
                    url = f"http://{authority}/ui/"
                    page.goto(url)
                    secret = manager.web()["secret"]
                    page.locator('input[type="password"]').fill(secret)
                    page.get_by_role("button", name="Save", exact=True).click()
                    expect(page.get_by_text("browser-test", exact=True)).to_be_visible()
                    page.get_by_text("browser-test", exact=True).click()
                    page.get_by_text("REJECT", exact=True).click()
                    expect(page.get_by_text("REJECT", exact=True).first).to_be_visible()
                    controller = Controller(manager.engine)
                    assert controller.proxies()["browser-test"]["now"] == "REJECT"
                    for tab in ("rules", "connections", "logs"):
                        page.locator(f'a[href="#/{tab}"]').click()
                        expect(page).to_have_url(url + "#/" + tab)
                    page.wait_for_timeout(1200)
                    assert sockets, "No authenticated WebSocket frames received"
                    manager.start(restart=True)
                    assert manager.web()["secret"] == secret
                    page.goto(url)
                    expect(page.get_by_text("browser-test", exact=True)).to_be_visible()
                    assert page.locator('input[type="password"]').count() == 0
                    assert not external, f"Browser attempted requests outside the core: {external}"
                    print("Chromium: login/select/rules/connections/logs/WebSocket/restart passed; "
                          "external requests=0")
                finally:
                    browser.close()
        finally:
            manager.engine.stop()


if __name__ == "__main__":
    main()
