# Web 订阅管理

## 启动与访问

```bash
pip install 'mihomo-py[web]'
mihomo-py web serve --port 19091
```

也可以在 TUI 顶部点击「开启 Web」。服务会作为独立后台进程运行，退出 TUI 后仍保持运行；再次点击「关闭 Web」只停止 Web 服务，不会停止 mihomo 内核。TUI 使用设置中的管理监听地址（默认 `0.0.0.0`）和端口 `9091`；如需自定义 Web 端口，请使用上面的命令启动。启动失败时在 TUI 显示错误，详细日志位于实例目录的 `web.log`。

默认监听 `0.0.0.0:9091`。以上命令在前台运行；访问 `http://服务器IP:19091/`，输入原管理 API 密钥。密钥可用 `mihomo-py --format table web secret` 查看，即使内核未启动也可使用。密钥不写入页面文件或浏览器页面地址；服务访问日志关闭。HTTP 使用 Bearer 认证，节点面板的 WebSocket 握手使用下文说明的查询参数。

页面有「订阅管理」和「节点面板」。节点面板使用包内 zashboard，HTTP / WebSocket 转发到此实例的 mihomo；切换订阅不需要换地址或重新登录。

Web 服务端口须与代理、管理 API 端口不同。TUI / `config set --controller-host` 控制内核管理端口；`web serve --host` / `--port` 控制独立 Web 服务。原来的管理端口 `/ui/` 继续提供节点面板，订阅管理使用统一入口。Web 服务运行时，TUI 的「Web 面板」和 `core web` 自动指向统一入口。

若要沿用公开地址的 `19090` 端口，可先将内核管理 API 移到本机端口，再在 `19090` 启动 Web 服务：

```bash
mihomo-py config set --controller-host 127.0.0.1 --controller-port 19092
mihomo-py web serve --port 19090
```

这样浏览器始终访问 `http://服务器IP:19090/`，内核 API 的内部端口不需要对外开放。

停止 Web 服务不会停止内核；停止内核后仍可管理订阅。长期运行时可将该命令交给 systemd 等进程管理器。退出终端会影响前台运行的服务。

## 操作与行为

- 登录密钥：右上角「设置」中填写新密钥并确认，支持 1–256 个可见 ASCII 字符，不含空格。Web、管理 API 和 TUI 查看入口共用密钥；当前浏览器保持登录，其他浏览器需重新登录。运行中保存会重启内核并保留订阅与节点选择；失败恢复旧密钥和实例。内核停止时也能修改，下次启动使用新密钥。
- 添加：填写名称、HTTP(S) 订阅地址或服务器 YAML 路径，下载与真实内核校验成功后保存。
- 修改来源、更新：复用 CLI/TUI 校验、缓存和事务逻辑，失败保留旧配置。
- 切换：与 TUI/CLI 同步；运行中会重启应用新订阅，失败恢复原实例。停止时只改变选中项，再点击「启动内核」。
- 删除：页面需确认，正在使用的订阅不能删除，先切换或停止内核。
- 启动 / 停止内核：Web 服务继续运行，登录密钥保持不变。

普通列表隐藏 URL 的路径、查询参数和配置原文；「修改来源」在登录后读取完整地址。密钥持有者拥有本实例的管理员权限，包括下载订阅、读取服务器 YAML 路径。通过可信局域网或 Tailscale 使用。

React / Tailwind CSS / TypeScript 源码在 `web/frontend/`，构建结果和许可随 `mihomo-py-web` 分发，不依赖 CDN。`aiohttp` 仅随 `[web]` 安装；Node.js 仅供维护者重建前端。用户主动更新订阅仍访问订阅来源。

## 接口与并发

接口前缀 `/mihomo-py/api`。HTTP 使用 `Authorization: Bearer <密钥>`，不使用 cookie，不允许跨站 Origin。WebSocket 兼容 zashboard 的 `token` 查询参数，不将其转发到内核或写入访问日志。

| 方法 | 路径 | 含义 |
| --- | --- | --- |
| GET | `/subscriptions` | 列表与内核状态，不返回密钥 |
| POST | `/subscriptions` | 添加，JSON `{name, source}` |
| GET | `/subscriptions/{name}/source` | 读取完整来源 |
| PATCH | `/subscriptions/{name}` | 修改，JSON `{source}` |
| POST | `/subscriptions/{name}/update` | 更新缓存 |
| POST | `/subscriptions/{name}/use` | 选择 / 切换 |
| DELETE | `/subscriptions/{name}` | 删除 |
| POST | `/core/start`、`/core/stop` | 启动 / 停止内核 |
| PUT | `/settings/secret` | 修改共用登录密钥，JSON `{secret}`，响应不返回密钥 |

写操作在工作线程中执行，使用与 CLI/TUI 相同的实例锁。锁忙返回 409 / `busy`，校验错误返回 400，未找到返回 404。浏览器断开不会中止已执行到后台的保存或重启事务。

## 开发与验证

```bash
pip install -e ./web -e '.[dev,web]'
cd web/frontend
npm ci
npm run build
cd ../..
pytest tests/test_web_server.py
python scripts/check_subscriptions_web.py
```

pip 构建和安装只读取已提交的页面资源，不执行 npm，不访问外部下载站。
