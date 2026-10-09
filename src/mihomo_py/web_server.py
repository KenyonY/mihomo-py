"""Optional same-origin subscription manager and mihomo HTTP/WebSocket gateway."""

import asyncio
import json
import logging
import os
import secrets
from contextlib import suppress

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web

from .bundle import dashboard_root, seed_dashboard
from .config import check_name
from .engine import process_identity
from .errors import AppError
from .store import atomic_write, controller_address

API = "/mihomo-py/api"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data: blob:; font-src 'self'; "
    "frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
)


def secure(response):
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def create_app(manager, *, host=None, port=None):
    root = dashboard_root()
    if root is None:
        raise AppError("web_missing", "尚未安装 Web 资源。", 3, "pip install 'mihomo-py[web]'。")
    portal = root.parent / "portal"
    credential_key = web.RequestKey("credential", str)
    data = manager.store.root / "web-data"
    with manager.store.lock():
        manager.engine.controller_secret()
        data.mkdir(exist_ok=True, mode=0o700)
        dashboard = data / seed_dashboard(data)

    @web.middleware
    async def errors(request, handler):
        try:
            response = await handler(request)
        except AppError as error:
            status = 401 if error.kind == "unauthorized" else (
                {2: 400, 3: 404, 4: 403, 5: 409}.get(error.code, 500)
            )
            response = web.json_response(error.as_dict(), status=status)
        except (OSError, ClientError, TimeoutError):
            logging.getLogger(__name__).exception("Web operation failed")
            response = web.json_response(
                {
                    "error": "operation_failed",
                    "message": "操作失败，请检查服务日志后重试。",
                },
                status=502,
            )
        if not response.prepared:
            secure(response)
        return response

    @web.middleware
    async def authenticate(request, handler):
        public = request.method in ("GET", "HEAD") and (
            request.path == "/" or request.path.startswith(("/assets/", "/ui/"))
        )
        if not public:
            authorization = request.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            if not authorization and request.headers.get("Upgrade", "").lower() == "websocket":
                token = request.query.get("token", "")
            credential = await asyncio.to_thread(manager.engine.controller_secret)
            if not secrets.compare_digest(token.encode(), credential.encode()):
                return web.json_response(
                    {
                        "error": "unauthorized",
                        "message": "登录密钥错误或尚未登录。",
                    },
                    status=401,
                )
            origin = request.headers.get("Origin")
            if origin and origin != f"{request.scheme}://{request.host}":
                return web.json_response(
                    {
                        "error": "invalid_origin",
                        "message": "拒绝跨站请求。",
                    },
                    status=403,
                )
            request[credential_key] = token
        return await handler(request)

    app = web.Application(middlewares=[errors, authenticate], client_max_size=8 * 1024 * 1024)
    session_key = web.AppKey("controller_session", ClientSession)
    sockets = set()
    credential_lock = asyncio.Lock()

    async def close_sockets(application):
        await asyncio.gather(*(socket.close() for socket in tuple(sockets)))

    app.on_shutdown.append(close_sockets)

    if host is not None:
        registration_path = manager.store.root / "web-service.json"
        registration = {
            "pid": os.getpid(), "identity": process_identity(os.getpid()),
            "host": host, "port": port,
        }

        async def register(application):
            with manager.store.lock():
                if manager.web_gateway():
                    raise AppError("web_running", "此实例已有 Web 管理服务运行。", 5)
                atomic_write(registration_path, json.dumps(registration))

        async def unregister(application):
            if registration_path.exists() and json.loads(
                registration_path.read_text()
            ) == registration:
                registration_path.unlink()

        app.on_startup.append(register)
        app.on_cleanup.append(unregister)

    async def sessions(application):
        async with ClientSession(timeout=ClientTimeout(total=60), trust_env=False) as session:
            application[session_key] = session
            yield

    app.cleanup_ctx.append(sessions)

    async def call(request, operation, *, mutate=False):
        def execute():
            if mutate:
                with manager.store.lock():
                    # Recheck after acquiring the lock: a previous request may have rotated it.
                    if not secrets.compare_digest(
                        request[credential_key].encode(),
                        manager.engine.controller_secret().encode(),
                    ):
                        raise AppError("unauthorized", "登录密钥已修改，请重新登录。", 4)
                    return operation()
            return operation()

        return web.json_response(await asyncio.to_thread(execute))

    async def body(request, fields):
        try:
            value = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AppError("invalid_request", "需要 JSON 对象。", 2) from None
        if (
            not isinstance(value, dict)
            or set(value) != set(fields)
            or not all(isinstance(value[key], str) and value[key].strip() for key in fields)
        ):
            raise AppError("invalid_request", "字段须为非空字符串。", 2)
        return value

    def name(request):
        value = request.match_info["name"]
        check_name(value)
        return value

    async def list_subscriptions(request):
        return await call(
            request,
            lambda: {
                "subscriptions": manager.list_subs(),
                "status": manager.status(),
            }
        )

    async def add(request):
        value = await body(request, ("name", "source"))
        return await call(
            request,
            lambda: manager.put_sub(value["name"], value["source"], create=True), mutate=True
        )

    async def source(request):
        subscription = name(request)
        return await call(
            request,
            lambda: {
                "source": manager.subscription(manager.store.read(), subscription)[1]["source"],
            }
        )

    async def edit(request):
        subscription = name(request)
        value = await body(request, ("source",))
        return await call(
            request, lambda: manager.put_sub(subscription, value["source"]), mutate=True
        )

    async def update(request):
        subscription = name(request)
        return await call(request, lambda: manager.put_sub(subscription), mutate=True)

    async def use(request):
        subscription = name(request)
        return await call(request, lambda: manager.use(subscription), mutate=True)

    async def remove(request):
        subscription = name(request)
        return await call(request, lambda: manager.remove(subscription), mutate=True)

    async def start(request):
        return await call(request, manager.start, mutate=True)

    async def stop(request):
        return await call(request, lambda: {"stopped": manager.engine.stop()}, mutate=True)

    async def set_secret(request):
        value = await body(request, ("secret",))
        async with credential_lock:
            response = await call(request, lambda: manager.set_secret(value["secret"]), mutate=True)
            if json.loads(response.body)["changed"]:
                await close_sockets(app)
        return response

    async def index(request):
        return web.FileResponse(portal / "index.html")

    async def ui_index(request):
        return web.FileResponse(dashboard / "index.html")

    async def proxy(request):
        record = await asyncio.to_thread(manager.engine.running)
        if record is None:
            return web.json_response(
                {
                    "error": "core_stopped",
                    "message": "内核未运行，请先选择订阅并启动。",
                },
                status=503,
            )
        settings = record["settings"]
        target = f"http://{controller_address(settings)}:{settings['controller_port']}"
        target += request.rel_url.with_query(
            [(key, value) for key, value in request.query.items() if key != "token"]
        ).path_qs
        headers = {"Authorization": "Bearer " + record["secret"]}
        session = request.app[session_key]
        downstream = secure(web.WebSocketResponse(heartbeat=30))
        if downstream.can_prepare(request).ok:
            async with session.ws_connect(target, headers=headers, heartbeat=30) as upstream:
                async with credential_lock:
                    credential = await asyncio.to_thread(manager.engine.controller_secret)
                    if not secrets.compare_digest(
                        request[credential_key].encode(), credential.encode()
                    ):
                        raise AppError("unauthorized", "登录密钥已修改，请重新登录。", 4)
                    await downstream.prepare(request)
                    sockets.add(downstream)

                async def relay(reader, writer):
                    async for message in reader:
                        if message.type == WSMsgType.TEXT:
                            await writer.send_str(message.data)
                        elif message.type == WSMsgType.BINARY:
                            await writer.send_bytes(message.data)
                        else:
                            break

                tasks = [
                    asyncio.create_task(relay(downstream, upstream)),
                    asyncio.create_task(relay(upstream, downstream)),
                ]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await downstream.close()
                    sockets.discard(downstream)
            return downstream
        headers["Content-Type"] = request.content_type
        async with session.request(
            request.method,
            target,
            headers=headers,
            data=await request.read(),
            allow_redirects=False,
        ) as upstream:
            response = secure(web.StreamResponse(status=upstream.status))
            response.headers["Content-Type"] = upstream.headers.get(
                "Content-Type", "application/octet-stream"
            )
            await response.prepare(request)
            with suppress(ConnectionResetError):
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    await response.write(chunk)
                await response.write_eof()
            return response

    app.add_routes(
        [
            web.get("/", index),
            web.get("/ui/", ui_index),
            web.get(API + "/subscriptions", list_subscriptions),
            web.post(API + "/subscriptions", add),
            web.get(API + "/subscriptions/{name}/source", source),
            web.patch(API + "/subscriptions/{name}", edit),
            web.post(API + "/subscriptions/{name}/update", update),
            web.post(API + "/subscriptions/{name}/use", use),
            web.delete(API + "/subscriptions/{name}", remove),
            web.post(API + "/core/start", start),
            web.post(API + "/core/stop", stop),
            web.put(API + "/settings/secret", set_secret),
        ]
    )
    app.router.add_static("/assets/", portal / "assets", follow_symlinks=False)
    app.router.add_static("/ui/", dashboard, follow_symlinks=False)
    app.router.add_route("*", "/{path:.*}", proxy)
    return app
