# mihomo-py

面向 Linux 服务器的 mihomo CLI / TUI 客户端，可选 zashboard Web 面板，支持订阅管理、节点切换和延迟测试、配置校验、独立本机设置及后台进程管理。

需要 Python 3.11+、Linux（支持 pidfd 的内核，5.3+），支持 x86_64 / aarch64。支持系统 Python 和 Conda：Python 缺少原生 pidfd 接口时，通过标准库 ctypes 调用相同的 Linux 系统接口，不需要编译器或切换 Python；容器须允许这些系统调用。已用系统 Python 3.12.3、Conda Python 3.12.4、Textual 8.2.8、mihomo v1.19.19 验证（x86_64）。发行包内置 mihomo 内核与默认地理数据库；systemd 和 TUN 是后续阶段。

## 安装与开始使用

发行包发布并同步到所用镜像后，在当前 Python 环境（包括 Conda）中安装：

```bash
python -m pip install mihomo-py
mihomo-py
```

或使用独立虚拟环境：

```bash
uv venv --python /usr/bin/python3
uv pip install -e .
source .venv/bin/activate
mihomo-py                 # 进入 TUI，也可使用 mihomo-py tui
```

也可以使用子命令：

```bash
mihomo-py --help

# 导入本地完整的 Clash/Mihomo YAML 配置
mihomo-py sub add work ./config.yaml
mihomo-py sub use work
mihomo-py core start
mihomo-py core status
mihomo-py core logs --follow
mihomo-py core stop
```

在 TUI 的「设置」中填写代理监听地址，或使用 CLI：

```bash
mihomo-py config set --host 0.0.0.0
```

`host` 是代理监听 IPv4 地址，默认 `127.0.0.1`；`0.0.0.0` 允许通过本机各网络接口访问 HTTP/SOCKS 代理。管理 API / Web 面板由独立的 `controller_host` 设置控制，默认是 `0.0.0.0`。运行中修改会重启内核。

## Web 面板

先安装可选资源：`pip install 'mihomo-py[web]'`（开发目录使用 `pip install -e ./web` 后 `pip install -e '.[web]'`）。启动或重启内核后，在 TUI 点击「Web 面板」查看地址和登录密钥，或运行：

```bash
mihomo-py config set --controller-host 0.0.0.0 --controller-port 19090
mihomo-py --format table core web
```

其他设备打开 `http://服务器IP:19090/ui/`，在面板的 Password / 密码字段填写上述密钥并保存。例如：`http://100.83.37.33:19090/ui/`。`0.0.0.0` 是监听地址，浏览器使用服务器的实际 IP。修改代理 `host` 不会开放面板。

zashboard v3.29.1 的静态资源通过可选资源包提供，使用系统字体，不需要 GitHub、CDN 或额外 Web 服务。页面请求限制到当前内核地址，关闭默认更新、外部 IP 和连通性检查。密钥不会出现在网页资源或普通状态输出中，重启后保持有效；`core web` 输出包含密钥，请勿分享。设置 `0.0.0.0` 会同时开放带密钥验证的管理 API，请在可信网络访问。

Web 面板管理当前内核的节点、规则、流量和连接。需要浏览器订阅管理时，运行 `mihomo-py web serve --port 19091`，访问 `http://服务器IP:19091/`；该入口支持添加、更新、修改来源、切换、删除订阅以及启动 / 停止内核，与 TUI/CLI 共用配置和密钥。详见 [Web 订阅管理](docs/subscriptions-web.md) 和 [节点面板](docs/web.md)。

安装只需要可用的 pip 镜像源：内核和默认 GEO 数据已包含在 wheel / 源码发行包中，安装、构建和首次准备这些资源不访问 GitHub 或其他下载站。镜像需要同步本项目的发行包及 Python 依赖；安装 `[web]` 时还需要 `mihomo-py-web` 资源包。默认使用包内内核；如需覆盖，设置 `MIHOMO_PY_BINARY=/path/to/mihomo` 或使用全局选项 `--core-binary`（显式传入 `mihomo` 才会查找 PATH）。

远程订阅、自定义 `geox-url`、远程 rule/proxy providers 不属于内置资源，仍需可达或提前提供本地文件。默认 GEO 自动更新关闭，避免启动后触发外网更新。详见 [离线安装与发行](docs/packaging.md)。

远程订阅支持 HTTP(S)。可以从 stdin 输入地址，避免把令牌放进命令历史：

```bash
read -rs 'SUB_URL?订阅 URL: '
printf '%s\n' "$SUB_URL" | mihomo-py sub add work -
unset SUB_URL
```

上例使用 zsh 的 `read` 语法；bash 可使用 `read -rs -p '订阅 URL: ' SUB_URL`。
也可以直接 `mihomo-py sub add work 'https://example.com/sub?token=…'`。
支持完整 YAML 配置，不转换 Base64 节点列表或 `ss://` 等单节点链接。

