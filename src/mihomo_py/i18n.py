"""Chinese and English text for the terminal UI."""

# Translation entries intentionally keep complete sentences on one logical line.
# ruff: noqa: E501

import locale
import os

from .errors import Message

MESSAGES = {
    "follow": ("继续跟随", "Resume"),
    "close": ("关闭", "Close"),
    "error_details": ("错误详情", "Error details"),
    "cancel": ("取消", "Cancel"),
    "confirm": ("确定", "OK"),
    "quit": ("退出", "Quit"),
    "copy": ("复制选区", "Copy selection"),
    "refresh": ("刷新", "Refresh"),
    "add_sub": ("添加订阅", "Add subscription"),
    "subscriptions": ("订阅", "Subscriptions"),
    "nodes": ("节点", "Nodes"),
    "logs": ("日志", "Logs"),
    "search": ("搜索", "Search"),
    "clear_search": ("清空搜索", "Clear search"),
    "details": ("详情", "Details"),
    "help": ("帮助", "Help"),
    "app_subtitle": ("服务器代理管理", "Server proxy manager"),
    "brand": ("mihomo  /  代理管理", "mihomo  /  Proxy manager"),
    "edition": ("本机 · mihomo-py", "Local · mihomo-py"),
    "kernel": ("内核", "Core"),
    "reading": ("○  读取中", "○  Loading"),
    "subscription_metric": ("订阅 · 已选 / 运行", "Subscription · selected / running"),
    "proxy_metric": ("本机代理", "Local proxy"),
    "mode_metric": ("路由模式", "Routing mode"),
    "start": ("启动", "Start"),
    "stop": ("停止", "Stop"),
    "restart": ("重启", "Restart"),
    "settings": ("设置", "Settings"),
    "web": ("Web 面板", "Web"),
    "web_start": ("开启 Web", "Enable Web"),
    "web_stop": ("关闭 Web", "Disable Web"),
    "sub_hint": (
        "订阅库   /   ↑↓ 选择，双击或 Enter 使用",
        "Subscriptions   /   ↑↓ select, double-click or Enter to use",
    ),
    "sub_empty": (
        "还没有订阅\n\n添加订阅 URL 或本地 YAML，开始配置代理\n\nCtrl+A 添加订阅",
        "No subscriptions yet\n\nAdd a subscription URL or local YAML to configure the proxy\n\nCtrl+A to add a subscription",
    ),
    "add": ("添加", "Add"),
    "use": ("使用", "Use"),
    "update": ("更新", "Update"),
    "edit": ("改来源", "Change source"),
    "remove": ("删除", "Delete"),
    "sub_col": ("订阅", "Subscription"),
    "source_col": ("来源", "Source"),
    "updated_col": ("更新于", "Updated"),
    "group_prompt": ("选择代理组", "Choose proxy group"),
    "node_search": ("/ 搜索节点名称", "/ Search node names"),
    "group_hint": ("启动内核后显示节点", "Nodes appear after the core starts"),
    "node_col": ("节点", "Node"),
    "type_col": ("类型", "Type"),
    "delay_col": ("延迟", "Latency"),
    "node_empty": (
        "选择订阅并启动内核后，即可查看节点",
        "Select a subscription and start the core to view nodes",
    ),
    "select_node": ("切换节点", "Switch node"),
    "test_latency": ("测试延迟", "Test latency"),
    "target": ("目标", "Target"),
    "latency_url": ("延迟测试 URL", "Latency test URL"),
    "log_hint": ("最近 200 行 · 跟随中", "Latest 200 lines · following"),
    "pause": ("暂停", "Pause"),
    "resume": ("继续", "Resume"),
    "no_logs": ("尚无日志", "No logs yet"),
    "keep_core": ("退出界面会保留运行中的内核。", "Exiting the UI leaves a running core in place."),
    "no_selection": (
        "没有选中内容：鼠标拖选文本后再复制；退出请按 q 或 Ctrl+Q。",
        "Nothing selected: drag over text with the mouse to copy; press q or Ctrl+Q to exit.",
    ),
    "copied": (
        "已发送选中的 {count} 个字符到剪贴板。",
        "Sent {count} selected characters to the clipboard.",
    ),
    "keyboard_help_title": ("键盘操作", "Keyboard shortcuts"),
    "help_text": (
        "1 / 2 / 3    订阅 / 节点 / 日志\nTab / Shift-Tab    下一个 / 上一个控件\n↑ / ↓    选择列表行\nEnter    使用订阅 / 切换手动组节点\n/    搜索节点；Esc 清空并返回列表\ni    查看当前行完整信息\nCtrl-R    刷新\nCtrl-A    添加订阅\nF8    查看完整错误\nEnd    日志恢复跟随\n?    打开帮助\nEsc    关闭弹窗\nCtrl-C / Ctrl-Shift-C    复制选中的文本\nq / Ctrl-Q    退出（保留内核）\n\n输入框保留文本编辑快捷键；弹窗内不切换页面。\n● 表示已选订阅 / 生效节点，高亮背景表示正在浏览的行。\n订阅单击选择，双击、Enter 或使用按钮执行；运行中切换会重启内核。\n点击弹窗外部可关闭；执行中的表单需等待完成。\n日志可能包含订阅内部地址。",
        "1 / 2 / 3    Subscriptions / Nodes / Logs\nTab / Shift-Tab    Next / previous control\n↑ / ↓    Select a list row\nEnter    Use a subscription / switch a manual group node\n/    Search nodes; Esc clears search and returns to the list\ni    View full details for the current row\nCtrl-R    Refresh\nCtrl-A    Add a subscription\nF8    View full error details\nEnd    Resume following logs\n?    Open help\nEsc    Close a dialog\nCtrl-C / Ctrl-Shift-C    Copy selected text\nq / Ctrl-Q    Exit (core keeps running)\n\nText inputs keep their editing shortcuts; dialogs do not change pages.\n● marks the selected subscription / active node; a highlighted background marks the row being browsed.\nSingle-click subscriptions to select; double-click, Enter, or the button applies them. Switching while running restarts the core.\nClick outside a dialog to close it; active forms must finish before closing.\nLogs may contain internal subscription addresses.",
    ),
    "sub_details": ("订阅详情", "Subscription details"),
    "node_details": ("节点详情", "Node details"),
    "name": ("名称", "Name"),
    "source": ("来源", "Source"),
    "updated": ("更新于", "Updated"),
    "selected_sub": ("已选订阅", "Selected subscription"),
    "running_sub": ("运行订阅", "Running subscription"),
    "group": ("代理组", "Proxy group"),
    "type": ("类型", "Type"),
    "active": ("当前生效", "Active"),
    "yes": ("是", "Yes"),
    "no": ("否", "No"),
    "delay": ("延迟", "Latency"),
    "log_following": ("跟随中", "Following"),
    "log_paused": ("已暂停 · End 继续", "Paused · End to resume"),
    "log_updated": ("有更新", "updated"),
    "log_recent": ("最近 200 行 · {state}", "Latest 200 lines · {state}"),
    "testing": ("测试中…", "Testing…"),
    "failed": ("失败", "Failed"),
    "pending": ("待测", "Pending"),
    "node_list": ("节点列表", "Node list"),
    "api_unavailable": ("内核 API 不可用", "Core API unavailable"),
    "core_stopped": ("内核已停止", "Core stopped"),
    "no_groups": ("暂无可用代理组", "No proxy groups"),
    "check_config": ("请检查订阅配置后刷新", "Check the subscription configuration and refresh"),
    "start_to_view": (
        "选择订阅并启动内核后，即可查看节点",
        "Select a subscription and start the core to view nodes",
    ),
    "no_match": (
        "没有匹配的节点\n\nEsc 清空搜索，或尝试其他名称",
        "No matching nodes\n\nEsc clears the search, or try another name",
    ),
    "enter_switch": ("Enter 切换", "Enter to switch"),
    "auto_view": ("自动选择 · 仅查看/测速", "Automatic selection · view/test only"),
    "nodes_count": (
        "{shown}/{total} 节点 · 当前 {current} · {action}",
        "{shown}/{total} nodes · current {current} · {action}",
    ),
    "group_current": (
        "{type} · 当前：{current} · {action}",
        "{type} · current: {current} · {action}",
    ),
    "use_restart": ("使用并重启", "Use and restart"),
    "use_tip_restart": (
        "应用此订阅，将重启内核并短暂中断连接。",
        "Applying this subscription restarts the core and briefly interrupts connections.",
    ),
    "use_tip": (
        "应用此订阅；内核停止时不会自动启动。",
        "Applies this subscription; it does not start a stopped core.",
    ),
    "update_apply": ("更新并应用", "Update and apply"),
    "restart_warning": (
        "配置有变化时将重启内核并短暂中断连接。",
        "A configuration change restarts the core and briefly interrupts connections.",
    ),
    "update_tip": ("读取来源并校验、保存订阅。", "Read, validate, and save the source."),
    "edit_tip": ("修改来源并重新读取、校验订阅。", "Change the source, then read and validate it."),
    "action_use_restart": ("双击/Enter 使用并重启", "Double-click/Enter to use and restart"),
    "action_use": ("双击/Enter 使用", "Double-click/Enter to use"),
    "update_hint": (
        "{count} 个订阅 · 单击选择 · {action}{warning} · i 详情",
        "{count} subscriptions · click to select · {action}{warning} · i for details",
    ),
    "update_restart_hint": (" · 更新变更将重启", " · updates restart the core"),
    "operation_done": ("{label}完成", "{label} complete"),
    "delay_result": ("{name}：{delay} ms", "{name}: {delay} ms"),
    "progress_done": (
        "操作正在完成，请稍后退出。",
        "The operation is still running; wait before exiting.",
    ),
    "state_healthy": ("运行正常", "Running"),
    "state_pending": ("运行中 / API 未就绪", "Running / API not ready"),
    "state_stopped": ("已停止", "Stopped"),
    "api_pending": ("API 未就绪", "API not ready"),
    "status_tooltip": ("{state} · PID {pid}", "{state} · PID {pid}"),
    "selected_running": (
        "已选：{selected} · 运行：{running}",
        "Selected: {selected} · Running: {running}",
    ),
    "add_dialog": ("添加订阅", "Add subscription"),
    "sub_name": ("订阅名称", "Subscription name"),
    "sub_source": ("订阅 URL 或本地 YAML 路径", "Subscription URL or local YAML path"),
    "added": ("已添加订阅，按 Enter 使用。", "Subscription added. Press Enter to use it."),
    "switch_sub": ("切换订阅", "Switch subscription"),
    "sub_used_start": (
        "订阅已使用，按 Enter 启动内核。",
        "Subscription selected. Press Enter to start the core.",
    ),
    "switch_node": ("切换节点", "Switch node"),
    "remove_confirm": (
        "删除订阅 {name} 及其缓存原文？",
        "Delete subscription {name} and its cached source?",
    ),
    "settings_title": (
        "本机设置（运行中应用会重启内核）",
        "Local settings (applying while running restarts the core)",
    ),
    "proxy_port": ("代理端口", "Proxy port"),
    "controller_port": ("管理 / Web 端口", "Management / Web port"),
    "mode": ("模式：rule / global / direct", "Mode: rule / global / direct"),
    "proxy_host": (
        "代理监听 IPv4 地址（0.0.0.0 为所有接口）",
        "Proxy IPv4 bind address (0.0.0.0 for all interfaces)",
    ),
    "controller_host": (
        "管理 / Web IPv4 地址（0.0.0.0 为所有接口）",
        "Management / Web IPv4 bind address (0.0.0.0 for all interfaces)",
    ),
    "mode_rule": ("规则 · rule", "Rule · rule"),
    "mode_global": ("全局 · global", "Global · global"),
    "mode_direct": ("直连 · direct", "Direct · direct"),
    "edit_source_title": ("修改来源：{name}", "Change source: {name}"),
    "restart_config": ("（配置变更将重启内核）", " (configuration change restarts the core)"),
    "web_title": ("Web 面板（密钥请勿分享）", "Web dashboard (do not share the key)"),
    "listen": ("监听", "Listen"),
    "login_secret": ("登录密钥", "Login key"),
    "web_remote": (
        "远程访问请在设置中修改管理 / Web 监听地址。",
        "Change the management / Web bind address in Settings for remote access.",
    ),
    "language_button": ("语言：{name}", "Language: {name}"),
    "language_button_auto": ("语言：{name}（跟随系统）", "Language: {name} (system)"),
    "language_title": ("界面语言", "Interface language"),
    "language_field": ("语言", "Language"),
    "language_auto": ("跟随系统", "Follow system"),
    "language_zh": ("中文", "中文"),
    "language_en": ("English", "English"),
    "language_save": ("保存语言", "Save language"),
    "language_saved": ("语言已更新。", "Language updated."),
    "language_name_zh": ("中文", "Chinese"),
    "language_name_en": ("English", "English"),
    "language_system": ("系统语言", "System language"),
    "form_error": ("检查表单", "Check form"),
    "fill_field": ("请填写此字段。", "Please fill in this field."),
    "form_operation": ("界面操作", "Form validation"),
    "error_operation": ("操作", "Operation"),
    "error_type": ("异常", "Exception"),
    "error_code": ("错误代码", "Error code"),
    "cause": ("原因异常", "Caused by"),
    "system_error": ("系统错误", "System error"),
    "system_reason": ("系统原因", "System reason"),
    "local_io_error": (
        "本地 IO 操作失败（errno={errno}）。",
        "Local I/O operation failed (errno={errno}).",
    ),
    "operation_failed": (
        "操作失败（{type}），请重试。",
        "Operation failed ({type}). Please try again.",
    ),
    "invalid_state": (
        "state.json 损坏或版本不支持，请从备份恢复。",
        "state.json is corrupted or uses an unsupported version. Restore it from a backup.",
    ),
    "reading_source": ("正在读取订阅来源…", "Reading subscription source…"),
    "validating": ("订阅已读取，正在校验配置…", "Subscription read; validating configuration…"),
    "validation_saved": (
        "配置校验通过，正在应用并保存订阅…",
        "Configuration validated; applying and saving…",
    ),
    "stale_refresh": ("内核状态已变化，正在刷新。", "The core state changed; refreshing."),
    "removed_refresh": (
        "订阅已移除，请刷新后重试。",
        "The subscription was removed. Refresh and try again.",
    ),
    "ipv4_error": (
        "须为 IPv4 地址，例如 127.0.0.1 或 0.0.0.0。",
        "Use an IPv4 address, for example 127.0.0.1 or 0.0.0.0.",
    ),
    "port_error": ("端口须为 1–65535 的整数。", "The port must be an integer from 1 to 65535."),
    "ports_different": (
        "代理端口和管理端口必须不同。",
        "The proxy and management ports must be different.",
    ),
    "web_hint": (
        "从其他设备访问时，将 URL 中的地址换成服务器 IP；登录时填写此密钥。",
        "Replace the URL host with the server IP when connecting from another device; enter this key to log in.",
    ),
    "web_local_hint": (
        "此入口提供订阅管理与节点面板；远程访问时换成服务器 IP。",
        "This entry provides subscription and node management; use the server IP for remote access.",
    ),
    "geodata": ("订阅已读取，正在准备地理数据…", "Subscription read; preparing geodata…"),
    "validating_reused": (
        "已复用 {count} 个地理数据文件，正在校验配置…",
        "Reused {count} geodata files; validating configuration…",
    ),
    "retrying": (
        "订阅连接或读取超时，正在重试一次…",
        "The subscription timed out while connecting or reading; retrying once…",
    ),
}

