import json
import os
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mihomo_py import proxy
from mihomo_py.errors import AppError
from mihomo_py.manager import Manager


def test_proxy_env_read_only_and_formats(command, tmp_path):
    value = json.loads(command('proxy', 'env').stdout)
    assert not value['running']
    assert value['variables']['HTTP_PROXY'] == 'http://127.0.0.1:7897'
    assert value['variables']['all_proxy'] == 'socks5h://127.0.0.1:7897'
    assert len(value['variables']) == 8
    assert not (tmp_path / 'home').exists()
    assert json.loads(command('proxy', 'env', '--format', 'json').stdout) == value
    for shell in ('bash', 'zsh', 'posix', 'fish'):
        output = command('proxy', 'env', '--shell', shell).stdout
        assert ('set -gx' if shell == 'fish' else 'export') in output
        output = command('proxy', 'env', '--unset', '--shell', shell).stdout
        assert ('set -e' if shell == 'fish' else 'unset') in output
        assert 'http://' not in output


@pytest.mark.parametrize('shell', ['bash', 'zsh', 'fish', 'posix'])
def test_shell_quoting_round_trip_without_execution(shell, tmp_path):
    executable = shutil.which('sh' if shell == 'posix' else shell)
    if not executable:
        pytest.skip(f'{shell} not installed')
    marker = tmp_path / 'injected'
    value = f"localhost,'quoted',\\,$(touch {marker}),`touch {marker}`\nnext"
    script = proxy.shell_statements({'NO_PROXY': value}, shell)
    script += '\n' + shlex.quote(sys.executable) + ' -c '
    script += shlex.quote('import os; print(repr(os.environ["NO_PROXY"]))')
    result = subprocess.run([executable, '-c', script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == repr(value)
    assert not marker.exists()


def test_proxy_env_uses_live_address_and_saved_no_proxy(tmp_path, monkeypatch):
    manager = Manager(tmp_path / 'state')
    manager.configure({'host': '0.0.0.0', 'no_proxy': 'example.test,10.0.0.1'})
    assert proxy.environment(manager)['host'] == '127.0.0.1'
    settings = {**manager.store.read()['settings'], 'proxy_port': 12345}
    monkeypatch.setattr(manager.engine, 'running', lambda: {'settings': settings})
    value = proxy.environment(manager)
    assert value['port'] == 12345 and value['running']
    assert value['variables']['NO_PROXY'] == 'example.test,10.0.0.1'
    assert proxy.environment(manager, '')['variables']['no_proxy'] == ''


def test_proxy_run_stopped_structured_error(command):
    result = command('proxy', 'run', '--', sys.executable, '-c', 'raise Exception()', expected=3)
    assert json.loads(result.stderr)['error'] == 'core_stopped'


def test_run_argv_environment_and_inherited_stdio(monkeypatch):
    manager = SimpleNamespace(engine=SimpleNamespace(running=lambda: {}, healthy=lambda r: True))
    monkeypatch.setattr(manager.engine, 'running', lambda: {'pid': 123})
    monkeypatch.setattr(proxy, 'environment', lambda *a: {'variables': {'HTTP_PROXY': 'new'}})
    monkeypatch.setenv('HtTp_PrOxY', 'old')
    monkeypatch.setenv('KEEP_ME', 'yes')
    calls = []

    class Child:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))

        def wait(self):
            return 17

    monkeypatch.setattr(proxy.subprocess, 'Popen', Child)
    assert proxy.run(manager, ['echo', '$(false)', '--flag']) == 17
    argv, options = calls[0]
    assert argv == ['echo', '$(false)', '--flag']
    assert set(options) == {'env'}  # stdio inherited; shell never used.
    assert options['env']['KEEP_ME'] == 'yes'
    assert options['env']['HTTP_PROXY'] == 'new'
    assert 'HtTp_PrOxY' not in options['env']
    monkeypatch.setattr(manager.engine, 'healthy', lambda r: False)
    with pytest.raises(AppError) as error:
        proxy.run(manager, ['false'])
    assert error.value.code == 3


@pytest.mark.integration
def test_real_proxy_run_stdio_exit_code_and_child_environment(real_core, source, command):
    real_core.put_sub('a', str(source), create=True)
    real_core.use('a')
    real_core.start()
    script = (
        'import sys,os,json; print(json.dumps({"input":sys.stdin.read(),'
        '"proxy":os.environ["HTTP_PROXY"],"no_proxy":os.environ["no_proxy"]}));'
        'print("child stderr",file=sys.stderr);sys.exit(23)'
    )
    before = dict(os.environ)
    result = command('proxy', 'run', '--no-proxy', 'custom.test', '--',
                     sys.executable, '-c', script, input='input data', expected=23)
    value = json.loads(result.stdout)
    assert value['input'] == 'input data'
    assert value['proxy'].endswith(':' + str(real_core.status()['settings']['proxy_port']))
    assert value['no_proxy'] == 'custom.test'
    assert result.stderr == 'child stderr\n'
    assert dict(os.environ) == before
