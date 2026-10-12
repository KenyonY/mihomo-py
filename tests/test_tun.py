import copy
import ipaddress
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from mihomo_py import pidfd, tun
from mihomo_py.config import parse, render
from mihomo_py.engine import Engine
from mihomo_py.errors import AppError
from mihomo_py.manager import Manager
from mihomo_py.store import DEFAULT_SETTINGS, Store


def settings(enabled=True, **values):
    result = copy.deepcopy(DEFAULT_SETTINGS)
    result['tun'].update(enabled=enabled, **values)
    return result


def test_old_state_defaults_do_not_mutate_defaults(tmp_path):
    store = Store(tmp_path)
    state = store.read()
    state['settings'].pop('tun')
    state['settings'].pop('no_proxy')
    store.save(state)
    loaded = store.read()
    assert loaded['settings']['tun'] == tun.DEFAULT_TUN
    loaded['settings']['tun']['enabled'] = True
    assert not Store(tmp_path / 'new').read()['settings']['tun']['enabled']
    assert 'tun' not in json.loads((tmp_path / 'state.json').read_text())['settings']


@pytest.mark.parametrize('values,kind', [
    ({'device': 'a' * 16}, 'tun_invalid_device'),
    ({'device': '../bad'}, 'tun_invalid_device'),
    ({'device': '.'}, 'tun_invalid_device'),
    ({'stack': 'wrong'}, 'tun_invalid_stack'),
    ({'exclude_address': ['bad']}, 'tun_invalid_address'),
    ({'exclude_address': [123]}, 'tun_invalid_address'),
    ({'enabled': 'true'}, 'invalid_settings'),
])
def test_tun_validation(values, kind):
    with pytest.raises(AppError) as error:
        tun.validate(settings(**values)['tun'])
    assert error.value.kind == kind
    assert error.value.code == 2 and error.value.suggestion


def test_tun_render_controls_routes_dns_and_exclusions():
    source = '''proxies: []
iptables: {enable: true}
listeners: [{type: tun}]
tunnels: [{network: tcp}]
tun: {enable: true, device: hostile, auto-redirect: true, route-address: [0.0.0.0/0]}
dns:
  enable: false
  listen: 0.0.0.0:53
  nameserver: [1.1.1.1]
  enhanced-mode: fake-ip
  fallback: [8.8.8.8]
'''
    cfg = yaml.safe_load(render(source, settings(exclude_address=['10.0.0.0/8']), 's'))
    assert cfg['tun'] == tun.configuration(settings(exclude_address=['10.0.0.0/8']))
    assert cfg['tun']['auto-redirect'] is False
    assert cfg['dns'] == {'enable': True, 'nameserver': ['1.1.1.1'],
                          'enhanced-mode': 'fake-ip', 'fallback': ['8.8.8.8']}
    assert not any(key in cfg for key in ('iptables', 'listeners', 'tunnels'))
    assert yaml.safe_load(render(source, settings(False), 's'))['tun'] == {'enable': False}
    assert yaml.safe_load(render('proxies: []', settings(), 's'))['dns']['enable']


@pytest.mark.parametrize('sniffer', [None, {'enable': False},
                                   {'enable': True, 'skip-domain': ['+.internal']}])
def test_tun_sniffer_defaults_preserve_explicit_subscription_choices(sniffer):
    source = {'proxies': []}
    if sniffer is not None:
        source['sniffer'] = sniffer
    rendered = yaml.safe_load(render(yaml.safe_dump(source), settings(), 's'))
    if sniffer is None:
        assert rendered['sniffer']['enable']
        assert rendered['sniffer']['parse-pure-ip']
        assert rendered['sniffer']['override-destination']
        assert set(rendered['sniffer']['sniff']) == {'HTTP', 'TLS', 'QUIC'}
        assert 'sniffer' not in yaml.safe_load(render(yaml.safe_dump(source), settings(False), 's'))
    else:
        assert rendered['sniffer'] == sniffer


@pytest.mark.parametrize('args', [
    ['tun', 'enable', '--dry-run'], ['tun', 'disable', '--dry-run'],
    ['tun', 'enable', '--device', 'test0', '--exclude-address', '10.2.3.4', '--dry-run'],
])
def test_dry_run_is_read_only(command, tmp_path, args):
    value = json.loads(command(*args, expected=10).stdout)
    assert not value['executed']
    assert not (tmp_path / 'home').exists()


def test_cli_confirmation_and_status(command, tmp_path):
    result = command('tun', 'enable', expected=2)
    assert json.loads(result.stderr)['error'] == 'confirmation_required'
    value = json.loads(command('tun', 'status').stdout)
    assert not value['configured'] and not value['active']
    assert value['device'] == 'mihomo' and value['stack'] == 'mips'
    assert not (tmp_path / 'home').exists()
    result = command('tun', 'enable', '--device', '../bad', '--dry-run', expected=2)
    assert json.loads(result.stderr)['error'] == 'tun_invalid_device'


