# 命令行参考

[English](cli.en.md) · [简体中文](cli.md) · [返回项目首页](../README.zh-CN.md)

## 快速开始

```bash
mihomo-py --help
mihomo-py sub add work ./config.yaml
mihomo-py sub use work
mihomo-py core start
mihomo-py core status
mihomo-py core logs --follow
mihomo-py core stop
```

远程订阅支持 HTTP(S)。可从 stdin 输入地址，避免将凭证放进命令历史：

```bash
# bash
read -rs -p '订阅 URL: ' SUB_URL
printf '%s\n' "$SUB_URL" | mihomo-py sub add work -
unset SUB_URL
```

zsh 使用 `read -rs 'SUB_URL?订阅 URL: '`。也可直接传入 URL：
`mihomo-py sub add work 'https://example.com/sub?token=…'`。
只支持完整 Clash/Mihomo YAML，不转换 Base64 节点列表或 `ss://` 等单节点链接。

## 全局选项

全局选项放在子命令前。

| 选项 | 用途 |
| --- | --- |
| `--data-dir PATH` | 独立实例的数据目录；也可用 `MIHOMO_PY_HOME` |
| `--core-binary PATH` | 覆盖包内内核；也可用 `MIHOMO_PY_BINARY` |
| `--timeout SECONDS` | 内核校验及启动就绪超时，默认 20 秒，范围 1–120 |
| `--format json` / `--json` | 显式使用 JSON 输出 |
| `--format table` | 显式使用表格或文本输出 |
| `--sudo` | 通过 sudo 为本次运行获取 TUN 权限，保持原用户和数据目录 |
| `--version` | 查看版本 |

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

订阅修改、配置修改、节点切换 / 测速及内核启动 / 重启 / 停止命令支持 `--dry-run`，输出 JSON 操作计划，退出码为 10。它不下载、不校验内核配置、不创建目录，因此只表示操作意图，不保证实际执行成功。终端删除会询问确认；管道中必须明确传入 `--yes`。

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

本机设置默认代理监听地址 `127.0.0.1`、代理端口 `7897`、管理端口 `9090`、路由模式 `rule`。模式可选 `rule/global/direct`。HTTP/SOCKS 共用代理端口，监听地址由本机 `host` 设置决定；管理接口由 `controller_host` 控制，默认监听 `0.0.0.0`，使用持久化的随机密钥；安装 `[web]` 后提供面板。当前固定禁用订阅携带的其他入站端口、自定义 listeners/tunnels、DNS/DoH 监听、iptables 接管、NTP 及订阅指定的外部 UI，保留节点、代理组、规则、DNS 解析配置，并启用节点选择缓存。代理端口不启用用户名密码验证。TUN 由本机设置显式启用，订阅不能启用或覆盖其策略。

订阅原文不会被本机设置改写；更新后重新合成运行配置。下载默认直连，不使用 `HTTP_PROXY/HTTPS_PROXY`；上限 8 MiB、网络操作超时 20 秒。HTTPS 连接在 TCP 或 TLS 失败时尝试域名的其他地址，每个连接/握手阶段最多等待 5 秒，地址尝试共用 20 秒预算；始终校验证书与原始域名。订阅下载超时自动重试一次，并在 TUI 中提示。使用 `mihomo -t` 校验合成配置；默认 GEO 数据由包内资源提供；订阅自定义的数据和规则源仍可能触发下载。内核校验/启动超时可用全局 `--timeout 60` 调整。

校验前会将缺少的地理数据库从本客户端的 `geodata/`、已有 mihomo 的数据目录（通常为 `~/.config/mihomo`）、包内快照按此优先级复制到订阅目录。支持 MMDB（`country.mmdb` / `geoip.db` / `geoip.metadb`）、`geoip.dat`、`geosite.dat`、`ASN.mmdb`，文件名不区分大小写。可用 `MIHOMO_PY_GEODATA_DIR=/path/to/geodata` 显式指定唯一来源，也可用它提供自定义 `geox-url` 对应的离线数据。只复制这些数据库，不复制订阅、provider 或节点选择缓存；保留目标已有有效文件。订阅显式配置 `geox-url` 时，相应数据库不会被默认快照替代。空文件和无效 MMDB 会被剔除后重新准备，最终由真实内核校验。校验超时请查看 `validation.log`，并检查自定义资源是否可达。

已有残缺 MMDB 的恢复、校验失败回滚和自定义来源隔离见 [地理数据说明](geodata.md)。

相对的 provider/规则/GEO 文件路径以对应的 `core-data/<订阅标识>/` 为基准，导入本地 YAML 不会复制其旁边的依赖文件。建议使用内嵌节点/规则或远程 providers。`sub remove` 删除缓存原文和来源，但保留内核派生数据及历史日志，便于排错。

