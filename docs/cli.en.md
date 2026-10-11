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
| `--sudo` | Request TUN permissions through sudo for this invocation, keeping the original user and data directory |
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

The rendered configuration disables subscription-supplied extra inbound ports, listeners/tunnels, DNS/DoH listeners, iptables takeover, NTP, and external UI paths. It preserves nodes, groups, rules, and DNS resolution settings, and enables persistent node choices. TUN is generated only from explicit local settings. Shell proxy commands print statements or set child environments; no systemd service is installed.

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

The TUI also has **Enable Web** and **Disable Web** controls. Enabling starts the gateway as a detached service that remains running after the TUI exits; disabling stops only the gateway. It uses the configured management bind address (default `0.0.0.0`) and port `9091`. Startup errors appear in the TUI; details are in the instance's `web.log`.

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

## Shell proxy and TUN

### Shell proxy

```bash
mihomo-py proxy env
mihomo-py proxy env --format json
mihomo-py proxy env --shell bash  # bash / zsh / fish / posix
eval "$(mihomo-py proxy env --shell bash)"
eval "$(mihomo-py proxy env --unset --shell bash)"
mihomo-py proxy run -- curl https://example.com
mihomo-py config set --no-proxy 'localhost,127.0.0.1,::1,.example.internal'
mihomo-py proxy run --no-proxy '' -- curl https://example.com
```

`proxy env` returns `running`, `host`, `port` and `variables`. It sets upper/lowercase HTTP_PROXY, HTTPS_PROXY, ALL_PROXY and NO_PROXY; the default bypass list is `localhost,127.0.0.1,::1`. HTTP/HTTPS use `http://`, ALL_PROXY uses `socks5h://`. A running instance's actual address takes precedence; when stopped, saved settings are used. A wildcard bind becomes the client destination `127.0.0.1`. `--no-proxy` temporarily replaces the saved bypass list; matching behavior depends on the target program.

Output is safely quoted. Only evaluating the output changes your current Shell. For fish, use `mihomo-py proxy env --shell fish | source`. `--unset` clears the eight variables, without restoring previous values. Stopping the core does not clear the parent Shell's environment. No Shell startup files or persistent environments are edited. Add `--data-dir PATH` before the subcommand for a custom instance.

`proxy run` executes an argument list without a Shell parser, overwrites proxy variables, preserves other environment values, and passes through standard input/output/error and the exit code. Programs must honor proxy environment variables; use TUN for programs that do not. A stopped/unhealthy core produces `core_stopped` with exit code 3. Missing/non-executable commands return 127/126. Child output is never wrapped in JSON.

### TUN activation, inspection and recovery

```bash
mihomo-py tun doctor
mihomo-py tun enable --dry-run
mihomo-py tun enable --device mihomo --stack mips --exclude-address 192.168.1.0/24
mihomo-py tun status
mihomo-py tun disable
# Scripts require explicit confirmation. Repeated exclusions replace the entire list.
mihomo-py tun enable --yes --exclude-address 203.0.113.10/32
mihomo-py tun enable --yes --clear-excludes
```

Local settings add `tun: {enabled: false, device: mihomo, stack: mips, exclude_address: []}` and `no_proxy`. Legacy state/process records receive disabled defaults on read. Device names use 1–15 letters, digits, dots, underscores or hyphens. Supported stacks are `mips/system/gvisor/mixed`. IP/CIDR exclusions are normalized; they can bypass management addresses or private networks. They do not automatically identify or guarantee preservation of SSH connections; keep an independent recovery path.

TUN requires Linux, iproute2, effective CAP_NET_ADMIN and read/write access to `/dev/net/tun`. Root inside a restricted container may lack these permissions. Use `mihomo-py --sudo` to open the TUI with a terminal password prompt managed by sudo. This also works with `tun doctor` and `core restart`. Install sudo and capsh (libcap2-bin on Debian/Ubuntu). Authorization preserves the original user, data directory, working directory and Python environment. CAP_NET_ADMIN passes to child processes until they exit; exiting the TUI leaves the core running. The application never reads or stores passwords or sets file capabilities. Help and `--dry-run` skip authorization. Noninteractive stdin uses sudo noninteractive mode and fails when authentication is unavailable. Authentication errors retain sudo output and exit codes. Environment preservation depends on sudo policy, which must allow this command. Bundled core v1.19.32 supports mips; custom binaries undergo real configuration validation.

