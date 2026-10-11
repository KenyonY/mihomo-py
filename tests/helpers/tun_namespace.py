"""Executed only inside a fresh user + network namespace by test_tun_network.py."""

import http.client
import json
import os
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from mihomo_py import pidfd, tun
from mihomo_py.errors import AppError
from mihomo_py.manager import Manager


def ip(*args):
    subprocess.run(["ip", *args], check=True, capture_output=True)


def tun_packets():
    result = subprocess.check_output(["ip", "-j", "-s", "link", "show", "dev", "mihomo"])
    value = json.loads(result)[0]
    return value.get("stats64", value.get("stats"))["tx"]["packets"]


def dns_query(host, tcp=False, qtype=1):
    packet = struct.pack("!HHHHHH", 123, 256, 1, 0, 0, 0)
    packet += b"".join(bytes([len(x)]) + x.encode() for x in "tun-test.example.org".split("."))
    packet += b"\0" + struct.pack("!HH", qtype, 1)
    with socket.socket(type=socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as client:
        client.settimeout(4)
        client.connect((host, 53))
        if tcp:
            client.sendall(struct.pack("!H", len(packet)) + packet)
            size = struct.unpack("!H", client.recv(2))[0]
            response = b""
            while len(response) < size:
                response += client.recv(size - len(response))
        else:
            client.send(packet)
            response = client.recv(4096)
    assert response[:2] == packet[:2] and response[3] & 15 == 0, response
    return socket.inet_ntop(
        socket.AF_INET6 if qtype == 28 else socket.AF_INET,
        response[-16:] if qtype == 28 else response[-4:],
    )


def dns_server(host, port, stub=False):
    server = socket.socket(type=socket.SOCK_DGRAM)
    server.bind((host, port))

    def serve():
        while True:
            try:
                data, client = server.recvfrom(4096)
                qtype = struct.unpack("!H", data[-4:-2])[0]
                address = socket.inet_pton(
                    socket.AF_INET6 if qtype == 28 else socket.AF_INET,
                    "2001:db8:2::2" if qtype == 28 else ("192.0.2.42" if stub else "198.51.100.2"),
                )
                reply = data[:2] + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0) + data[12:]
                reply += b"\xc0\x0c" + struct.pack("!HHIH", qtype, 1, 60, len(address)) + address
                server.sendto(reply, client)
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    return server


PEER = """
import socket, threading, sys, ssl, subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
print("ready", flush=True)
sys.stdin.readline()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"isolated HTTP response")
    def log_message(self, *args): pass
class IPv6Server(ThreadingHTTPServer): address_family = socket.AF_INET6
for cls,host in [(ThreadingHTTPServer,"198.51.100.2"),(IPv6Server,"2001:db8:2::2")]:
    server=cls((host,18080),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
cert, key = sys.argv[1] + '/cert.pem', sys.argv[1] + '/key.pem'
subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
    '-keyout',key,'-out',cert,'-subj','/CN=tun-test.example.org','-days','1'],
    check=True, capture_output=True)
for port in (80,443):
    server = ThreadingHTTPServer(('198.51.100.2',port),Handler)
    if port == 443:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert,key)
        server.socket = ctx.wrap_socket(server.socket,server_side=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
print("serving",flush=True)
sys.stdin.read()
"""


