import asyncio
import errno
import os
import threading

import pytest
from textual.widgets import Button, DataTable, Input, Select, Static, TabbedContent

from mihomo_py.controller import Controller
from mihomo_py.errors import AppError
from mihomo_py.manager import Manager
from mihomo_py.tui import Details, Form, MihomoApp

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
        assert app.focused.id == "subs"
        assert app.sub_names == ["work"]
        assert real_core.status()["selected"] is None
        assert not real_core.status()["running"]
        assert app.query_one("#subs").display
        assert not app.query_one("#sub-empty").display
        await pilot.click("#edit")
        assert not app.screen.query_one("#sub-source", Input).password
        assert app.screen.query_one("#sub-source", Input).value == str(source)
        await pilot.press("escape")
        app.query_one("#subs", DataTable).focus()
        await pilot.press("enter")
        await settled(app, pilot)
        assert real_core.store.read()["selected"] == "work"
        assert app.focused.id == "start"
        assert "Enter 启动" in str(app.query_one("#message", Static).content)
        assert not real_core.status()["running"]
        await pilot.press("enter")
        await settled(app, pilot)
        assert app.snapshot["status"]["healthy"]
        running_pid = real_core.status()["pid"]
        # Exiting this front end must not stop the managed core.
        await pilot.press("ctrl+q")
    assert real_core.status()["pid"] == running_pid
    app = MihomoApp(real_core)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        assert app.focused.id == "subs"
        await pilot.click("#stop")
        await settled(app, pilot)
        assert not real_core.status()["running"]
        await pilot.click("#remove")
        assert app.focused.id == "form-cancel"
        await pilot.press("enter")
        assert "work" in real_core.store.read()["subs"]
        await pilot.click("#remove")
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert not real_core.store.read()["subs"]


@pytest.mark.integration
async def test_addition_focuses_new_subscription_without_applying(real_core, source):
    real_core.put_sub("existing", str(source), create=True)
    real_core.use("existing")
    app = MihomoApp(real_core)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        assert app.focused.id == "subs"
        assert app.current_sub() == "existing"
        await pilot.press("2", "ctrl+a", *"new", "tab", *str(source), "enter")
        await settled(app, pilot)
        assert app.query_one(TabbedContent).active == "subscriptions"
        assert app.focused.id == "subs"
        assert app.current_sub() == "new"
        assert real_core.status()["selected"] == "existing"
        assert not real_core.status()["running"]
        await pilot.press("enter")
        await settled(app, pilot)
        assert real_core.status()["selected"] == "new"
        assert app.focused.id == "start"
        assert not real_core.status()["running"]


@pytest.mark.integration
async def test_subscription_click_browses_and_enter_applies(real_core, source):
    real_core.put_sub("work", str(source), create=True)
    real_core.put_sub("backup", str(source), create=True)
    real_core.use("work")
    real_core.start()
    pid = real_core.status()["pid"]
    app = MihomoApp(real_core)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        table = app.query_one("#subs", DataTable)
        table.move_cursor(row=0, column=1)
        assert app.current_sub() == "work"
        await pilot.pause()
        for _ in range(2):
            await pilot.click("#subs", offset=(8, 2))
            await settled(app, pilot)
        assert app.current_sub() == "backup"
        assert real_core.status()["selected"] == "work"
        assert real_core.status()["pid"] == pid
        assert str(app.query_one("#use", Button).label) == "使用并重启"
        assert "重启" in str(app.query_one("#sub-hint", Static).content)
        assert "中断连接" in app.query_one("#use").tooltip
        await pilot.press("enter")
        await settled(app, pilot)
        assert real_core.status()["selected"] == "backup"
        assert real_core.status()["running_subscription"] == "backup"
        assert real_core.status()["pid"] != pid
        assert str(app.query_one("#use", Button).label) == "使用"
        assert str(app.query_one("#update", Button).label) == "更新并应用"
        await pilot.click("#edit")
        await settled(app, pilot)
        assert "配置变更将重启" in app.screen.heading
        await pilot.press("escape")
        pid = real_core.status()["pid"]
        source.write_text(source.read_text() + "log-level: debug\n")
        await pilot.click("#update")
        await settled(app, pilot)
        assert real_core.status()["pid"] != pid
        assert real_core.status()["healthy"]