def test_permission_platform_and_route_conflicts(monkeypatch):
    value = settings()['tun']
    monkeypatch.setattr(sys, 'platform', 'other')
    with pytest.raises(AppError) as error:
        tun.preflight(value)
    assert error.value.kind == 'tun_unsupported_platform'
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(tun, 'permission', lambda: {'root': True, 'cap_net_admin': False,
                                                 'tun_access': True})
    with pytest.raises(AppError) as error:
        tun.preflight(value)
    assert error.value.code == 4  # restricted root is not sufficient.
    monkeypatch.setattr(tun, 'permission', lambda: {'root': False, 'cap_net_admin': True,
                                                 'tun_access': True})
    net = {'links': [], 'routes': [], 'rules': []}
    monkeypatch.setattr(tun, 'network', lambda: net)
    net['links'] = [{'ifname': 'mihomo'}]
    with pytest.raises(AppError) as error:
        tun.preflight(value)
    assert error.value.kind == 'tun_device_conflict'
    net['links'] = []
    for rule in ({'priority': 9005}, {'priority': 200, 'table': '2022'}):
        net['rules'] = [rule]
        with pytest.raises(AppError) as error:
            tun.preflight(value)
        assert error.value.kind == 'tun_route_conflict' and error.value.code == 5

    recorded = {'family': '-4', 'priority': 9002, 'table': '2022'}
    net['rules'] = [copy.deepcopy(recorded), copy.deepcopy(recorded)]
    monkeypatch.setattr(tun, 'owns_device', lambda record: True)
    record = {
        'pid': 123,
        'settings': settings(),
        'tun_resources': {'routes': [], 'rules': [recorded]},
    }
    with pytest.raises(AppError) as error:
        tun.preflight(value, record)
    assert error.value.kind == 'tun_route_conflict'


def test_device_alone_is_not_active_and_cleanup_is_checked(monkeypatch):
    record = {'pid': 123, 'settings': settings()}
    net = {'links': [{'ifname': 'mihomo'}], 'routes': [], 'rules': []}
    monkeypatch.setattr(tun, 'network', lambda: net)
    monkeypatch.setattr(tun, 'owns_device', lambda r: True)
    assert not tun.observation(record)['active']
    with pytest.raises(AppError) as error:
        tun.check_cleanup(record)
    assert error.value.kind == 'tun_cleanup_failed'
    net['links'] = []
    net['rules'] = [{'table': 2022}]
    with pytest.raises(AppError):
        tun.check_cleanup(record)
    net['rules'] = []
    tun.check_cleanup(record)


def test_cleanup_removes_only_recorded_residual_resources(monkeypatch):
    net = {
        'links': [],
        'routes': [
            {
                'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
                'gateway': '198.18.0.2', 'flags': [],
            },
            {
                'family': '-6', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
                'gateway': 'fdfe:dcba:9876::2', 'metric': 1024, 'pref': 'medium', 'flags': [],
            },
        ],
        'rules': [
            {'family': '-4', 'priority': 9002, 'src': 'all', 'table': '2022'},
            {'family': '-6', 'priority': 9010, 'src': 'all', 'nop': None},
        ],
    }
    record = {
        'pid': 123,
        'settings': settings(),
        'tun_resources': copy.deepcopy(tun.resources(net)),
    }
    # The kernel annotates rules that reference the now-removed TUN interface.
    net['rules'][0]['iif_detached'] = None
    calls = []
    monkeypatch.setattr(tun, 'network', lambda: copy.deepcopy(net))
    monkeypatch.setattr(tun, 'permission', lambda: {'cap_net_admin': True})
    monkeypatch.setattr(tun, 'ip_tool', lambda: '/usr/sbin/ip')

    def run(command, **kwargs):
        calls.append(command)
        family = command[1]
        if command[2:4] == ['rule', 'del']:
            priority = int(command[5])
            net['rules'][:] = [
                rule for rule in net['rules']
                if not (rule['family'] == family and rule['priority'] == priority)
            ]
        else:
            net['routes'][:] = [route for route in net['routes'] if route['family'] != family]
        return Mock(returncode=0)

    monkeypatch.setattr(tun.subprocess, 'run', run)
    tun.cleanup(record)
    assert calls == [
        ['/usr/sbin/ip', '-4', 'rule', 'del', 'priority', '9002',
         'from', 'all', 'table', '2022'],
        ['/usr/sbin/ip', '-6', 'rule', 'del', 'priority', '9010', 'from', 'all', 'nop'],
        ['/usr/sbin/ip', '-4', 'route', 'del', 'default', 'table', '2022',
         'via', '198.18.0.2', 'dev', 'mihomo'],
        ['/usr/sbin/ip', '-6', 'route', 'del', 'default', 'table', '2022',
         'via', 'fdfe:dcba:9876::2', 'dev', 'mihomo', 'metric', '1024',
         'pref', 'medium'],
    ]


