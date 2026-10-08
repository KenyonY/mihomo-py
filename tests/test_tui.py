import asyncio
import threading

import pytest
from textual.widgets import DataTable, Input, Static, TabbedContent

from mihomo_py.controller import Controller
from mihomo_py.manager import Manager
from mihomo_py.tui import Form, MihomoApp

pytestmark = pytest.mark.asyncio


async def settled(app, pilot):
    # Pilot waits for animations after keypresses, including after app shutdown.
    app.animation_level = "none"
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 20)
    await pilot.pause()


async def test_empty_ui_keyboard_and_modal_cancel(tmp_path):
    app = MihomoApp(Manager(tmp_path / "home"))
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        assert app.snapshot["status"]["running"] is False
        assert app.query_one("#sub-empty").display
        assert not app.query_one("#subs").display
        assert not (tmp_path / "home").exists()
        await pilot.press("ctrl+a")
        await pilot.press("w", "o", "r", "k", "q")
        assert isinstance(app.screen, Form)
        assert app.screen.query_one("#sub-name", Input).value == "workq"
        await pilot.press("escape")
        assert not isinstance(app.screen, Form)
        app.query_one(TabbedContent).active = "nodes"
        await pilot.pause()
        assert app.query_one("#test-url").region.bottom <= 22
        assert not app.node_names
        await pilot.press("ctrl+q")


@pytest.mark.integration
async def test_real_ui_subscription_lifecycle(real_core, source):
    app = MihomoApp(real_core)
    async with app.run_test(size=(100, 32)) as pilot:
        await settled(app, pilot)
        await pilot.click("#add")
        await pilot.press("w", "o", "r", "k", "tab")
        await pilot.press(*str(source))
        assert not app.screen.query_one("#sub-source", Input).password
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert not isinstance(app.screen, Form)
        # Trigger one refresh if modal dismissal deferred the automatic snapshot.
        app.action_refresh()
        await settled(app, pilot)
        assert app.sub_names == ["work"]
        assert app.query_one("#subs").display
        assert not app.query_one("#sub-empty").display
        await pilot.click("#edit")
        assert not app.screen.query_one("#sub-source", Input).password
        await pilot.press("escape")
        app.query_one("#subs", DataTable).focus()
        await pilot.press("enter")
        await settled(app, pilot)
        assert real_core.store.read()["selected"] == "work"
        await pilot.click("#start")
        await settled(app, pilot)
        assert app.snapshot["status"]["healthy"]
        running_pid = real_core.status()["pid"]
        # Exiting this front end must not stop the managed core.
        await pilot.press("ctrl+q")
    assert real_core.status()["pid"] == running_pid
    app = MihomoApp(real_core)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#stop")
        await settled(app, pilot)
        assert not real_core.status()["running"]
        await pilot.click("#remove")
        await pilot.click("#form-cancel")
        assert "work" in real_core.store.read()["subs"]
        await pilot.click("#remove")
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert not real_core.store.read()["subs"]


async def test_form_errors_preserve_inputs(tmp_path):
    app = MihomoApp(Manager(tmp_path / "home"))
    async with app.run_test() as pilot:
        await settled(app, pilot)
        await pilot.click("#add")
        await pilot.press("w", "tab")
        await pilot.press(*str(tmp_path / "missing.yaml"))
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert isinstance(app.screen, Form)
        assert app.screen.query_one("#sub-name", Input).value == "w"
        assert app.last_error and "无法读取" in app.last_error
        assert not app.busy
        assert not app.screen.query_one("#sub-source", Input).disabled
        await pilot.press("escape")