async def test_form_errors_preserve_inputs(tmp_path):
    app = MihomoApp(Manager(tmp_path / "home"))
    async with app.run_test() as pilot:
        await settled(app, pilot)
        await pilot.click("#add")
        await pilot.press("w", "tab")
        await pilot.press(*str(tmp_path / "missing.yaml"))
        await pilot.press("enter")
        await settled(app, pilot)
        assert isinstance(app.screen, Form)
        assert app.screen.query_one("#sub-name", Input).value == "w"
        assert app.last_error and "无法读取" in app.last_error
        assert not app.busy
        assert not app.screen.query_one("#sub-source", Input).disabled
        assert app.focused.id == "sub-source"
        assert "ENOENT" in app.last_error_details
        await pilot.press("escape")


@pytest.mark.parametrize("kind", ["permission", "wrapped", "wrapped_app", "validation"])
async def test_error_details_include_safe_context_without_credentials(tmp_path, kind):
    app = MihomoApp(Manager(tmp_path / "home"))
    secret = "PRIVATE-TOKEN-DO-NOT-DISPLAY"

    def fail():
        if kind == "permission":
            raise PermissionError(errno.EACCES, f"https://example.com/{secret}", secret)
        if kind == "wrapped":
            try:
                raise OSError(errno.ENOSPC, secret)
            except OSError as error:
                raise RuntimeError(secret) from error
        if kind == "wrapped_app":
            try:
                raise FileNotFoundError(errno.ENOENT, secret, secret)
            except OSError as error:
                raise AppError("source_unavailable", "无法读取订阅来源。") from error
        raise AppError(
            "validation_failed", "mihomo 拒绝此配置，原配置未替换。",
            suggestion=f"查看本地诊断文件：{tmp_path / 'validation.log'}",
        )

    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        app.operate("保存设置", fail)
        await settled(app, pilot)
        await pilot.press("f8")
        assert isinstance(app.screen, Details)
        content = app.screen.content
        assert "操作：保存设置" in content
        assert secret not in content
        assert content != app.last_error
        if kind == "validation":
            assert "validation_failed" in content
            assert str(tmp_path / "validation.log") in content
        else:
            code = {
                "permission": errno.EACCES, "wrapped": errno.ENOSPC, "wrapped_app": errno.ENOENT
            }[kind]
            assert errno.errorcode[code] in content
            assert os.strerror(code) in content
            if kind == "wrapped_app":
                assert "原因异常：FileNotFoundError" in content
        assert app.screen.query_one("#detail-close").region.bottom <= 23
        await pilot.press("escape")
        assert app.last_error_details == content


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
        table = app.query_one("#node-table", DataTable)
        table.move_cursor(row=0, column=1)
        await pilot.pause()
        await pilot.click("#node-table", offset=(8, 2))
        await settled(app, pilot)
        assert app.current_node() == "REJECT"
        assert Controller(real_core.engine).proxies()["代理 [group]"]["now"] == "DIRECT"
        await pilot.click("#node-table", offset=(8, 2))
        await settled(app, pilot)
        assert Controller(real_core.engine).proxies()["代理 [group]"]["now"] == "REJECT"
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
        await pilot.press("tab", "tab", "enter", "end", "enter")
        assert app.screen.query_one("#mode", Select).value == "direct"
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert real_core.status()["settings"]["mode"] == "direct"
        app.query_one(TabbedContent).active = "logs"
        app.action_refresh()
        await settled(app, pilot)
        assert "Mihomo" in str(app.query_one("#log-text", Static).content)


