# Terminal UI

[English](tui.en.md) · [简体中文](tui.md) · [Project home](../README.md)

Run `mihomo-py` in an interactive terminal, or use `mihomo-py tui` explicitly. A terminal of at least **80 × 24** is recommended. Mouse and Tab / Shift-Tab navigation are supported, including over SSH. The UI currently uses Chinese labels; this guide includes their English meanings.

Opening the TUI does not start the core. Exiting leaves a running core in the background. Data refreshes every two seconds; downloads and core operations run in background threads. Wait for an active operation to finish before exiting.

## Subscriptions: add → use → start

1. Click **添加** (Add) or press `Ctrl+A`. Enter a name and an HTTP(S) subscription URL or local YAML path.
2. After validation, the new subscription is highlighted. Press `Enter` or click **使用** (Use).
3. When the core is stopped, focus moves to **启动** (Start). Press `Enter` again or click the button.

Only complete Clash/mihomo YAML configurations are supported. Base64 node lists and individual links such as `ss://` are not converted. Local YAML imports do not copy adjacent provider files; see [configuration and data](cli.en.md#configuration-and-data).

A single click highlights a subscription; a double click or `Enter` applies it. The `●` marker indicates the selected subscription, which can differ from the highlighted row. The overview shows both the saved selection and the running subscription.

Use **更新** (Update), **改来源** (Change source), and **删除** (Delete) to manage subscriptions. Failed downloads or validation preserve the previous configuration. A running subscription cannot be deleted; switch to another one or stop the core first. Deletion requires confirmation.

Applying a different subscription, changed configuration, or settings while running restarts the core and briefly interrupts connections. Progress messages cover fetching, GEO preparation, validation, and saving. F8 opens safe error details; raw core diagnostics remain in the local `validation.log`.

Lists and details redact subscription URL paths and tokens. The source editor displays the full saved URL so that you can change it.

## Settings and Web access

Click **设置** (Settings) to set the proxy port, management port, routing mode, and their independent IPv4 bind addresses. Modes are `rule`, `global`, and `direct`.

- Proxy: defaults to `127.0.0.1:7897`. Binding to `0.0.0.0` allows other devices to use the unauthenticated HTTP/SOCKS proxy; use a trusted network.
- Management API / Web: defaults to `0.0.0.0:9090`, authenticated with a persistent random key. Use a trusted network or bind to `127.0.0.1`.

Install `python -m pip install 'mihomo-py[web]'`, then start or restart the core. **Web 面板** (Web dashboard) shows the address and login key. For remote access, replace the loopback address with the server's IP. Do not share the key. [Web setup](cli.en.md#web-access) also covers browser subscription management.

## Nodes

Press `2` for **节点** (Nodes), choose a proxy group, and press `/` to search by name. Select a row and press `Enter`, or click **切换节点** (Switch node). Only manual Selector groups can be switched; automatic groups support viewing and testing.

Switching a node takes effect immediately without restarting the core. Choices are stored per subscription and restored after core restarts. Refreshing preserves your selected row and scroll position; changing tabs or closing a dialog restores focus.

**测试延迟** (Test latency) tests one selected node. The target defaults to `https://www.gstatic.com/generate_204` and can be changed on the node page. This measures HTTP latency through the node, not bandwidth. The UI shows pending, testing, success, or failure; `i` opens full node details and test errors.

## Logs

Press `3` for **日志** (Logs). The latest 200 lines follow automatically. Scroll up or click **暂停** (Pause) to freeze your reading position. **继续** (Resume) or `End` in the log area loads new lines and resumes following.

Logs may contain addresses from your subscription. Review them before sharing.

## Selection, copying, and dialogs

Drag to select text in details, help, logs, or status areas; press `Ctrl+C` or `Ctrl+Shift+C` to copy. Input selections support the same keys. With no selection, the TUI shows guidance; these keys do not exit. To copy a table row, press `i` and select text in its details.

Copying sends terminal OSC52 and tries available `wl-copy`, `xclip`, or `xsel` helpers. Under tmux it also writes the paste buffer. SSH requires a terminal that permits OSC52 clipboard writes; tmux needs `set -g allow-passthrough on`. Your terminal may handle `Ctrl+Shift+C` itself.

Click outside a dialog or press Esc to close it without saving unsubmitted input. Active forms stay open until the operation finishes. Enter advances between text fields and submits at the last text field. Invalid values stay in the form with focus on the relevant field.

During background work you can still read lists, switch tabs, search, pause logs, and open help. Mutation controls are disabled until the operation completes.

## Shortcuts

| Key | Action |
| --- | --- |
| `1` / `2` / `3` | Subscriptions / Nodes / Logs |
| Tab / Shift-Tab | Next / previous control |
| ↑ / ↓ | Select a row |
| Enter | Apply a subscription, switch a manual node, or activate the focused control |
| `/` | Search nodes |
| Esc | Clear node search or close a dialog |
| `i` | Subscription or node details |
| `?` | Shortcut help |
| `Ctrl+R` | Refresh |
| `Ctrl+A` | Add subscription |
| `Ctrl+C` / `Ctrl+Shift+C` | Copy selected text |
| F8 | Error details |
| End | Resume following in the log area |
| `q` / `Ctrl+Q` | Exit the TUI, leaving the core running |

Inputs retain their normal editing shortcuts; typing in a field does not change tabs. Global tab shortcuts are inactive inside dialogs. `NO_COLOR` is supported; text and symbols also distinguish statuses.