def main():
    # No default routes, interfaces, or firewall rules are changed outside this namespace.
    assert len(tun.network()["links"]) == 1
    ip("link", "set", "lo", "up")
    temporary = tempfile.TemporaryDirectory()
    peer = subprocess.Popen(
        ["unshare", "--net", sys.executable, "-u", "-c", PEER, temporary.name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    manager = None
    try:
        assert peer.stdout.readline().strip() == "ready"
        ip("link", "add", "uplink", "type", "veth", "peer", "name", "peer0")
        ip("link", "set", "peer0", "netns", str(peer.pid))

        def peer_ip(*args):
            subprocess.run(
                ["nsenter", "-t", str(peer.pid), "-n", "ip", *args], check=True, capture_output=True
            )

        for run, device, v4, v6 in [
            (ip, "uplink", "192.0.2.1", "fd00:123::1"),
            (peer_ip, "peer0", "192.0.2.2", "fd00:123::2"),
        ]:
            run("link", "set", "lo", "up")
            run("address", "add", v4 + "/24", "dev", device)
            run("-6", "address", "add", v6 + "/64", "dev", device, "nodad")
            run("link", "set", device, "up")
        ip("route", "add", "default", "via", "192.0.2.2")
        ip("-6", "route", "add", "default", "via", "fd00:123::2")
        peer_ip("address", "add", "198.51.100.2/32", "dev", "lo")
        peer_ip("-6", "address", "add", "2001:db8:2::2/128", "dev", "lo", "nodad")
        peer_ip("route", "add", "default", "via", "192.0.2.1")
        peer_ip("-6", "route", "add", "default", "via", "fd00:123::1")
        peer.stdin.write("start\n")
        peer.stdin.flush()
        assert peer.stdout.readline().strip() == "serving"
        upstream = dns_server("127.0.0.1", 1053)
        stub = dns_server("127.0.0.53", 53, stub=True)
        with nullcontext(temporary.name) as directory:
            root = Path(directory)
            source = root / "source.yaml"
            source.write_text(
                'proxies: []\nrules: ["MATCH,DIRECT"]\nipv6: true\n'
                "dns: {enable: false, ipv6: true, nameserver: [127.0.0.1:1053]}\n"
            )
            manager = Manager(root / "home", timeout=5)
            manager.put_sub("a", str(source), create=True)
            manager.use("a")
            before = manager.start()
            # Never overwrite evidence of a failed TUN with an ordinary core.
            observe_initial = tun.observation
            killed = []

            def kill_new_tun(record, net=None):
                observed = observe_initial(record, net)
                if observed["active"] and not killed:
                    descriptor = pidfd.open_pidfd(record["pid"])
                    try:
                        pidfd.send_signal(descriptor, signal.SIGKILL)
                    finally:
                        os.close(descriptor)
                    killed.append(record["pid"])
                return {"active": False, "ipv4": False, "ipv6": False}

            with patch.object(tun, "observation", side_effect=kill_new_tun):
                try:
                    manager.configure_tun(True)
                    raise AssertionError("unclean replacement accepted")
                except AppError as error:
                    assert error.kind == "rollback_failed", error.kind
            assert killed and not manager.engine.running()
            assert not manager.store.read()["settings"]["tun"]["enabled"]
            assert manager.engine.record()["settings"]["tun"]["enabled"]
            assert manager.engine.record()["pid"] == killed[0]
            remaining = tun.resources(tun.network())
            assert remaining["rules"]
            # All remaining rules belong to this test's isolated namespace.
            for rule in remaining["rules"]:
                ip(rule["family"], "rule", "del", "priority", str(rule["priority"]))
            manager.start()
            manager.configure_tun(True)
            assert manager.status()["pid"] != before["pid"]
            status = manager.tun_status()
            assert status["active"] and status["ipv4"] and status["ipv6"], status
            for tcp in (False, True):
                assert dns_query("203.0.113.53", tcp=tcp) == "198.51.100.2"
            assert dns_query("203.0.113.53", qtype=28) == "2001:db8:2::2"
            # Loopback stubs remain local; only their non-excluded upstream traffic can enter TUN.
            assert dns_query("127.0.0.53") == "192.0.2.42"
            packets = tun_packets()
            for address in ("198.51.100.2", "2001:db8:2::2"):
                client = http.client.HTTPConnection(address, 18080, timeout=4)
                client.request("GET", "/")
                response = client.getresponse()
                assert response.status == 200 and response.read() == b"isolated HTTP response"
                client.close()
            assert tun_packets() > packets
            # Device and routing ownership are both checked across data directories.
            other = Manager(root / "other")
            other.put_sub("a", str(source), create=True)
            other.use("a")
            for device, kind in [
                ("mihomo", "tun_device_conflict"),
                ("other0", "tun_route_conflict"),
            ]:
                try:
                    other.configure_tun(True, device=device)
                    raise AssertionError("conflict accepted")
                except AppError as error:
                    assert error.kind == kind, error.kind
            # No privilege: real CLI subprocess with CAP_NET_ADMIN removed.
            state_before = (manager.store.root / "state.json").read_bytes()
            denied = subprocess.run(
                [
                    "setpriv",
                    "--bounding-set=-net_admin",
                    sys.executable,
                    "-m",
                    "mihomo_py",
                    "--data-dir",
                    str(manager.store.root),
                    "tun",
                    "enable",
                    "--yes",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert denied.returncode == 4, denied.stderr
            assert json.loads(denied.stderr)["error"] == "tun_permission_denied"
            inspected = subprocess.run(
                ["setpriv", "--bounding-set=-all", sys.executable, "-m", "mihomo_py",
                 "--data-dir", str(manager.store.root), "tun", "status"],
                capture_output=True, text=True, timeout=10,
            )
            assert inspected.returncode == 0, inspected.stderr
            assert json.loads(inspected.stdout)["inspection_error"]["error"] == (
                "tun_inspection_denied"
            )
            assert (manager.store.root / "state.json").read_bytes() == state_before
            assert manager.tun_status()["active"]
            # Missing inspection tools must not kill a healthy core during disable.
            old_pid = manager.engine.running()["pid"]
            with patch.dict(os.environ, {"PATH": "/nonexistent-iproute2"}):
                try:
                    manager.configure_tun(False)
                    raise AssertionError("disable accepted without network inspection")
                except AppError as error:
                    assert error.kind == "tun_inspection_failed", error.kind
                assert manager.engine.running()["pid"] == old_pid
                assert manager.engine.healthy(manager.engine.running())
            assert (manager.store.root / "state.json").read_bytes() == state_before
            # A cleanup inspection failure after exit must also enter rollback.
            check_cleanup = tun.check_cleanup
            failed_cleanup = []

            def cleanup_fails_once(record):
                if not manager.engine.running() and not failed_cleanup:
                    failed_cleanup.append(True)
                    raise AppError("tun_cleanup_failed", "injected cleanup inspection failure")
                return check_cleanup(record)

            with patch.object(tun, "check_cleanup", side_effect=cleanup_fails_once):
                try:
                    manager.configure_tun(False)
                    raise AssertionError("cleanup failure not propagated")
                except AppError as error:
                    assert error.kind == "tun_cleanup_failed", error.kind
            assert failed_cleanup and manager.engine.running()["pid"] != old_pid
            assert manager.tun_status()["active"] and manager.status()["healthy"]
            assert (manager.store.root / "state.json").read_bytes() == state_before
            # Persistence rollback uses the same recovery path as launch rollback.
            save_failures = []
            cleanup_failures = []

            def fail_save(state):
                save_failures.append(True)
                raise OSError("injected state save failure")

            def cleanup_after_save_failure(record):
                if save_failures and not manager.engine.running() and not cleanup_failures:
                    cleanup_failures.append(True)
                    raise AppError("tun_cleanup_failed", "injected inspection failure")
                return check_cleanup(record)

            with patch.object(manager.store, "save", side_effect=fail_save):
                with patch.object(tun, "check_cleanup", side_effect=cleanup_after_save_failure):
                    try:
                        manager.configure({"mode": "direct"})
                        raise AssertionError("save failure not propagated")
                    except OSError:
                        pass
            assert save_failures and cleanup_failures
            assert manager.tun_status()["active"] and manager.status()["healthy"]
            assert manager.engine.running()["settings"]["mode"] == "rule"
            assert (manager.store.root / "state.json").read_bytes() == state_before
            # Subnet rules still exist when catch-all rules disappear. Verify both
            # status and automatic repair against actual kernel routing decisions.
            for family, address, label in (
                ("-4", "198.51.100.2", "ipv4"),
                ("-6", "2001:db8:2::2", "ipv6"),
            ):
                old_pid = manager.engine.running()["pid"]
                for rule in tun.network()["rules"]:
                    if rule["family"] == family and rule.get("priority") == 9002:
                        ip(family, "rule", "del", "priority", "9002")
                route = json.loads(subprocess.check_output(
                    ["ip", family, "-j", "route", "get", address,
                     "ipproto", "tcp", "dport", "443"]
                ))
                assert route[0]["dev"] == "uplink", route
                status = manager.tun_status()
                assert not status["active"] and not status[label], status
                assert manager.start()["pid"] != old_pid
                status = manager.tun_status()
                assert status["active"] and status["ipv4"] and status["ipv6"], status
            # The real TUN core is recovered if the replacement cannot launch.
            original_launch = manager.engine.launch
            attempts = []

            def fail_once(*args, **kwargs):
                attempts.append(1)
                if len(attempts) == 1:
                    raise AppError("tun_start_failed", "injected launch failure")
                return original_launch(*args, **kwargs)

            with patch.object(manager.engine, "launch", side_effect=fail_once):
                try:
                    manager.configure_tun(True, device="replacement")
                    raise AssertionError("failure not propagated")
                except AppError as error:
                    assert error.kind == "tun_start_failed"
            assert len(attempts) == 2
            assert manager.tun_status()["active"] and manager.status()["healthy"]
            assert (manager.store.root / "state.json").read_bytes() == state_before
            # A healthy API without verified TUN readiness must still fail startup.
            observe = tun.observation

            def missing_tun(record, net=None):
                if record["settings"]["tun"]["device"] == "unready":
                    return {"active": False, "ipv4": False, "ipv6": False}
                return observe(record, net)

            with patch.object(tun, "observation", side_effect=missing_tun):
                with patch.object(manager.engine, "timeout", 1):
                    try:
                        manager.configure_tun(True, device="unready")
                        raise AssertionError("unready TUN accepted")
                    except AppError as error:
                        assert error.kind == "tun_start_failed", error.kind
            assert manager.tun_status()["active"] and manager.status()["healthy"]
            assert not any(x["ifname"] == "unready" for x in tun.network()["links"])
            # Lifecycle and exclusions retain the local policy.
            manager.put_sub("b", str(source), create=True)
            manager.use("b")
            manager.put_sub("b")
            manager.configure({"mode": "direct"})
            manager.start(restart=True)
            assert manager.tun_status()["active"]
            manager.configure_tun(True, exclude_address=["198.51.100.2/32", "2001:db8:2::2/128"])
            packets = tun_packets()
            for address in ("198.51.100.2", "2001:db8:2::2"):
                client = http.client.HTTPConnection(address, 18080, timeout=4)
                client.request("GET", "/")
                assert client.getresponse().read() == b"isolated HTTP response"
                client.close()
            assert tun_packets() == packets  # excluded addresses bypass the TUN.
            manager.configure_tun(False)
            assert manager.status()["healthy"] and not manager.tun_status()["active"]
            assert not any(tun.resources(tun.network()).values())
            assert not any(x["ifname"] == "mihomo" for x in tun.network()["links"])
            # IPv4-only subscriptions report IPv6 as unhandled, even on an IPv6 host.
            source.write_text(source.read_text().replace("ipv6: true", "ipv6: false"))
            manager.put_sub("b")
            manager.configure_tun(True)
            assert manager.tun_status()["active"] and not manager.tun_status()["ipv6"]
            manager.engine.stop()
            assert not any(tun.resources(tun.network()).values())
            # A foreign rule is rejected even without an existing TUN device.
            ip("rule", "add", "priority", "9005", "table", "main")
            try:
                manager.start()
                raise AssertionError("foreign route policy accepted")
            except AppError as error:
                assert error.kind == "tun_route_conflict"
            ip("rule", "del", "priority", "9005")
            # DNS bypasses TUN. Only HTTP Host / TLS SNI identifies the domain;
            # the intentionally wrong destination must be replaced after sniffing.
            manager.configure_tun(True, exclude_address=[])
            manager.configure({"mode": "rule"})
            source.write_text(
                'proxies: []\nrules: ["DOMAIN,tun-test.example.org,DIRECT", "MATCH,REJECT"]\n'
                'dns: {nameserver: [127.0.0.1:1053]}\nsniffer: {enable: false}\n'
            )
            manager.put_sub("b")
            manager.start()

            def sniffed_request(tls=False):
                with socket.create_connection(("203.0.113.77", 443 if tls else 80), 4) as raw:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    connection = context.wrap_socket(
                        raw, server_hostname="tun-test.example.org"
                    ) if tls else raw
                    with connection:
                        connection.sendall(
                            b"GET / HTTP/1.1\r\nHost: tun-test.example.org\r\n"
                            b"Connection: close\r\n\r\n"
                        )
                        response = http.client.HTTPResponse(connection)
                        response.begin()
                        assert response.status == 200
                        assert response.read() == b"isolated HTTP response"

            try:
                sniffed_request()
                raise AssertionError("domain rule unexpectedly worked without sniffing")
            except (OSError, http.client.HTTPException):
                pass
            source.write_text(source.read_text().replace('sniffer: {enable: false}\n', ''))
            manager.put_sub("b")
            for tls in (False, True):
                sniffed_request(tls)
            # SIGKILL leaves core-created policy rules; report and retain evidence.
            manager.start()
            record = manager.engine.running()
            descriptor = pidfd.open_pidfd(record["pid"])
            pidfd.send_signal(descriptor, signal.SIGKILL)
            os.close(descriptor)
            deadline = time.monotonic() + 5
            while manager.engine.running() and time.monotonic() < deadline:
                time.sleep(0.02)
            try:
                manager.configure_tun(False)
                raise AssertionError("unclean stop accepted")
            except AppError as error:
                assert error.kind == "tun_cleanup_failed", error.kind
            assert manager.engine.record_path.exists()
            assert manager.store.read()["settings"]["tun"]["enabled"]
            # This test owns its entire isolated namespace; remove only its residual rules.
            for rule in tun.resources(tun.network())["rules"]:
                ip(rule["family"], "rule", "del", "priority", str(rule["priority"]))
            manager.configure_tun(False)
            print(
                "TUN IPv4/IPv6 HTTP, UDP/TCP DNS, stub DNS, conflicts, permission, "
                "rollback, lifecycle, exclusions and cleanup passed",
                flush=True,
            )
        upstream.close()
        stub.close()
    except BaseException:
        if manager and manager.engine.log_path.exists():
            print(manager.engine.log_path.read_text(), file=sys.stderr)
        raise
    finally:
        try:
            if manager:
                manager.engine.stop()
        finally:
            peer.terminate()
            peer.communicate(timeout=5)
            temporary.cleanup()


if __name__ == "__main__":
    main()