def test_cleanup_rejects_unrecorded_or_unprivileged_resources(monkeypatch):
    net = {
        'links': [], 'routes': [],
        'rules': [
            {'family': '-4', 'priority': 9002, 'src': 'all', 'table': '2022'}
        ],
    }
    record = {'pid': 123, 'settings': settings(), 'tun_resources': {'routes': [], 'rules': []}}
    run = Mock()
    monkeypatch.setattr(tun, 'network', lambda: net)
    monkeypatch.setattr(tun.subprocess, 'run', run)
    with pytest.raises(AppError) as error:
        tun.cleanup(record)
    assert error.value.kind == 'tun_cleanup_failed'
    run.assert_not_called()

    record['tun_resources'] = {
        'routes': [],
        'rules': [copy.deepcopy(net['rules'][0])],
    }
    net['rules'].append(copy.deepcopy(net['rules'][0]))
    with pytest.raises(AppError) as error:
        tun.cleanup(record)
    assert error.value.kind == 'tun_cleanup_failed'
    run.assert_not_called()

    net['rules'].pop()
    record['tun_resources'] = copy.deepcopy(tun.resources(net))
    monkeypatch.setattr(tun, 'permission', lambda: {'cap_net_admin': False})
    with pytest.raises(AppError) as error:
        tun.cleanup(record)
    assert error.value.kind == 'tun_cleanup_permission_denied' and error.value.code == 4
    run.assert_not_called()


def test_cleanup_preserves_resource_added_during_recovery(monkeypatch):
    owned = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
        'gateway': '198.18.0.2', 'flags': [],
    }
    foreign = {'family': '-4', 'table': '2022', 'dev': 'foreign0', 'dst': '10.0.0.0/8'}
    net = {'links': [], 'routes': [owned], 'rules': []}
    record = {
        'pid': 123,
        'settings': settings(),
        'tun_resources': {'routes': [copy.deepcopy(owned)], 'rules': []},
    }
    monkeypatch.setattr(tun, 'network', lambda: copy.deepcopy(net))
    monkeypatch.setattr(tun, 'permission', lambda: {'cap_net_admin': True})
    monkeypatch.setattr(tun, 'ip_tool', lambda: '/usr/sbin/ip')

    def run(command, **kwargs):
        assert command == [
            '/usr/sbin/ip', '-4', 'route', 'del', 'default', 'table', '2022',
            'via', '198.18.0.2', 'dev', 'mihomo',
        ]
        net['routes'].remove(owned)
        net['routes'].append(foreign)
        return Mock(returncode=0)

    monkeypatch.setattr(tun.subprocess, 'run', run)
    with pytest.raises(AppError) as error:
        tun.cleanup(record)
    assert error.value.kind == 'tun_cleanup_failed'
    assert net['routes'] == [foreign]


def test_cleanup_rejects_semantically_foreign_snapshot(monkeypatch):
    foreign = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': '10.0.0.0/8',
        'gateway': '198.18.0.2', 'flags': [],
    }
    net = {'links': [], 'routes': [foreign], 'rules': []}
    record = {
        'pid': 123,
        'settings': settings(),
        'tun_resources': {'routes': [copy.deepcopy(foreign)], 'rules': []},
    }
    run = Mock()
    monkeypatch.setattr(tun, 'network', lambda: copy.deepcopy(net))
    monkeypatch.setattr(tun.subprocess, 'run', run)

    with pytest.raises(AppError) as error:
        tun.cleanup(record)
    assert error.value.kind == 'tun_cleanup_failed'
    run.assert_not_called()


def test_startup_snapshot_never_claims_foreign_resources():
    record = tun_record()
    owned = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
        'gateway': '198.18.0.2', 'flags': [],
    }
    foreign = {'family': '-4', 'table': '2022', 'dev': 'foreign0', 'dst': '10.0.0.0/8'}
    owned_rule = {
        'family': '-4', 'priority': 9000, 'src': 'all', 'dst': '198.18.0.0',
        'dstlen': 30, 'table': '2022',
    }
    foreign_rule = {'family': '-4', 'priority': 9005, 'table': 'main', 'src': 'all'}
    net = {
        'links': [],
        'routes': [owned, foreign],
        'rules': [owned_rule, foreign_rule],
    }

    snapshot, clean = tun.owned_snapshot(record, net, {'routes': [], 'rules': []})
    assert snapshot == {'routes': [owned], 'rules': [owned_rule]}
    assert clean is False
    with pytest.raises(AppError) as error:
        tun.require_owned_snapshot(record, net, {'routes': [], 'rules': []})
    assert error.value.kind == 'tun_route_conflict'