Enabling checks device and route conflicts and validates the configuration before changing a running instance. Interactive CLI and TUI require confirmation of networking risks; noninteractive CLI requires `--yes`. When stopped, enabling only saves settings; `core start` creates the TUN later. A failed replacement attempts to restore the old core and settings, without silently downgrading to ordinary proxy mode. TUN may interrupt SSH and existing connections; unavailable proxy nodes may interrupt host connectivity.

Generated policy fixes `auto-route: true`, `auto-detect-interface: true`, `auto-redirect: false` and TCP/UDP `any:53` interception. DNS is enabled while preserving nameservers, fallback and fake-ip options; the DNS listener is always removed. When no custom resolvers are supplied, core defaults apply. When the subscription has no `sniffer` section, TUN enables HTTP (80, 8080–8880) and TLS/QUIC (443, 8443) sniffing with destination replacement. This recovers domain routing when DNS bypasses TUN through a local stub or another VPN. Explicit subscription sniffer settings remain unchanged. Recovery is not guaranteed for other protocols, ports or encrypted names. The mixed HTTP/SOCKS port remains available. Subscriptions cannot override local TUN, inbound or firewall policy.

`config show` includes saved `tun` settings plus `tun_status`; `core status` includes a `tun` status object. `configured` reports the saved switch; `active` requires an enabled device held by the verified core, a healthy core, and complete IPv4 routes and policy rules. Instances with IPv6 routes at startup also require complete IPv6 rules. `ipv4/ipv6` report completeness. After rules disappear, `core start` rebuilds the instance. `running_device/running_stack` describe actual running parameters; `restart_required` indicates a difference from saved settings. Active status does not prove reachability of every destination. If device descriptor inspection is denied, `inspection_error` reports `tun_inspection_denied`; `active: false` then means unverified. Recheck with `mihomo-py --sudo tun status`. IPv6 depends on the subscription's top-level `ipv6`, host networking and observed routes. Requests to local DNS stubs such as `127.0.0.53` remain local; only upstream requests routed into TUN and not excluded can be intercepted. DoH/DoT are outside port-53 DNS interception.

This release reserves routing table 2022 and priorities 9000–9020 in each network namespace. Another TUN or external rules using these resources are rejected even with a different device name. Parallel TUN instances require separate network namespaces. The client never removes another instance's devices/rules. Ordinary proxy instances can still use separate directories and ports.

`tun doctor` checks permissions and device/route conflicts, then validates with the real binary in a temporary directory without starting TUN or modifying the current instance. Diagnostics copy the selected subscription’s GEO cache and instance GEO overrides, allowing offline validation with cached custom datasets. Local provider resources must be available in that validation environment. Output contains `ok/checks/status/proposed`; failed checks exit 1. It accepts `--device/--stack/--exclude-address/--clear-excludes` for candidate settings. `tun enable/disable --dry-run` prints a JSON plan and exits 10 without creating directories or starting processes.

Use `tun disable` to restore ordinary proxy operation, or `core stop` to stop entirely. Disabling TUN first checks network inspection availability. If that check fails, the existing core keeps running. mihomo performs cleanup; the client verifies removal of devices/routes. Replacement and configuration save failures use the same restoration path. Transient cleanup inspection failures after exit are retried. Remaining resources prevent restoration, and the failed process record is retained instead of starting an ordinary proxy. On `tun_cleanup_failed`, inspect `tun doctor` and `core logs`, then have an administrator verify residual resources instead of blindly deleting host routes. Forced termination can leave policy rules; cleanup is not reported as successful when resources remain. Permission errors exit 4; device/route conflicts exit 5; invalid device/stack/address values exit 2; startup/cleanup failures exit 1. Errors carry stable types, repair suggestions and `retryable`.

This release targets local traffic routed into TUN. It does not provide router gateway support, desktop system proxy settings or a Web TUN settings page. The TUI offers a switch, status and copyable Shell examples; advanced parameters use the CLI.

Run `pytest tests/test_tun_network.py -q` for network acceptance. Tests use fresh user/network namespaces and local HTTP/DNS services for IPv4/IPv6, UDP/TCP DNS, local stubs, exclusions, permission failures, conflicts, rollback and cleanup. Host routes/firewalls are untouched. Requires `ip/unshare/nsenter/setpriv`, working user namespaces and `/dev/net/tun`; unavailable environments explicitly skip this test. Release acceptance requires a capable environment.
