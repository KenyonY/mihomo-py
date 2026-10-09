# CLI reference

[English](cli.en.md) · [简体中文](cli.md) · [Project home](../README.md)

## Quick start

```bash
mihomo-py --help
mihomo-py sub add work ./config.yaml
mihomo-py sub use work
mihomo-py core start
mihomo-py core status
mihomo-py core logs --follow
mihomo-py core stop
```

HTTP(S) subscription URLs are supported. Read a URL from stdin to keep credentials out of shell history:

```bash
# bash
read -rs -p 'Subscription URL: ' SUB_URL
printf '%s\n' "$SUB_URL" | mihomo-py sub add work -
unset SUB_URL
```

In zsh, use `read -rs 'SUB_URL?Subscription URL: '`. You can also pass a URL directly:
`mihomo-py sub add work 'https://example.com/sub?token=…'`.
Only complete Clash/mihomo YAML configurations are accepted; Base64 node lists and individual proxy links are not converted.

## Global options

Place global options before subcommands: `mihomo-py --json sub list`.

| Option | Purpose |
| --- | --- |
| `--data-dir PATH` | Use a separate instance directory; also configurable with `MIHOMO_PY_HOME` |
| `--core-binary PATH` | Override the bundled core; also configurable with `MIHOMO_PY_BINARY` |
| `--timeout SECONDS` | Core validation / readiness timeout; default 20, range 1–120 |
| `--format json` / `--json` | Explicit JSON output |
| `--format table` | Explicit table / text output |
| `--version` | Print the version |

The bundled core is used by default. PATH is searched only when you explicitly supply a command name such as `--core-binary mihomo`.

## Commands

| Command | Behavior |
| --- | --- |
| `tui` | Open the interactive terminal UI |
| `sub add NAME SOURCE` | Fetch and validate before saving; does not select or start |
| `sub list` | Show selection, redacted sources, and update times |
| `sub set NAME SOURCE` | Replace the source and refresh its cache; failures keep the previous configuration |
| `sub update [NAME]` | Fetch the source again; defaults to the selected subscription |
| `sub use NAME` | Select cached content; validate and restart if running, otherwise only save the selection |
| `sub remove NAME --yes` | Delete the record and cached YAML; the running subscription cannot be deleted |
| `config show` | Show local settings |
| `config set --proxy-port 17897 --controller-port 19090 --mode rule` | Change settings; restart if running |
| `core start` | Start cached content in the background; leave a healthy existing instance running |
| `core restart` | Validate and restart without fetching the subscription |
| `core stop` | Stop this instance; safe to repeat |
| `core status` | Show PID, health, saved and running subscriptions, and settings |
| `core logs [--lines 100] [--follow]` | Read core logs |
| `core web` | Show the dashboard URL and login key; requires `[web]` |
| `web serve` | Run the Web subscription gateway in the foreground; defaults to `0.0.0.0:9091` |
| `web secret` | Show the Web/API login key, even when the core is stopped |
| `node list [--group GROUP]` | List groups, or a group's nodes, selection, and recent delays |
| `node use NAME --group GROUP` | Switch a manual group using full names; no restart |
| `node test NAME [--url URL] [--timeout-ms 5000]` | Test one node's HTTP latency; default timeout 5 seconds |

Subscription mutations, configuration changes, node switching / testing, and core start / restart / stop support `--dry-run`. It emits a JSON plan and exits with **10**. It does not download, validate core configuration, or create directories; a plan does not guarantee execution will succeed. Deletion prompts interactively and requires `--yes` in a pipeline.

## Configuration and data

The default directory is `${XDG_CONFIG_HOME:-~/.config}/mihomo-py`. An empty or relative `XDG_CONFIG_HOME` falls back to `~/.config`. Override it with `MIHOMO_PY_HOME` or `--data-dir`.

```text
state.json         # Sources, cached YAML, selection, and local settings
runtime.yaml       # Cached YAML combined with local settings
process.json       # PID, Linux process identity, and running settings
controller-secret  # Persistent management / Web login key
core.log           # Appended core logs
validation.log     # Latest failed or timed-out core validation
geodata/           # Optional manually supplied offline GEO databases
core-data/         # Per-subscription core data, providers, and node choices
```

Directories created by the client default to 0700; its state, configuration, and log files use 0600. Cached content contains subscription credentials; listing commands redact URL paths and tokens.

| Setting | Default |
| --- | --- |
| Proxy bind / mixed HTTP-SOCKS port | `127.0.0.1:7897` |
| Management API / optional dashboard | `0.0.0.0:9090`, protected by a persistent random key |
| Routing mode | `rule` (`global` and `direct` also supported) |

`config set --host` controls the proxy bind. `--controller-host` controls the management API / dashboard independently. Both accept IPv4 addresses; `0.0.0.0` listens on all interfaces. The proxy has no username/password authentication. Use a trusted network for remote access.

The rendered configuration disables subscription-supplied extra inbound ports, listeners/tunnels, TUN, DNS/DoH listeners, iptables takeover, NTP, and external UI paths. It preserves nodes, groups, rules, and DNS resolution settings, and enables persistent node choices. It does not modify shell proxy settings or install a systemd service.

