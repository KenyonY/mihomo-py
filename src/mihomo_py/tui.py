"""Interactive front end. All blocking backend work runs off the UI thread."""

import asyncio

from rich.text import Text
from textual import on
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


def error_message(error):
    if isinstance(error, AppError):
        return str(error) + (f" {error.suggestion}" if error.suggestion else "")
    if isinstance(error, OSError):
        return f"本地 IO 操作失败（errno={error.errno}）。"
    return f"操作失败（{type(error).__name__}），请重试。"


class Form(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, title, fields, submit):
        super().__init__()
        self.heading = title
        self.fields = fields
        self.submit = submit

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog") as dialog:
            dialog.styles.height = 12 + 4 * len(self.fields)
            yield Static(self.heading, classes="dialog-title", markup=False)
            with VerticalScroll(id="form-fields"):
                for key, label, value, password in self.fields:
                    yield Static(label, markup=False)
                    yield Input(value=value, password=password, id=key)
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("确定", variant="primary", id="form-submit")
                yield Button("取消", id="form-cancel")

    def on_mount(self):
        inputs = list(self.query(Input))
        if inputs:
            inputs[0].focus()

    @on(Button.Pressed, "#form-submit")
    def accept(self, event):
        event.stop()
        if self.app.busy:
            return
        values = {key: self.query_one(f"#{key}", Input).value.strip() for key, *_ in self.fields}
        if any(not value for value in values.values()):
            self.query_one("#form-error", Static).update("请填写所有字段。")
            return
        self.submit(values, self)

    @on(Button.Pressed, "#form-cancel")
    def cancel_button(self, event):
        event.stop()
        self.action_cancel()

    def action_cancel(self):
        if not self.app.busy:
            self.dismiss()