async def test_settings_host_validation_save_and_display(tmp_path):
    manager = Manager(tmp_path / "home")
    app = MihomoApp(manager)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#settings")
        host = app.screen.query_one("#host", Input)
        assert host.value == "127.0.0.1"
        host.focus()
        await pilot.press("ctrl+a", "ctrl+k", *"invalid")
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert isinstance(app.screen, Form)
        assert app.focused.id == "host"
        assert host.value == "invalid"
        await pilot.press("ctrl+a", "ctrl+k", *"0.0.0.0")
        await pilot.click("#form-submit")
        await settled(app, pilot)
        assert not isinstance(app.screen, Form)
        assert manager.store.read()["settings"]["host"] == "0.0.0.0"
        app.action_refresh()
        await settled(app, pilot)
        assert "0.0.0.0" in str(app.query_one("#proxy-value", Static).content)
        assert app.query_one("#proxy-value").tooltip == "0.0.0.0:7897"


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
            assert app.screen.query_one("#sub-source", Input).disabled
            assert app.screen.query_one("#form-cancel", Button).disabled
            await pilot.press("escape", "ctrl+q")
            assert isinstance(app.screen, Form)
            assert app.is_running and app.busy
        finally:
            gate.set()
        await settled(app, pilot)
        assert not isinstance(app.screen, Form)
        assert "x" in app.manager.store.read()["subs"]


def populated_snapshot(app, monkeypatch):
    snapshot = app.read_snapshot()
    names = [f"香港 [节点] - production-{i:02d}" for i in range(60)]
    snapshot["subs"] = [
        {
            "name": name,
            "source": "https://example.com/…",
            "selected": i == 0,
            "updated_at": "2026-10-08T08:00:00+00:00",
        }
        for i, name in enumerate(names)
    ]
    snapshot["status"].update(selected=names[0], running=True, healthy=True)
    snapshot["proxies"] = {
        "代理 [group]": {"type": "Selector", "all": names, "now": names[0]},
        **{name: {"type": "Shadowsocks", "history": [{"delay": 100}]} for name in names},
    }
    monkeypatch.setattr(app, "read_snapshot", lambda: snapshot)
    return snapshot


@pytest.mark.parametrize("label", ["测试延迟", "更新订阅"])
async def test_busy_operation_allows_browsing_and_read_only_dialogs(tmp_path, monkeypatch, label):
    app = MihomoApp(Manager(tmp_path / "home"))
    snapshot = populated_snapshot(app, monkeypatch)
    snapshot["proxies"]["备用组"] = {"type": "Selector", "all": ["DIRECT"], "now": "DIRECT"}
    snapshot["proxies"]["DIRECT"] = {"type": "Direct", "history": []}
    gate = threading.Event()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        app.operate(label, lambda: gate.wait(20))
        try:
            await pilot.pause()
            assert app.busy
            await pilot.press("2", "slash", "p", "r", "o")
            assert app.query_one("#filter", Input).value == "pro"
            assert app.node_names
            await pilot.press("escape")
            group = app.query_one("#group", Select)
            assert not group.disabled
            group.focus()
            await pilot.press("enter", "end", "enter")
            assert app.group_name == "备用组"
            assert app.node_names == ["DIRECT"]
            app.query_one("#node-table").focus()
            assert app.query_one("#select-node", Button).disabled
            await pilot.press("enter")
            assert app.busy
            await pilot.press("i")
            assert isinstance(app.screen, Details)
            assert "DIRECT" in app.screen.content
            await pilot.press("escape", "question_mark")
            assert isinstance(app.screen, Details)
            await pilot.click("#detail-close")
            assert not isinstance(app.screen, Details)
            await pilot.press("question_mark", "ctrl+q")
            assert not isinstance(app.screen, Details)
            assert app.busy and app.is_running
            await pilot.press("3")
            assert app.query_one(TabbedContent).active == "logs"
            await pilot.click("#log-follow")
            assert not app.log_following
            await pilot.click("#--content-tab-subscriptions")
            assert app.query_one(TabbedContent).active == "subscriptions"
            for selector in ("#use", "#update", "#add", "#stop", "#settings"):
                assert app.query_one(selector, Button).disabled
            await pilot.press("ctrl+a")
            assert not isinstance(app.screen, Form)
            await pilot.press("question_mark")
            assert isinstance(app.screen, Details)
        finally:
            gate.set()
        await settled(app, pilot)
        assert not app.busy
        assert isinstance(app.screen, Details)
        await pilot.press("escape")
        assert not app.query_one("#use", Button).disabled