@pytest.mark.parametrize('resource', [
    {
        'family': '-4', 'priority': 9005, 'src': 'all', 'dst': '198.18.0.1',
        'dstlen': 32, 'table': '2022',
    },
    {
        'family': '-4', 'priority': 9000, 'src': 'all', 'dst': '198.18.0.0',
        'dstlen': 30, 'table': '2022', 'fwmark': '0x1',
    },
    {'family': '-4', 'priority': 9010, 'nop': None, 'fwmark': '0x1'},
])
def test_near_mihomo_foreign_rules_are_not_owned(resource):
    assert not tun.owned_rule(resource)


@pytest.mark.parametrize('route', [
    {'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': '10.0.0.0/8'},
    {'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default', 'metric': 5},
    {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': '10.0.0.0/8',
        'gateway': '198.18.0.2', 'flags': [],
    },
])
def test_same_device_foreign_routes_are_not_owned(route):
    assert not tun.owned_route(route, 'mihomo')


def test_only_routes_derived_from_exclusions_are_owned():
    excluded = ['10.0.0.0/8']
    expected = tun.route_destinations(excluded, 4)
    assert ipaddress.ip_network('128.0.0.0/1') in expected
    assert ipaddress.ip_network('10.0.0.0/8') not in expected
    route = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': '128.0.0.0/1',
        'gateway': '198.18.0.2', 'flags': [],
    }
    assert tun.owned_route(route, 'mihomo', excluded)
    route['dst'] = '10.0.0.0/8'
    assert not tun.owned_route(route, 'mihomo', excluded)


def tun_record():
    return {
        'pid': 123,
        'identity': {'start_ticks': '1', 'boot_id': 'boot', 'cmdline': ['/core']},
        'data_dir': '/data',
        'settings': settings(),
        'tun_resources': {'routes': [], 'rules': []},
    }


def starting_record(engine, enabled):
    data_dir = str(engine.root / 'data')
    command = ['/core', '-d', data_dir, '-f', str(engine.runtime_path)]
    return {
        'pid': 123,
        'identity': None,
        'state': 'starting',
        'command': command,
        'data_dir': data_dir,
        'subscription': 'a',
        'fingerprint': 'digest',
        'secret': 'secret',
        'settings': settings(enabled),
        **({'tun_resources': {'routes': [], 'rules': []}} if enabled else {}),
    }


class ReadyPoll:
    def register(self, descriptor, event):
        self.descriptor = descriptor

    def poll(self, timeout):
        return [(self.descriptor, 1)]


def prepare_live_starting_stop(engine, record, monkeypatch):
    engine.record_path.write_text(json.dumps(record))
    identity = {'start_ticks': '1', 'boot_id': 'boot', 'cmdline': record['command']}
    monkeypatch.setattr('mihomo_py.engine.process_identity', lambda pid: identity)
    monkeypatch.setattr(engine, 'require_process_api', lambda: None)
    descriptor = os.open('/dev/null', os.O_RDONLY)
    monkeypatch.setattr(pidfd, 'open_pidfd', lambda pid: descriptor)
    monkeypatch.setattr('mihomo_py.engine.select.poll', ReadyPoll)
    send_signal = Mock()
    monkeypatch.setattr(pidfd, 'send_signal', send_signal)
    return descriptor, send_signal


