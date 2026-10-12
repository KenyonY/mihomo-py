import io
import socket
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from mihomo_py.errors import AppError
from mihomo_py.manager import Manager


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_detached_web_lifecycle(tmp_path):
    manager = Manager(tmp_path / "state")
    with manager.store.lock():
        manager.configure(
            {
                "controller_host": "127.0.0.1",
                "controller_port": free_port(),
                "proxy_port": free_port(),
            }
        )
    port = free_port()
    try:
        info = manager.start_web(host="127.0.0.1", port=port)
        assert manager.web_gateway()["port"] == port
        with urllib.request.urlopen(info["url"], timeout=3) as response:
            assert response.status == 200
            assert "订阅管理" in response.read().decode()
    finally:
        manager.stop_web()
    assert manager.web_gateway() is None


def test_start_web_reaps_child_on_unexpected_readiness_error(tmp_path, monkeypatch):
    manager = Manager(tmp_path / "state")
    child = Mock(pid=123)
    child.poll.return_value = None
    monkeypatch.setattr("mihomo_py.manager.dashboard_root", lambda: tmp_path)
    monkeypatch.setattr("mihomo_py.manager.subprocess.Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(
        manager,
        "web_gateway",
        Mock(side_effect=[None, {"host": "127.0.0.1", "port": 9091}]),
    )
    monkeypatch.setattr(
        manager.engine,
        "controller_secret",
        Mock(side_effect=AppError("invalid_secret", "invalid")),
    )

    with pytest.raises(AppError, match="invalid"):
        manager._start_web()
    child.terminate.assert_called_once_with()
    child.wait.assert_called_once_with(timeout=5)
    child.kill.assert_not_called()


def test_start_web_closes_http_error_response(tmp_path, monkeypatch):
    manager = Manager(tmp_path / "state")
    child = Mock(pid=123)
    child.poll.side_effect = [1, 1]
    error = urllib.error.HTTPError(
        "http://127.0.0.1:9091/", 503, "unavailable", {}, io.BytesIO()
    )
    opener = Mock()
    opener.open.side_effect = error
    monkeypatch.setattr("mihomo_py.manager.dashboard_root", lambda: tmp_path)
    monkeypatch.setattr("mihomo_py.manager.subprocess.Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr("mihomo_py.manager.urllib.request.build_opener", lambda *args: opener)
    monkeypatch.setattr(
        manager,
        "web_gateway",
        Mock(side_effect=[None, {"host": "127.0.0.1", "port": 9091}]),
    )

    with pytest.raises(AppError) as raised:
        manager._start_web()
    assert raised.value.kind == "web_start_failed"
    assert error.closed
    child.wait.assert_called_once_with()
