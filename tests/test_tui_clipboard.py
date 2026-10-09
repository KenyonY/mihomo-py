import asyncio
import threading

import pytest
from rich.cells import cell_len
from textual import events
from textual.widgets import Input, Static

from mihomo_py import clipboard
from mihomo_py.manager import Manager
from mihomo_py.tui import Details, Form, MihomoApp

pytestmark = pytest.mark.asyncio


async def settled(app, pilot):
    app.animation_level = "none"
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 20)
    await pilot.pause()


async def drag(pilot, app, start, end):
    moves = [
        (events.MouseMove, (
            start[0] + round((end[0] - start[0]) * step / 6),
            start[1] + round((end[1] - start[1]) * step / 6),
        ))
        for step in range(1, 7)
    ]
    for event_type, (x, y) in [(events.MouseDown, start), *moves, (events.MouseUp, end)]:
        app.screen._forward_event(
            event_type(app.screen, x, y, 0, 0, 1, False, False, False, screen_x=x, screen_y=y)
        )
        await pilot.pause()


@pytest.mark.parametrize("key", ["ctrl+c", "ctrl+shift+c"])
@pytest.mark.parametrize("context", ["main", "form", "details", "busy"])
async def test_copy_without_selection_never_exits(tmp_path, monkeypatch, key, context):
    app = MihomoApp(Manager(tmp_path / "home"))
    monkeypatch.setattr(clipboard, "copy_external", lambda _: pytest.fail("Nothing to copy"))
    gate = threading.Event()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        if context == "form":
            await pilot.press("ctrl+a")
        elif context == "details":
            await pilot.press("question_mark")
        elif context == "busy":
            app.operate("慢操作", lambda: gate.wait(20))
            await pilot.pause()
        screen = app.screen
        app._clipboard = "previous"
        try:
            await pilot.press(key)
            assert app.is_running and app.screen is screen
            assert app.clipboard == "previous"
            assert "没有选中内容" in list(app._notifications)[-1].message
        finally:
            gate.set()
        await settled(app, pilot)


@pytest.mark.parametrize("key", ["ctrl+c", "ctrl+shift+c"])
@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_dragged_unicode_selection_copies_and_keeps_dialog(tmp_path, monkeypatch, key, size):
    app = MihomoApp(Manager(tmp_path / "home"))
    copied = []
    monkeypatch.setattr(clipboard, "copy_external", copied.append)
    async with app.run_test(size=size) as pilot:
        await settled(app, pilot)
        app.show_dialog(Details("文本详情", "before 中文字符 after"))
        await pilot.pause()
        dialog = app.screen
        text = dialog.query_one("#detail-scroll").query_one(Static)
        x, y = text.content_region.x, text.content_region.y
        await drag(pilot, app, (x + cell_len("before "), y),
                   (x + cell_len("before 中文"), y))
        assert app.screen.get_selected_text() == "中文字"
        selection_bg = app.screen.get_component_rich_style("screen--selection").bgcolor
        highlighted = "".join(
            segment.text for segment in text.render_line(0)
            if segment.style and segment.style.bgcolor == selection_bg
        )
        assert highlighted == "中文字"
        assert not copied  # Merely selecting must not change the clipboard.
        await pilot.press(key)
        assert copied == ["中文字"]
        assert app.clipboard == "中文字"
        assert app.screen is dialog and app.is_running
        assert app.screen.get_selected_text() is None
        await pilot.press("escape")
        assert app.focused.id == "add"


@pytest.mark.parametrize("key", ["ctrl+c", "ctrl+shift+c"])
@pytest.mark.parametrize("modal", [False, True])
async def test_input_selection_copies_with_both_shortcuts(tmp_path, monkeypatch, key, modal):
    app = MihomoApp(Manager(tmp_path / "home"))
    copied = []
    monkeypatch.setattr(clipboard, "copy_external", copied.append)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        if modal:
            await pilot.press("ctrl+a")
            field = app.screen.query_one("#sub-name", Input)
            assert isinstance(app.screen, Form)
        else:
            await pilot.press("2", "slash")
            field = app.query_one("#filter", Input)
        field.value = "abc中文"
        field.action_home()
        for _ in range(3):
            field.action_cursor_right(select=True)
        await pilot.pause()
        assert field.selected_text == "abc"
        screen = app.screen
        await pilot.press(key)
        assert copied == ["abc"] and app.clipboard == "abc"
        assert app.screen is screen and app.is_running
        assert field.value == "abc中文" and field.selected_text == "abc"


async def test_wrapped_text_selection_and_scrolled_logs(tmp_path, monkeypatch):
    app = MihomoApp(Manager(tmp_path / "home"))
    copied = []
    monkeypatch.setattr(clipboard, "copy_external", copied.append)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        content = "abcdefghij" * 20
        app.show_dialog(Details("长文本", content))
        await pilot.pause()
        text = app.screen.query_one("#detail-scroll").query_one(Static)
        x, y = text.content_region.x, text.content_region.y
        await drag(pilot, app, (x + 2, y), (x + 5, y + 1))
        expected = content[2:text.content_size.width + 6]
        assert app.screen.get_selected_text() == expected
        await pilot.press("ctrl+c", "escape", "3")
        assert copied == [expected]
        logs = "\n".join(f"line{i:03d} abcdefghij" for i in range(60))
        app.log_following = False
        app.query_one("#log-text", Static).update(logs)
        scroll = app.query_one("#log-scroll")
        scroll.scroll_to(y=20, animate=False)
        await pilot.pause()
        x, y = scroll.content_region.x, scroll.content_region.y
        await drag(pilot, app, (x, y), (x + 6, y))
        assert app.screen.get_selected_text() == "line020"
        await pilot.press("ctrl+shift+c")
        assert copied[-1] == "line020"