Original subscription YAML remains unchanged. Downloads connect directly, ignoring `HTTP_PROXY` / `HTTPS_PROXY`, with an 8 MiB limit and a 20-second network timeout. HTTPS retries other resolved addresses after TCP/TLS failures, with up to five seconds per connect/handshake and a shared 20-second budget. Certificates are checked against the original hostname. A subscription download timeout is retried once.

The composed configuration is validated with the real `mihomo -t`. Default GEO resources come from the package; custom GEO URLs and remote providers may still require network access. Adjust the core validation / readiness timeout with `--timeout 60`; inspect `validation.log` after a failure.

Missing GEO files are copied from this instance's `geodata/`, an existing mihomo directory (normally `~/.config/mihomo`), then the bundled snapshot. Supported names include `country.mmdb`, `geoip.db`, `geoip.metadb`, `geoip.dat`, `geosite.dat`, and `ASN.mmdb`, case-insensitively. `MIHOMO_PY_GEODATA_DIR` selects an exclusive source with no fallback. Custom `geox-url` entries are not replaced by default snapshots. Existing valid files are retained; empty files and invalid MMDB files are removed and prepared again before real core validation. Default GEO automatic updates are disabled. [Detailed GEO guide (Chinese)](geodata.md).

Relative provider, rule, and GEO paths resolve inside the subscription's `core-data/<id>/`, not beside an imported YAML file. Importing a local YAML does not copy its dependencies. Prefer inline nodes/rules or accessible remote providers. Removing a subscription preserves derived core data and historical logs.

Updating the running configuration, switching subscriptions, or changing local settings can briefly interrupt connections. Changes are validated before restarting; a failed new start attempts to restore the previous instance. State commits after successful startup. File contents are fsynced and atomically renamed; directory-entry durability across power loss is not promised. Process state and file state cannot form one atomic transaction: `core status` reports saved and running subscriptions separately, and `core start` reapplies the saved selection.

Process management verifies PID, start time, boot identity, and command line, then signals through pidfd. Separate data directories can run independent instances with distinct ports. Linux kernel 5.3+ with pidfd support is required; containers must permit the syscalls. Python builds without native pidfd wrappers use the same Linux interfaces through standard-library `ctypes`.

## Web access

```bash
python -m pip install 'mihomo-py[web]'
mihomo-py core restart  # After selecting a subscription
mihomo-py --format table core web
```

The bundled zashboard node dashboard is available at `http://SERVER_IP:9090/ui/` by default. Use the key printed by `core web` in the Password field and save. For another bind or port:

```bash
mihomo-py config set --controller-host 0.0.0.0 --controller-port 19090
```

The optional Web gateway manages subscriptions and embeds the node dashboard:

```bash
mihomo-py web serve --port 19091
```

Open `http://SERVER_IP:19091/` and log in with the same key. To obtain it with the core stopped, use `mihomo-py --format table web secret` in another terminal. The gateway runs in the foreground; stopping it leaves the core running. Its port must differ from the proxy and core management ports.

The TUI's **Web 面板** button and `core web` point to the gateway while it is running. Web settings can change the shared key; saving restarts a running core, keeps the current browser logged in, and requires other browsers to log in again. A key holder has administrator access to this instance, including reading server YAML paths. Use a trusted LAN or Tailscale. Do not share key-bearing CLI output.

Web resources ship in `mihomo-py-web`; no CDN or Node.js is required at runtime. Bundled dashboard requests stay on the local service. Subscription updates, custom providers, and user-triggered latency tests still use their configured destinations. [Node dashboard details (Chinese)](web.md) · [Web gateway details (Chinese)](subscriptions-web.md).

## Scripting and errors

Subcommands default to tables/text on an interactive terminal and JSON in a pipeline. With no subcommand, an interactive terminal opens the TUI; a pipeline or explicit output format returns status. `tui` requires an interactive terminal.

Data goes to stdout; structured errors go to stderr. Ordinary subcommands emit no ANSI color codes. `core logs --follow` produces NDJSON in JSON mode. `--help` and `--version` always produce text; CLI help and diagnostic messages currently use Chinese.

```bash
mihomo-py --json sub list
mihomo-py --json core status
mihomo-py config set --mode direct --dry-run
```

| Exit code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | IO, download, startup, or other runtime error |
| 2 | Invalid arguments or configuration |
| 3 | Subscription, core, or log not found |
| 4 | Permission denied |
| 5 | Existing resource, resource in use, port conflict, or concurrent modification |
| 10 | Dry-run plan emitted; nothing executed |
| 130 | User cancellation |

Error shape: `{"error":"not_found","message":"…","suggestion":"…","retryable":false}`.

## Development and verification

From a source checkout:

```bash
python -m pip install -e ./web -e '.[dev,web]'
pytest
ruff check .
python -m build --installer uv
```

Real core tests use the bundled binary or `MIHOMO_TEST_BINARY`. They use temporary directories, free ports, direct routes, and local HTTP targets; they do not read existing subscriptions or alter your proxy. TLS download regression tests require `openssl` to generate temporary certificates; running the client does not.

[Offline packaging and release verification (Chinese)](packaging.md) · [Documentation index](README.md).
