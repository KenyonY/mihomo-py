<div align="center">

# mihomo-py

**Your server's proxies. One terminal.**

Subscriptions, nodes and logs — without leaving SSH.

[![PyPI](https://img.shields.io/pypi/v/mihomo-py?color=8bbbc5)](https://pypi.org/project/mihomo-py/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-8bbbc5)](https://pypi.org/project/mihomo-py/)
![Platform](https://img.shields.io/badge/Linux-x86__64%20%7C%20aarch64-a3afc2)

[English](README.md) · [简体中文](README.zh-CN.md)

</div>

![TUI: subscriptions, core controls and server status](docs/assets/tui-subscriptions.png)

A [mihomo](https://github.com/MetaCubeX/mihomo) client for Linux servers. Add a subscription, start the core, and manage it with your mouse or keyboard. The core and default GEO databases ship with the package.

The screenshots below use the Chinese locale; the same screens are available in English from the language button.

## Install. Open. Done.

```bash
python -m pip install mihomo-py
mihomo-py
```

Requires **Python 3.11+**, **Linux kernel 5.3+** with pidfd support, and **x86_64 / aarch64**. Works with system Python and Conda. Recommended terminal size: **80 × 24** or larger. The TUI follows the system language on first launch and supports Chinese and English; use the language button to switch between Follow system, Chinese, and English.

1. Click **Add**, or press `Ctrl+A`. Enter a name and a subscription URL or local YAML path.
2. Select the subscription and press `Enter` to use it.
3. Click **Start**. Your HTTP/SOCKS proxy is ready at `127.0.0.1:7897` by default.

Subscriptions must be complete Clash/mihomo YAML configurations. Base64 node lists and individual proxy links are not supported.

## Find a node. Switch. Test.

![TUI: proxy groups, node selection and latency testing](docs/assets/tui-nodes.png)

Press `2` for nodes, select a proxy group, and press `/` to search. In a manual group, `Enter` switches the selected node immediately; **Test latency** measures one node. Node choices survive core restarts.

## Follow logs. Keep your place.

![TUI: live logs with pause and resume controls](docs/assets/tui-logs.png)

Press `3` for logs. Scroll up to pause; press `End` to resume following. Press `q` to leave the TUI — **the core keeps running**.

`1 / 2 / 3` switch tabs · `i` opens details · `?` shows shortcuts. [Full TUI guide →](docs/tui.en.md)

## Prefer a browser?

![Web: subscription management using the same configuration as the TUI](docs/assets/web-subscriptions.png)

Install the optional Web resources with `python -m pip install 'mihomo-py[web]'`, then click **Enable Web** in the TUI. The gateway runs as a separate service on port `9091` by default, uses the configured management bind address, and stays running after the TUI exits. Click **Web** for the address and login key; **Settings** can rotate the shared login key. The same gateway manages subscriptions and embeds the node dashboard. [Setup →](docs/cli.en.md#web-access)

## Go further

[CLI & scripting](docs/cli.en.md) · [TUI guide](docs/tui.en.md) · [Documentation](docs/README.md) · [Offline installation](docs/packaging.md)

The proxy listens on `127.0.0.1:7897` by default. The authenticated management API listens on `0.0.0.0:9090`; the optional Web gateway listens on `0.0.0.0:9091`. Use a trusted network or change the bind address in **Settings**. TUN and automatic system proxy setup are outside the current scope.

*Screenshots use an isolated local demo: direct routes, a local latency target, and no real subscription credentials. Bundled resources retain their [upstream notices](src/mihomo_py/_vendor/NOTICE.txt).*