运行中更新、切换订阅或修改本机设置会短暂中断连接：校验成功后重启；新实例启动失败时尝试恢复旧实例，失败则明确报告。状态文件在成功启动后提交；普通启动失败保留旧订阅记录。文件内容先 fsync，再通过 rename 原子替换；不承诺目录项在断电后的持久化。进程和文件无法形成跨资源原子事务，断电或强制终止仍可能使运行状态与选择不同；`core status` 分别展示两者，`core start` 重新应用已保存选择。

进程管理核对 PID、启动时间、启动标识及命令行，并通过 pidfd 发送信号；不使用 `pkill`。不同 `--data-dir` 可运行独立实例，但必须设置不同端口。默认不配置 systemd；Shell 代理命令只输出语句或设置子进程环境。

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

更多文档见 [文档导航](README.md)。

## Shell 代理与 TUN

### Shell 代理

```bash
mihomo-py proxy env                          # 管道默认 JSON，终端默认文本
mihomo-py proxy env --format json
mihomo-py proxy env --shell bash             # 也支持 zsh / fish / posix
eval "$(mihomo-py proxy env --shell bash)"
eval "$(mihomo-py proxy env --unset --shell bash)"
mihomo-py proxy run -- curl https://example.com
mihomo-py config set --no-proxy 'localhost,127.0.0.1,::1,.example.internal'
mihomo-py proxy run --no-proxy '' -- curl https://example.com
```

`proxy env` 返回 `running`、`host`、`port`、`variables`。变量包括大小写的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`，默认绕过 `localhost,127.0.0.1,::1`。运行时使用实际实例端口，停止时使用保存的端口；`0.0.0.0` 转成客户端可用的 `127.0.0.1`。HTTP/HTTPS 变量使用 `http://`，ALL_PROXY 使用 `socks5h://`。`--no-proxy` 可临时替换保存的绕过列表；具体匹配语义由目标程序决定。

Shell 输出经过引用，只有用户执行输出语句才会改变自己的 Shell。fish 中可执行 `mihomo-py proxy env --shell fish | source`。`--unset` 清除这八个变量，不恢复此前值；关闭内核不会清除父 Shell 的变量。命令不写 `.bashrc`、`.zshrc` 或持久化用户环境。使用自定义实例时，所有示例应在子命令前添加 `--data-dir PATH`。

`proxy run` 直接执行参数列表、不解析 Shell 字符串，覆盖大小写代理变量并保留其他环境，透传 stdin/stdout/stderr 和退出码。它只适用于支持代理环境变量的程序；不支持的程序需要 TUN。内核停止或不健康时返回 `core_stopped`，退出码 3；命令不存在/不可执行分别为 127/126。子命令输出不会包装成 JSON。

### TUN 启用、检查与恢复

```bash
mihomo-py tun doctor
mihomo-py tun enable --dry-run
mihomo-py tun enable --device mihomo --stack mips --exclude-address 192.168.1.0/24
mihomo-py tun status
mihomo-py tun disable
# 脚本中必须明确确认；--exclude-address 可重复，指定时替换整个列表
mihomo-py tun enable --yes --exclude-address 203.0.113.10/32
mihomo-py tun enable --yes --clear-excludes
```

本机设置新增 `tun: {enabled: false, device: mihomo, stack: mips, exclude_address: []}` 和 `no_proxy`。旧状态和进程记录读取时自动补全；TUN 默认关闭。设备名为 1–15 个字母、数字、点、下划线或连字符；协议栈可选 `mips/system/gvisor/mixed`。IP/CIDR 排除地址会标准化，适合绕过管理连接或内网服务。排除不会自动识别或保证 SSH 连接不中断，请保留独立的恢复通道。

TUN 需要 Linux、iproute2、有效的 CAP_NET_ADMIN 及 `/dev/net/tun` 读写权限；受限容器中的 root 不一定具有这些权限。使用 `mihomo-py --sudo` 启动 TUI，由 sudo 在终端提示密码；也可执行 `mihomo-py --sudo tun doctor` 或 `mihomo-py --sudo core restart`。需要系统安装 sudo 和 capsh（Debian/Ubuntu 包名为 libcap2-bin）。程序授权后保留原用户、数据目录、工作目录和 Python 环境，并向子进程传递 CAP_NET_ADMIN，不设置文件 capabilities，不保存密码。权限持续到进程退出，退出 TUI 不会停止后台内核。每次需要权限时显式传入 `--sudo`；帮助和 `--dry-run` 不请求权限。无交互 stdin 时使用 sudo 非交互模式，认证不可用会直接失败，程序不读取管道中的密码。sudo 认证错误沿用 sudo 的错误输出和退出码。环境继承受 sudo 策略控制，管理员须允许该授权命令。包内内核已锁定到支持 mips 的 v1.19.32；自定义内核由真实配置校验确认兼容性。

启用前检查设备和路由占用、真实配置校验。交互 CLI 和 TUI 显示网络风险确认；非交互 CLI 必须 `--yes`。内核停止时只保存设置，下一次 `core start` 才创建 TUN。运行时先校验再重启，启动失败尝试恢复旧实例；TUN 失败不会自动降级。启用可能影响 SSH 和已有连接；代理不可用时本机网络可能中断。

