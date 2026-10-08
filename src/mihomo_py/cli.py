import json
import os
import sys
import time
from collections import deque
from pathlib import Path

import click

from . import __version__
from .config import check_name, normalize_source, source_label
from .controller import DEFAULT_TEST_URL, Controller
from .errors import AppError
from .manager import Manager


def emit(data, fmt):
    if fmt == "json":
        click.echo(json.dumps(data, ensure_ascii=False))
    elif isinstance(data, list):
        if not data:
            click.echo("暂无记录。")
        else:
            columns = list(data[0])
            rows = [[str(row.get(key, "")) for key in columns] for row in data]
            widths = [
                max(len(key), *(len(row[i]) for row in rows)) for i, key in enumerate(columns)
            ]
            for row in [columns, *rows]:
                click.echo("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))
    else:
        for key, value in data.items():
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            click.echo(f"{key}: {value}")


def default_directory():
    configured = os.environ.get("XDG_CONFIG_HOME")
    base = Path(configured) if configured else Path.home() / ".config"
    if not base.is_absolute():
        base = Path.home() / ".config"
    return str(base / "mihomo-py")


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--data-dir",
    envvar="MIHOMO_PY_HOME",
    default=default_directory,
    show_default=True,
    type=click.Path(file_okay=False),
    help="独立的状态和内核运行目录。",
)
@click.option(
    "--core-binary",
    envvar="MIHOMO_PY_BINARY",
    default=None,
    help="指定 mihomo 可执行文件；默认使用安装包内的内核。",
)
@click.option(
    "--timeout",
    type=click.IntRange(1, 120),
    default=20,
    show_default=True,
    help="内核校验及启动就绪超时（秒）。",
)
@click.option(
    "--format", "fmt", type=click.Choice(["json", "table"]), help="终端默认 table，管道默认 json。"
)
@click.option("--json", "json_output", is_flag=True, help="等同于 --format json。")
@click.version_option(__version__)
@click.pass_context
def cli(ctx, data_dir, core_binary, timeout, fmt, json_output):
    """管理服务器上的 mihomo 订阅、节点与进程；终端中直接运行进入 TUI。

    \b
    示例：mihomo-py sub add work ./config.yaml
          mihomo-py sub use work
          mihomo-py core start

    \b
    退出码：0 成功，1 运行错误，2 参数/配置错误，3 未找到，
    4 权限错误，5 冲突，10 dry-run 计划（未执行）。
    全局选项放在子命令之前。
    """
    ctx.obj = {
        "manager": Manager(data_dir, core_binary, timeout),
        "format": "json" if json_output else fmt or ("table" if sys.stdout.isatty() else "json"),
    }
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty() and sys.stdout.isatty() and not json_output and fmt is None:
            launch_tui(ctx.obj["manager"])
        else:
            emit(ctx.obj["manager"].status(), ctx.obj["format"])


def launch_tui(manager):
    if not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
        raise AppError("terminal_required", "TUI 需要交互式终端。请使用子命令或 core status。", 2)
    from .tui import MihomoApp

    MihomoApp(manager).run()


@cli.command("tui")
@click.pass_context
def tui_command(ctx):
    """打开交互终端界面；退出界面不停止内核。"""
    launch_tui(ctx.obj["manager"])


@cli.group()
def node():
    """查看代理组、切换节点和测量延迟（内核须已运行）。"""


@node.command("list")
@click.option("--group", help="显示指定组的节点；省略时列出代理组。")
@click.pass_context
def node_list(ctx, group):
    controller = Controller(ctx.obj["manager"].engine)
    proxies = controller.proxies()
    emit(
        controller.members(proxies, group) if group else controller.groups(proxies),
        ctx.obj["format"],
    )


@node.command("use")
@click.argument("name")
@click.option("--group", required=True, help="手动代理组的完整名称。")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def node_use(ctx, name, group, dry_run):
    """按完整名称切换组内节点，无需重启内核。"""
    mutate(
        ctx,
        "node.use",
        {"group": group, "name": name},
        dry_run,
        lambda: Controller(ctx.obj["manager"].engine).select(group, name),
    )


