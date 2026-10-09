import json
import socket

import pytest
from aiohttp import WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from mihomo_py.manager import Manager
from mihomo_py.web_server import API, create_app

pytestmark = pytest.mark.asyncio


async def test_service_discovery_and_cleanup_without_core(tmp_path):
    manager = Manager(tmp_path / "state")
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    app = create_app(manager, host="127.0.0.1", port=port)
    async with TestClient(TestServer(app, port=port)) as client:
        info = manager.web()
        assert info["url"] == f"http://127.0.0.1:{port}/"
        assert info["secret"] == manager.engine.controller_secret()
        assert (await client.get("/")).status == 200
        assert manager.web_gateway()
        record_path = manager.store.root / "web-service.json"
        registration = json.loads(record_path.read_text())
        stale = {**registration, "identity": {**registration["identity"], "start_ticks": "0"}}
        record_path.write_text(json.dumps(stale))
        assert manager.web_gateway() is None
        record_path.write_text(json.dumps(registration))
    assert manager.web_gateway() is None
    assert not (manager.store.root / "web-service.json").exists()


async def test_portal_login_without_core_and_static_isolation(tmp_path):
    manager = Manager(tmp_path / "state")
    async with TestClient(TestServer(create_app(manager))) as client:
        secret = manager.engine.controller_secret()
        for path in ("/", "/ui/", "/ui/mihomo-py.js"):
            response = await client.get(path)
            assert response.status == 200
            assert secret not in await response.text()
            assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
        for path in (API + "/subscriptions", "/version", "/controller-secret"):
            assert (await client.get(path)).status == 401
            assert (await client.get(path + "?token=" + secret)).status == 401
            assert (await client.get(path, headers={"Authorization": secret})).status == 401
        with pytest.raises(WSServerHandshakeError) as error:
            await client.ws_connect("/traffic?token=wrong")
        assert error.value.status == 401
        headers = {"Authorization": "Bearer " + secret}
        response = await client.get(API + "/subscriptions", headers=headers)
        assert (await response.json())["subscriptions"] == []
        assert (await client.get("/version", headers=headers)).status == 503
        assert (
            await client.get(
                API + "/subscriptions", headers={**headers, "Origin": "https://untrusted.example"}
            )
        ).status == 403
        response = await client.post(API + "/subscriptions", json={"name": "a", "source": "/x"})
        assert response.status == 401 and not manager.store.read()["subs"]
        assert (manager.store.root / "controller-secret").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"name": "a", "source": 1},
        {"name": "a", "source": ""},
        {"name": "a", "source": "/x", "extra": "x"},
    ],
)
async def test_invalid_request_does_not_change_state(tmp_path, value):
    manager = Manager(tmp_path / "state")
    async with TestClient(TestServer(create_app(manager))) as client:
        headers = {"Authorization": "Bearer " + manager.engine.controller_secret()}
        response = await client.post(API + "/subscriptions", headers=headers, json=value)
        assert response.status == 400
        assert (await response.json())["error"] == "invalid_request"
        response = await client.post(API + "/subscriptions", headers=headers, data="{")
        assert response.status == 400 and not manager.store.read()["subs"]


@pytest.mark.integration
async def test_real_subscription_crud_switch_proxy_websocket_and_core_lifecycle(
    real_core, http_source
):
    async with TestClient(TestServer(create_app(real_core))) as client:
        secret = real_core.engine.controller_secret()
        headers = {"Authorization": "Bearer " + secret}

        async def request(method, path, payload=None, expected=200):
            response = await client.request(method, API + path, json=payload, headers=headers)
            value = await response.json()
            assert response.status == expected, value
            return value

        await request("POST", "/subscriptions", {"name": "a", "source": http_source["url"]})
        await request(
            "POST",
            "/subscriptions",
            {
                "name": "a",
                "source": http_source["url"],
            },
            expected=409,
        )
        await request("POST", "/subscriptions/a/use")
        assert real_core.store.read()["selected"] == "a" and not real_core.status()["running"]
        await request("POST", "/core/start")
        previous = real_core.status()["pid"]
        http_source["content"] = (
            b"proxies: []\nproxy-groups:\n"
            b" - {name: second-group, type: select, proxies: [DIRECT, REJECT]}\n"
            b"rules: ['MATCH,DIRECT']\n"
        )
        await request("POST", "/subscriptions", {"name": "b", "source": http_source["url"]})
        await request("POST", "/subscriptions/b/use")
        assert real_core.status()["healthy"] and real_core.status()["pid"] != previous
        assert real_core.store.read()["selected"] == "b"
        assert real_core.web()["secret"] == secret
        response = await client.get("/proxies", headers=headers)
        assert response.status == 200 and "second-group" in (await response.json())["proxies"]
        assert response.headers["Cache-Control"] == "no-store"
        async with client.ws_connect("/traffic?token=" + secret) as stream:
            message = await stream.receive(timeout=5)
            assert message.type == WSMsgType.TEXT
            assert "down" in json.loads(message.data)
        value = await request("GET", "/subscriptions")
        assert "PRIVATE-TOKEN" not in json.dumps(value) and "secret" not in value["status"]
        assert (await request("GET", "/subscriptions/b/source"))["source"] == http_source["url"]
        await request("DELETE", "/subscriptions/b", expected=409)
        await request("DELETE", "/subscriptions/a")
        await request("POST", "/subscriptions/b/update")
        new_source = http_source["url"] + "-EDIT"
        await request("PATCH", "/subscriptions/b", {"source": new_source})
        assert real_core.store.read()["subs"]["b"]["source"] == new_source
        previous = real_core.status()["pid"]
        before = real_core.store.read()
        http_source["content"] = b"not valid Clash YAML"
        await request("POST", "/subscriptions/b/update", expected=400)
        assert real_core.status()["pid"] == previous and real_core.store.read() == before
        with real_core.store.lock():
            value = await request("POST", "/subscriptions/b/use", expected=409)
            assert value["error"] == "busy" and value["retryable"]
        await request("POST", "/core/stop")
        assert not real_core.status()["running"]
        assert (await request("GET", "/subscriptions"))["status"]["running"] is False
        await request("DELETE", "/subscriptions/b")
        assert not real_core.store.read()["subs"] and real_core.store.read()["selected"] is None
        assert (await client.get("/ui/")).status == 200
        assert (await client.get("/version", headers=headers)).status == 503


@pytest.mark.integration
async def test_gateway_shutdown_closes_websocket_but_keeps_core(real_core, source):
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    real_core.start()
    async with TestClient(TestServer(create_app(real_core))) as client:
        stream = await client.ws_connect("/traffic?token=" + real_core.engine.controller_secret())
        assert (await stream.receive(timeout=5)).type == WSMsgType.TEXT
    assert stream.closed and real_core.status()["healthy"]