生成的配置固定 `auto-route: true`、`auto-detect-interface: true`、`auto-redirect: false`、TCP/UDP `any:53` DNS 劫持。保留订阅的解析器、fallback、fake-ip 设置并强制启用 DNS，始终移除 DNS listen。无自定义解析器时沿用内核默认解析器。订阅未配置 `sniffer` 时，TUN 默认启用 HTTP（80、8080–8880）、TLS/QUIC（443、8443）域名嗅探，并用域名替换目标地址。这样 DNS 经过本地 stub 或其他 VPN、未进入 TUN 时，仍可按域名分流。显式订阅嗅探配置保持不变。其他协议、端口及加密隐藏的域名不保证恢复。继续提供 HTTP/SOCKS 混合端口。订阅不能指定其他入站、TUN 接管或防火墙策略。

`config show` 同时提供保存的 `tun` 设置和 `tun_status`；`core status` 提供 `tun` 状态。`configured` 表示保存的开关，`active` 要求受本实例进程持有且已启用的设备、健康内核，以及完整的 IPv4 路由和策略规则；启动时存在 IPv6 路由的实例也要求 IPv6 规则完整。`ipv4/ipv6` 分别报告完整性。规则丢失后，`core start` 会重建实例。`running_device/running_stack` 表示运行中的参数，`restart_required` 提示保存与运行状态不一致。`active` 不证明所有目标都可访问。检查者无权限读取内核的设备描述符时，`inspection_error` 返回 `tun_inspection_denied`；此时 `active: false` 表示无法确认，应使用 `mihomo-py --sudo tun status` 复查。IPv6 取决于订阅的顶层 `ipv6`、宿主网络和实际生成的路由；关闭 IPv6 时不会宣称已接管 IPv6。发往 `127.0.0.53` 等本地 DNS stub 的请求不直接进入 TUN；其上游请求只有经过 TUN 路由且未排除时才会被劫持。DoH/DoT 不属于 53 端口 DNS 劫持范围。

首期同一网络命名空间共用保留路由表 2022 和规则优先级 9000–9020。即使设备名不同，已有 TUN 或外部规则占用这些资源也会明确拒绝；需要并行 TUN 实例时使用独立网络命名空间。不会删除其他实例的设备或规则。普通 HTTP/SOCKS 实例仍可使用不同目录和端口并行运行。

`tun doctor` 检查权限、设备/路由冲突，并在临时目录执行真实内核配置校验，不启动 TUN、不修改当前实例。诊断复制当前订阅的 GEO 缓存和实例的 GEO 覆盖文件，支持已有自定义 GEO 缓存的离线校验；本地 provider 文件须在校验环境中可用。输出 `ok/checks/status/proposed`，检查失败退出码 1。`--device/--stack/--exclude-address/--clear-excludes` 可检查候选设置。`tun enable/disable --dry-run` 只输出 JSON 操作意图，退出码 10，不创建目录或启动进程。

恢复时先运行 `tun disable`；停止内核使用 `core stop`。关闭 TUN 前会确认可读取网络状态，检查失败时保留原内核。正常清理由 mihomo 执行，客户端核验设备及路由是否消失；进程被强制终止时，客户端只会清理 `process.json` 中该实例启动期间记录且仍完全匹配的路由和策略规则，不按设备名或保留编号盲删未知资源。清理需要 CAP_NET_ADMIN，因此异常退出后可运行 `mihomo-py --sudo core stop`，随后重试原操作。替换、启动或保存配置失败时使用同一恢复流程；无法证明归属的残留仍保留进程记录并返回 `tun_cleanup_failed`，不会启动普通代理覆盖证据。此时先用 `tun doctor` 和 `core logs` 检查，再由管理员核对，不要盲目删除主机路由。`tun_permission_denied` 和 `tun_cleanup_permission_denied` 退出码 4，设备或路由冲突退出码 5，设备/协议栈/地址参数错误退出码 2；启动/清理失败退出码 1。错误包含稳定类型、修复建议和 `retryable`。

本期面向本机通过路由进入 TUN 的流量，不提供路由器网关、桌面系统代理或 Web TUN 配置页面。高级参数通过 CLI 设置；TUI 设置页提供开关、状态及 Shell 使用示例。

真实网络验收：`pytest tests/test_tun_network.py -q`。测试使用独立 user/network namespace 和本地 HTTP/DNS 服务，覆盖 IPv4/IPv6、UDP/TCP DNS、本地 stub、地址排除、无权限、冲突、回滚和清理；不改宿主机路由或防火墙。环境需要 `ip/unshare/nsenter/setpriv`、可用的 user namespace 和 `/dev/net/tun`，不满足时明确跳过，发行验收须在支持这些能力的环境完成。