@node.command("test")
@click.argument("name")
@click.option("--url", default=DEFAULT_TEST_URL, show_default=True)
@click.option("--timeout-ms", type=click.IntRange(1, 30000), default=5000, show_default=True)
@click.option("--dry-run", is_flag=True, help="仅输出测速计划，不发送测试请求。")
@click.pass_context
def node_test(ctx, name, url, timeout_ms, dry_run):
    """测量一个节点到指定 URL 的延迟，不是带宽测速。"""
    mutate(
        ctx,
        "node.test",
        {"name": name, "timeout_ms": timeout_ms},
        dry_run,
        lambda: Controller(ctx.obj["manager"].engine).test(name, url, timeout_ms),
    )


def mutate(ctx, action, target, dry_run, operation):
    if dry_run:
        emit({"action": action, "target": target, "executed": False}, "json")
        ctx.exit(10)
    with ctx.obj["manager"].store.lock():
        result = operation()
    emit(result, ctx.obj["format"])


def read_source(source):
    if source == "-":
        if sys.stdin.isatty():
            raise AppError("missing_source", "请通过管道从 stdin 提供订阅地址或文件路径。", 2)
        source = sys.stdin.readline().strip()
    if not source:
        raise AppError("missing_source", "订阅来源不能为空。", 2)
    return normalize_source(source)


@cli.group()
def sub():
    """添加、更新、切换和删除订阅（Clash/Mihomo YAML）。"""


@sub.command("list")
@click.pass_context
def sub_list(ctx):
    """列出订阅；远程 URL 隐藏路径和令牌。"""
    emit(ctx.obj["manager"].list_subs(), ctx.obj["format"])


@sub.command("add")
@click.argument("name")
@click.argument("source")
@click.option("--dry-run", is_flag=True, help="仅输出计划，不下载、不写入。")
@click.pass_context
def sub_add(ctx, name, source, dry_run):
    """下载并校验新订阅；SOURCE 为 URL、文件路径或 -（从 stdin 读地址）。"""
    check_name(name)
    source = read_source(source)
    mutate(
        ctx,
        "sub.add",
        {"name": name, "source": source_label(source)},
        dry_run,
        lambda: ctx.obj["manager"].put_sub(name, source, create=True),
    )


@sub.command("set")
@click.argument("name")
@click.argument("source")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def sub_set(ctx, name, source, dry_run):
    """更换来源并下载校验；失败时保留旧来源和缓存。"""
    check_name(name)
    source = read_source(source)
    mutate(
        ctx,
        "sub.set",
        {"name": name, "source": source_label(source)},
        dry_run,
        lambda: ctx.obj["manager"].put_sub(name, source),
    )


@sub.command("update")
@click.argument("name", required=False)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def sub_update(ctx, name, dry_run):
    """重新读取来源并校验；省略 NAME 时更新当前订阅。"""
    manager = ctx.obj["manager"]
    name, _ = manager.subscription(manager.store.read(), name)
    mutate(ctx, "sub.update", {"name": name}, dry_run, lambda: manager.put_sub(name))


@sub.command("use")
@click.argument("name")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def sub_use(ctx, name, dry_run):
    """选择缓存订阅；运行中则重启生效，停止时只保存选择。"""
    mutate(ctx, "sub.use", {"name": name}, dry_run, lambda: ctx.obj["manager"].use(name))


@sub.command("remove")
@click.argument("name")
@click.option("--yes", is_flag=True, help="确认删除订阅及其缓存原文。")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def sub_remove(ctx, name, yes, dry_run):
    """删除订阅；运行中的当前订阅须先停止或切换。"""
    if not dry_run and not yes:
        if not sys.stdin.isatty():
            raise AppError("confirmation_required", "删除订阅需要 --yes。", 2)
        click.confirm(f"删除订阅 {name}？", abort=True, err=True)
    mutate(ctx, "sub.remove", {"name": name}, dry_run, lambda: ctx.obj["manager"].remove(name))


