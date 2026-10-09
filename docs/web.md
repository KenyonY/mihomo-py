# Web 面板

可选 [zashboard](https://github.com/Zephyruso/zashboard) v3.29.1，使用无字体静态发行包。面板通过 mihomo 的 `external-ui` 功能在管理端口的 `/ui/` 提供，不需要额外服务器或 Node.js。

## 打开与登录

先执行 `pip install 'mihomo-py[web]'`，再选择订阅并启动内核。已运行的实例安装资源包后需要 `core restart`。TUI 的「Web 面板」显示地址、监听信息和登录密钥；CLI 使用 `mihomo-py --format table core web`。普通 `core status` 不输出密钥，面板资源也不包含密钥。

默认监听 `0.0.0.0:9090`，从浏览器访问 `http://服务器IP:9090/ui/`。远程访问在「设置」中修改**管理 / Web IPv4 地址**和端口，或执行：

```bash
mihomo-py config set --controller-host 0.0.0.0 --controller-port 19090
```

浏览器打开 `http://服务器IP:19090/ui/`，在自动弹出的后端配置中填写 Password / 密码，再点击 Save / 保存。无需填写主机、端口或把密钥放在 URL 中。浏览器会保存登录信息，内核重启和切换订阅后密钥不变。端口或访问 IP 改变时，新的页面地址需要重新登录。

`host` 仅控制 HTTP/SOCKS 代理，`controller_host` 独立控制管理 API 和 Web 面板。两者支持 IPv4 地址，`0.0.0.0` 监听所有接口；不能作为浏览器目标地址。修改运行中的监听设置会重启内核，端口冲突或启动失败恢复旧实例。老版本设置缺少 `controller_host` 时使用新默认值 `0.0.0.0`；尚未重启的旧进程记录仍按原来的 `127.0.0.1` 识别，重启后应用新设置。

页面静态资源不需要登录；节点、配置、流量等 API 必须验证密钥。密钥保存于实例目录下权限 0600 的 `controller-secret`，`core web` 的输出属于敏感信息。`0.0.0.0` 会开放完整管理 API，应通过可信局域网或 Tailscale 使用。

## 功能与离线行为

面板用于当前内核的节点切换、延迟测试、连接、流量、规则和日志。订阅来源可通过 CLI/TUI 或 [Web 订阅管理](subscriptions-web.md) 操作，`mihomo-py web serve` 提供订阅与节点的统一入口；面板直接修改内核的设置不会保存到本项目的 `state.json`，下次启动会重新应用客户端设置。

主包不包含面板文件。`[web]` 依赖 `mihomo-py-web` 和 `aiohttp`；资源包的 wheel / 源码包提供静态资源、许可和第三方归属信息，直接访问内核 `/ui/` 不需要额外进程；订阅管理使用独立 Python Web 服务。镜像需要同步这个资源包。首次校验从包内 ZIP 解压到订阅的 `core-data/<标识>/ui-<资源标识>/`，升级 pip 包后重启内核使用新版资源；不访问外部下载站。

本地接入脚本按固定版本的存储字段连接当前页面所在的内核，禁用默认核心更新检查、自动更新、外部 IP 和连通性检查。页面 CSP 限制 API、WebSocket、图片和字体到本地资源；外部 IP 查询、远程图片、其他服务器后端等功能不适用于内置面板。更新面板和内核请升级 pip 包后运行 `core restart`。

用户主动测速、订阅更新或远程 providers 仍使用配置中的目标地址，不属于软件资源的离线安装契约。

## 验证

`tests/test_web.py` 使用真实内核验证两种管理绑定、静态资源、无密钥 / 错误密钥拒绝、CLI API 操作、旧状态迁移、密钥持久化和端口冲突回滚。

`python scripts/check_web.py` 使用 Chromium 打开实际面板、输入密钥、切换节点、查看规则及连接、重启后重新打开，并阻断外部浏览器请求。需要开发依赖中的 Playwright 和 Chromium；可用 `MIHOMO_PY_BROWSER=/path/to/chromium` 指定系统浏览器。