@pytest.mark.parametrize("size", [(80, 24), (100, 32), (120, 40)])
async def test_layout_resize_long_names_and_details(tmp_path, monkeypatch, size):
    app = MihomoApp(Manager(tmp_path / "home"))
    snapshot = populated_snapshot(app, monkeypatch)
    address = "255.255.255.255:65535"
    snapshot["status"]["settings"].update(host="255.255.255.255", proxy_port=65535)
    async with app.run_test(size=size) as pilot:
        await settled(app, pilot)
        proxy = app.query_one("#proxy-value", Static)
        assert address in "".join(strip.text for strip in proxy.render_lines(proxy.size.region))
        snapshot["status"]["healthy"] = False
        app.apply_snapshot(snapshot)
        await pilot.pause()
        kernel = app.query_one("#status", Static)
        assert "API 未就绪" in "".join(
            strip.text for strip in kernel.render_lines(kernel.size.region)
        )
        await pilot.press("2")
        table = app.query_one("#node-table", DataTable)
        assert table.region.height - table.header_height >= 6
        assert not table.show_horizontal_scrollbar
        assert app.query_one("#test-url").region.bottom <= size[1] - 2
        await pilot.press("i")
        assert isinstance(app.screen, Details)
        assert "香港 [节点] - production-00" in app.screen.content
        await pilot.press("escape")
        assert app.focused is table
        table.move_cursor(row=35)
        await pilot.pause()
        selected = app.current_node()
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert address in "".join(strip.text for strip in proxy.render_lines(proxy.size.region))
        assert app.current_node() == selected
        assert table.region.height - table.header_height >= 6
        assert not table.show_horizontal_scrollbar
        await pilot.click("#settings")
        assert app.screen.query_one("#form-submit").region.bottom <= 23
        assert app.screen.query_one("#mode").region.bottom <= 23


async def test_refresh_preserves_cursor_and_reading_position(tmp_path, monkeypatch):
    import copy

    app = MihomoApp(Manager(tmp_path / "home"))
    snapshot = populated_snapshot(app, monkeypatch)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        for page, selector in (("1", "#subs"), ("2", "#node-table")):
            await pilot.press(page)
            table = app.query_one(selector, DataTable)
            table.focus()
            table.move_cursor(row=35)
            await pilot.pause()
            table.scroll_relative(y=-3, animate=False)
            await pilot.pause()
            before = table.scroll_offset
            app.apply_snapshot(copy.deepcopy(snapshot))
            await pilot.pause()
            assert table.cursor_row == 35
            assert table.scroll_offset == before
            changed = copy.deepcopy(snapshot)
            changed["proxies"][app.node_names[35]]["history"] = [{"delay": 321}]
            changed["subs"][0]["selected"] = False
            app.apply_snapshot(changed)
            await pilot.pause()
            assert table.cursor_row == 35
            assert table.scroll_offset == before
        changed["proxies"][app.group_name]["all"].pop(0)
        selected = app.current_node()
        app.apply_snapshot(changed)
        await pilot.pause()
        assert app.current_node() == selected
        assert table.scroll_offset == before


async def test_shortcuts_search_empty_result_and_focus_restore(tmp_path, monkeypatch):
    app = MihomoApp(Manager(tmp_path / "home"))
    populated_snapshot(app, monkeypatch)
    async with app.run_test() as pilot:
        await settled(app, pilot)
        await pilot.press("2", "slash")
        assert app.focused.id == "filter"
        await pilot.press("1", "2", "3", "question_mark", "i", "q")
        assert app.query_one("#filter", Input).value == "123?iq"
        assert app.query_one(TabbedContent).active == "nodes"
        assert not app.node_names
        await pilot.press("escape")
        assert app.query_one("#filter", Input).value == ""
        assert len(app.node_names) == 60
        assert app.focused.id == "node-table"
        await pilot.press("question_mark")
        assert isinstance(app.screen, Details)
        assert app.screen.query_one("#detail-close").region.bottom <= 23
        await pilot.press("1")
        assert app.query_one(TabbedContent).active == "nodes"
        await pilot.press("escape")
        assert app.focused.id == "node-table"
        await pilot.press("3", "2")
        assert app.focused.id == "node-table"
        app.query_one("#group", Select).focus()
        await pilot.press("enter", "1")
        assert app.query_one(TabbedContent).active == "nodes"
        await pilot.press("escape")


