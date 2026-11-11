#!/usr/bin/env python3
"""TLS front end for the OpenHands Agent Server.

The Agent Server speaks plain HTTP and calls itself over plain HTTP on loopback,
so it cannot terminate TLS. Run it on 127.0.0.1 and put this in front of it:

    tls_front.py gen-cert --name mac.internal.example \\
        --dir ~/.openhands-agent-server/tls
    tls_front.py serve --cert .../cert.pem --key .../key.pem --upstream-port 18100
    tls_front.py cert-info --cert .../cert.pem

``serve`` accepts TLS and copies bytes to the Agent Server without parsing them,
so REST and WebSockets both work. It uses only the standard library.
``gen-cert`` and ``cert-info`` need ``cryptography``, which the Agent Server's
virtualenv already contains.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import ipaddress
import os
import re
import signal
import ssl
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


CHUNK = 65536
MIN_DAYS_LEFT = 30
DNS_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")


def log(message: str) -> None:
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


def parse_networks(values: Sequence[str]) -> list[Any]:
    try:
        return [ipaddress.ip_network(v, strict=False) for v in values]
    except ValueError as error:
        raise SystemExit(f"error: bad --allow-from value: {error}") from None


class Front:
    def __init__(
        self,
        upstream: tuple[str, int],
        networks: list[Any],
        max_connections: int,
        idle_timeout: float,
    ) -> None:
        self.upstream = upstream
        self.networks = networks
        self.max_connections = max_connections
        self.idle_timeout = idle_timeout
        self.active = 0

    def allowed(self, peer: str) -> bool:
        if not self.networks:
            return True
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self.networks)

    async def copy(
        self,
        source: asyncio.StreamReader,
        sink: asyncio.StreamWriter,
        half_close: bool,
    ) -> bool:
        """Copy until EOF. True means a clean EOF, False an error or idle timeout."""
        try:
            while True:
                data = await asyncio.wait_for(source.read(CHUNK), self.idle_timeout)
                if not data:
                    if half_close and sink.can_write_eof():
                        sink.write_eof()
                    return True
                sink.write(data)
                await sink.drain()
        except (OSError, ssl.SSLError, asyncio.TimeoutError):
            return False

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = str((writer.get_extra_info("peername") or ("?",))[0])
        upstream_writer = None
        try:
            if not self.allowed(peer):
                log(f"rejected {peer}: not in --allow-from")
                return
            if self.active >= self.max_connections:
                log(f"rejected {peer}: too many connections")
                return
            self.active += 1
            try:
                try:
                    upstream_reader, upstream_writer = await asyncio.wait_for(
                        asyncio.open_connection(*self.upstream), 5
                    )
                except (OSError, asyncio.TimeoutError) as error:
                    log(f"upstream unreachable for {peer}: {type(error).__name__}")
                    return
                await self.relay(reader, writer, upstream_reader, upstream_writer)
            finally:
                self.active -= 1
        finally:
            for stream in (writer, upstream_writer):
                if stream is not None:
                    await close(stream)

    async def relay(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        up = asyncio.ensure_future(self.copy(reader, upstream_writer, True))
        down = asyncio.ensure_future(self.copy(upstream_reader, writer, False))
        done, _ = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        if up in done and up.result():
            await down
        for task in (up, down):
            task.cancel()


async def close(stream: asyncio.StreamWriter) -> None:
    try:
        stream.close()
        await asyncio.wait_for(stream.wait_closed(), 5)
    except (OSError, ssl.SSLError, asyncio.TimeoutError):
        pass


async def serve(args: argparse.Namespace) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    front = Front(
        (args.upstream_host, args.upstream_port),
        parse_networks(args.allow_from),
        args.max_connections,
        args.idle_timeout,
    )
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(
        lambda _loop, ctx: log(f"error: {ctx.get('message', 'unexpected')}")
    )
    server = await asyncio.start_server(
        front.handle,
        args.listen_host,
        args.listen_port,
        ssl=context,
        ssl_handshake_timeout=args.handshake_timeout,
        backlog=128,
    )
    host, port = server.sockets[0].getsockname()[:2]
    log(
        f"listening on {host}:{port}, forwarding to "
        f"{args.upstream_host}:{args.upstream_port}"
    )
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    await stop.wait()
    server.close()


def load_crypto() -> Any:
    try:
        from cryptography import x509
    except ImportError:
        raise SystemExit(
            "error: this command needs the 'cryptography' package; run it with the "
            "Agent Server's virtualenv Python"
        ) from None
    return x509


def fingerprint(cert: Any) -> str:
    from cryptography.hazmat.primitives import hashes

    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return "SHA256:" + ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def cert_names(cert: Any) -> list[str]:
    x509 = load_crypto()
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return [str(n) for n in san.get_values_for_type(x509.DNSName)] + [
        str(n) for n in san.get_values_for_type(x509.IPAddress)
    ]


def days_left(cert: Any) -> int:
    expires = cert.not_valid_after_utc
    return (expires - datetime.datetime.now(datetime.timezone.utc)).days


def read_cert(path: Path) -> Any:
    x509 = load_crypto()
    return x509.load_pem_x509_certificate(path.read_bytes())


def write_private(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def generate_cert(
    name: str, directory: Path, days: int = 825, keep_if_valid: bool = False
) -> tuple[Path, bool]:
    """Write cert.pem and key.pem for ``name``. Returns (cert path, regenerated)."""
    x509 = load_crypto()
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    try:
        names: list[Any] = [x509.IPAddress(ipaddress.ip_address(name))]
    except ValueError:
        if not DNS_NAME.match(name):
            raise SystemExit(
                f"error: {name!r} is not a host name or IP address"
            ) from None
        names = [x509.DNSName(name)]
    names += [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    names = list(dict.fromkeys(names))

    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    if keep_if_valid and cert_path.exists() and key_path.exists():
        existing = read_cert(cert_path)
        if name in cert_names(existing) and days_left(existing) > MIN_DAYS_LEFT:
            return cert_path, False

    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    write_private(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        0o600,
    )
    write_private(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return cert_path, True


def describe(cert: Any) -> str:
    return (
        f"names={','.join(cert_names(cert))} "
        f"expires={cert.not_valid_after_utc.date()} days_left={days_left(cert)} "
        f"fingerprint={fingerprint(cert)}"
    )


def read_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TLS front end for the OpenHands Agent Server."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "serve", help="accept TLS and forward to the Agent Server"
    )
    run.add_argument("--cert", required=True)
    run.add_argument("--key", required=True)
    run.add_argument("--listen-host", default="0.0.0.0")
    run.add_argument("--listen-port", type=int, default=18443)
    run.add_argument("--upstream-host", default="127.0.0.1")
    run.add_argument("--upstream-port", type=int, default=18100)
    run.add_argument(
        "--allow-from",
        action="append",
        default=[],
        metavar="CIDR",
        help="accept only these source networks (repeatable); default: any",
    )
    run.add_argument("--max-connections", type=int, default=64)
    run.add_argument("--handshake-timeout", type=float, default=10)
    run.add_argument("--idle-timeout", type=float, default=3600)

    gen = commands.add_parser(
        "gen-cert", help="write a self-signed cert.pem and key.pem"
    )
    gen.add_argument("--name", required=True, help="host name or IP clients connect to")
    gen.add_argument("--dir", required=True)
    gen.add_argument("--days", type=int, default=825)
    gen.add_argument(
        "--keep-if-valid",
        action="store_true",
        help="keep an existing cert that covers --name and has 30+ days left",
    )

    info = commands.add_parser("cert-info", help="print names, expiry and fingerprint")
    info.add_argument("--cert", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = read_args(argv)
    if args.command == "serve":
        asyncio.run(serve(args))
    elif args.command == "gen-cert":
        path, regenerated = generate_cert(
            args.name, Path(args.dir), args.days, args.keep_if_valid
        )
        print(("created " if regenerated else "kept ") + str(path))
        print(describe(read_cert(path)))
    else:
        print(describe(read_cert(Path(args.cert))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