## 终端界面

在交互终端直接运行 `mihomo-py`，或显式执行 `mihomo-py tui`。界面采用克制的深色设计，自适应常见终端尺寸；建议至少 80×24，支持鼠标、键盘与 `NO_COLOR`。

- **订阅**：添加、使用、更新、更换来源及确认删除，提供表单校验和下载 / 校验进度。
- **节点**：选择代理组、搜索、切换手动组节点及单节点测速；刷新保留列表阅读位置。
- **日志**：最近 200 行，支持自动跟随、暂停阅读和恢复跟随。

`1/2/3` 切页，`/` 搜索节点，`i` 查看详情，`?` 查看快捷键，`Ctrl-R` 刷新，`Ctrl-A` 添加订阅。输入框保留原生编辑快捷键。打开界面不会自动启动内核，退出会保留运行中的内核。

完整操作与错误处理说明见 [终端界面文档](docs/tui.md)，其他文档见 [文档导航](docs/README.md)。

## 命令

全局选项放在子命令前，例如 `mihomo-py --json sub list`。

| 命令 | 行为 |
|---|---|
| `tui` | 打开交互终端界面 |
| `sub add NAME SOURCE` | 读取来源、内核校验成功后保存；不会自动选择或启动 |
| `sub list` | 显示当前选择、来源和更新时间；隐藏 URL 路径及令牌 |
| `sub set NAME SOURCE` | 修改来源并刷新缓存；失败保留旧来源和配置 |
| `sub update [NAME]` | 重新读取来源；省略名称时更新当前订阅 |
| `sub use NAME` | 选择已缓存订阅；运行中则校验并重启，停止时只保存选择 |
| `sub remove NAME --yes` | 删除订阅记录和缓存原文；不能删除运行中的订阅 |
| `config show` | 查看本机设置 |
| `config set --proxy-port 17897 --controller-port 19090 --mode rule` | 修改设置；运行中自动重启生效 |
| `core start` | 从当前缓存后台启动；已健康运行则不重复启动 |
| `core restart` | 校验后重启；不刷新订阅 |
| `core stop` | 停止本实例；重复执行安全 |
| `core status` | 显示 PID、健康状态、已选订阅、实际运行订阅和设置 |
| `core logs [--lines 100] [--follow]` | 查看内核日志 |
| `core web` | 显示面板地址和登录密钥，须安装 `[web]` |
| `web serve` | 前台运行 Web 订阅管理与节点统一入口，默认 `0.0.0.0:9091` |
| `web secret` | 查看 Web/API 登录密钥，内核停止时也可用 |
| `node list [--group GROUP]` | 列出代理组，或指定组的节点、选择和最近延迟 |
| `node use NAME --group GROUP` | 按完整名称切换手动组节点，无需重启 |
| `node test NAME [--url URL] [--timeout-ms 5000]` | 测量一个节点的 HTTP 延迟，默认超时 5 秒 |

所有修改命令支持 `--dry-run`，输出 JSON 操作计划，退出码为 10。它不下载、不校验内核配置、不创建目录，因此只表示操作意图，不保证实际执行成功。终端删除会询问确认；管道中必须明确传入 `--yes`。

## 配置与数据

默认目录为 `${XDG_CONFIG_HOME:-~/.config}/mihomo-py`；空或相对路径的 `XDG_CONFIG_HOME` 使用 `~/.config`。可通过 `MIHOMO_PY_HOME` 或 `--data-dir` 覆盖。

```text
state.json        # 订阅来源、缓存 YAML 原文、当前选择、本机设置（原子替换）
runtime.yaml      # 由缓存原文与本机设置生成的实际配置
process.json      # PID、Linux 启动标识、命令行、实际运行设置
controller-secret # 持久化的管理 / Web 登录密钥（0600）
core.log          # 内核日志，追加写入
validation.log    # 最近一次内核校验失败或超时的详细输出
geodata/          # 可选：手动放置离线地理数据库，供新订阅复制使用
core-data/        # 按订阅隔离的内核数据、provider 和节点选择缓存
```

目录默认权限 0700；客户端写入的状态、配置和日志文件为 0600。缓存包含订阅凭证，列举订阅时仅展示脱敏地址。

本机设置默认代理监听地址 `127.0.0.1`、代理端口 `7897`、管理端口 `9090`、路由模式 `rule`。模式可选 `rule/global/direct`。HTTP/SOCKS 共用代理端口，监听地址由本机 `host` 设置决定；管理接口由 `controller_host` 控制，默认监听 `0.0.0.0`，使用持久化的随机密钥；安装 `[web]` 后提供面板。当前固定禁用订阅携带的其他入站端口、自定义 listeners/tunnels、TUN、DNS/DoH 监听、iptables 接管、NTP 及订阅指定的外部 UI，保留节点、代理组、规则、DNS 解析配置，并启用节点选择缓存。代理端口不启用用户名密码验证。后续阶段再提供显式的入站和网络接管配置。

