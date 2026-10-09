"""Interactive front end. All blocking backend work runs off the UI thread."""

import asyncio
import errno
import os
from itertools import chain

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from .controller import DEFAULT_TEST_URL, Controller, instance_id
from .errors import AppError
from .store import valid_host


class StableTable(DataTable):
    """Only rebuild on structural changes; keep reading position during polling."""

    def __init__(self, headings, **kwargs):
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self.headings = headings
        self.records = []
        self.column_widths = ()

    def on_resize(self):
        self.set_records(self.records)

    async def _on_click(self, event: events.Click) -> None:
        # Browsing a subscription must not apply it, even on repeated clicks.
        if self.id == "subs":
            event.prevent_default()  # Textual otherwise invokes the base handler again.
            with self.prevent(DataTable.RowSelected):
                await super()._on_click(event)

    def set_records(self, records):
        available = max(52, self.size.width - 10)  # cell padding + scrollbar
        if self.id == "subs":
            name_width = max(16, (available - 18) * 3 // 5)
            widths = (2, name_width, available - 18 - name_width, 16)
        else:
            widths = (2, available - 24, 12, 10)
        keys = [key for key, _ in records]
        old_keys = [key for key, _ in self.records]
        old_row = self.cursor_row
        selected = old_keys[old_row] if old_row < len(old_keys) else None
        scroll = self.scroll_offset
        rebuild = widths != self.column_widths or keys != old_keys
        if rebuild:
            self.clear(columns=True)
            for index, (label, width) in enumerate(zip(self.headings, widths)):
                self.add_column(label, width=width, key=str(index))
        for key, cells in records:
            rendered = []
            for cell, width in zip(cells, widths):
                value = cell.copy() if isinstance(cell, Text) else Text(str(cell))
                value.truncate(width, overflow="ellipsis")
                rendered.append(value)
            if rebuild:
                self.add_row(*rendered, key=key)
            else:
                for index, value in enumerate(rendered):
                    if self.get_cell(key, str(index)) != value:
                        self.update_cell(key, str(index), value)
        self.records = records
        self.column_widths = widths
        if rebuild and records:
            row = keys.index(selected) if selected in keys else min(old_row, len(keys) - 1)
            self.move_cursor(row=row, scroll=False)
            self.call_after_refresh(
                self.scroll_to, x=scroll.x, y=scroll.y, animate=False, force=True
            )


class LogScroll(VerticalScroll):
    BINDINGS = [Binding("end", "tail", "继续跟随")]

    def watch_scroll_y(self, old_value, new_value):
        super().watch_scroll_y(old_value, new_value)
        if new_value < old_value and new_value < self.max_scroll_y and not self.app.updating_logs:
            self.app.pause_logs()

    def on_resize(self):
        if self.app.log_following:
            self.app.render_logs(force=True)

    def action_tail(self):
        self.app.resume_logs()

    def on_mouse_scroll_up(self):
        self.app.pause_logs()

    def action_page_up(self):
        self.app.pause_logs()
        super().action_page_up()

    def action_scroll_up(self):
        self.app.pause_logs()
        super().action_scroll_up()

    def action_scroll_home(self):
        self.app.pause_logs()
        super().action_scroll_home()


class Dialog(ModalScreen):
    BINDINGS = [
        Binding("escape", "cancel", "关闭"),
        Binding("f8", "error_details", "错误详情", show=False),
    ]

    def action_cancel(self):
        self.dismiss()

    def action_error_details(self):
        self.app.action_error_details()


class Details(Dialog):
    def __init__(self, title, content):
        super().__init__()
        self.heading, self.content = title, content

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.heading, classes="dialog-title", markup=False)
            with VerticalScroll(id="detail-scroll", can_focus=True):
                yield Static(self.content, markup=False)
            yield Button("关闭 · Esc", id="detail-close", variant="primary")

    @on(Button.Pressed, "#detail-close")
    def close(self, event):
        event.stop()
        self.action_cancel()


def error_message(error):
    if isinstance(error, AppError):
        return str(error) + (f" {error.suggestion}" if error.suggestion else "")
    if isinstance(error, OSError):
        return f"本地 IO 操作失败（errno={error.errno}）。"
    return f"操作失败（{type(error).__name__}），请重试。"


def error_details(error, operation):
    """Describe safe error metadata, never raw exceptions or core diagnostics."""
    lines = [f"操作：{operation}", f"异常：{type(error).__name__}"]
    if isinstance(error, AppError):
        lines.extend([f"错误代码：{error.kind}", error_message(error)])
    else:
        lines.append(error_message(error))
    cause = error if isinstance(error, OSError) else error.__cause__
    if isinstance(cause, OSError) and cause.errno is not None:
        code = cause.errno
        lines.extend([
            f"原因异常：{type(cause).__name__}",
            f"系统错误：{errno.errorcode.get(code, 'UNKNOWN')}（errno={code}）",
            f"系统原因：{os.strerror(code)}",
        ])
    return "\n".join(lines)