@pytest.mark.integration
async def test_nodes_search_switch_delay_settings_and_logs(real_core, source, http_source):
    source.write_text("""proxies: []
proxy-groups:
  - name: "代理 [group]"
    type: select
    proxies: [DIRECT, REJECT]
rules: ["MATCH,DIRECT"]
""")
    real_core.put_sub("work", str(source), create=True)
    real_core.use("work")
    real_core.start()
    app = MihomoApp(real_core)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        app.query_one(TabbedContent).active = "nodes"
        await pilot.pause()
        assert app.group_name == "代理 [group]"
        assert app.query_one("#node-table").region.height >= 4
        await pilot.click("#filter")
        await pilot.press("R", "E", "J")
        assert app.node_names == ["REJECT"]
        app.query_one("#node-table", DataTable).focus()
        await pilot.press("enter")
        await settled(app, pilot)
        assert Controller(real_core.engine).proxies()["代理 [group]"]["now"] == "REJECT"
        await pilot.click("#filter")
        await pilot.press("ctrl+a", "ctrl+k", "D", "I", "R")
        assert app.node_names == ["DIRECT"]
        await pilot.click("#test-url")
        await pilot.press("ctrl+a", "ctrl+k", *http_source["url"])
        await pilot.click("#test-node")
        await settled(app, pilot)
        assert app.delay_results["DIRECT"] >= 0
        assert http_source["requests"]
        await pilot.click("#settings")
        await pilot.press("tab", "tab", "ctrl+a", "ctrl+k", *"direct")
        assert app.focused.id == "mode"
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert real_core.status()["settings"]["mode"] == "direct"
        app.query_one(TabbedContent).active = "logs"
        app.action_refresh()
        await settled(app, pilot)
        assert "Mihomo" in str(app.query_one("#log-text", Static).content)


async def test_busy_operation_keeps_ui_responsive_and_blocks_quit(tmp_path):
    app = MihomoApp(Manager(tmp_path / "home"))
    gate = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        gate.wait(5)

    async with app.run_test() as pilot:
        await settled(app, pilot)
        app.operate("慢操作", slow)
        try:
            await asyncio.to_thread(started.wait, 2)
            await pilot.press("ctrl+q")
            assert app.busy and app.is_running
            assert "稍后退出" in str(app.query_one("#message", Static).content)
        finally:
            gate.set()
        await settled(app, pilot)
        assert not app.busy


async def test_stale_refresh_cannot_overwrite_action_result(tmp_path, monkeypatch):
    app = MihomoApp(Manager(tmp_path / "home"))
    async with app.run_test() as pilot:
        await settled(app, pilot)
        old_snapshot = app.snapshot
        gate = threading.Event()
        entered = threading.Event()
        original = app.read_snapshot

        def delayed():
            entered.set()
            gate.wait(5)
            return old_snapshot

        monkeypatch.setattr(app, "read_snapshot", delayed)
        app.action_refresh()
        await asyncio.to_thread(entered.wait, 2)
        app.operate("设置", lambda: app.manager.configure({"mode": "direct"}))
        await pilot.pause()
        monkeypatch.setattr(app, "read_snapshot", original)
        gate.set()
        await settled(app, pilot)
        assert app.snapshot["status"]["settings"]["mode"] == "direct"


async def test_subscription_progress_visible_while_validation_runs(tmp_path, monkeypatch):
    import mihomo_py.manager as manager_module

    gate = threading.Event()
    validating = threading.Event()
    app = MihomoApp(Manager(tmp_path / "home"))
    monkeypatch.setattr(manager_module, "fetch", lambda *args, **kwargs: "proxies: []")

    def validate(content, settings, name, *, progress=None):
        progress("订阅已读取，正在校验配置…")
        validating.set()
        gate.wait(5)

    monkeypatch.setattr(app.manager.engine, "validate", validate)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#add")
        await pilot.press("x", "tab", *"https://example.invalid/sub")
        await pilot.click("#form-submit")
        try:
            assert await asyncio.to_thread(validating.wait, 2)
            await pilot.pause()
            assert app.busy
            assert "订阅已读取" in str(app.screen.query_one("#form-error", Static).content)
            assert app.screen.query_one("#form-submit").region.bottom <= 24
        finally:
            gate.set()
        await settled(app, pilot)
        assert not isinstance(app.screen, Form)
        assert "x" in app.manager.store.read()["subs"]
