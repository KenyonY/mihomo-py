# mihomo-py

面向 Linux 服务器的 mihomo CLI / TUI 客户端，支持订阅管理、节点切换和延迟测试、配置校验、独立本机设置及后台进程管理。

需要 Python 3.11+、Linux（支持 pidfd 的内核，5.3+）和已有的 `mihomo` 可执行文件。支持系统 Python 和 Conda：Python 缺少原生 pidfd 接口时，在 x86_64 / aarch64 的 64 位环境下通过标准库 ctypes 调用相同的 Linux 系统接口，不需要编译器或切换 Python。其他架构需要 Python 原生 pidfd 接口；容器须允许这些系统调用。已用系统 Python 3.12.3、Conda Python 3.12.4、Textual 8.2.8、mihomo v1.19.19 验证（x86_64）。内核安装更新、systemd 和 TUN 是后续阶段。

## 安装与开始使用

在当前 Python 环境（包括 Conda）中安装：

```bash
python -m pip install -e .
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

内核不在 PATH 时，设置 `MIHOMO_PY_BINARY=/path/to/mihomo`，或使用全局选项 `--core-binary`。

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

在交互终端直接运行 `mihomo-py`，或显式执行 `mihomo-py tui`。采用深色紧凑布局、顶部状态分区和单行操作栏；尊重 `NO_COLOR` 设置。建议终端至少 80×24，支持鼠标和键盘 Tab / Shift-Tab 导航。

- **订阅**：添加 URL 或本地 YAML、选择缓存订阅、更新、更换来源、确认删除。添加后选择该行并按 Enter 使用，再点击「启动」。来源明文输入，便于检查和编辑；列表里的远程地址仍脱敏。
- **节点**：启动内核后选择代理组，按名称搜索；选中节点按 Enter 或点击「切换节点」。目前仅手动选择组（Selector）支持切换；其他组可查看和测试延迟。
- **日志**：自动刷新最近 200 行。顶部提供启动、停止、重启及端口/模式设置。

`Ctrl-R` 刷新，`Ctrl-A` 添加订阅（输入框内保留文本编辑快捷键），`q` / `Ctrl-Q` 退出，Esc 关闭弹窗。打开界面不会自动启动代理，退出界面会保留运行中的内核；正在保存或操作内核时需等操作完成再退出。后台状态每 2 秒刷新，网络请求和内核操作不会阻塞界面输入。

添加、更新和更换订阅来源时，界面分别显示读取订阅、准备地理数据、配置校验、应用保存的进度。下载超时与校验超时会给出不同提示；校验失败时不保存新订阅，也不覆盖旧配置。

延迟测试由用户手动触发，默认目标为 `https://www.gstatic.com/generate_204`，可在节点页修改；测试请求经所选节点发出。这是 HTTP 延迟测量，不是下载带宽测试。失败会显示错误，可更换目标或节点后重试。

节点切换立即生效，无需重启；选择按订阅缓存，在内核重启后恢复。若从旧版升级且内核仍在运行，请先重启一次内核，使 `profile.store-selected` 设置生效。

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
core.log          # 内核日志，追加写入
validation.log    # 最近一次内核校验失败或超时的详细输出
geodata/          # 可选：手动放置离线地理数据库，供新订阅复制使用
core-data/        # 按订阅隔离的内核数据、provider 和节点选择缓存
```

目录默认权限 0700；客户端写入的状态、配置和日志文件为 0600。缓存包含订阅凭证，列举订阅时仅展示脱敏地址。

本机设置默认代理端口 `7897`、管理端口 `9090`、路由模式 `rule`。模式可选 `rule/global/direct`。HTTP/SOCKS 共用代理端口，管理接口使用随机密钥；两者只监听本机。当前固定禁用订阅携带的其他入站端口、自定义 listeners/tunnels、TUN、DNS/DoH 监听、iptables 接管、NTP 与外部 UI，保留节点、代理组、规则、DNS 解析配置，并启用节点选择缓存。代理端口不启用用户名密码验证。后续阶段再提供显式的入站和网络接管配置。

订阅原文不会被本机设置改写；更新后重新合成运行配置。下载默认直连，不使用 `HTTP_PROXY/HTTPS_PROXY`；上限 8 MiB、网络操作超时 20 秒。HTTPS 连接在 TCP 或 TLS 失败时尝试域名的其他地址，每个连接/握手阶段最多等待 5 秒，地址尝试共用 20 秒预算；始终校验证书与原始域名。订阅下载超时自动重试一次，并在 TUI 中提示。使用 `mihomo -t` 校验合成配置；内核校验可能下载规则或 GEO 数据，相关行为由订阅配置和 mihomo 决定。内核校验/启动超时可用全局 `--timeout 60` 调整。

校验前会将缺少的地理数据库从本客户端的 `geodata/`、已有 mihomo 的数据目录（通常为 `~/.config/mihomo`）按此优先级复制到订阅目录。支持 MMDB（`Country.mmdb` / `geoip.db` / `geoip.metadb`）、`GeoIP.dat`、`GeoSite.dat`、`ASN.mmdb`，文件名不区分大小写。可用 `MIHOMO_PY_GEODATA_DIR=/path/to/geodata` 显式指定唯一来源。只复制这些数据库，不复制订阅、provider 或节点选择缓存；保留目标已有的有效文件，订阅中显式配置了 `geox-url` 的相应数据库不复用。没有可复用文件时仍由内核下载；MMDB/ASN 在复制前后检查可读性，DAT 内容由内核校验。校验失败会恢复原 GEO 文件。如果提示“订阅已读取，但内核配置校验超时”，说明订阅已下载，阻塞在校验或依赖下载，请查看 `validation.log`。

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
pip install -e '.[dev]'
pytest
ruff check .
python -m build --installer uv
```

真实内核测试在 PATH 中有 mihomo 时运行（也支持 `MIHOMO_TEST_BINARY`）；使用临时目录、空闲端口、直连规则和本地 HTTP 测速目标，不读取现有订阅，不访问机场服务，不改变现有代理。覆盖真实节点 API、选择持久化、TUI 订阅/内核/节点交互，以及 PTY 中的默认入口和退出。缺少内核则跳过并明确显示。

本地 TLS 下载回归测试需要 `openssl` 命令来生成临时测试证书；运行客户端本身无需此命令。

配置字段参考 [mihomo 全局配置](https://wiki.metacubex.one/config/general/)。