@cli.group()
def config():
    """管理独立于订阅的本机设置。"""


@config.command("show")
@click.pass_context
def config_show(ctx):
    """查看端口和路由模式。"""
    emit(ctx.obj["manager"].store.read()["settings"], ctx.obj["format"])


@config.command("set")
@click.option("--proxy-port", type=click.IntRange(1, 65535))
@click.option("--controller-port", type=click.IntRange(1, 65535))
@click.option("--mode", type=click.Choice(["rule", "global", "direct"]))
@click.option("--dry-run", is_flag=True)
@click.pass_context
def config_set(ctx, proxy_port, controller_port, mode, dry_run):
    """保存本机设置；内核运行时重启应用，失败则恢复旧配置。"""
    values = {
        key: value
        for key, value in {
            "proxy_port": proxy_port,
            "controller_port": controller_port,
            "mode": mode,
        }.items()
        if value is not None
    }
    if not values:
        raise click.UsageError("至少指定一项设置。")
    mutate(ctx, "config.set", values, dry_run, lambda: ctx.obj["manager"].configure(values))


@cli.group()
def core():
    """管理本客户端启动的 mihomo 实例。"""


@core.command("start")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def core_start(ctx, dry_run):
    """使用当前订阅缓存后台启动；已健康运行时不重复启动。"""
    mutate(ctx, "core.start", {}, dry_run, lambda: ctx.obj["manager"].start())


@core.command("restart")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def core_restart(ctx, dry_run):
    """校验后重启当前实例。"""
    mutate(ctx, "core.restart", {}, dry_run, lambda: ctx.obj["manager"].start(restart=True))


@core.command("stop")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def core_stop(ctx, dry_run):
    """停止本实例；不会按进程名终止其他 mihomo。"""
    mutate(ctx, "core.stop", {}, dry_run, lambda: {"stopped": ctx.obj["manager"].engine.stop()})


@core.command("status")
@click.pass_context
def core_status(ctx):
    """查看进程身份、健康状态、当前订阅和端口。"""
    emit(ctx.obj["manager"].status(), ctx.obj["format"])


@core.command("logs")
@click.option("--lines", type=click.IntRange(1, 10000), default=100, show_default=True)
@click.option("--follow", is_flag=True, help="持续跟踪；JSON 模式逐行输出 NDJSON。")
@click.pass_context
def core_logs(ctx, lines, follow):
    """查看内核日志（可能含订阅内部地址）。Ctrl-C 结束跟踪。"""
    path = ctx.obj["manager"].engine.log_path
    if not path.exists():
        raise AppError("not_found", "尚无内核日志。", 3)
    with path.open(errors="replace") as stream:
        recent = list(deque(stream, maxlen=lines))
        if not follow:
            if ctx.obj["format"] == "json":
                emit({"lines": [line.rstrip("\n") for line in recent]}, "json")
            else:
                click.echo("".join(recent), nl=False)
            return
        while True:
            for line in recent:
                if ctx.obj["format"] == "json":
                    emit({"line": line.rstrip("\n")}, "json")
                else:
                    click.echo(line, nl=False)
            recent = stream.readlines()
            if not recent:
                time.sleep(0.2)


def main():
    error = None
    try:
        result = cli.main(standalone_mode=False)
        if isinstance(result, int):
            sys.exit(result)
    except AppError as exc:
        error = exc
    except click.ClickException as exc:
        error = AppError("usage_error", exc.format_message(), exc.exit_code)
    except (click.Abort, KeyboardInterrupt):
        error = AppError("interrupted", "操作已取消。", 130)
    except PermissionError:
        error = AppError("permission_denied", "无权限读取或修改所需文件、端口或进程。", 4)
    except OSError as exc:
        # Do not echo raw network exceptions, which can contain a subscription URL.
        error = AppError("io_error", f"本地 IO 操作失败（errno={exc.errno}）。")
    if error:
        # Errors remain structured even when argument parsing failed before context creation.
        click.echo(json.dumps(error.as_dict(), ensure_ascii=False), err=True)
        sys.exit(error.code)
