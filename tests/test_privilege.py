import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from mihomo_py import privilege
from mihomo_py.cli import cli
from mihomo_py.errors import AppError


@pytest.mark.parametrize('args', [
    ['--help'], ['--version'], ['tun', '--help'], ['tun', 'enable', '--help'],
    ['tun', 'enable', '--dry-run'], ['core', 'restart', '--dry-run'],
    ['tun', 'enable', '--unknown-option'],
])
def test_help_plans_and_invalid_arguments_never_authorize(monkeypatch, tmp_path, args):
    execute = Mock(side_effect=AssertionError('unexpected sudo'))
    monkeypatch.setattr(privilege.os, 'execv', execute)
    result = CliRunner().invoke(cli, ['--data-dir', str(tmp_path / 'home'), '--sudo', *args])
    assert result.exit_code in (0, 2, 10), result.exception
    execute.assert_not_called()
    assert not (tmp_path / 'home').exists()


def test_sudo_is_opt_in(monkeypatch, tmp_path):
    execute = Mock(side_effect=AssertionError('unexpected sudo'))
    monkeypatch.setattr(privilege.os, 'execv', execute)
    result = CliRunner().invoke(cli, ['--data-dir', str(tmp_path), 'tun', 'status'])
    assert result.exit_code == 0, result.exception
    execute.assert_not_called()


def test_reexec_preserves_interpreter_directory_environment_and_child_arguments(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(privilege, 'ready', lambda: False)
    monkeypatch.delenv(privilege.REEXEC, raising=False)
    monkeypatch.setattr(privilege, 'system_tool', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'venv/bin/python'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home with spaces'))
    monkeypatch.setenv('PATH', '/custom/env/bin:/usr/bin')
    monkeypatch.setenv('MIHOMO_PY_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('MIHOMO_PY_BINARY', '/core with spaces/mihomo')
    monkeypatch.setenv('SECRET_TOKEN', 'never-copy-to-argv')
    calls = []

    def execute(path, arguments):
        calls.append((path, arguments))
        raise SystemExit(0)

    monkeypatch.setattr(privilege.os, 'execv', execute)
    original = ['--sudo', 'proxy', 'run', '--', 'echo', '--help', '--dry-run', '$(id)', '--sudo']
    result = CliRunner().invoke(cli, original)
    assert result.exit_code == 0, result.exception
    path, args = calls[0]
    assert path == '/usr/bin/sudo' and args[:3] == [path, '-E', '-n']
    assert args[-len(original):] == original
    assert args[args.index('-c') + 1] == 'cd "$1" && shift && exec "$@"'
    assert os.getcwd() in args
    assert str(tmp_path / 'venv/bin/python') in args
    assert str(tmp_path / 'state') in args
    assert '/core with spaces/mihomo' in args
    assert f'HOME={tmp_path / "home with spaces"}' in args
    assert 'PATH=/custom/env/bin:/usr/bin' in args
    assert 'never-copy-to-argv' not in ' '.join(args)
    assert '--caps=cap_net_admin+eip' in args
    assert '--addamb=cap_net_admin' in args


def test_failed_grant_does_not_recurse(monkeypatch, tmp_path):
    monkeypatch.setattr(privilege, 'ready', lambda: False)
    monkeypatch.setenv(privilege.REEXEC, '1')
    result = CliRunner().invoke(cli, ['--data-dir', str(tmp_path), '--sudo', 'tun', 'status'])
    assert isinstance(result.exception, AppError)
    assert result.exception.kind == 'sudo_permission_denied'


@pytest.mark.parametrize('effective,ambient,uid,expected', [
    (False, '1000', 1000, False), (True, '0', 1000, False),
    (True, '1000', 1000, True), (True, '0', 0, True),
])
def test_ready_requires_inheritable_grant(monkeypatch, effective, ambient, uid, expected):
    monkeypatch.setattr(privilege.tun, 'permission', lambda: {'cap_net_admin': effective})
    monkeypatch.setattr(privilege.os, 'geteuid', lambda: uid)
    monkeypatch.setattr(Path, 'read_text', lambda self: f'CapAmb:\t{ambient}\n')
    assert privilege.ready() is expected