BACKEND_MESSAGES = {
    **{chinese: english for chinese, english in MESSAGES.values()},
    "包内 Web 面板校验失败，请重新安装发行包。": "The bundled Web dashboard failed verification. Reinstall the package.",
    "包内 Web 面板缺少入口文件。": "The bundled Web dashboard is missing its entry file.",
    "安装包缺少当前架构的 mihomo 内核。": "The package is missing a mihomo core for this architecture.",
    "请从 pip 镜像源重新安装完整发行包，或用 --core-binary 指定内核。": "Reinstall the complete distribution from your pip mirror, or specify a core with --core-binary.",
    "找不到 mihomo 内核。": "The mihomo core could not be found.",
    "检查 --core-binary / MIHOMO_PY_BINARY；省略时使用包内 mihomo。": "Check --core-binary / MIHOMO_PY_BINARY; the bundled core is used when neither is set.",
    "进程管理需要支持 pidfd 的 Linux 环境。": "Process management requires Linux with pidfd support.",
    "当前系统无法使用 pidfd 进程控制（errno={errno}）。": "This system cannot use pidfd process control (errno={errno}).",
    "需要 Linux 5.3+ 且容器允许 pidfd 系统调用；Python 缺少原生接口时，兼容层支持 x86_64/aarch64 64 位环境。": "Linux 5.3+ is required, and containers must allow pidfd system calls. When Python has no native interface, the compatibility layer supports 64-bit x86_64/aarch64.",
    "订阅已读取，但内核配置校验超时；订阅尚未保存，原配置未替换。": "The subscription was read, but core validation timed out. The subscription was not saved and the original configuration was not replaced.",
    "默认地理数据已随包提供；检查订阅的自定义数据或规则源是否可达。可通过 MIHOMO_PY_GEODATA_DIR 提供自定义离线地理数据；详见 {path}。": "Default geodata is bundled. Check whether the subscription's custom data or rule sources are reachable. Use MIHOMO_PY_GEODATA_DIR for custom offline geodata; see {path}.",
    "mihomo 拒绝此配置，原配置未替换。": "mihomo rejected this configuration; the original was not replaced.",
    "查看本地诊断文件：{path}": "See the local diagnostic file: {path}",
    "process.json 损坏，无法确认进程身份。": "process.json is corrupted; the process identity cannot be verified.",
    "内核尚未退出，请检查进程状态。": "The core has not exited. Check the process status.",
    "端口 {port} 不可用。": "Port {port} is unavailable.",
    "用 config set 指定空闲的代理端口和管理端口。": "Use config set to specify available proxy and management ports.",
    "内核退出或未能确认启动身份。": "The core exited or its startup identity could not be verified.",
    "内核未在期限内就绪。": "The core did not become ready before the deadline.",
    "运行 core logs 查看日志。": "Run core logs to view the logs.",
    "controller-secret 无效，请恢复备份。": "controller-secret is invalid. Restore a backup.",
    "启动失败，恢复旧实例也失败。订阅状态未修改。": "Startup and restoration of the previous instance both failed. Subscription state was not changed.",
    "检查 core logs 后重新 core start。": "Check core logs, then run core start again.",
    "内核下载的地理数据库 {name} 无效；订阅尚未保存。": "The downloaded geodata file {name} is invalid; the subscription was not saved.",
    "检查 geox-url 来源返回的数据库文件是否完整。": "Check whether the database returned by the geox-url source is complete.",
    "MIHOMO_PY_GEODATA_DIR 不是可读取的目录。": "MIHOMO_PY_GEODATA_DIR is not a readable directory.",
    "未找到订阅或尚未选择订阅。": "No subscription was found or selected.",
    "用 sub list 查看，再用 sub use 选择。": "Use sub list to view subscriptions, then sub use to select one.",
    "内核未运行。": "The core is not running.",
    "先运行 core start。": "Run core start first.",
    "尚未安装 Web 面板资源。": "The Web dashboard resources are not installed.",
    "运行 pip install 'mihomo-py[web]'，然后 core restart。": "Run pip install 'mihomo-py[web]', then core restart.",
    "当前内核尚未加载 Web 面板。": "The running core has not loaded the Web dashboard.",
    "安装 [web] 后运行 core restart，再打开 Web 面板。": "After installing [web], run core restart, then open the Web dashboard.",
    "密钥须为 1–256 个可见 ASCII 字符，不含空格。": "The key must contain 1–256 printable ASCII characters without spaces.",
    "密钥保存失败，恢复旧实例也失败。": "Saving the key and restoring the previous instance both failed.",
    "订阅 {name} 已存在。": "Subscription {name} already exists.",
    "用 sub set 修改来源。": "Use sub set to change the source.",
    "无法删除正在使用的订阅。": "A subscription that is in use cannot be deleted.",
    "先 core stop 或 sub use 其他订阅。": "Run core stop, or use sub use to select another subscription first.",
    "监听地址须为 IPv4 地址，例如 0.0.0.0。": "The bind address must be an IPv4 address, for example 0.0.0.0.",
    "名称限 1–64 个字母、数字、中文、下划线、点或连字符。": "Names must contain 1–64 letters, numbers, Chinese characters, underscores, dots, or hyphens.",
    "订阅地址必须是无内嵌用户名和密码的 HTTP(S) URL。": "The subscription address must be an HTTP(S) URL without embedded credentials.",
    "订阅下载失败（HTTP {status}）。": "Subscription download failed (HTTP {status}).",
    "检查订阅地址是否过期；使用 sub set 修改地址。": "Check whether the subscription address has expired; use sub set to change it.",
    "读取订阅来源超时，尚未取得完整配置。": "Reading the subscription source timed out; the complete configuration has not been received.",
    "连接订阅来源超时，尚未取得配置。": "Connecting to the subscription source timed out; no configuration was received.",
    "无法读取订阅来源。请检查文件或网络。": "Unable to read the subscription source. Check the file or network.",
    "订阅为空或超过 8 MiB。": "The subscription is empty or exceeds 8 MiB.",
    "配置必须是 UTF-8 YAML。": "The configuration must be UTF-8 YAML.",
    "无法解析 YAML 配置。": "Unable to parse the YAML configuration.",
    "需要 Clash/Mihomo YAML 配置，不支持节点 URI 列表。": "A Clash/Mihomo YAML configuration is required; node URI lists are not supported.",
    "profile 必须是 YAML 对象。": "profile must be a YAML object.",
    "先启动内核，再查看或切换节点。": "Start the core before viewing or switching nodes.",
    "内核实例已变化，请刷新后重新操作。": "The core instance changed. Refresh before trying again.",
    "内核 API 请求失败（HTTP {status}）。": "Core API request failed (HTTP {status}).",
    "刷新后重试；测速失败可更换测试地址或节点。": "Refresh and try again; if the latency test fails, try another target or node.",
    "无法连接内核 API 或请求超时。": "Unable to connect to the core API, or the request timed out.",
    "内核 API 返回了无效 JSON。": "The core API returned invalid JSON.",
    "内核 API 缺少 proxies 数据。": "The core API response is missing proxies data.",
    "内核节点数据无效。": "The core node data is invalid.",
    "内核代理组数据无效。": "The core proxy group data is invalid.",
    "未找到此代理组。": "The proxy group was not found.",
    "当前仅支持手动选择组（Selector）的节点切换。": "Only manual Selector groups support node switching.",
    "节点不属于此代理组，请刷新列表。": "The node does not belong to this proxy group. Refresh the list.",
    "内核未确认节点切换，请刷新检查。": "The core did not confirm the node switch. Refresh and check.",
    "测速地址必须是 HTTP(S) URL。": "The latency test URL must be an HTTP(S) URL.",
    "测速超时须在 1–30000 毫秒内。": "The latency timeout must be between 1 and 30000 milliseconds.",
    "未找到此节点。": "The node was not found.",
    "内核未返回有效的延迟结果。": "The core did not return a valid latency result.",
    "另一个命令正在修改此实例，请稍后重试。": "Another command is modifying this instance. Try again later.",
    "尚未安装 Web 资源。": "The Web resources are not installed.",
    "pip install 'mihomo-py[web]'。": "Run pip install 'mihomo-py[web]'.",
    "Web 服务端口须与管理 API、代理端口不同。": "The Web service port must differ from the management API and proxy ports.",
    "Web 服务未能启动，请检查 web.log。": "The Web service failed to start. Check web.log.",
    "运行 mihomo-py web serve 可查看前台错误。": "Run mihomo-py web serve to see foreground errors.",
    "Web 服务尚未退出，请检查进程状态。": "The Web service has not exited. Check the process status.",
    "此实例已有 Web 管理服务运行。": "A Web management service is already running for this instance.",
    "登录密钥已修改，请重新登录。": "The login key changed. Sign in again.",
    "需要 JSON 对象。": "A JSON object is required.",
    "字段须为非空字符串。": "Each field must be a non-empty string.",
    "TUI 需要交互式终端。请使用子命令或 core status。": "The TUI requires an interactive terminal. Use a subcommand or core status.",
    "订阅来源不能为空。": "The subscription source cannot be empty.",
    "请通过管道从 stdin 提供订阅地址或文件路径。": "Provide the subscription URL or file path through stdin.",
    "删除订阅需要 --yes。": "Deleting a subscription requires --yes.",
    "尚无内核日志。": "No core logs are available yet.",
    "操作已取消。": "Operation cancelled.",
    "无权限读取或修改所需文件、端口或进程。": "Permission denied while accessing the required files, ports, or processes.",
    "尚未安装 Web 服务依赖。": "The Web service dependencies are not installed.",
}


def detect_system_language():
    """Respect locale precedence; unsupported or neutral locales use Chinese."""
    value = next(
        (
            os.environ[name]
            for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG")
            if os.environ.get(name)
        ),
        None,
    )
    if value is None:
        try:
            value = locale.getlocale(locale.LC_MESSAGES)[0]
        except (AttributeError, ValueError, locale.Error):
            value = None
    # LANGUAGE may contain a colon-separated list of preferred locales.
    language = (value or "").split(":", 1)[0].replace("-", "_").split("_", 1)[0]
    language = language.split(".", 1)[0].lower()
    return "en" if language == "en" else "zh"


def resolve_language(preference):
    return preference if preference in ("zh", "en") else detect_system_language()


def localized_text(language, key, **values):
    text = MESSAGES[key][0 if language == "zh" else 1]
    return text.format(**values)


def translated_message(language, message):
    if language == "zh":
        return str(message)
    template = message.template if isinstance(message, Message) else message
    text = BACKEND_MESSAGES.get(template, template)
    return text.format(**message.values) if isinstance(message, Message) else text