def test_stop_terminates_live_non_tun_starting_process(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    record = starting_record(engine, False)
    descriptor, send_signal = prepare_live_starting_stop(engine, record, monkeypatch)
    cleanup = Mock()
    monkeypatch.setattr(tun, 'cleanup', cleanup)

    assert engine.stop() is True
    send_signal.assert_called_once_with(descriptor, signal.SIGTERM)
    cleanup.assert_called_once()
    assert not engine.record_path.exists()


def test_stop_captures_resources_from_live_tun_starting_process(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    record = starting_record(engine, True)
    descriptor, send_signal = prepare_live_starting_stop(engine, record, monkeypatch)
    residual = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
        'gateway': '198.18.0.2', 'flags': [],
    }
    monkeypatch.setattr(
        tun, 'settled_network',
        lambda value: {'links': [], 'routes': [residual], 'rules': []},
    )
    cleanup = Mock()
    monkeypatch.setattr(tun, 'cleanup', cleanup)

    assert engine.stop() is True
    send_signal.assert_called_once_with(descriptor, signal.SIGTERM)
    assert cleanup.call_args.args[0]['tun_resources'] == {
        'routes': [residual], 'rules': []
    }
    assert not engine.record_path.exists()


def test_stop_preserves_starting_record_after_pid_reuse(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    record = starting_record(engine, False)
    engine.record_path.write_text(json.dumps(record))
    reused = {'start_ticks': '2', 'boot_id': 'boot', 'cmdline': ['/other']}
    monkeypatch.setattr('mihomo_py.engine.process_identity', lambda pid: reused)
    cleanup = Mock()
    monkeypatch.setattr(tun, 'cleanup', cleanup)

    with pytest.raises(AppError) as error:
        engine.stop()
    assert error.value.kind == 'unconfirmed_starting_process'
    cleanup.assert_not_called()
    assert engine.record_path.exists()


def test_stop_recovers_exited_tun_starting_process(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    record = starting_record(engine, True)
    engine.record_path.write_text(json.dumps(record))
    monkeypatch.setattr('mihomo_py.engine.process_identity', lambda pid: None)
    residual = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
        'gateway': '198.18.0.2', 'flags': [],
    }
    monkeypatch.setattr(
        tun, 'settled_network',
        lambda value: {'links': [], 'routes': [residual], 'rules': []},
    )
    cleanup = Mock()
    monkeypatch.setattr(tun, 'cleanup', cleanup)

    assert engine.stop() is False
    assert cleanup.call_args.args[0]['tun_resources'] == {
        'routes': [residual], 'rules': []
    }
    assert not engine.record_path.exists()


def test_stop_recovers_if_process_exits_before_pidfd_open(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    record = tun_record()
    engine.record_path.write_text('{}')
    monkeypatch.setattr(engine, 'record', lambda: record)
    monkeypatch.setattr(engine, 'running', lambda: record)
    monkeypatch.setattr(engine, 'require_process_api', lambda: None)
    monkeypatch.setattr(pidfd, 'open_pidfd', Mock(side_effect=ProcessLookupError))
    cleanup = Mock()
    monkeypatch.setattr(tun, 'cleanup', cleanup)

    assert engine.stop() is False
    cleanup.assert_called_once_with(record)
    assert not engine.record_path.exists()


def test_stop_recovers_if_identity_disappears_after_pidfd_open(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    record = tun_record()
    engine.record_path.write_text('{}')
    monkeypatch.setattr(engine, 'record', lambda: record)
    monkeypatch.setattr(engine, 'running', lambda: record)
    monkeypatch.setattr(engine, 'require_process_api', lambda: None)
    descriptor = os.open('/dev/null', os.O_RDONLY)
    monkeypatch.setattr(pidfd, 'open_pidfd', lambda pid: descriptor)
    send_signal = Mock()
    monkeypatch.setattr(pidfd, 'send_signal', send_signal)
    monkeypatch.setattr('mihomo_py.engine.process_identity', lambda pid: None)
    cleanup = Mock()
    monkeypatch.setattr(tun, 'cleanup', cleanup)

    assert engine.stop() is False
    cleanup.assert_called_once_with(record)
    send_signal.assert_not_called()
    assert not engine.record_path.exists()
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_launch_reaps_child_if_log_close_fails(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    child = Mock(pid=123)
    child.poll.return_value = None
    child.wait.return_value = 0
    data_dir = str(tmp_path / 'data')
    command = ['/core', '-d', data_dir, '-f', str(engine.runtime_path)]
    monkeypatch.setattr(engine, 'check_ports', lambda settings: None)
    monkeypatch.setattr(engine, 'executable', lambda: '/core')
    monkeypatch.setattr(
        'mihomo_py.engine.process_identity',
        lambda pid: {'start_ticks': '1', 'boot_id': 'boot', 'cmdline': command},
    )
    monkeypatch.setattr('mihomo_py.engine.subprocess.Popen', lambda *args, **kwargs: child)
    real_close = os.close
    close_calls = 0

    def close(descriptor):
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            raise OSError('injected close failure')
        real_close(descriptor)

    monkeypatch.setattr('mihomo_py.engine.os.close', close)
    with pytest.raises(OSError, match='injected'):
        engine.launch(
            'proxies: []', 'a', settings(False), 'digest', 'secret', data_directory=data_dir
        )
    child.terminate.assert_called_once_with()
    child.wait.assert_called_once_with(timeout=5)
    assert not engine.record_path.exists()


def mock_tun_launch(engine, tmp_path, monkeypatch, child, final_net):
    data_dir = str(tmp_path / 'data')
    command = ['/core', '-d', data_dir, '-f', str(engine.runtime_path)]
    identity = {'start_ticks': '1', 'boot_id': 'boot', 'cmdline': command}
    empty = {'links': [], 'routes': [], 'rules': []}
    monkeypatch.setattr(engine, 'check_ports', lambda settings: None)
    monkeypatch.setattr(engine, 'executable', lambda: '/core')
    monkeypatch.setattr('mihomo_py.engine.process_identity', lambda pid: identity)
    monkeypatch.setattr('mihomo_py.engine.subprocess.Popen', lambda *args, **kwargs: child)
    monkeypatch.setattr(tun, 'preflight', lambda value: empty)
    monkeypatch.setattr(tun, 'network', lambda: empty)
    monkeypatch.setattr(tun, 'settled_network', lambda record: final_net)
    monkeypatch.setattr(engine, 'healthy', lambda record: False)
    monkeypatch.setattr('mihomo_py.engine.time.sleep', lambda seconds: None)
    return data_dir


def test_launch_captures_resources_created_just_before_exit(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    child = Mock(pid=123)
    child.poll.side_effect = [None, None, 1]
    child.wait.return_value = 1
    residual = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
        'gateway': '198.18.0.2', 'flags': [],
    }
    final_net = {'links': [], 'routes': [residual], 'rules': []}
    data_dir = mock_tun_launch(engine, tmp_path, monkeypatch, child, final_net)
    cleanup = Mock()
    monkeypatch.setattr(tun, 'cleanup', cleanup)

    with pytest.raises(AppError) as error:
        engine.launch(
            'proxies: []', 'a', settings(), 'digest', 'secret', data_directory=data_dir
        )
    assert error.value.kind == 'tun_start_failed'
    cleanup_record = cleanup.call_args.args[0]
    assert cleanup_record['tun_resources'] == {'routes': [residual], 'rules': []}
    assert not engine.record_path.exists()


def test_tun_log_close_failure_preserves_cleanup_evidence(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    child = Mock(pid=123)
    child.poll.return_value = None
    child.wait.return_value = 1
    residual = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
        'gateway': '198.18.0.2', 'flags': [],
    }
    final_net = {'links': [], 'routes': [residual], 'rules': []}
    data_dir = mock_tun_launch(engine, tmp_path, monkeypatch, child, final_net)
    cleanup = Mock(side_effect=AppError('tun_cleanup_failed', 'injected'))
    monkeypatch.setattr(tun, 'cleanup', cleanup)
    real_close = os.close
    close_calls = 0

    def close(descriptor):
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            raise OSError('injected close failure')
        real_close(descriptor)

    monkeypatch.setattr('mihomo_py.engine.os.close', close)
    with pytest.raises(AppError) as error:
        engine.launch(
            'proxies: []', 'a', settings(), 'digest', 'secret', data_directory=data_dir
        )
    assert error.value.kind == 'tun_cleanup_failed'
    saved = engine.record()
    assert saved['pid'] == 123
    assert saved['identity']['start_ticks'] == '1'
    assert saved['tun_resources'] == {'routes': [residual], 'rules': []}


def test_exit_before_identity_preserves_cleanup_evidence(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    child = Mock(pid=123)
    child.poll.return_value = 1
    child.wait.return_value = 1
    residual = {
        'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default',
        'gateway': '198.18.0.2', 'flags': [],
    }
    final_net = {'links': [], 'routes': [residual], 'rules': []}
    data_dir = mock_tun_launch(engine, tmp_path, monkeypatch, child, final_net)
    monkeypatch.setattr('mihomo_py.engine.process_identity', lambda pid: None)
    monkeypatch.setattr(
        tun, 'cleanup', Mock(side_effect=AppError('tun_cleanup_failed', 'injected'))
    )

    with pytest.raises(AppError) as error:
        engine.launch(
            'proxies: []', 'a', settings(), 'digest', 'secret', data_directory=data_dir
        )
    assert error.value.kind == 'tun_cleanup_failed'
    saved = engine.record()
    assert saved['state'] == 'starting' and saved['identity'] is None
    assert saved['pid'] == 123
    assert saved['tun_resources'] == {'routes': [residual], 'rules': []}


@pytest.mark.parametrize('snapshot', [
    {'routes': [], 'rules': [1]},
    {'routes': [], 'rules': [{}]},
    {'routes': [], 'rules': [{'family': 'bad', 'priority': 9000}]},
    {'routes': [], 'rules': [{'family': '-4', 'priority': '9000'}]},
    {'routes': [{'family': '-4', 'table': [], 'dst': 'default', 'dev': 'mihomo'}],
     'rules': []},
    {
        'routes': [{
            'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': '10.0.0.0/8',
            'gateway': '198.18.0.2', 'flags': [],
        }],
        'rules': [],
    },
    {
        'routes': [],
        'rules': [{
            'family': '-4', 'priority': 9005, 'src': 'all', 'dst': '198.18.0.0',
            'dstlen': 30, 'table': '2022',
        }],
    },
])
def test_invalid_tun_snapshot_is_structured_state_error(tmp_path, snapshot):
    engine = Engine(tmp_path)
    record = {
        'pid': 123,
        'identity': {'start_ticks': '1', 'boot_id': 'boot', 'cmdline': ['/core']},
        'data_dir': '/data',
        'subscription': 'a',
        'fingerprint': 'digest',
        'secret': 'secret',
        'settings': settings(),
        'tun_resources': snapshot,
    }
    engine.record_path.write_text(json.dumps(record))
    with pytest.raises(AppError) as error:
        engine.record()
    assert error.value.kind == 'invalid_process_state'


def test_enable_stopped_prechecks_validates_then_saves(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    state = manager.store.read()
    state['selected'] = 'a'
    state['subs']['a'] = {'source': 'local', 'content': 'proxies: []', 'updated_at': ''}
    manager.store.save(state)
    calls = []
    monkeypatch.setattr(tun, 'preflight', lambda *a: calls.append('preflight'))
    monkeypatch.setattr(manager.engine, 'validate', lambda *a: calls.append('validate'))
    monkeypatch.setattr(manager.engine, 'launch', lambda *a: pytest.fail('must not start'))
    value = manager.configure_tun(True, exclude_address=['10.1.2.3', 'fd00::1'])
    assert calls == ['preflight', 'validate']
    assert value['configured'] and not value['active']
    assert value['exclude_address'] == ['10.1.2.3/32', 'fd00::1/128']


def test_failed_precheck_leaves_state_intact(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    manager.configure({'mode': 'direct'})
    before = (tmp_path / 'state.json').read_bytes()

    def fail(*args):
        raise AppError('tun_permission_denied', 'denied', 4)

    monkeypatch.setattr(tun, 'preflight', fail)
    with pytest.raises(AppError):
        manager.configure_tun(True)
    assert (tmp_path / 'state.json').read_bytes() == before


@pytest.mark.integration
@pytest.mark.parametrize('stack', tun.STACKS)
def test_real_core_validates_every_supported_stack(tmp_path, stack):
    engine = Engine(tmp_path)
    engine.validate('proxies: []\nrules: ["MATCH,DIRECT"]\n', settings(stack=stack), 'a')


def test_doctor_without_state_creates_no_instance_files(command, tmp_path):
    value = json.loads(command('tun', 'doctor', expected=1).stdout)
    assert not value['ok']
    assert value['checks'][-1]['error'] == 'not_found'
    assert not (tmp_path / 'home').exists()


@pytest.mark.integration
def test_doctor_real_configuration_keeps_instance_unchanged(real_core, source):
    real_core.put_sub('a', str(source), create=True)
    real_core.use('a')
    before = {str(p): p.read_bytes() for p in real_core.store.root.rglob('*') if p.is_file()}
    result = real_core.tun_doctor()
    assert next(c for c in result['checks'] if c['check'] == 'configuration')['ok']
    after = {str(p): p.read_bytes() for p in real_core.store.root.rglob('*') if p.is_file()}
    assert before == after
    assert not real_core.engine.running()


@pytest.mark.integration
def test_old_process_record_gets_disabled_tun_without_rewrite(real_core, source):
    real_core.put_sub('a', str(source), create=True)
    real_core.use('a')
    real_core.start()
    path = real_core.engine.record_path
    old = json.loads(path.read_text())
    del old['settings']['tun']
    del old['settings']['no_proxy']
    path.write_text(json.dumps(old))
    record = real_core.engine.running()
    assert record['settings']['tun'] == tun.DEFAULT_TUN
    assert 'tun' not in json.loads(path.read_text())['settings']


def test_network_transition_lock_blocks_only_tun(tmp_path):
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as lock:
        lock.bind('\0mihomo-py-network-transition')
        engine = Engine(tmp_path)
        assert engine.stop() is False  # Ordinary instances remain independent.
        with pytest.raises(AppError) as error:
            engine.launch('', 'a', settings(), 'digest', 'secret')
        assert error.value.kind == 'busy' and error.value.code == 5
    assert not tmp_path.joinpath('process.json').exists()


def test_owns_device_continues_after_descriptor_closes(monkeypatch):
    closed = Mock()
    closed.read_text.side_effect = FileNotFoundError()
    attached = Mock()
    attached.read_text.return_value = 'pos:\t0\niff:\tmihomo\n'
    monkeypatch.setattr(Path, 'iterdir', lambda p: iter([closed, attached]))
    assert tun.owns_device({'pid': 123, 'settings': settings()})
    attached.read_text.assert_called_once()


def test_owns_device_does_not_claim_unreadable_descriptors(monkeypatch):
    monkeypatch.setattr(Path, 'iterdir', Mock(side_effect=PermissionError()))
    with pytest.raises(AppError) as error:
        tun.owns_device({'pid': 123, 'settings': settings()})
    assert error.value.kind == 'tun_inspection_denied'
    assert '--sudo tun status' in error.value.suggestion


@pytest.mark.parametrize('family', ['-4', '-6'])
@pytest.mark.parametrize('resource', ['routes', 'rules'])
def test_missing_recorded_policy_is_inactive(monkeypatch, family, resource):
    net = {'links': [{'ifname': 'mihomo', 'flags': ['UP']}], 'routes': [], 'rules': []}
    for af in ('-4', '-6'):
        net['routes'].append({'family': af, 'table': '2022', 'dev': 'mihomo', 'dst': 'default'})
        net['rules'].extend([
            {'family': af, 'priority': 9000, 'table': '2022', 'dst': '198.18.0.0'},
            {'family': af, 'priority': 9002, 'table': '2022', 'src': 'all'},
            {'family': af, 'priority': 9010, 'nop': None},
        ])
    monkeypatch.setattr(tun, 'owns_device', lambda record: True)
    record = {'pid': 123, 'settings': settings(),
              'tun_resources': copy.deepcopy(tun.resources(net))}
    assert tun.observation(record, net) == {'active': True, 'ipv4': True, 'ipv6': True}
    for item in list(net[resource]):
        if item['family'] != family:
            continue
        net[resource].remove(item)
        result = tun.observation(record, net)
        assert not result['active']
        assert not result['ipv4' if family == '-4' else 'ipv6']
        assert result['ipv6' if family == '-4' else 'ipv4']
        net[resource].append(item)


def test_subnet_rule_alone_is_not_ready_during_startup(monkeypatch):
    monkeypatch.setattr(tun, 'owns_device', lambda record: True)
    net = {
        'links': [{'ifname': 'mihomo', 'flags': ['UP']}],
        'routes': [{'family': '-4', 'table': '2022', 'dev': 'mihomo', 'dst': 'default'}],
        'rules': [{'family': '-4', 'priority': 9000, 'table': '2022', 'dst': '198.18.0.0'},
                  {'family': '-4', 'priority': 9010, 'nop': None}],
    }
    assert not tun.observation({'pid': 123, 'settings': settings()}, net)['active']


@pytest.mark.parametrize('still_running', [True, False])
def test_stop_failure_preserves_or_restores_old_core(tmp_path, monkeypatch, still_running):
    engine = Engine(tmp_path)
    old = {'pid': 123, 'settings': settings(), 'subscription': 'old',
           'fingerprint': 'old-digest', 'secret': 'old-secret', 'data_dir': '/old-data'}
    running = [old]
    monkeypatch.setattr(engine, 'record', lambda: old)
    monkeypatch.setattr(engine, 'running', lambda: running[0])
    monkeypatch.setattr(engine, 'check_ports', lambda *a, **kw: None)
    monkeypatch.setattr(tun, 'network', lambda: {'links': [], 'routes': [], 'rules': []})
    engine.runtime_path.write_text('old-runtime')

    def stop():
        if not still_running:
            running[0] = None
        raise AppError('stop_failed' if still_running else 'tun_cleanup_failed', 'injected')

    launch = Mock()
    monkeypatch.setattr(engine, 'stop', stop)
    monkeypatch.setattr(engine, 'launch', launch)
    with pytest.raises(AppError) as error:
        engine.replace_runtime('new-runtime', 'new', settings(False), 'new-digest', 'new-secret')
    assert error.value.kind == ('stop_failed' if still_running else 'tun_cleanup_failed')
    if still_running:
        launch.assert_not_called()
    else:
        launch.assert_called_once_with('old-runtime', 'old', old['settings'], 'old-digest',
                                       'old-secret', data_directory='/old-data')


@pytest.mark.integration
def test_doctor_uses_offline_custom_geo_cache(real_core, source, mmdb_bytes):
    content = ('proxies: []\nrules: ["GEOIP,CN,DIRECT", "MATCH,DIRECT"]\n'
               'geox-url: {mmdb: "http://127.0.0.1:1/unavailable.mmdb"}\n')
    source.write_text(content)
    directory = real_core.engine.data_dir('offline', parse(content))
    (directory / 'country.mmdb').write_bytes(mmdb_bytes)
    real_core.put_sub('offline', str(source), create=True)
    real_core.use('offline')
    before = {str(p): p.read_bytes() for p in real_core.store.root.rglob('*') if p.is_file()}
    result = real_core.tun_doctor()
    assert next(c for c in result['checks'] if c['check'] == 'configuration')['ok'], result
    after = {str(p): p.read_bytes() for p in real_core.store.root.rglob('*') if p.is_file()}
    assert after == before


def test_doctor_copies_geo_overrides_without_sharing_files(tmp_path, monkeypatch, mmdb_bytes):
    manager = Manager(tmp_path)
    state = manager.store.read()
    state['selected'] = 'a'
    state['subs']['a'] = {'content': 'proxies: []'}
    override = tmp_path / 'geodata' / 'country.mmdb'
    override.parent.mkdir()
    override.write_bytes(mmdb_bytes)
    cached = manager.engine.data_dir('a') / 'country.mmdb'
    cached.symlink_to(override)

    def validate(engine, content, settings, name):
        for path in (engine.data_path(name) / 'country.mmdb', engine.root / 'geodata/country.mmdb'):
            assert not path.is_symlink() and path.read_bytes() == mmdb_bytes
            path.write_bytes(b'changed in isolated validation')

    monkeypatch.setattr(Engine, 'validate', validate)
    manager._check_tun_configuration(state, settings()['tun'])
    assert override.read_bytes() == cached.read_bytes() == mmdb_bytes
    assert cached.is_symlink()
