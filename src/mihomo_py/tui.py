"""Interactive front end. All blocking backend work runs off the UI thread."""

import asyncio
import errno
import os
from dataclasses import replace
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

from . import clipboard
from .bundle import dashboard_root
from .controller import DEFAULT_TEST_URL, Controller, instance_id
from .errors import AppError
from .i18n import localized_text, resolve_language, translated_message
from .store import LANGUAGE_OPTIONS, valid_host


def error_message(error, language="zh"):
    if isinstance(error, AppError):
        message = translated_message(language, error.message)
        if error.suggestion:
            message += " " + translated_message(language, error.suggestion)
        return message
    if isinstance(error, OSError):
        return (
            localized_text(language, "local_io_error", errno=error.errno)
            if error.errno is not None
            else localized_text(language, "operation_failed", type=type(error).__name__)
        )
    return localized_text(language, "operation_failed", type=type(error).__name__)


def error_details(error, operation, language="zh"):
    """Describe safe error metadata, never raw exceptions or core diagnostics."""
    lines = [
        f"{localized_text(language, 'error_operation')}：{operation}"
        if language == "zh"
        else f"{localized_text(language, 'error_operation')}: {operation}",
        f"{localized_text(language, 'error_type')}：{type(error).__name__}"
        if language == "zh"
        else f"{localized_text(language, 'error_type')}: {type(error).__name__}",
    ]
    if isinstance(error, AppError):
        label = localized_text(language, "error_code")
        separator = "：" if language == "zh" else ": "
        lines.extend([f"{label}{separator}{error.kind}", error_message(error, language)])
    else:
        lines.append(error_message(error, language))
    cause = error if isinstance(error, OSError) else error.__cause__
    if isinstance(cause, OSError) and cause.errno is not None:
        code = cause.errno
        separator = "：" if language == "zh" else ": "
        lines.extend(
            [
                f"{localized_text(language, 'cause')}{separator}{type(cause).__name__}",
                (
                    f"{localized_text(language, 'system_error')}{separator}"
                    f"{errno.errorcode.get(code, 'UNKNOWN')} (errno={code})"
                    if language == "en"
                    else f"{localized_text(language, 'system_error')}{separator}"
                    f"{errno.errorcode.get(code, 'UNKNOWN')}（errno={code}）"
                ),
                f"{localized_text(language, 'system_reason')}{separator}{os.strerror(code)}",
            ]
        )
    return "\n".join(lines)


class StableTable(DataTable):
    """Only rebuild on structural changes; keep reading position during polling."""

    def __init__(self, headings, **kwargs):
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self.headings = headings
        self.records = []
        self.column_widths = ()

    def on_resize(self):
        self.set_records(self.records)

    def set_headings(self, headings):
        self.headings = tuple(headings)
        self.column_widths = ()
        self.set_records(self.records)

    async def _on_click(self, event: events.Click) -> None:
        # Only a double-click applies a subscription; single clicks just browse.
        # Other tables keep Textual's automatic base-handler dispatch.
        if self.id == "subs":
            event.prevent_default()  # Textual otherwise invokes the base handler again.
            if event.button == 1 and event.chain == 2:
                await super()._on_click(event)
            else:
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
    BINDINGS = [Binding("end", "tail", "Resume")]

    def watch_scroll_y(self, old_value, new_value):
        super().watch_scroll_y(old_value, new_value)
        if new_value < old_value and new_value < self.max_scroll_y and not self.app.updating_logs:
            self.app.pause_logs()

    def on_mount(self):
        self.app.localize_bindings(self, {"tail": "follow"})

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
        Binding("escape", "cancel", "Close"),
        Binding("f8", "error_details", "Error details", show=False),
    ]

    def on_click(self, event: events.Click):
        if event.button == 1 and event.widget is self:
            event.stop()
            self.action_cancel()

    def on_mount(self):
        self.localize_bindings()

    def localize_bindings(self):
        self.app.localize_bindings(
            self,
            {
                "cancel": "cancel" if isinstance(self, Form) else "close",
                "error_details": "error_details",
            },
        )

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
            yield Button(f"{self.app.t('close')} · Esc", id="detail-close", variant="primary")

    @on(Button.Pressed, "#detail-close")
    def close(self, event):
        event.stop()
        self.action_cancel()