class Form(Dialog):
    BINDINGS = [("escape", "cancel", "取消")]

    def action_cancel(self):
        if not self.app.busy:
            self.dismiss()

    def __init__(self, title, fields, submit, *, choices=None):
        super().__init__()
        self.heading = title
        self.fields = fields
        self.submit = submit
        self.choices = choices or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.heading, classes="dialog-title", markup=False)
            with VerticalScroll(id="form-fields"):
                for key, label, value, password in self.fields:
                    yield Static(label, markup=False, classes="field-label")
                    if key in self.choices:
                        yield Select(
                            self.choices[key],
                            value=value,
                            allow_blank=False,
                            id=key,
                            compact=True,
                        )
                    else:
                        yield Input(value=value, password=password, id=key, compact=True)
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("确定", variant="primary", id="form-submit")
                yield Button("取消", id="form-cancel")
                yield Button("错误详情", id="form-details", disabled=True)

    def on_mount(self):
        inputs = list(self.query(Input))
        if inputs:
            inputs[0].focus()
        else:
            self.query_one("#form-cancel", Button).focus()

    @on(Button.Pressed, "#form-submit")
    def accept(self, event):
        event.stop()
        self.submit_values()

    def submit_values(self):
        if self.app.busy:
            return
        values = {}
        for key, *_ in self.fields:
            widget = self.query_one(f"#{key}")
            values[key] = str(widget.value).strip()
            if not values[key]:
                self.field_error(key, "请填写此字段。")
                return
        self.submit(values, self)

    def field_error(self, key, message):
        self.app.show_error(AppError("invalid_field", message, 2), operation="检查表单")
        widget = self.query_one(f"#{key}")
        widget.add_class("invalid")
        widget.focus()

    @on(Input.Changed)
    def clear_invalid(self, event):
        event.input.remove_class("invalid")

    @on(Input.Submitted)
    def input_submitted(self, event):
        event.stop()
        keys = [key for key, *_ in self.fields if key not in self.choices]
        index = keys.index(event.input.id)
        if index == len(keys) - 1:
            self.submit_values()
        else:
            self.query_one(f"#{keys[index + 1]}").focus()

    @on(Button.Pressed, "#form-details")
    def error_details(self, event):
        event.stop()
        self.app.action_error_details()

    @on(Button.Pressed, "#form-cancel")
    def cancel_button(self, event):
        event.stop()
        self.action_cancel()


