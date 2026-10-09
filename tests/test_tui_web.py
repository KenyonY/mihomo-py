import asyncio

import pytest
from textual.widgets import Button, Input

from mihomo_py.manager import Manager
from mihomo_py.tui import Details, Form, MihomoApp

pytestmark = pytest.mark.asyncio


async def settled(app, pilot):
    app.animation_level = "none"
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 20)
    await pilot.pause()


async def test_controller_host_form_at_80x24(tmp_path):
    manager = Manager(tmp_path / "state")
    app = MihomoApp(manager)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        assert app.query_one("#web", Button).disabled
        await pilot.click("#settings")
        field = app.screen.query_one("#controller-host", Input)
        assert field.value == "0.0.0.0"
        field.value = "invalid"
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert isinstance(app.screen, Form) and app.focused.id == "controller-host"
        field.value = "0.0.0.0"
        await pilot.press("enter")
        await settled(app, pilot)
        assert not isinstance(app.screen, Form)
        assert manager.store.read()["settings"]["controller_host"] == "0.0.0.0"
        assert manager.store.read()["settings"]["host"] == "127.0.0.1"


@pytest.mark.integration
async def test_running_web_dialog_at_80x24(real_core, source):
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    real_core.start()
    info = real_core.web()
    app = MihomoApp(real_core)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        assert not app.query_one("#web", Button).disabled
        await pilot.click("#web")
        await settled(app, pilot)
        assert isinstance(app.screen, Details)
        assert info["secret"] in app.screen.content and info["url"] in app.screen.content
        await pilot.press("escape")
        await settled(app, pilot)
        assert real_core.status()["healthy"]


async def test_stopped_core_with_web_service_can_open_panel(tmp_path):
    import socket

    from aiohttp.test_utils import TestClient, TestServer

    from mihomo_py.web_server import create_app

    manager = Manager(tmp_path / "state")
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    application = create_app(manager, host="127.0.0.1", port=port)
    async with TestClient(TestServer(application, port=port)):
        app = MihomoApp(manager)
        async with app.run_test(size=(80, 24)) as pilot:
            await settled(app, pilot)
            assert not app.query_one("#web", Button).disabled
            await pilot.click("#web")
            await settled(app, pilot)
            assert isinstance(app.screen, Details)
            assert f":{port}/" in app.screen.content
            assert not manager.status()["running"]
