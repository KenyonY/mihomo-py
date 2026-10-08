import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseRequestHandler, ThreadingTCPServer

import pytest

from mihomo_py.download import SubscriptionHTTPSConnection


@pytest.fixture
def tls_server(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=subscription.test",
            "-addext",
            "subjectAltName=DNS:subscription.test",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    hosts, names = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hosts.append(self.headers.get("Host"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"proxies: []")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.set_servername_callback(lambda sock, name, ctx: names.append(name))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, ssl.create_default_context(cafile=str(cert)), hosts, names
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("fault", ["invalid_tls", "timeout"])
def test_tls_failure_tries_next_address_with_original_sni(tls_server, monkeypatch, fault):
    port, context, hosts, names = tls_server
    release = threading.Event()

    class BrokenTLS(BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            if fault == "timeout":
                release.wait(10)
            else:
                self.request.sendall(b"not a TLS server\r\n")

    with ThreadingTCPServer(("127.0.0.1", 0), BrokenTLS) as broken:
        thread = threading.Thread(target=broken.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p))
                for p in (broken.server_address[1], port)
            ],
        )
        connection = SubscriptionHTTPSConnection("subscription.test", timeout=7, context=context)
        try:
            connection.request("GET", "/sub")
            response = connection.getresponse()
            assert response.status == 200
            assert response.read() == b"proxies: []"
            assert hosts == ["subscription.test"]
            assert names == ["subscription.test"]
        finally:
            connection.close()
            release.set()
            broken.shutdown()
            thread.join()


def test_invalid_certificate_is_not_bypassed(tls_server, monkeypatch):
    port, _, _, _ = tls_server
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))],
    )
    connection = SubscriptionHTTPSConnection("subscription.test", timeout=2)
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            connection.request("GET", "/sub")
    finally:
        connection.close()