class MihomoApp(App):
    TITLE = "mihomo-py"
    SUB_TITLE = "服务器代理管理"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+q", "quit", "退出", show=False, priority=True),
        Binding("ctrl+c", "quit", "退出", show=False, priority=True),
        Binding("ctrl+r", "refresh", "刷新"),
        Binding("ctrl+a", "add", "添加订阅"),
    ]
    CSS = """
    Screen { background: $background; color: $foreground; }
    #masthead { height: 2; padding: 0 2; }
    #brand { width: 1fr; color: $accent; text-style: bold; }
    #edition { width: auto; color: $text-muted; }
    #overview { height: 4; margin: 0 2; border: round #303c50; padding: 0 1; }
    .metric { width: 1fr; height: 2; }
    #subscription-metric { width: 2fr; }
    .metric-label { height: 1; color: $text-muted; }
    .metric-value { height: 1; text-style: bold; }
    #status { color: $text-muted; }
    #status.online { color: $success; }
    #status.pending { color: $warning; }
    .buttons { height: 1; }
    Button { height: 1; min-width: 8; min-height: 1; border: none;
             padding: 0 1; margin-right: 1; background: $surface; color: $foreground; }
    Button:hover { background: #2b3a50; }
    Button:focus { background: #34465e; text-style: bold; }
    Button.-primary, Button.-success { background: $accent; color: $background; }
    Button.-error { background: $surface; color: $error; }
    Button:disabled { background: $background; color: #536176; text-opacity: 100%; }
    #controls { margin: 0 2; }
    TabbedContent { height: 1fr; margin: 0 2; }
    Tabs { height: 2; }
    Tab { padding: 0 2; color: $text-muted; }
    Tab.-active { color: $accent; text-style: bold; }
    Underline > .underline--bar { color: $accent; background: #303c50; }
    ContentSwitcher { height: 1fr; }
    TabPane { padding: 0 1; border: round #303c50; }
    DataTable { height: 1fr; min-height: 3; background: $background; }
    DataTable > .datatable--header { background: $surface; color: #99aac2;
                                   text-style: not bold; }
    DataTable > .datatable--odd-row { background: #131d2d; }
    DataTable > .datatable--even-row { background: $background; }
    DataTable > .datatable--cursor { background: #243d50; color: #e4f5f3; }
    DataTable:focus > .datatable--cursor { background: #264a5b; color: #ffffff; }
    .hint { height: 1; color: $text-muted; margin-bottom: 1; }
    .empty { height: 1fr; min-height: 3; content-align: center middle;
             color: $text-muted; text-align: center; }
    .list-area { height: 1fr; }
    #message { height: auto; max-height: 3; margin: 0 2; color: $text-muted; }
    #message.error { color: $error; }
    #message.success { color: $success; }
    #node-filters { height: 3; }
    #node-filters Select { width: 1fr; margin-right: 1; }
    #node-filters Input { width: 1fr; }
    Input { background: $surface; border: tall #303c50; }
    Input:focus { border: tall $accent; }
    SelectCurrent { background: $surface; border: tall #303c50; }
    #group-hint { height: 1; color: $text-muted; }
    #test-tools { height: 1; margin-top: 1; }
    #test-label { width: 7; color: $text-muted; }
    #test-url { width: 1fr; height: 1; border: none; padding: 0 1; }
    #test-url:focus { border: none; background: #243d50; }
    #log-text { height: auto; color: #99aac2; }
    #log-scroll { height: 1fr; }
    Footer { background: $surface; padding: 0 2; }
    Form { align: center middle; background: $background 75%; }
    #dialog { width: 64; max-width: 95%; height: auto; max-height: 90%;
              padding: 1 2; border: round $accent; background: $surface; }
    .dialog-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #form-fields { height: 1fr; }
    #form-error { height: auto; max-height: 3; color: $error; margin: 1 0; }
    #form-error.progress { color: $accent; }
    """

    def __init__(self, manager):
        super().__init__()
        self.register_theme(
            Theme(
                name="mihomo-night",
                primary="#68d5c3",
                accent="#68d5c3",
                secondary="#8ca9df",
                background="#101826",
                surface="#1a2638",
                panel="#1a2638",
                foreground="#d9e2ef",
                success="#83d9a6",
                warning="#e7bf76",
                error="#ed929f",
                dark=True,
                variables={"footer-key-foreground": "#68d5c3", "footer-background": "#1a2638"},
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
        self.delay_results = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("◈  mihomo", id="brand")
            yield Static("服务器代理管理 / mihomo-py", id="edition")
        with Horizontal(id="overview"):
            with Vertical(classes="metric"):
                yield Static("内核", classes="metric-label")
                yield Static("○  读取中", id="status", classes="metric-value", markup=False)
            with Vertical(id="subscription-metric", classes="metric"):
                yield Static("订阅 · 已选 / 运行", classes="metric-label")
                yield Static("—", id="subscription-value", classes="metric-value", markup=False)
            with Vertical(classes="metric"):
                yield Static("本机代理", classes="metric-label")
                yield Static("—", id="proxy-value", classes="metric-value")
            with Vertical(classes="metric"):
                yield Static("路由模式", classes="metric-label")
                yield Static("—", id="mode-value", classes="metric-value")
        with Horizontal(id="controls", classes="buttons"):
            yield Button("启动", id="start", variant="success")
            yield Button("停止", id="stop", variant="error")
            yield Button("重启", id="restart")
            yield Button("设置", id="settings")
            yield Button("刷新", id="refresh")
        with TabbedContent():
            with TabPane("订阅", id="subscriptions"):
                yield Static("订阅库   /   ↑↓ 选择，Enter 使用", id="sub-hint", classes="hint")
                with Vertical(classes="list-area"):
                    yield DataTable(id="subs", cursor_type="row", zebra_stripes=True)
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
            with TabPane("节点", id="nodes"):
                with Horizontal(id="node-filters"):
                    yield Select([], prompt="选择代理组", id="group")
                    yield Input(placeholder="搜索节点名称", id="filter")
                yield Static("启动内核后显示节点", id="group-hint", markup=False)
                with Vertical(classes="list-area"):
                    yield DataTable(id="node-table", cursor_type="row", zebra_stripes=True)
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
                    yield Input(value=DEFAULT_TEST_URL, placeholder="延迟测试 URL", id="test-url")
            with TabPane("日志", id="logs"):
                yield Static("最近 200 行 · 自动刷新 · 可能含订阅内部地址", classes="hint")
                with VerticalScroll(id="log-scroll"):
                    yield Static("尚无日志", id="log-text", markup=False)
        yield Static("退出界面会保留运行中的内核。", id="message", markup=False)
        yield Footer()

    def on_mount(self):
        table = self.query_one("#subs", DataTable)
        table.add_column("", width=2)
        table.add_column("订阅", width=16)
        table.add_column("来源", width=26)
        table.add_column("更新于", width=16)
        table = self.query_one("#node-table", DataTable)
        table.add_column("", width=2)
        table.add_column("节点", width=32)
        table.add_column("类型", width=12)
        table.add_column("延迟", width=10)
        self.query_one("#subs", DataTable).focus()
        self.action_refresh()
        self.set_interval(2, self.action_refresh)

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
        if self.busy or self.reading or isinstance(self.screen, Form):
            return
        self.reading = True
        self.run_worker(self.refresh_snapshot(self.revision), group="refresh")

    async def refresh_snapshot(self, revision):
        try:
            snapshot = await asyncio.to_thread(self.read_snapshot)
            if revision == self.revision and not self.busy:
                self.apply_snapshot(snapshot)
        except Exception as error:
            if revision == self.revision and not self.busy:
                self.show_error(error)
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
        status_widget.update(f"{'●' if status['running'] else '○'}  {state}")
        status_widget.set_class(status["healthy"], "online")
        status_widget.set_class(status["running"] and not status["healthy"], "pending")
        status_widget.tooltip = f"PID {status['pid'] or '—'}"
        selected, running = status["selected"] or "—", status["running_subscription"] or "—"
        subscription = selected if selected == running else f"{selected} / {running}"
        self.query_one("#subscription-value", Static).update(subscription)
        self.query_one("#subscription-value").tooltip = f"已选：{selected} · 运行：{running}"
        self.query_one("#proxy-value", Static).update(f":{settings['proxy_port']}")
        self.query_one("#proxy-value").tooltip = f"127.0.0.1:{settings['proxy_port']}"
        self.query_one("#mode-value", Static).update(settings["mode"].upper())
        self.query_one("#sub-hint", Static).update(
            f"订阅库 · {len(snapshot['subs'])} 项   /   ↑↓ 选择，Enter 使用"
        )
        self.query_one("#subs").display = bool(snapshot["subs"])
        self.query_one("#sub-empty").display = not snapshot["subs"]
        if not previous or previous["subs"] != snapshot["subs"]:
            table = self.query_one("#subs", DataTable)
            selected = self.current_sub()
            table.clear()
            self.sub_names = [sub["name"] for sub in snapshot["subs"]]
            for sub in snapshot["subs"]:
                table.add_row(
                    *[
                        Text(str(value))
                        for value in (
                            "●" if sub["selected"] else "",
                            sub["name"],
                            sub["source"],
                            sub["updated_at"][:16].replace("T", " "),
                        )
                    ],
                    key=sub["name"],
                )
            if selected in self.sub_names:
                table.move_cursor(row=self.sub_names.index(selected))
        if not previous or previous["instance"] != snapshot["instance"]:
            self.delay_results.clear()
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
        if not previous or previous["logs"] != snapshot["logs"]:
            self.query_one("#log-text", Static).update(snapshot["logs"])
        self.update_buttons()

    def update_nodes(self):
        if not self.snapshot:
            return
        proxies = self.snapshot["proxies"]
        table = self.query_one("#node-table", DataTable)
        previous_node = self.current_node()
        table.clear()
        self.node_names = []
        hint = self.query_one("#group-hint", Static)
        if self.group_name not in proxies:
            hint.update("节点列表")
            table.display = False
            empty = self.query_one("#node-empty", Static)
            empty.display = True
            empty.update(
                self.snapshot["api_error"] or "暂无可用代理组\n\n选择订阅并启动内核后，即可查看节点"
            )
            return
        group = proxies[self.group_name]
        hint.update(f"{group['type']} · 当前：{group.get('now') or '自动'} · Enter 切换手动组")
        search = self.query_one("#filter", Input).value.casefold()
        for node in Controller.members(proxies, self.group_name):
            if search not in node["name"].casefold():
                continue
            self.node_names.append(node["name"])
            delay = self.delay_results.get(node["name"], node["delay_ms"])
            table.add_row(
                *[
                    Text(str(value))
                    for value in (
                        "●" if node["selected"] else "",
                        node["name"],
                        node["type"],
                        f"{delay} ms" if delay is not None else "—",
                    )
                ],
                key=node["name"],
            )
        table.display = bool(self.node_names)
        empty = self.query_one("#node-empty", Static)
        empty.display = not self.node_names
        empty.update("没有匹配的节点\n\n试试其他名称，或清空搜索条件")
        if previous_node in self.node_names:
            table.move_cursor(row=self.node_names.index(previous_node))

    def current_sub(self):
        index = self.query_one("#subs", DataTable).cursor_row
        return self.sub_names[index] if 0 <= index < len(self.sub_names) else None

    def current_node(self):
        index = self.query_one("#node-table", DataTable).cursor_row
        return self.node_names[index] if 0 <= index < len(self.node_names) else None

    def update_buttons(self):
        for button in self.query(Button):
            button.disabled = self.busy
        for widget in self.query(Input):
            widget.disabled = self.busy
        if not self.busy and self.snapshot:
            running = self.snapshot["status"]["running"]
            self.query_one("#stop", Button).disabled = not running
            self.query_one("#restart", Button).disabled = not running
            for selector in ("#use", "#update", "#edit", "#remove"):
                self.query_one(selector, Button).disabled = not self.sub_names
            proxies = self.snapshot["proxies"]
            selectable = proxies.get(self.group_name, {}).get("type") == "Selector"
            self.query_one("#select-node", Button).disabled = not self.node_names or not selectable
            self.query_one("#test-node", Button).disabled = not self.node_names

    def show_error(self, error):
        message = error_message(error)
        self.last_error = message
        self.query_one("#message", Static).update(message)
        self.query_one("#message").set_classes("error")
        if isinstance(self.screen, Form):
            self.screen.query_one("#form-error", Static).update(message)
            self.screen.query_one("#form-error").remove_class("progress")

    def subscription_progress(self, message):
        self.call_from_thread(self.show_progress, message)

    def show_progress(self, message):
        if not self.busy:
            return
        self.query_one("#message", Static).update(message)
        if isinstance(self.screen, Form):
            self.screen.query_one("#form-error", Static).update(message)
            self.screen.query_one("#form-error").add_class("progress")

    def operate(self, label, operation, form=None):
        if self.busy:
            return
        self.busy = True
        self.revision += 1
        self.last_error = None
        self.query_one("#message").set_classes("")
        self.query_one("#message", Static).update(f"{label}…")
        self.update_buttons()
        self.run_worker(self.run_operation(label, operation, form), group="operation")

    async def run_operation(self, label, operation, form):
        def execute():
            with self.manager.store.lock():
                return operation()

        try:
            result = await asyncio.to_thread(execute)
            if isinstance(result, dict) and "delay_ms" in result:
                self.delay_results[result["name"]] = result["delay_ms"]
                message = f"{result['name']}：{result['delay_ms']} ms"
            else:
                message = f"{label}完成"
            if form:
                form.dismiss()
            self.query_one("#message", Static).update(message)
            self.query_one("#message").set_classes("success")
        except Exception as error:
            self.show_error(error)
        finally:
            self.busy = False
            self.update_buttons()
            # A refresh started before this operation must never overwrite its result.
            self.action_refresh()

    async def action_quit(self):
        if self.busy:
            self.query_one("#message", Static).update("操作正在完成，请稍后退出。")
        elif isinstance(self.screen, Form):
            self.screen.dismiss()
        else:
            self.exit()

    def action_add(self):
        if self.busy or isinstance(self.screen, Form):
            return
        self.push_screen(
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
        if name:
            self.operate("切换订阅", lambda: self.manager.use(name))

    def select_node(self):
        name, group = self.current_node(), self.group_name
        expected = self.snapshot["instance"] if self.snapshot else None
        if name and group:
            self.operate(
                "切换节点", lambda: Controller(self.manager.engine, expected).select(group, name)
            )

    @on(Button.Pressed)
    def pressed(self, event):
        if self.busy:
            return
        name = self.current_sub()
        button = event.button.id
        if button == "refresh":
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
            self.push_screen(
                Form(
                    f"修改来源：{name}",
                    [
                        ("sub-source", "新的 URL 或本地 YAML 路径", "", False),
                    ],
                    lambda values, form: self.operate(
                        "修改来源",
                        lambda: self.manager.put_sub(
                            name, values["sub-source"], progress=self.subscription_progress
                        ),
                        form,
                    ),
                )
            )
        elif button == "remove" and name:
            self.push_screen(
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
            self.push_screen(
                Form(
                    "本机设置（运行中应用会重启内核）",
                    [
                        ("proxy-port", "代理端口", str(settings["proxy_port"]), False),
                        ("controller-port", "管理端口", str(settings["controller_port"]), False),
                        ("mode", "模式：rule / global / direct", settings["mode"], False),
                    ],
                    self.save_settings,
                )
            )
        elif button == "select-node":
            self.select_node()
        elif button == "test-node" and self.current_node():
            node = self.current_node()
            self.delay_results[node] = None
            url = self.query_one("#test-url", Input).value
            expected = self.snapshot["instance"]
            self.operate(
                "测试延迟", lambda: Controller(self.manager.engine, expected).test(node, url)
            )

    def save_settings(self, values, form):
        try:
            settings = {
                "proxy_port": int(values["proxy-port"]),
                "controller_port": int(values["controller-port"]),
                "mode": values["mode"],
            }
            if not all(1 <= settings[key] <= 65535 for key in ("proxy_port", "controller_port")):
                raise ValueError
            if settings["mode"] not in ("rule", "global", "direct"):
                raise ValueError
        except ValueError:
            self.show_error(
                AppError("invalid_settings", "端口须为 1–65535，模式须为 rule/global/direct。", 2)
            )
            return
        self.operate("应用设置", lambda: self.manager.configure(settings), form)
