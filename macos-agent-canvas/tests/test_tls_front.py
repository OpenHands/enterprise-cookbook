"""Tests for tls_front.py.

The tests run the real command line as a subprocess in front of real local
servers and talk to it with real TLS clients. No network access is needed.
"""

import importlib.util
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "tls_front.py"
spec = importlib.util.spec_from_file_location("tls_front", SCRIPT)
assert spec is not None and spec.loader is not None
tls_front = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tls_front
spec.loader.exec_module(tls_front)


class Upstream:
    """A plain HTTP server that records requests and echoes POST bodies."""

    def __init__(self):
        self.hits = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                upstream.hits.append(self.path)
                body = b"hello from the agent server"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                upstream.hits.append(self.path)
                body = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Front:
    """The real tls_front.py serve command, started on a free port."""

    def __init__(self, cert_dir, upstream_port, *extra):
        self.cert = cert_dir / "cert.pem"
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT),
                "serve",
                "--cert",
                str(self.cert),
                "--key",
                str(cert_dir / "key.pem"),
                "--listen-host",
                "127.0.0.1",
                "--listen-port",
                "0",
                "--upstream-port",
                str(upstream_port),
                *extra,
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        assert "listening on" in line, line
        self.port = int(line.split("listening on ")[1].split(",")[0].split(":")[1])

    def context(self, cafile=None):
        return ssl.create_default_context(cafile=str(cafile or self.cert))

    def get(self, path="/", host="localhost"):
        url = f"https://{host}:{self.port}{path}"
        return urllib.request.urlopen(url, context=self.context(), timeout=10)

    def close(self):
        self.process.terminate()
        self.process.wait(timeout=10)


@pytest.fixture
def cert_dir(tmp_path):
    directory = tmp_path / "tls"
    tls_front.generate_cert("mac.internal.example", directory)
    return directory


@pytest.fixture
def upstream():
    server = Upstream()
    yield server
    server.close()


@pytest.fixture
def front(cert_dir, upstream):
    started = []

    def start(*extra):
        started.append(Front(cert_dir, upstream.port, *extra))
        return started[-1]

    yield start
    for item in started:
        item.close()


def test_https_request_reaches_the_agent_server(front, upstream):
    with front().get("/ready") as response:
        assert response.status == 200
        assert response.read() == b"hello from the agent server"
    assert upstream.hits == ["/ready"]


def test_large_bodies_survive_in_both_directions(front):
    server = front()
    payload = b"x" * 3_000_000
    request = urllib.request.Request(
        f"https://localhost:{server.port}/echo", data=payload, method="POST"
    )
    with urllib.request.urlopen(request, context=server.context(), timeout=30) as r:
        assert r.read() == payload


def test_non_http_bytes_pass_through_unchanged(cert_dir):
    echo = socket.create_server(("127.0.0.1", 0))

    def serve():
        connection, _ = echo.accept()
        with connection:
            while data := connection.recv(1024):
                connection.sendall(data[::-1])

    threading.Thread(target=serve, daemon=True).start()
    server = Front(cert_dir, echo.getsockname()[1])
    try:
        raw = socket.create_connection(("127.0.0.1", server.port), timeout=10)
        with server.context().wrap_socket(raw, server_hostname="localhost") as tls:
            tls.sendall(b"abc")
            assert tls.recv(10) == b"cba"
    finally:
        server.close()
        echo.close()


def test_plain_http_to_the_tls_port_never_reaches_the_agent_server(front, upstream):
    server = front()
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as raw:
        raw.sendall(b"GET /ready HTTP/1.1\r\nHost: x\r\n\r\n")
        raw.settimeout(5)
        try:
            raw.recv(1024)
        except OSError:
            pass
    time.sleep(0.3)
    assert upstream.hits == []


def test_a_client_that_does_not_trust_the_certificate_is_refused(
    front, upstream, tmp_path
):
    server = front()
    other = tmp_path / "other"
    tls_front.generate_cert("other.example", other)
    with pytest.raises(urllib.error.URLError, match="CERTIFICATE_VERIFY_FAILED"):
        urllib.request.urlopen(
            f"https://localhost:{server.port}/",
            context=server.context(other / "cert.pem"),
            timeout=10,
        )
    assert upstream.hits == []


def test_the_wrong_host_name_is_refused(front, upstream):
    server = front()
    raw = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    with pytest.raises(ssl.SSLCertVerificationError, match="mismatch"):
        server.context().wrap_socket(raw, server_hostname="wrong.example")
    assert upstream.hits == []