class Form(Dialog):
    BINDINGS = [("escape", "cancel", "Cancel")]

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
                yield Button(self.app.t("confirm"), variant="primary", id="form-submit")
                yield Button(self.app.t("cancel"), id="form-cancel")
                yield Button(self.app.t("error_details"), id="form-details", disabled=True)

    def on_mount(self):
        self.localize_bindings()
        fields = list(self.query(Input)) + list(self.query(Select))
        if fields:
            fields[0].focus()
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
                self.field_error(key, self.app.t("fill_field"))
                return
        self.submit(values, self)

    def field_error(self, key, message):
        self.app.show_error(
            AppError("invalid_field", message, 2), operation=self.app.t("form_operation")
        )
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
    SUB_TITLE = "Server proxy manager"
    ENABLE_COMMAND_PALETTE = False
    READ_ONLY_BUTTONS = {"detail-close", "log-follow", "error-details"}
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+q", "quit", "退出", show=False, priority=True),
        Binding("ctrl+c,ctrl+shift+c", "copy_selection", "复制选区", show=False, priority=True),
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
        state = manager.store.read()
        self.language_preference = state["language"]
        self.language = resolve_language(self.language_preference)
        self.sub_title = self.t("app_subtitle")
        self.busy = False
        self.reading = False
        self.revision = 0
        self.snapshot = None
        self.group_name = None
        self.node_names = []
        self.sub_names = []
        self.last_error = None
        self.last_error_details = None
        self.last_error_exception = None
        self.next_focus = None
        self.delay_results = {}
        self.testing_node = None
        self.delay_errors = {}
        self.log_following = True
        self.updating_logs = False
        self.page_focus = {}

    def t(self, key, **values):
        return localized_text(self.language, key, **values)

    def language_name(self):
        return self.t("language_name_zh" if self.language == "zh" else "language_name_en")

    def label(self, key, value):
        return f"{self.t(key)}：{value}" if self.language == "zh" else f"{self.t(key)}: {value}"

    def localize_bindings(self, node, labels):
        for key, bindings in node._bindings.key_to_bindings.items():
            node._bindings.key_to_bindings[key] = [
                replace(binding, description=self.t(labels[binding.action]))
                if binding.action in labels
                else binding
                for binding in bindings
            ]
        if node is self.screen:
            node.refresh_bindings()

    def update_binding_labels(self):
        labels = {
            "quit": "quit",
            "copy_selection": "copy",
            "refresh": "refresh",
            "add": "add_sub",
            "page('subscriptions')": "subscriptions",
            "page('nodes')": "nodes",
            "page('logs')": "logs",
            "search": "search",
            "clear_search": "clear_search",
            "details": "details",
            "help": "help",
            "error_details": "error_details",
        }
        self.localize_bindings(self, labels)
        if self.screen:
            self.screen.refresh_bindings()

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static(self.t("brand"), id="brand")
            yield Static(self.t("edition"), id="edition")
            yield Button("中文 / EN", id="language")
        with Horizontal(id="overview"):
            with Vertical(id="kernel-metric", classes="metric"):
                yield Static(self.t("kernel"), classes="metric-label")
                yield Static(self.t("reading"), id="status", classes="metric-value", markup=False)
            with Vertical(id="subscription-metric", classes="metric"):
                yield Static(self.t("subscription_metric"), classes="metric-label")
                yield Static("—", id="subscription-value", classes="metric-value", markup=False)
            with Vertical(id="proxy-metric", classes="metric"):
                yield Static(self.t("proxy_metric"), classes="metric-label")
                yield Static("—", id="proxy-value", classes="metric-value")
            with Vertical(id="mode-metric", classes="metric"):
                yield Static(self.t("mode_metric"), classes="metric-label")
                yield Static("—", id="mode-value", classes="metric-value")
        with Horizontal(id="controls", classes="buttons"):
            yield Button(self.t("start"), id="start", variant="success")
            yield Button(self.t("stop"), id="stop", variant="error")
            yield Button(self.t("restart"), id="restart")
            yield Button(self.t("settings"), id="settings")
            yield Button(self.t("web_start"), id="web-toggle")
            yield Button(self.t("web"), id="web")
            yield Button(self.t("refresh"), id="refresh")
        with TabbedContent():
            with TabPane(f"1 {self.t('subscriptions')}", id="subscriptions"):
                yield Static(self.t("sub_hint"), id="sub-hint", classes="hint")
                with Vertical(classes="list-area"):
                    yield StableTable(
                        ("", self.t("sub_col"), self.t("source_col"), self.t("updated_col")),
                        id="subs",
                    )
                    yield Static(
                        self.t("sub_empty"),
                        id="sub-empty",
                        classes="empty",
                    )
                with Horizontal(classes="buttons"):
                    yield Button(self.t("add"), id="add")
                    yield Button(self.t("use"), id="use", variant="primary")
                    yield Button(self.t("update"), id="update")
                    yield Button(self.t("edit"), id="edit")
                    yield Button(self.t("remove"), id="remove", variant="error")
            with TabPane(f"2 {self.t('nodes')}", id="nodes"):
                with Horizontal(id="node-filters"):
                    yield Select([], prompt=self.t("group_prompt"), id="group", compact=True)
                    yield Input(placeholder=self.t("node_search"), id="filter", compact=True)
                yield Static(self.t("group_hint"), id="group-hint", markup=False)
                with Vertical(classes="list-area"):
                    yield StableTable(
                        ("", self.t("node_col"), self.t("type_col"), self.t("delay_col")),
                        id="node-table",
                    )
                    yield Static(
                        self.t("node_empty"),
                        id="node-empty",
                        classes="empty",
                        markup=False,
                    )
                with Horizontal(classes="buttons"):
                    yield Button(self.t("select_node"), id="select-node", variant="primary")
                    yield Button(self.t("test_latency"), id="test-node")
                with Horizontal(id="test-tools"):
                    yield Static(self.t("target"), id="test-label")
                    yield Input(
                        value=DEFAULT_TEST_URL,
                        placeholder=self.t("latency_url"),
                        id="test-url",
                        compact=True,
                    )
            with TabPane(f"3 {self.t('logs')}", id="logs"):
                with Horizontal(id="log-tools"):
                    yield Static(self.t("log_hint"), id="log-hint", classes="hint")
                    yield Button(self.t("pause"), id="log-follow")
                with LogScroll(id="log-scroll", can_focus=True):
                    yield Static(self.t("no_logs"), id="log-text", markup=False)
        with Horizontal(id="feedback"):
            yield Static(self.t("keep_core"), id="message", markup=False)
            yield Button(self.t("error_details"), id="error-details", disabled=True)
        yield Footer()

    def on_mount(self):
        self.refresh_language_labels()
        self.update_binding_labels()
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
            return not isinstance(self.screen, ModalScreen) and self.focused is self.query_one(
                "#filter"
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

    def copy_to_clipboard(self, text):
        self._clipboard = text
        if self._driver is not None:
            self._driver.write(clipboard.osc52(text))
        clipboard.copy_external(text)

    def action_copy_selection(self):
        text = self.screen.get_selected_text()
        if not text and isinstance(self.focused, Input):
            text = self.focused.selected_text
        if not text:
            self.notify(self.t("no_selection"))
            return
        self.copy_to_clipboard(text)
        self.screen.clear_selection()
        self.notify(self.t("copied", count=len(text)))

    def action_help(self):
        self.show_dialog(
            Details(
                self.t("keyboard_help_title"),
                self.t("help_text"),
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
                    self.t("sub_details"),
                    (
                        f"{self.label('name', sub['name'])}\n"
                        f"{self.label('source', sub['source'])}\n"
                        f"{self.label('updated', sub['updated_at'])}\n\n"
                        f"{self.label('selected_sub', status['selected'] or '—')}\n"
                        f"{self.label('running_sub', status['running_subscription'] or '—')}"
                    ),
                )
            )
        elif page == "nodes" and self.current_node():
            node = next(
                n
                for n in Controller.members(self.snapshot["proxies"], self.group_name)
                if n["name"] == self.current_node()
            )
            active = self.t("yes") if node["selected"] else self.t("no")
            self.show_dialog(
                Details(
                    self.t("node_details"),
                    (
                        f"{self.label('name', node['name'])}\n"
                        f"{self.label('group', self.group_name)}\n"
                        f"{self.label('type', node['type'])}\n"
                        f"{self.label('active', active)}\n"
                        f"{self.label('delay', self.node_delay(node))}\n"
                        f"{self.node_error(node['name'])}"
                    ),
                )
            )

    def action_error_details(self):
        if isinstance(self.screen, Details):
            return
        if self.last_error:
            self.show_dialog(Details(self.t("error_details"), self.last_error_details))

    def pause_logs(self):
        self.log_following = False
        self.update_log_hint()

    def resume_logs(self):
        self.log_following = True
        self.render_logs(force=True)

    def update_log_hint(self):
        displayed = self.snapshot["logs"] or self.t("no_logs") if self.snapshot else ""
        pending = self.snapshot and displayed != str(self.query_one("#log-text", Static).content)
        state = self.t("log_following") if self.log_following else self.t("log_paused")
        if not self.log_following and pending:
            state += f" · {self.t('log_updated')}"
        self.query_one("#log-hint", Static).update(self.t("log_recent", state=state))
        self.query_one("#log-follow", Button).label = (
            self.t("pause") if self.log_following else self.t("resume")
        )

    def render_logs(self, *, force=False):
        widget = self.query_one("#log-text", Static)
        displayed = self.snapshot["logs"] or self.t("no_logs") if self.snapshot else ""
        changed = self.snapshot and str(widget.content) != displayed
        should_render = self.snapshot and (
            self.log_following and (changed or force)
            or not self.snapshot["logs"] and changed
        )
        if should_render:
            self.updating_logs = True
            widget.update(displayed)

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
                api_error = error
        log_path = self.manager.engine.log_path
        logs = ""
        if log_path.exists():
            with log_path.open("rb") as stream:
                size = stream.seek(0, 2)
                start = max(0, size - 256 * 1024)
                stream.seek(start)
                data = stream.read(256 * 1024)
                if start:
                    data = data.partition(b"\n")[2]
                logs = "\n".join(data.decode(errors="replace").splitlines()[-200:])
        if instance_id(self.manager.engine.running()) != instance_id(record):
            raise AppError("stale_instance", self.t("stale_refresh"), 5)
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
                self.show_error(error, operation=self.t("refresh"))
        finally:
            self.reading = False
            if revision != self.revision:
                self.action_refresh()

    def apply_snapshot(self, snapshot):
        previous = self.snapshot
        self.snapshot = snapshot
        status = snapshot["status"]
        state = (
            self.t("state_healthy")
            if status["healthy"]
            else self.t("state_pending")
            if status["running"]
            else self.t("state_stopped")
        )
        settings = status["running_settings"] or status["settings"]
        status_widget = self.query_one("#status", Static)
        display_state = (
            self.t("api_pending") if status["running"] and not status["healthy"] else state
        )
        status_widget.update(f"{'●' if status['running'] else '○'}  {display_state}")
        status_widget.set_class(status["healthy"], "online")
        status_widget.set_class(status["running"] and not status["healthy"], "pending")
        status_widget.tooltip = self.t("status_tooltip", state=state, pid=status["pid"] or "—")
        selected, running = status["selected"] or "—", status["running_subscription"] or "—"
        subscription = selected if selected == running else f"{selected} / {running}"
        self.query_one("#subscription-value", Static).update(subscription)
        self.query_one("#subscription-value").tooltip = Text(
            self.t("selected_running", selected=selected, running=running)
        )
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
            return self.t("testing")
        if name in self.delay_errors:
            return self.t("failed")
        delay = self.delay_results.get(name, node["delay_ms"])
        return f"{delay} ms" if delay is not None else self.t("pending")

    def node_error(self, node):
        error = self.delay_errors.get(node)
        if not error:
            return ""
        return f"\n\n{error_details(error, self.t('test_latency'), self.language)}"

    def update_nodes(self):
        if not self.snapshot:
            return
        proxies = self.snapshot["proxies"]
        table = self.query_one("#node-table", StableTable)
        hint = self.query_one("#group-hint", Static)
        empty = self.query_one("#node-empty", Static)
        records = []
        if self.group_name not in proxies:
            hint.update(self.t("node_list"))
            hint.tooltip = None
            if self.snapshot["api_error"]:
                empty.update(
                    self.t("api_unavailable")
                    + "\n\n"
                    + error_message(self.snapshot["api_error"], self.language)
                )
            elif not self.snapshot["status"]["running"]:
                empty.update(self.t("core_stopped") + "\n\n" + self.t("start_to_view"))
            else:
                empty.update(self.t("no_groups") + "\n\n" + self.t("check_config"))
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
                            Text(
                                delay,
                                style=self.current_theme.error if delay == self.t("failed") else "",
                            ),
                        ),
                    )
                )
            action = self.t("enter_switch") if group["type"] == "Selector" else self.t("auto_view")
            hint.update(
                Text(
                    self.t(
                        "nodes_count",
                        shown=len(records),
                        total=len(nodes),
                        current=group.get("now") or ("自动" if self.language == "zh" else "auto"),
                        action=action,
                    )
                )
            )
            hint.tooltip = Text(
                self.t(
                    "group_current",
                    type=group["type"],
                    current=group.get("now") or ("自动" if self.language == "zh" else "auto"),
                    action=action,
                )
            )
            empty.update(self.t("no_match"))
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
            update_restarts = (
                bool(name)
                and running
                and name in (status["selected"], status["running_subscription"])
            )
            use = self.query_one("#use", Button)
            use.label = self.t("use_restart") if use_restarts else self.t("use")
            use.tooltip = self.t("use_tip_restart") if use_restarts else self.t("use_tip")
            update = self.query_one("#update", Button)
            update.label = self.t("update_apply") if update_restarts else self.t("update")
            warning = self.t("restart_warning")
            update.tooltip = warning if update_restarts else self.t("update_tip")
            self.query_one("#edit", Button).tooltip = (
                warning if update_restarts else self.t("edit_tip")
            )
            action = self.t("action_use_restart") if use_restarts else self.t("action_use")
            warning_hint = self.t("update_restart_hint") if update_restarts else ""
            self.query_one("#sub-hint", Static).update(
                self.t(
                    "update_hint", count=len(self.sub_names), action=action, warning=warning_hint
                )
            )
            disabled.update(
                start=not status["selected"],
                stop=not running,
                restart=not running,
                web=dashboard_root() is None or not (running or self.manager.web_gateway()),
            )
            gateway = self.manager.web_gateway()
            toggle = self.query_one("#web-toggle", Button)
            toggle.label = self.t("web_stop") if gateway else self.t("web_start")
            disabled["web-toggle"] = dashboard_root() is None
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
            button.disabled = unavailable or (self.busy and button.id not in self.READ_ONLY_BUTTONS)
        for widget in chain.from_iterable(
            screen.query("Input, Select") for screen in self.screen_stack
        ):
            widget.disabled = self.busy and any(
                isinstance(parent, Form) for parent in widget.ancestors
            )

    def show_error(self, error, *, operation=None):
        operation = operation or self.t("form_operation")
        message = error_message(error, self.language)
        self.last_error = message
        self.last_error_exception = error
        self.last_error_details = error_details(error, operation, self.language)
        self.query_one("#message", Static).update(message)
        self.query_one("#message").set_classes("error")
        if isinstance(self.screen, Form):
            self.screen.query_one("#form-error", Static).update(message)
            self.screen.query_one("#form-error").remove_class("progress")
        self.update_buttons()

    def subscription_progress(self, message):
        self.call_from_thread(self.show_progress, translated_message(self.language, message))

    def show_progress(self, message):
        if not self.busy:
            return
        self.query_one("#message", Static).update(message)
        if isinstance(self.screen, Form):
            self.screen.query_one("#form-error", Static).update(message)
            self.screen.query_one("#form-error").add_class("progress")

    def operate(
        self, label, operation, form=None, *, focus=None, success=None, on_success=None,
        locked=True,
    ):
        if self.busy:
            return
        form_focus = self.focused if form else None
        self.busy = True
        self.revision += 1
        self.last_error = None
        self.last_error_details = None
        self.last_error_exception = None
        self.query_one("#message").set_classes("progress")
        self.query_one("#message", Static).update(f"{label}…")
        self.update_buttons()
        self.run_worker(
            self.run_operation(
                label, operation, form, focus, success, form_focus, on_success, locked
            ),
            group="operation",
        )

    async def run_operation(
        self, label, operation, form, focus, success, form_focus, on_success=None, locked=True
    ):
        def execute():
            if locked:
                with self.manager.store.lock():
                    return operation()
            return operation()

        try:
            result = await asyncio.to_thread(execute)
            if isinstance(result, dict) and "delay_ms" in result:
                self.delay_results[result["name"]] = result["delay_ms"]
                message = self.t("delay_result", name=result["name"], delay=result["delay_ms"])
            else:
                message = success or self.t("operation_done", label=label)
            self.next_focus = focus
            if form:
                form.dismiss()
            if on_success:
                callback_message = on_success()
                if callback_message:
                    message = callback_message
            self.query_one("#message", Static).update(message)
            self.query_one("#message").set_classes("success")
        except Exception as error:
            if self.testing_node:
                self.delay_errors[self.testing_node] = error
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
            self.query_one("#message", Static).update(self.t("progress_done"))
        elif isinstance(self.screen, ModalScreen):
            self.screen.dismiss()
        else:
            self.exit()

    def action_language(self):
        if self.busy or isinstance(self.screen, ModalScreen):
            return
        self.show_dialog(
            Form(
                self.t("language_title"),
                [("language", self.t("language_field"), self.language_preference, False)],
                self.save_language,
                choices={
                    "language": [
                        (self.t("language_auto"), "auto"),
                        (self.t("language_zh"), "zh"),
                        (self.t("language_en"), "en"),
                    ]
                },
            )
        )

    def save_language(self, values, form):
        preference = values["language"]
        if preference not in LANGUAGE_OPTIONS:
            form.field_error("language", self.t("fill_field"))
            return

        def persist():
            state = self.manager.store.read()
            state["language"] = preference
            self.manager.store.save(state)

        self.operate(
            self.t("language_save"),
            persist,
            form,
            on_success=lambda: self.apply_language(preference),
        )

    def apply_language(self, preference):
        self.language_preference = preference
        self.language = resolve_language(preference)
        self.sub_title = self.t("app_subtitle")
        self.refresh_language_labels()
        self.update_binding_labels()
        if self.last_error_exception:
            self.show_error(self.last_error_exception, operation=self.t("form_operation"))
        if self.snapshot:
            self.apply_snapshot(self.snapshot)
        self.render_logs(force=True)
        self.update_buttons()
        return self.t("language_saved")

    def refresh_language_labels(self):
        for selector, key in {
            "#brand": "brand",
            "#edition": "edition",
            "#start": "start",
            "#stop": "stop",
            "#restart": "restart",
            "#settings": "settings",
            "#web-toggle": "web_start",
            "#web": "web",
            "#refresh": "refresh",
            "#add": "add",
            "#edit": "edit",
            "#remove": "remove",
            "#select-node": "select_node",
            "#test-node": "test_latency",
            "#error-details": "error_details",
            "#test-label": "target",
        }.items():
            widget = self.query_one(selector)
            if isinstance(widget, Button):
                widget.label = self.t(key)
            else:
                widget.update(self.t(key))
        for widget, key in zip(
            self.query(".metric-label"),
            ("kernel", "subscription_metric", "proxy_metric", "mode_metric"),
        ):
            widget.update(self.t(key))
        tabs = self.query_one(TabbedContent)
        for page, number, key in (
            ("subscriptions", 1, "subscriptions"),
            ("nodes", 2, "nodes"),
            ("logs", 3, "logs"),
        ):
            tabs.get_tab(page).label = f"{number} {self.t(key)}"
        self.query_one("#group", Select).prompt = self.t("group_prompt")
        self.query_one("#sub-empty", Static).update(self.t("sub_empty"))
        self.query_one("#node-empty", Static).update(self.t("node_empty"))
        self.query_one("#filter", Input).placeholder = self.t("node_search")
        self.query_one("#test-url", Input).placeholder = self.t("latency_url")
        self.query_one("#log-follow", Button).label = (
            self.t("pause") if self.log_following else self.t("resume")
        )
        self.query_one("#language", Button).tooltip = self.t(
            "language_button_auto"
            if self.language_preference == "auto"
            else "language_button",
            name=self.language_name(),
        )
        toggle = self.query_one("#web-toggle", Button)
        toggle.label = self.t("web_stop" if self.manager.web_gateway() else "web_start")
        self.query_one("#subs", StableTable).set_headings(
            ("", self.t("sub_col"), self.t("source_col"), self.t("updated_col"))
        )
        self.query_one("#node-table", StableTable).set_headings(
            ("", self.t("node_col"), self.t("type_col"), self.t("delay_col"))
        )
        self.localize_bindings(self.query_one("#log-scroll", LogScroll), {"tail": "follow"})
        self.query_one("#sub-hint", Static).update(self.t("sub_hint"))
        self.query_one("#log-hint", Static).update(self.t("log_hint"))
        if not self.last_error:
            self.query_one("#message", Static).update(self.t("keep_core"))

    def action_add(self):
        if self.busy or isinstance(self.screen, ModalScreen):
            return
        self.show_dialog(
            Form(
                self.t("add_dialog"),
                [
                    ("sub-name", self.t("sub_name"), "", False),
                    ("sub-source", self.t("sub_source"), "", False),
                ],
                lambda values, form: self.operate(
                    self.t("add_dialog"),
                    lambda: self.manager.put_sub(
                        values["sub-name"],
                        values["sub-source"],
                        create=True,
                        progress=self.subscription_progress,
                    ),
                    form,
                    focus=("#subs", values["sub-name"]),
                    success=self.t("added"),
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
                self.t("switch_sub"),
                lambda: self.manager.use(name),
                focus=("#start", None) if stopped else None,
                success=self.t("sub_used_start") if stopped else None,
            )

    def select_node(self):
        name, group = self.current_node(), self.group_name
        expected = self.snapshot["instance"] if self.snapshot else None
        if name and group and not self.query_one("#select-node", Button).disabled:
            self.operate(
                self.t("switch_node"),
                lambda: Controller(self.manager.engine, expected).select(group, name),
            )

    @on(Button.Pressed)
    def pressed(self, event):
        if event.button.disabled or (self.busy and event.button.id not in self.READ_ONLY_BUTTONS):
            return
        name = self.current_sub()
        button = event.button.id
        if button == "error-details":
            self.action_error_details()
        elif button == "log-follow":
            self.pause_logs() if self.log_following else self.resume_logs()
        elif button == "refresh":
            self.action_refresh()
        elif button == "language":
            self.action_language()
        elif button == "add":
            self.action_add()
        elif button == "start":
            self.operate(self.t("start"), self.manager.start)
        elif button == "stop":
            self.operate(self.t("stop"), self.manager.engine.stop)
        elif button == "restart":
            self.operate(self.t("restart"), lambda: self.manager.start(restart=True))
        elif button == "use":
            self.use_sub()
        elif button == "update" and name:
            self.operate(
                self.t("update"),
                lambda: self.manager.put_sub(name, progress=self.subscription_progress),
            )
        elif button == "edit" and name:
            self.run_worker(self.edit_source(name), group="edit-source", exclusive=True)
        elif button == "remove" and name:
            self.show_dialog(
                Form(
                    self.t("remove_confirm", name=name),
                    [],
                    lambda values, form: self.operate(
                        self.t("remove"), lambda: self.manager.remove(name), form
                    ),
                )
            )
        elif button == "settings" and self.snapshot:
            settings = self.snapshot["status"]["settings"]
            self.show_dialog(
                Form(
                    self.t("settings_title"),
                    [
                        ("proxy-port", self.t("proxy_port"), str(settings["proxy_port"]), False),
                        (
                            "controller-port",
                            self.t("controller_port"),
                            str(settings["controller_port"]),
                            False,
                        ),
                        ("mode", self.t("mode"), settings["mode"], False),
                        (
                            "host",
                            self.t("proxy_host"),
                            settings["host"],
                            False,
                        ),
                        (
                            "controller-host",
                            self.t("controller_host"),
                            settings["controller_host"],
                            False,
                        ),
                    ],
                    self.save_settings,
                    choices={
                        "mode": [
                            (self.t("mode_rule"), "rule"),
                            (self.t("mode_global"), "global"),
                            (self.t("mode_direct"), "direct"),
                        ]
                    },
                )
            )
        elif button == "web":
            self.run_worker(self.show_web(), group="web", exclusive=True)
        elif button == "web-toggle":
            if self.manager.web_gateway():
                self.operate(self.t("web_stop"), self.manager.stop_web, locked=False)
            else:
                self.operate(self.t("web_start"), self.manager.start_web, locked=False)
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
                self.t("test_latency"),
                lambda: Controller(self.manager.engine, expected).test(node, url),
            )

    async def edit_source(self, name):
        try:
            state = await asyncio.to_thread(self.manager.store.read)
            if not self.is_running or self.busy or isinstance(self.screen, ModalScreen):
                return
            if name not in state["subs"]:
                raise AppError("not_found", self.t("removed_refresh"), 3)
            status = self.snapshot["status"]
            restarting = status["running"] and name in (
                status["selected"],
                status["running_subscription"],
            )
            self.show_dialog(
                Form(
                    self.t("edit_source_title", name=name)
                    + (self.t("restart_config") if restarting else ""),
                    [
                        (
                            "sub-source",
                            self.t("sub_source"),
                            state["subs"][name]["source"],
                            False,
                        )
                    ],
                    lambda values, form: self.operate(
                        self.t("edit"),
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
                self.show_error(error, operation=self.t("source"))

    async def show_web(self):
        try:
            info = await asyncio.to_thread(self.manager.web)
            if self.is_running and not isinstance(self.screen, ModalScreen):
                listen = f"{info['listen_host']}:{info['port']}"
                hint = (
                    self.t("web_local_hint")
                    if info.get("hint")
                    == "此入口提供订阅管理与节点面板；远程访问时换成服务器 IP。"
                    else self.t("web_hint")
                )
                self.show_dialog(
                    Details(
                        self.t("web_title"),
                        f"{info['url']}\n\n{self.label('listen', listen)}\n"
                        f"{self.label('login_secret', info['secret'])}\n\n"
                        f"{hint}\n"
                        f"{self.t('web_remote')}",
                    )
                )
        except Exception as error:
            if self.is_running:
                self.show_error(error)

    @on(DataTable.RowHighlighted)
    def row_highlighted(self):
        self.update_buttons()

    def save_settings(self, values, form):
        for key in ("host", "controller-host"):
            if not valid_host(values[key]):
                form.field_error(key, self.t("ipv4_error"))
                return
        settings = {
            "mode": values["mode"],
            "host": values["host"],
            "controller_host": values["controller-host"],
        }
        for key in ("proxy-port", "controller-port"):
            try:
                value = int(values[key])
                if not 1 <= value <= 65535:
                    raise ValueError
            except ValueError:
                form.field_error(key, self.t("port_error"))
                return
            settings[key.replace("-", "_")] = value
        if settings["proxy_port"] == settings["controller_port"]:
            form.field_error("controller-port", self.t("ports_different"))
            return
        self.operate(self.t("settings"), lambda: self.manager.configure(settings), form)
