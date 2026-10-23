"""Issuer-only helper. Keep license_private.pem off GitHub and out of the EXE."""
from __future__ import annotations

import argparse
import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent
PRIVATE_KEY_PATH = ROOT / "license_private.pem"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_keypair():
    if PRIVATE_KEY_PATH.exists():
        raise SystemExit("Lisans imzalama anahtarı zaten var; mevcut anahtarı koruyun.")
    private = Ed25519PrivateKey.generate()
    PRIVATE_KEY_PATH.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print("Oluşturuldu: license_private.pem (BUNU GITHUB'A/EXE'YE KOYMAYIN)")
    print("license_config.py içindeki PUBLIC_KEY_B64 değerini bununla güncelleyin, sonra EXE'yi yeniden derleyin:")
    print(base64.b64encode(public).decode("ascii"))


def issue(customer: str, days: int):
    if not PRIVATE_KEY_PATH.exists():
        raise SystemExit("license_private.pem bulunamadı. Önce: python license_admin.py generate")
    if days < 1 or days > 3650:
        raise SystemExit("Süre 1 ile 3650 gün arasında olmalı.")
    private = serialization.load_pem_private_key(PRIVATE_KEY_PATH.read_bytes(), password=None)
    now = datetime.now(timezone.utc)
    payload = {
        "v": 1,
        "customer": customer.strip()[:80] or "Kullanıcı",
        "issued": int(now.timestamp()),
        "exp": int((now + timedelta(days=days)).timestamp()),
        "id": secrets.token_hex(8),
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token = "IS1." + b64url(payload_bytes) + "." + b64url(private.sign(payload_bytes))
    print("\nLİSANS ANAHTARI (müşteriye gönderin):\n")
    print(token)
    print(f"\nMüşteri: {payload['customer']}\nBitiş (UTC): {datetime.fromtimestamp(payload['exp'], timezone.utc):%Y-%m-%d %H:%M}")


def main():
    parser = argparse.ArgumentParser(description="Süreli uygulama lisansı üreticisi")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate", help="İmzalama anahtarı oluştur (bir kez çalıştırılır)")
    issue_parser = sub.add_parser("issue", help="Süreli lisans anahtarı oluştur")
    issue_parser.add_argument("--customer", required=True, help="Müşteri etiketi veya sipariş numarası")
    issue_parser.add_argument("--days", required=True, type=int, help="Lisans süresi (gün)")
    args = parser.parse_args()
    if args.command == "generate":
        generate_keypair()
    elif args.command == "issue":
        issue(args.customer, args.days)


if __name__ == "__main__":
    main()