def test_only_allowed_source_networks_are_served(front, upstream):
    blocked = front("--allow-from", "10.0.0.0/8")
    with pytest.raises((urllib.error.URLError, OSError)):
        blocked.get()
    assert upstream.hits == []

    allowed = front("--allow-from", "10.0.0.0/8", "--allow-from", "127.0.0.0/8")
    with allowed.get() as response:
        assert response.status == 200


def test_connections_over_the_limit_are_dropped_and_slots_are_reused(front):
    server = front("--max-connections", "1")
    first = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    held = server.context().wrap_socket(first, server_hostname="localhost")
    time.sleep(0.3)
    with pytest.raises((urllib.error.URLError, OSError)):
        server.get()
    held.close()
    deadline = time.time() + 10
    while True:
        try:
            with server.get() as response:
                assert response.status == 200
            break
        except (urllib.error.URLError, OSError):
            assert time.time() < deadline, "slot was not released"
            time.sleep(0.2)


def test_idle_connections_are_closed(front):
    server = front("--idle-timeout", "1")
    raw = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    with server.context().wrap_socket(raw, server_hostname="localhost") as tls:
        tls.settimeout(10)
        started = time.time()
        try:
            assert tls.recv(10) == b""
        except (ssl.SSLError, OSError):
            pass
        assert time.time() - started < 8


def test_an_unreachable_agent_server_closes_the_connection(cert_dir):
    free = socket.create_server(("127.0.0.1", 0))
    dead_port = free.getsockname()[1]
    free.close()
    server = Front(cert_dir, dead_port)
    try:
        with pytest.raises((urllib.error.URLError, OSError)):
            server.get()
    finally:
        server.close()


def test_generated_cert_names_permissions_and_trust(tmp_path):
    directory = tmp_path / "tls"
    cert_path, regenerated = tls_front.generate_cert("mac.internal.example", directory)

    cert = tls_front.read_cert(cert_path)
    assert regenerated
    assert set(tls_front.cert_names(cert)) == {
        "mac.internal.example",
        "localhost",
        "127.0.0.1",
    }
    assert (directory / "key.pem").stat().st_mode & 0o777 == 0o600
    assert tls_front.days_left(cert) > 800
    ssl.create_default_context(cadata=cert_path.read_text())


def test_a_cert_for_an_ip_address_verifies_against_that_ip(tmp_path):
    directory = tmp_path / "tls"
    cert_path, _ = tls_front.generate_cert("127.0.0.1", directory)
    upstream = Upstream()
    server = Front(directory, upstream.port)
    try:
        with server.get(host="127.0.0.1") as response:
            assert response.status == 200
    finally:
        server.close()
        upstream.close()
    assert "127.0.0.1" in tls_front.cert_names(tls_front.read_cert(cert_path))


def test_keep_if_valid_keeps_the_cert_for_the_same_name_only(tmp_path):
    directory = tmp_path / "tls"
    path, _ = tls_front.generate_cert("a.example", directory)
    first = path.read_bytes()

    _, regenerated = tls_front.generate_cert("a.example", directory, keep_if_valid=True)
    assert not regenerated and path.read_bytes() == first

    _, regenerated = tls_front.generate_cert("b.example", directory, keep_if_valid=True)
    assert regenerated and path.read_bytes() != first


def test_keep_if_valid_replaces_a_cert_that_expires_soon(tmp_path):
    directory = tmp_path / "tls"
    path, _ = tls_front.generate_cert("a.example", directory, days=10)
    first = path.read_bytes()

    _, regenerated = tls_front.generate_cert("a.example", directory, keep_if_valid=True)

    assert regenerated and path.read_bytes() != first


@pytest.mark.parametrize("name", ["bad name", "*.example.com", "-x", "a_b"])
def test_gen_cert_rejects_invalid_names(tmp_path, name):
    with pytest.raises(SystemExit, match="not a host name"):
        tls_front.generate_cert(name, tmp_path / "tls")


def test_cli_prints_the_fingerprint_and_cert_info_matches(tmp_path):
    directory = tmp_path / "tls"
    created = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "gen-cert",
            "--name",
            "a.example",
            "--dir",
            str(directory),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    info = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "cert-info",
            "--cert",
            str(directory / "cert.pem"),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert created.startswith("created ")
    assert created.splitlines()[1] == info.strip()
    assert "fingerprint=SHA256:" in info and "names=a.example,localhost" in info


def test_names_are_not_duplicated_when_the_name_is_localhost(tmp_path):
    path, _ = tls_front.generate_cert("localhost", tmp_path / "tls")

    assert tls_front.cert_names(tls_front.read_cert(path)) == ["localhost", "127.0.0.1"]
