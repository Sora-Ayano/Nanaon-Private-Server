#!/usr/bin/env python3
"""Generate the self-signed TLS certificate and key used by the gateway.

The Windows installer ships a bundled generator under ``runtime/`` (excluded
from source control). This standalone version covers container deployments
and any manual setup: it writes the files referenced by ``config.yaml``
(``var/certs/gateway_trust_cert.pem`` and ``var/certs/game_key.pem``).

The certificate is self-signed with the CA bit set, so it can be installed
as an Android system CA exactly like the release certificate. It covers all
game domains plus localhost for direct testing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CERT = BASE_DIR / "var" / "certs" / "gateway_trust_cert.pem"
DEFAULT_KEY = BASE_DIR / "var" / "certs" / "game_key.pem"

GAME_DOMAINS = (
    "227.hand.co.jp",
    "prd-asset.227.hand.co.jp",
    "eomwzup9a3.execute-api.ap-northeast-1.amazonaws.com",
    "1d8r7iwbqc.execute-api.ap-northeast-1.amazonaws.com",
    "api.gaudiy.com",
    "*.hand.co.jp",
    "localhost",
)
GAME_IPS = (ipaddress.IPv4Address("127.0.0.1"),)


def build_certificate(days: int) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nanaon Private Gateway")])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(domain) for domain in GAME_DOMAINS]
                + [x509.IPAddress(ip) for ip in GAME_IPS]
            ),
            critical=False,
        )
    )
    return key, builder.sign(key, hashes.SHA256())


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the local gateway TLS certificate")
    parser.add_argument("--cert", default=str(DEFAULT_CERT), help="certificate output path (PEM)")
    parser.add_argument("--key", default=str(DEFAULT_KEY), help="private key output path (PEM)")
    parser.add_argument("--days", type=int, default=3650, help="validity in days (default 10 years)")
    args = parser.parse_args()

    key, certificate = build_certificate(args.days)

    cert_path, key_path = Path(args.cert), Path(args.key)
    for path in (cert_path, key_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.chmod(0o600)

    print(f"gateway certificate: {cert_path}")
    print(f"gateway private key: {key_path}")
    print(f"valid for {args.days} days; install the certificate as an Android system CA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