class MihomoApp(App):
    TITLE = "mihomo-py"
    SUB_TITLE = "服务器代理管理"
    ENABLE_COMMAND_PALETTE = False
    READ_ONLY_BUTTONS = {"detail-close", "log-follow", "error-details"}
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+q", "quit", "退出", show=False, priority=True),
        Binding("ctrl+c", "quit", "退出", show=False, priority=True),
        Binding("ctrl+r", "refresh", "刷新"),
        Binding("ctrl+a", "add", "添加订阅"),
        Binding("1", "page('subscriptions')", "订阅", show=False),
        Binding("2", "page('nodes')", "节点", show=False),
        Binding("3", "page('logs')", "日志", show=False),
        Binding("slash", "search", "搜索", show=False),
        Binding("escape", "clear_search", "清空搜索", show=False),
        Binding("i", "details", "详情", show=False),
        Binding("question_mark", "help", "帮助"),
        Binding("f8", "error_details", "错误详情", show=False),
    ]
    CSS_PATH = "tui.tcss"

    def __init__(self, manager):
        super().__init__()
        self.register_theme(
            Theme(
                name="mihomo-night",
                primary="#8bbbc5",
                accent="#8bbbc5",
                secondary="#a3afc2",
                background="#161b22",
                surface="#202731",
                panel="#293440",
                foreground="#dce3eb",
                success="#99c5aa",
                warning="#d9bd86",
                error="#df9a9e",
                dark=True,
                variables={"footer-key-foreground": "#8bbbc5", "footer-background": "#202731"},
            )
        )
        self.theme = "mihomo-night"
        self.manager = manager
        self.busy = False
        self.reading = False
        self.revision = 0
        self.snapshot = None
        self.group_name = None
        self.node_names = []
        self.sub_names = []
        self.last_error = None
        self.last_error_details = None
        self.next_focus = None
        self.delay_results = {}
        self.testing_node = None
        self.delay_errors = {}
        self.log_following = True
        self.updating_logs = False
        self.page_focus = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("mihomo  /  代理管理", id="brand")
            yield Static("本机 · mihomo-py", id="edition")
        with Horizontal(id="overview"):
            with Vertical(id="kernel-metric", classes="metric"):
                yield Static("内核", classes="metric-label")
                yield Static("○  读取中", id="status", classes="metric-value", markup=False)
            with Vertical(id="subscription-metric", classes="metric"):
                yield Static("订阅 · 已选 / 运行", classes="metric-label")
                yield Static("—", id="subscription-value", classes="metric-value", markup=False)
            with Vertical(id="proxy-metric", classes="metric"):
                yield Static("本机代理", classes="metric-label")
                yield Static("—", id="proxy-value", classes="metric-value")
            with Vertical(id="mode-metric", classes="metric"):
                yield Static("路由模式", classes="metric-label")
                yield Static("—", id="mode-value", classes="metric-value")
        with Horizontal(id="controls", classes="buttons"):
            yield Button("启动", id="start", variant="success")
            yield Button("停止", id="stop", variant="error")
            yield Button("重启", id="restart")
            yield Button("设置", id="settings")
            yield Button("刷新", id="refresh")
        with TabbedContent():
            with TabPane("1 订阅", id="subscriptions"):
                yield Static("订阅库   /   ↑↓ 选择，Enter 使用", id="sub-hint", classes="hint")
                with Vertical(classes="list-area"):
                    yield StableTable(("", "订阅", "来源", "更新于"), id="subs")
                    yield Static(
                        "还没有订阅\n\n添加订阅 URL 或本地 YAML，开始配置代理\n\nCtrl+A 添加订阅",
                        id="sub-empty",
                        classes="empty",
                    )
                with Horizontal(classes="buttons"):
                    yield Button("添加", id="add")
                    yield Button("使用", id="use", variant="primary")
                    yield Button("更新", id="update")
                    yield Button("改来源", id="edit")
                    yield Button("删除", id="remove", variant="error")
            with TabPane("2 节点", id="nodes"):
                with Horizontal(id="node-filters"):
                    yield Select([], prompt="选择代理组", id="group", compact=True)
                    yield Input(placeholder="/ 搜索节点名称", id="filter", compact=True)
                yield Static("启动内核后显示节点", id="group-hint", markup=False)
                with Vertical(classes="list-area"):
                    yield StableTable(("", "节点", "类型", "延迟"), id="node-table")
                    yield Static(
                        "选择订阅并启动内核后，即可查看节点",
                        id="node-empty",
                        classes="empty",
                        markup=False,
                    )
                with Horizontal(classes="buttons"):
                    yield Button("切换节点", id="select-node", variant="primary")
                    yield Button("测试延迟", id="test-node")
                with Horizontal(id="test-tools"):
                    yield Static("目标", id="test-label")
                    yield Input(
                        value=DEFAULT_TEST_URL,
                        placeholder="延迟测试 URL",
                        id="test-url",
                        compact=True,
                    )
            with TabPane("3 日志", id="logs"):
                with Horizontal(id="log-tools"):
                    yield Static("最近 200 行 · 跟随中", id="log-hint", classes="hint")
                    yield Button("暂停", id="log-follow")
                with LogScroll(id="log-scroll", can_focus=True):
                    yield Static("尚无日志", id="log-text", markup=False)
        with Horizontal(id="feedback"):
            yield Static("退出界面会保留运行中的内核。", id="message", markup=False)
            yield Button("错误详情", id="error-details", disabled=True)
        yield Footer()

    def on_mount(self):
        self.on_resize()
        self.query_one("#add", Button).focus()
        self.action_refresh()
        self.set_interval(2, self.action_refresh)

    def on_resize(self, event=None):
        size = event.size if event is not None else self.size
        self.screen_stack[0].set_class(size.height < 30 or size.width < 100, "compact")

    def show_dialog(self, dialog):
        focus = self.focused

        def closed(_):
            if focus and focus.is_mounted and focus.visible and not focus.disabled:
                focus.focus()
            self.update_buttons()
            self.action_refresh()

        self.push_screen(dialog, closed)

    def check_action(self, action, parameters):
        if action in {"page", "search", "details", "help"}:
            editing = self.focused and any(
                isinstance(widget, Input) or (isinstance(widget, Select) and widget.expanded)
                for widget in self.focused.ancestors_with_self
            )
            return not isinstance(self.screen, ModalScreen) and not editing
        if action == "clear_search":
            return (
                not isinstance(self.screen, ModalScreen)
                and self.focused is self.query_one("#filter")
            )
        return True

    def action_page(self, page):
        tabs = self.query_one(TabbedContent)
        if tabs.active != page:
            tabs.active = page

    def on_descendant_focus(self, event):
        for parent in event.widget.ancestors:
            if isinstance(parent, TabPane):
                self.page_focus[parent.id] = event.widget
                break

    @on(TabbedContent.TabActivated)
    def page_activated(self, event):
        page = event.pane.id
        tabs = self.query_one(TabbedContent)
        if tabs.active != page:
            return
        defaults = {
            "subscriptions": "#subs" if self.sub_names else "#add",
            "nodes": "#node-table" if self.node_names else "#group",
            "logs": "#log-scroll",
        }
        widget = self.page_focus.get(page)
        if not widget or not widget.is_mounted or not widget.visible or widget.disabled:
            widget = self.query_one(defaults[page])

        if not isinstance(self.screen, ModalScreen):
            self.screen.set_focus(widget)
        if page == "logs":
            self.render_logs(force=True)

    def action_search(self):
        if self.query_one(TabbedContent).active == "nodes":
            self.screen.set_focus(self.query_one("#filter", Input))

    def action_clear_search(self):
        self.query_one("#filter", Input).value = ""
        self.update_nodes()
        self.query_one("#node-table" if self.node_names else "#group").focus()

    def action_help(self):
        self.show_dialog(
            Details(
                "键盘操作",
                (
                    "1 / 2 / 3    订阅 / 节点 / 日志\n"
                    "Tab / Shift-Tab    下一个 / 上一个控件\n"
                    "↑ / ↓    选择列表行\nEnter    使用订阅 / 切换手动组节点\n"
                    "/    搜索节点；Esc 清空并返回列表\ni    查看当前行完整信息\n"
                    "Ctrl-R    刷新\nCtrl-A    添加订阅\nF8    查看完整错误\n"
                    "End    日志恢复跟随\n?    打开帮助\nEsc    关闭弹窗\n"
                    "q / Ctrl-Q    退出（保留内核）\n\n"
                    "输入框保留文本编辑快捷键；弹窗内不切换页面。\n"
                    "● 表示已选订阅 / 生效节点，高亮背景表示正在浏览的行。\n"
                    "订阅单击只选择，Enter 或使用按钮执行；运行中切换会重启内核。\n"
                    "日志可能包含订阅内部地址。"
                ),
            )
        )

    def action_details(self):
        if not self.snapshot:
            return
        page = self.query_one(TabbedContent).active
        if page == "subscriptions" and self.current_sub():
            sub = next(s for s in self.snapshot["subs"] if s["name"] == self.current_sub())
            status = self.snapshot["status"]
            self.show_dialog(
                Details(
                    "订阅详情",
                    (
                        f"名称：{sub['name']}\n来源：{sub['source']}\n更新于：{sub['updated_at']}\n\n"
                        f"已选订阅：{status['selected'] or '—'}\n"
                        f"运行订阅：{status['running_subscription'] or '—'}"
                    ),
                )
            )
        elif page == "nodes" and self.current_node():
            node = next(
                n
                for n in Controller.members(self.snapshot["proxies"], self.group_name)
                if n["name"] == self.current_node()
            )
            self.show_dialog(
                Details(
                    "节点详情",
                    (
                        f"名称：{node['name']}\n代理组：{self.group_name}\n类型：{node['type']}\n"
                        f"当前生效：{'是' if node['selected'] else '否'}\n"
                        f"延迟：{self.node_delay(node)}\n"
                        f"{self.delay_errors.get(node['name'], '')}"
                    ),
                )
            )

    def action_error_details(self):
        if isinstance(self.screen, Details):
            return
        if self.last_error:
            self.show_dialog(Details("错误详情", self.last_error_details))

    def pause_logs(self):
        self.log_following = False
        self.update_log_hint()

    def resume_logs(self):
        self.log_following = True
        self.render_logs(force=True)

    def update_log_hint(self):
        pending = self.snapshot and self.snapshot["logs"] != str(
            self.query_one("#log-text", Static).content
        )
        state = "跟随中" if self.log_following else "已暂停 · End 继续"
        if not self.log_following and pending:
            state += " · 有更新"
        self.query_one("#log-hint", Static).update(f"最近 200 行 · {state}")
        self.query_one("#log-follow", Button).label = "暂停" if self.log_following else "继续"

    def render_logs(self, *, force=False):
        widget = self.query_one("#log-text", Static)
        changed = self.snapshot and str(widget.content) != self.snapshot["logs"]
        if self.snapshot and self.log_following and (changed or force):
            self.updating_logs = True
            if changed:
                widget.update(self.snapshot["logs"])

            def follow():
                if self.log_following:
                    self.query_one("#log-scroll").scroll_end(animate=False, immediate=True)
                self.updating_logs = False

            self.call_after_refresh(follow)
        self.update_log_hint()

    def read_snapshot(self):
        status = self.manager.status()
        subs = self.manager.list_subs()
        record = self.manager.engine.running()
        proxies, api_error = {}, None
        if record:
            try:
                proxies = Controller(self.manager.engine, instance_id(record)).proxies()
            except AppError as error:
                api_error = error_message(error)
        log_path = self.manager.engine.log_path
        logs = "尚无日志"
        if log_path.exists():
            with log_path.open("rb") as stream:
                size = stream.seek(0, 2)
                start = max(0, size - 256 * 1024)
                stream.seek(start)
                data = stream.read(256 * 1024)
                if start:
                    data = data.partition(b"\n")[2]
                logs = "\n".join(data.decode(errors="replace").splitlines()[-200:]) or "尚无日志"
        if instance_id(self.manager.engine.running()) != instance_id(record):
            raise AppError("stale_instance", "内核状态已变化，正在刷新。", 5)
        return {
            "status": status,
            "subs": subs,
            "proxies": proxies,
            "instance": instance_id(record),
            "api_error": api_error,
            "logs": logs,
        }

    def action_refresh(self):
        if not self.is_running or self.busy or self.reading or isinstance(self.screen, ModalScreen):
            return
        self.reading = True
        self.run_worker(self.refresh_snapshot(self.revision), group="refresh")

    async def refresh_snapshot(self, revision):
        try:
            snapshot = await asyncio.to_thread(self.read_snapshot)
            if (
                self.is_running
                and revision == self.revision
                and not self.busy
                and not isinstance(self.screen, ModalScreen)
            ):
                self.apply_snapshot(snapshot)
        except Exception as error:
            if (
                self.is_running
                and revision == self.revision
                and not self.busy
                and not isinstance(self.screen, ModalScreen)
            ):
                self.show_error(error, operation="刷新状态")
        finally:
            self.reading = False
            if revision != self.revision:
                self.action_refresh()

    def apply_snapshot(self, snapshot):
        previous = self.snapshot
        self.snapshot = snapshot
        status = snapshot["status"]
        state = (
            "运行正常"
            if status["healthy"]
            else "运行中 / API 未就绪"
            if status["running"]
            else "已停止"
        )
        settings = status["running_settings"] or status["settings"]
        status_widget = self.query_one("#status", Static)
        display_state = "API 未就绪" if status["running"] and not status["healthy"] else state
        status_widget.update(f"{'●' if status['running'] else '○'}  {display_state}")
        status_widget.set_class(status["healthy"], "online")
        status_widget.set_class(status["running"] and not status["healthy"], "pending")
        status_widget.tooltip = f"{state} · PID {status['pid'] or '—'}"
        selected, running = status["selected"] or "—", status["running_subscription"] or "—"
        subscription = selected if selected == running else f"{selected} / {running}"
        self.query_one("#subscription-value", Static).update(subscription)
        self.query_one("#subscription-value").tooltip = Text(f"已选：{selected} · 运行：{running}")
        address = f"{settings['host']}:{settings['proxy_port']}"
        self.query_one("#proxy-value", Static).update(address)
        self.query_one("#proxy-value").tooltip = address
        self.query_one("#mode-value", Static).update(settings["mode"].upper())
        self.query_one("#subs").display = bool(snapshot["subs"])
        self.query_one("#sub-empty").display = not snapshot["subs"]
        self.query_one("#subs", StableTable).set_records(
            [
                (
                    sub["name"],
                    (
                        Text("●", style=self.current_theme.accent) if sub["selected"] else "",
                        sub["name"],
                        sub["source"],
                        sub["updated_at"][:16].replace("T", " "),
                    ),
                )
                for sub in snapshot["subs"]
            ]
        )
        self.sub_names = [sub["name"] for sub in snapshot["subs"]]
        if not previous or previous["instance"] != snapshot["instance"]:
            self.delay_results.clear()
            self.delay_errors.clear()
        groups = Controller.groups(snapshot["proxies"])
        old_groups = Controller.groups(previous["proxies"]) if previous else []
        names = [group["name"] for group in groups]
        if [group["name"] for group in old_groups] != names:
            select = self.query_one("#group", Select)
            self.group_name = (
                self.group_name
                if self.group_name in names
                else next(
                    (name for name in names if name != "GLOBAL"),
                    names[0] if names else None,
                )
            )
            with select.prevent(Select.Changed):
                select.set_options((Text(name), name) for name in names)
                select.value = self.group_name if self.group_name else Select.NULL
        self.update_nodes()
        self.render_logs()
        self.update_buttons()
        tabs = self.query_one(TabbedContent)
        table = self.query_one("#subs", DataTable)
        if self.next_focus:
            selector, name = self.next_focus
            self.next_focus = None
            if selector == "#subs" and name in self.sub_names:
                tabs.active = "subscriptions"
                table.move_cursor(row=self.sub_names.index(name))
                table.focus()
            elif selector == "#start" and tabs.active == "subscriptions" and not status["running"]:
                self.query_one("#start").focus()
        elif previous is None and self.sub_names and tabs.active == "subscriptions":
            if status["selected"] in self.sub_names:
                table.move_cursor(row=self.sub_names.index(status["selected"]))
            table.focus()

    def node_delay(self, node):
        name = node["name"]
        if name == self.testing_node:
            return "测试中…"
        if name in self.delay_errors:
            return "失败"
        delay = self.delay_results.get(name, node["delay_ms"])
        return f"{delay} ms" if delay is not None else "待测"

    def update_nodes(self):
        if not self.snapshot:
            return
        proxies = self.snapshot["proxies"]
        table = self.query_one("#node-table", StableTable)
        hint = self.query_one("#group-hint", Static)
        empty = self.query_one("#node-empty", Static)
        records = []
        if self.group_name not in proxies:
            hint.update("节点列表")
            hint.tooltip = None
            if self.snapshot["api_error"]:
                empty.update("内核 API 不可用\n\n" + self.snapshot["api_error"])
            elif not self.snapshot["status"]["running"]:
                empty.update("内核已停止\n\n选择订阅并启动内核后，即可查看节点")
            else:
                empty.update("暂无可用代理组\n\n请检查订阅配置后刷新")
        else:
            group = proxies[self.group_name]
            search = self.query_one("#filter", Input).value.casefold()
            nodes = Controller.members(proxies, self.group_name)
            for node in nodes:
                if search not in node["name"].casefold():
                    continue
                delay = self.node_delay(node)
                records.append(
                    (
                        node["name"],
                        (
                            Text("●", style=self.current_theme.accent) if node["selected"] else "",
                            node["name"],
                            node["type"],
                            Text(delay, style=self.current_theme.error if delay == "失败" else ""),
                        ),
                    )
                )
            action = "Enter 切换" if group["type"] == "Selector" else "自动选择 · 仅查看/测速"
            hint.update(
                Text(
                    f"{len(records)}/{len(nodes)} 节点 · 当前 {group.get('now') or '自动'} "
                    f"· {action}"
                )
            )
            hint.tooltip = Text(f"{group['type']} · 当前：{group.get('now') or '自动'} · {action}")
            empty.update("没有匹配的节点\n\nEsc 清空搜索，或尝试其他名称")
        table.set_records(records)
        self.node_names = [name for name, _ in records]
        table.display = bool(records)
        empty.display = not records

    def current_sub(self):
        index = self.query_one("#subs", DataTable).cursor_row
        return self.sub_names[index] if 0 <= index < len(self.sub_names) else None

    def current_node(self):
        index = self.query_one("#node-table", DataTable).cursor_row
        return self.node_names[index] if 0 <= index < len(self.node_names) else None

    def update_buttons(self):
        disabled = {}
        if self.snapshot:
            status = self.snapshot["status"]
            running = status["running"]
            name = self.current_sub()
            use_restarts = bool(name) and running and name != status["running_subscription"]
            update_restarts = bool(name) and running and name in (
                status["selected"], status["running_subscription"]
            )
            use = self.query_one("#use", Button)
            use.label = "使用并重启" if use_restarts else "使用"
            use.tooltip = (
                "应用此订阅，将重启内核并短暂中断连接。"
                if use_restarts else "应用此订阅；内核停止时不会自动启动。"
            )
            update = self.query_one("#update", Button)
            update.label = "更新并应用" if update_restarts else "更新"
            warning = "配置有变化时将重启内核并短暂中断连接。"
            update.tooltip = warning if update_restarts else "读取来源并校验、保存订阅。"
            self.query_one("#edit", Button).tooltip = (
                warning if update_restarts else "修改来源并重新读取、校验订阅。"
            )
            action = "Enter 使用并重启" if use_restarts else "Enter 使用"
            warning_hint = " · 更新变更将重启" if update_restarts else ""
            self.query_one("#sub-hint", Static).update(
                f"{len(self.sub_names)} 个订阅 · 单击选择 · {action}{warning_hint} · i 详情"
            )
            disabled.update(start=not status["selected"], stop=not running, restart=not running)
            for key in ("use", "update", "edit", "remove"):
                disabled[key] = not self.current_sub()
            disabled["remove"] = not self.current_sub() or (
                running
                and self.current_sub() in (status["selected"], status["running_subscription"])
            )
            selectable = self.snapshot["proxies"].get(self.group_name, {}).get("type") == "Selector"
            disabled["select-node"] = not self.node_names or not selectable
            disabled["test-node"] = not self.node_names
        for button in chain.from_iterable(screen.query(Button) for screen in self.screen_stack):
            unavailable = disabled.get(button.id, False)
            if button.id in ("error-details", "form-details"):
                has_error = bool(self.last_error)
                if button.id == "form-details":
                    form = next(p for p in button.ancestors if isinstance(p, Form))
                    has_error = has_error and bool(form.query_one("#form-error", Static).content)
                button.display = has_error
                unavailable = not has_error
            button.disabled = unavailable or (
                self.busy and button.id not in self.READ_ONLY_BUTTONS
            )
        for widget in chain.from_iterable(
            screen.query("Input, Select") for screen in self.screen_stack
        ):
            widget.disabled = self.busy and any(
                isinstance(parent, Form) for parent in widget.ancestors
            )

    def show_error(self, error, *, operation="界面操作"):
        message = error_message(error)
        self.last_error = message
        self.last_error_details = error_details(error, operation)
        self.query_one("#message", Static).update(message)
        self.query_one("#message").set_classes("error")
        if isinstance(self.screen, Form):
            self.screen.query_one("#form-error", Static).update(message)
            self.screen.query_one("#form-error").remove_class("progress")
        self.update_buttons()

    def subscription_progress(self, message):
        self.call_from_thread(self.show_progress, message)

    def show_progress(self, message):
        if not self.busy:
            return
        self.query_one("#message", Static).update(message)
        if isinstance(self.screen, Form):
            self.screen.query_one("#form-error", Static).update(message)
            self.screen.query_one("#form-error").add_class("progress")

    def operate(self, label, operation, form=None, *, focus=None, success=None):
        if self.busy:
            return
        form_focus = self.focused if form else None
        self.busy = True
        self.revision += 1
        self.last_error = None
        self.last_error_details = None
        self.query_one("#message").set_classes("progress")
        self.query_one("#message", Static).update(f"{label}…")
        self.update_buttons()
        self.run_worker(
            self.run_operation(label, operation, form, focus, success, form_focus),
            group="operation",
        )

    async def run_operation(self, label, operation, form, focus, success, form_focus):
        def execute():
            with self.manager.store.lock():
                return operation()

        try:
            result = await asyncio.to_thread(execute)
            if isinstance(result, dict) and "delay_ms" in result:
                self.delay_results[result["name"]] = result["delay_ms"]
                message = f"{result['name']}：{result['delay_ms']} ms"
            else:
                message = success or f"{label}完成"
            self.next_focus = focus
            if form:
                form.dismiss()
            self.query_one("#message", Static).update(message)
            self.query_one("#message").set_classes("success")
        except Exception as error:
            if self.testing_node:
                self.delay_errors[self.testing_node] = error_details(error, label)
                self.delay_results.pop(self.testing_node, None)
            self.show_error(error, operation=label)
        finally:
            self.testing_node = None
            self.busy = False
            if self.is_running:
                self.update_nodes()
                self.update_buttons()
                if form and self.screen is form and form_focus:
                    form_focus.focus()
                # A refresh started before this operation must never overwrite its result.
                self.action_refresh()

    async def action_quit(self):
        if isinstance(self.screen, Details):
            self.screen.dismiss()
        elif self.busy:
            self.query_one("#message", Static).update("操作正在完成，请稍后退出。")
        elif isinstance(self.screen, ModalScreen):
            self.screen.dismiss()
        else:
            self.exit()

    def action_add(self):
        if self.busy or isinstance(self.screen, ModalScreen):
            return
        self.show_dialog(
            Form(
                "添加订阅",
                [
                    ("sub-name", "订阅名称", "", False),
                    ("sub-source", "订阅 URL 或本地 YAML 路径", "", False),
                ],
                lambda values, form: self.operate(
                    "添加订阅",
                    lambda: self.manager.put_sub(
                        values["sub-name"],
                        values["sub-source"],
                        create=True,
                        progress=self.subscription_progress,
                    ),
                    form,
                    focus=("#subs", values["sub-name"]),
                    success="已添加订阅，按 Enter 使用。",
                ),
            )
        )

    @on(Select.Changed, "#group")
    def group_changed(self, event):
        self.group_name = event.value if event.value is not Select.NULL else None
        self.update_nodes()
        self.update_buttons()

    @on(Input.Changed, "#filter")
    def filter_changed(self):
        self.update_nodes()
        self.update_buttons()

    @on(DataTable.RowSelected, "#subs")
    def sub_selected(self):
        self.use_sub()

    @on(DataTable.RowSelected, "#node-table")
    def node_selected(self):
        self.select_node()

    def use_sub(self):
        name = self.current_sub()
        if name and not self.query_one("#use", Button).disabled:
            stopped = not self.snapshot["status"]["running"]
            self.operate(
                "切换订阅", lambda: self.manager.use(name),
                focus=("#start", None) if stopped else None,
                success="订阅已使用，按 Enter 启动内核。" if stopped else None,
            )

    def select_node(self):
        name, group = self.current_node(), self.group_name
        expected = self.snapshot["instance"] if self.snapshot else None
        if name and group and not self.query_one("#select-node", Button).disabled:
            self.operate(
                "切换节点", lambda: Controller(self.manager.engine, expected).select(group, name)
            )

    @on(Button.Pressed)
    def pressed(self, event):
        if event.button.disabled or (
            self.busy and event.button.id not in self.READ_ONLY_BUTTONS
        ):
            return
        name = self.current_sub()
        button = event.button.id
        if button == "error-details":
            self.action_error_details()
        elif button == "log-follow":
            self.pause_logs() if self.log_following else self.resume_logs()
        elif button == "refresh":
            self.action_refresh()
        elif button == "add":
            self.action_add()
        elif button == "start":
            self.operate("启动内核", self.manager.start)
        elif button == "stop":
            self.operate("停止内核", self.manager.engine.stop)
        elif button == "restart":
            self.operate("重启内核", lambda: self.manager.start(restart=True))
        elif button == "use":
            self.use_sub()
        elif button == "update" and name:
            self.operate(
                "更新订阅", lambda: self.manager.put_sub(name, progress=self.subscription_progress)
            )
        elif button == "edit" and name:
            self.run_worker(self.edit_source(name), group="edit-source", exclusive=True)
        elif button == "remove" and name:
            self.show_dialog(
                Form(
                    f"删除订阅 {name} 及其缓存原文？",
                    [],
                    lambda values, form: self.operate(
                        "删除订阅", lambda: self.manager.remove(name), form
                    ),
                )
            )
        elif button == "settings" and self.snapshot:
            settings = self.snapshot["status"]["settings"]
            self.show_dialog(
                Form(
                    "本机设置（运行中应用会重启内核）",
                    [
                        ("proxy-port", "代理端口", str(settings["proxy_port"]), False),
                        (
                            "controller-port", "管理端口",
                            str(settings["controller_port"]), False,
                        ),
                        ("mode", "模式：rule / global / direct", settings["mode"], False),
                        (
                            "host", "代理监听 IPv4 地址（0.0.0.0 为所有接口）",
                            settings["host"], False,
                        ),
                    ],
                    self.save_settings,
                    choices={
                        "mode": [
                            ("规则 · rule", "rule"),
                            ("全局 · global", "global"),
                            ("直连 · direct", "direct"),
                        ]
                    },
                )
            )
        elif button == "select-node":
            self.select_node()
        elif button == "test-node" and self.current_node():
            node = self.current_node()
            self.testing_node = node
            self.delay_errors.pop(node, None)
            self.update_nodes()
            url = self.query_one("#test-url", Input).value
            expected = self.snapshot["instance"]
            self.operate(
                "测试延迟", lambda: Controller(self.manager.engine, expected).test(node, url)
            )

    async def edit_source(self, name):
        try:
            state = await asyncio.to_thread(self.manager.store.read)
            if not self.is_running or self.busy or isinstance(self.screen, ModalScreen):
                return
            if name not in state["subs"]:
                raise AppError("not_found", "订阅已移除，请刷新后重试。", 3)
            status = self.snapshot["status"]
            restarting = status["running"] and name in (
                status["selected"], status["running_subscription"]
            )
            self.show_dialog(
                Form(
                    f"修改来源：{name}" + ("（配置变更将重启内核）" if restarting else ""),
                    [
                        (
                            "sub-source",
                            "订阅 URL 或本地 YAML 路径",
                            state["subs"][name]["source"],
                            False,
                        )
                    ],
                    lambda values, form: self.operate(
                        "修改来源",
                        lambda: self.manager.put_sub(
                            name,
                            values["sub-source"],
                            progress=self.subscription_progress,
                        ),
                        form,
                    ),
                )
            )
        except Exception as error:
            if self.is_running:
                self.show_error(error, operation="读取订阅来源")


    @on(DataTable.RowHighlighted)
    def row_highlighted(self):
        self.update_buttons()

    def save_settings(self, values, form):
        if not valid_host(values["host"]):
            form.field_error("host", "须为 IPv4 地址，例如 127.0.0.1 或 0.0.0.0。")
            return
        settings = {"mode": values["mode"], "host": values["host"]}
        for key in ("proxy-port", "controller-port"):
            try:
                value = int(values[key])
                if not 1 <= value <= 65535:
                    raise ValueError
            except ValueError:
                form.field_error(key, "端口须为 1–65535 的整数。")
                return
            settings[key.replace("-", "_")] = value
        if settings["proxy_port"] == settings["controller_port"]:
            form.field_error("controller-port", "代理端口和管理端口必须不同。")
            return
        self.operate("应用设置", lambda: self.manager.configure(settings), form)