订阅原文不会被本机设置改写；更新后重新合成运行配置。下载默认直连，不使用 `HTTP_PROXY/HTTPS_PROXY`；上限 8 MiB、网络操作超时 20 秒。HTTPS 连接在 TCP 或 TLS 失败时尝试域名的其他地址，每个连接/握手阶段最多等待 5 秒，地址尝试共用 20 秒预算；始终校验证书与原始域名。订阅下载超时自动重试一次，并在 TUI 中提示。使用 `mihomo -t` 校验合成配置；默认 GEO 数据由包内资源提供；订阅自定义的数据和规则源仍可能触发下载。内核校验/启动超时可用全局 `--timeout 60` 调整。

校验前会将缺少的地理数据库从本客户端的 `geodata/`、已有 mihomo 的数据目录（通常为 `~/.config/mihomo`）、包内快照按此优先级复制到订阅目录。支持 MMDB（`country.mmdb` / `geoip.db` / `geoip.metadb`）、`geoip.dat`、`geosite.dat`、`ASN.mmdb`，文件名不区分大小写。可用 `MIHOMO_PY_GEODATA_DIR=/path/to/geodata` 显式指定唯一来源，也可用它提供自定义 `geox-url` 对应的离线数据。只复制这些数据库，不复制订阅、provider 或节点选择缓存；保留目标已有有效文件。订阅显式配置 `geox-url` 时，相应数据库不会被默认快照替代。空文件和无效 MMDB 会被剔除后重新准备，最终由真实内核校验。校验超时请查看 `validation.log`，并检查自定义资源是否可达。

已有残缺 MMDB 的恢复、校验失败回滚和自定义来源隔离见 [地理数据说明](docs/geodata.md)。

相对的 provider/规则/GEO 文件路径以对应的 `core-data/<订阅标识>/` 为基准，导入本地 YAML 不会复制其旁边的依赖文件。建议使用内嵌节点/规则或远程 providers。`sub remove` 删除缓存原文和来源，但保留内核派生数据及历史日志，便于排错。

运行中更新、切换订阅或修改本机设置会短暂中断连接：校验成功后重启；新实例启动失败时尝试恢复旧实例，失败则明确报告。状态文件在成功启动后提交；普通启动失败保留旧订阅记录。文件内容先 fsync，再通过 rename 原子替换；不承诺目录项在断电后的持久化。进程和文件无法形成跨资源原子事务，断电或强制终止仍可能使运行状态与选择不同；`core status` 分别展示两者，`core start` 重新应用已保存选择。

进程管理核对 PID、启动时间、启动标识及命令行，并通过 pidfd 发送信号；不使用 `pkill`。不同 `--data-dir` 可运行独立实例，但必须设置不同端口。默认不配置 systemd，也不修改 shell 代理环境。

## 脚本与错误处理

子命令在交互终端默认表格/文本，管道默认 JSON；可显式 `--format table` 或 `--json`。不带子命令时，交互终端进入 TUI，管道或显式指定输出格式时输出状态；`tui` 子命令要求交互终端。数据写 stdout，结构化错误写 stderr；普通子命令输出无 ANSI 色码。日志可能包含内核返回的地址，`core logs --follow` 在 JSON 模式下逐行输出 NDJSON。

```bash
mihomo-py --json sub list
mihomo-py --json core status
mihomo-py config set --mode direct --dry-run
```

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | IO、下载、启动或其他运行错误 |
| 2 | 参数或配置错误 |
| 3 | 订阅、内核或日志不存在 |
| 4 | 权限不足 |
| 5 | 已存在、资源使用中、端口冲突或并发修改 |
| 10 | 已输出 dry-run 计划，未执行 |
| 130 | 用户取消 |

失败示例：`{"error":"not_found","message":"…","suggestion":"…","retryable":false}`。`--help` 与 `--version` 始终输出文本。

## 开发与验证

```bash
pip install -e ./web -e '.[dev,web]'
pytest
ruff check .
python -m build --installer uv
```

真实内核测试默认使用包内 mihomo（也支持 `MIHOMO_TEST_BINARY`）；使用临时目录、空闲端口、直连规则和本地 HTTP 测速目标，不读取现有订阅，不访问机场服务，不改变现有代理。覆盖真实节点 API、选择持久化、TUI 订阅/内核/节点交互，以及 PTY 中的默认入口和退出。发行包验证必须运行真实内核测试。

本地 TLS 下载回归测试需要 `openssl` 命令来生成临时测试证书；运行客户端本身无需此命令。

配置字段参考 [mihomo 全局配置](https://wiki.metacubex.one/config/general/)。

文档目录见 [docs/README.md](docs/README.md)。
