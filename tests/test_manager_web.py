import socket
import urllib.request

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