async def test_form_validation_enter_and_error_details(tmp_path):
    app = MihomoApp(Manager(tmp_path / "home"))
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.click("#settings")
        port = app.screen.query_one("#proxy-port", Input)
        port.value = "70000"
        await pilot.click("#form-submit")
        assert app.focused is port
        assert port.has_class("invalid")
        await pilot.press("f8")
        assert isinstance(app.screen, Details)
        assert "65535" in app.screen.content
        await pilot.press("escape")
        assert app.focused is port
        app.show_error(AppError("long", "错误详情 " * 100))
        await pilot.pause()
        assert app.screen.query_one("#form-submit").region.bottom <= 23
        port.value = "9090"
        await pilot.click("#form-submit")
        assert app.focused.id == "controller-port"
        assert "必须不同" in str(app.screen.query_one("#form-error", Static).content)
        await pilot.press("escape")
        assert app.focused.id == "settings"
        await pilot.press("ctrl+a", "enter")
        assert app.focused.id == "sub-source"
        await pilot.press(*str(tmp_path / "missing.yaml"), "enter")
        assert app.focused.id == "sub-name"
        assert app.screen.query_one("#sub-name").has_class("invalid")
        await pilot.press("w", "enter", "enter")
        await settled(app, pilot)
        assert isinstance(app.screen, Form)
        assert "无法读取" in app.last_error
        assert not app.screen.query_one("#sub-name").has_class("invalid")


async def test_log_pause_freezes_window_and_end_resumes(tmp_path, monkeypatch):
    import copy

    app = MihomoApp(Manager(tmp_path / "home"))
    snapshot = populated_snapshot(app, monkeypatch)
    snapshot["logs"] = "\n".join(f"line {i}" for i in range(200))
    async with app.run_test() as pilot:
        await settled(app, pilot)
        await pilot.press("3")
        scroll = app.query_one("#log-scroll")
        assert app.log_following
        assert scroll.scroll_y == scroll.max_scroll_y > 0
        await pilot.press("pageup")
        assert not app.log_following
        old_y = scroll.scroll_y
        changed = copy.deepcopy(snapshot)
        changed["logs"] = "\n".join(f"new {i}" for i in range(200))
        monkeypatch.setattr(app, "read_snapshot", lambda: changed)
        app.apply_snapshot(changed)
        await pilot.pause()
        assert str(app.query_one("#log-text", Static).content) == snapshot["logs"]
        assert scroll.scroll_y == old_y
        assert "有更新" in str(app.query_one("#log-hint", Static).content)
        await pilot.press("end")
        assert app.log_following
        assert str(app.query_one("#log-text", Static).content) == changed["logs"]
        assert scroll.scroll_y == scroll.max_scroll_y
        await pilot.click("#log-follow")
        assert not app.log_following
        await pilot.pause(0.3)  # Textual debounces clicks during the button's active effect.
        await pilot.click("#log-follow")
        assert app.log_following


async def test_automatic_group_enter_and_delay_failure_retry(tmp_path, monkeypatch):
    app = MihomoApp(Manager(tmp_path / "home"))
    snapshot = populated_snapshot(app, monkeypatch)
    snapshot["proxies"]["代理 [group]"]["type"] = "URLTest"
    gate = threading.Event()
    entered = threading.Event()

    def test_delay(*_):
        entered.set()
        gate.wait(5)
        raise AppError("timeout", "测试目标不可达，请更换目标。")

    class TestController:
        def __init__(self, *args):
            pass

        test = test_delay
        groups = staticmethod(Controller.groups)
        members = staticmethod(Controller.members)

        def select(self, *args):
            pytest.fail("automatic groups must not submit a selection")

    monkeypatch.setattr("mihomo_py.tui.Controller", TestController)
    async with app.run_test() as pilot:
        await settled(app, pilot)
        await pilot.press("2", "enter")
        assert not app.busy and not app.last_error
        assert app.query_one("#select-node", Button).disabled
        table = app.query_one("#node-table", DataTable)
        name = app.current_node()
        await pilot.click("#test-node")
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert "测试中" in str(table.get_cell(name, "3"))
            assert not app.query_one("#group", Select).disabled
        finally:
            gate.set()
        await settled(app, pilot)
        assert str(table.get_cell(name, "3")) == "失败"
        assert "不可达" in app.last_error
        monkeypatch.setattr(TestController, "test", lambda *args: {"name": name, "delay_ms": 12})
        await pilot.click("#test-node")
        await settled(app, pilot)
        assert str(table.get_cell(name, "3")) == "12 ms"
        assert name not in app.delay_errors


