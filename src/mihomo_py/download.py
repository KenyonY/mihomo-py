"""Direct HTTPS with address fallback covering TLS, not just TCP connect."""

import http.client
import socket
import ssl
import time
import urllib.request


class SubscriptionHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        # urllib's usual create_connection fallback ends before the TLS handshake.
        # Keep the original host for SNI/certificate checks while trying each address.
        timeout = self.timeout
        deadline = time.monotonic() + timeout
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        failure = TimeoutError("HTTPS connection timed out")
        for family, kind, protocol, _, address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock = socket.socket(family, kind, protocol)
            try:
                sock.settimeout(min(5, remaining))
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.connect(address)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("HTTPS connection timed out")
                sock.settimeout(min(5, remaining))
                # The context is supplied by HTTPSHandler and verifies certificates by default.
                secure = self._context.wrap_socket(sock, server_hostname=self.host)
                secure.settimeout(timeout)
                self.sock = secure
                return
            except ssl.SSLCertVerificationError:
                # Invalid certificates must remain explicit failures, never bypassed.
                sock.close()
                raise
            except OSError as exc:
                sock.close()
                failure = exc
        raise failure


class SubscriptionHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(SubscriptionHTTPSConnection, request, context=self._context)