async def test_external_names_render_literally_in_tooltips(tmp_path, monkeypatch):
    from textual.widgets import Tooltip

    app = MihomoApp(Manager(tmp_path / "home"))
    snapshot = populated_snapshot(app, monkeypatch)
    name = "节点 [bold]literal[/bold] [group]"
    snapshot["status"]["selected"] = name
    snapshot["proxies"]["代理 [group]"]["now"] = name
    async with app.run_test(size=(100, 32), tooltips=True) as pilot:
        await settled(app, pilot)
        await pilot.hover("#subscription-value")
        await pilot.pause(app.TOOLTIP_DELAY + 0.2)
        tooltip = app.screen.query_one(Tooltip)
        assert tooltip.display
        rendered = "".join(strip.text for strip in tooltip.render_lines(tooltip.region.size.region))
        assert name.replace(" ", "") in rendered.replace(" ", "")
        await pilot.press("2")
        await pilot.hover("#group-hint")
        await pilot.pause(app.TOOLTIP_DELAY + 0.2)
        assert tooltip.display
        rendered = "".join(strip.text for strip in tooltip.render_lines(tooltip.region.size.region))
        assert name.replace(" ", "") in rendered.replace(" ", "")
        stopped = {**snapshot, "proxies": {}}
        monkeypatch.setattr(app, "read_snapshot", lambda: stopped)
        app.apply_snapshot(stopped)
        await pilot.hover("#brand")
        await pilot.hover("#group-hint")
        await pilot.pause(app.TOOLTIP_DELAY + 0.2)
        assert not tooltip.display


async def test_last_text_field_enter_saves_settings(tmp_path):
    app = MihomoApp(Manager(tmp_path / "home"))
    async with app.run_test() as pilot:
        await settled(app, pilot)
        await pilot.click("#settings")
        app.screen.query_one("#proxy-port", Input).value = "17897"
        app.screen.query_one("#mode", Select).value = "direct"
        app.screen.query_one("#controller-host", Input).focus()
        await pilot.press("enter")
        await settled(app, pilot)
        assert not isinstance(app.screen, Form)
        assert app.manager.status()["settings"]["proxy_port"] == 17897
        assert app.manager.status()["settings"]["mode"] == "direct"
        assert app.focused.id == "settings"


async def test_log_resize_preserves_follow_or_pause(tmp_path, monkeypatch):
    app = MihomoApp(Manager(tmp_path / "home"))
    snapshot = populated_snapshot(app, monkeypatch)
    snapshot["logs"] = "\n".join(f"line {i}" for i in range(200))
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.press("3")
        scroll = app.query_one("#log-scroll")
        for size in ((120, 40), (80, 24)):
            await pilot.resize_terminal(*size)
            await pilot.pause()
            assert app.log_following
            assert scroll.scroll_y == scroll.max_scroll_y
        await pilot.press("pageup", "pageup")
        position = scroll.scroll_y
        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        assert not app.log_following
        assert scroll.scroll_y == position


@pytest.mark.parametrize("fails", [False, True])
async def test_pending_source_read_cannot_open_dialog_after_quit(tmp_path, monkeypatch, fails):
    app = MihomoApp(Manager(tmp_path / "home"))
    populated_snapshot(app, monkeypatch)
    entered = threading.Event()
    gate = threading.Event()
    completed = threading.Event()

    def read():
        entered.set()
        gate.wait(5)
        completed.set()
        if fails:
            raise OSError("read failed")
        return {"subs": {app.sub_names[0]: {"source": "https://example.com/sub"}}}

    try:
        async with app.run_test() as pilot:
            await settled(app, pilot)
            monkeypatch.setattr(app.manager.store, "read", read)
            await pilot.click("#edit")
            assert await asyncio.to_thread(entered.wait, 2)
            await pilot.press("ctrl+q")
            assert not app.is_running
            gate.set()
            assert await asyncio.to_thread(completed.wait, 2)
            await pilot.pause()
            assert not isinstance(app.screen, Form)
            assert app.last_error is None
    finally:
        gate.set()
