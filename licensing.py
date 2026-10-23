"""Offline, signed time-limited license validation."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from license_config import PUBLIC_KEY_B64

# Small, dependency-free Ed25519 verifier. Only verification is included in the
# customer app; the private signing key stays in the separate issuer tool.
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _decode_point(encoded: bytes) -> tuple[int, int]:
    if len(encoded) != 32:
        raise ValueError("Invalid Ed25519 point length")
    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= _Q:
        raise ValueError("Non-canonical Ed25519 point")
    y2 = y * y % _Q
    x2 = (y2 - 1) * pow((_D * y2 + 1) % _Q, _Q - 2, _Q) % _Q
    x = pow(x2, (_Q + 3) // 8, _Q)
    if (x * x - x2) % _Q:
        x = x * _I % _Q
    if (x * x - x2) % _Q:
        raise ValueError("Invalid Ed25519 point")
    if x == 0 and sign:
        raise ValueError("Non-canonical Ed25519 sign")
    if (x & 1) != sign:
        x = _Q - x
    return x, y


def _point_add(p: tuple[int, int], q: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p
    x2, y2 = q
    product = _D * x1 * x2 * y1 * y2 % _Q
    x3 = (x1 * y2 + x2 * y1) * pow((1 + product) % _Q, _Q - 2, _Q) % _Q
    y3 = (y1 * y2 + x1 * x2) * pow((1 - product) % _Q, _Q - 2, _Q) % _Q
    return x3, y3


def _scalar_mult(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


_BASE = _decode_point(bytes.fromhex("5866666666666666666666666666666666666666666666666666666666666666"))


def _verify_signature(public_key: bytes, signature: bytes, message: bytes) -> None:
    if len(signature) != 64:
        raise ValueError("Invalid Ed25519 signature length")
    r_encoded, s_encoded = signature[:32], signature[32:]
    s = int.from_bytes(s_encoded, "little")
    if s >= _L:
        raise ValueError("Non-canonical Ed25519 scalar")
    a_point = _decode_point(public_key)
    r_point = _decode_point(r_encoded)
    challenge = int.from_bytes(hashlib.sha512(r_encoded + public_key + message).digest(), "little") % _L
    if _scalar_mult(_BASE, s) != _point_add(r_point, _scalar_mult(a_point, challenge)):
        raise ValueError("Invalid Ed25519 signature")


def _decode_part(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_license(token: str, secret_reader=None, secret_writer=None) -> tuple[bool, str, dict | None]:
    """Validate token signature, expiry, and optional local clock rollback guard."""
    try:
        prefix, payload_part, signature_part = token.strip().split(".")
        if prefix != "IS1":
            return False, "Lisans anahtarı biçimi tanınmadı.", None
        payload_bytes = _decode_part(payload_part)
        signature = _decode_part(signature_part)
        _verify_signature(base64.b64decode(PUBLIC_KEY_B64), signature, payload_bytes)
        payload = json.loads(payload_bytes.decode("utf-8"))
        expiry = datetime.fromtimestamp(int(payload["exp"]), timezone.utc)
        now = datetime.now(timezone.utc)
        if now >= expiry:
            return False, f"Lisans süresi {expiry.astimezone().strftime('%d.%m.%Y %H:%M')} tarihinde sona ermiş.", None
        if secret_reader and secret_writer:
            previous = secret_reader("license_last_seen")
            if previous:
                try:
                    last_seen = float(previous)
                except ValueError:
                    last_seen = 0
                if now.timestamp() + 300 < last_seen:
                    return False, "Bilgisayarın tarih/saat ayarı geriye alınmış görünüyor. Tarihi düzeltip tekrar deneyin.", None
                secret_writer("license_last_seen", str(max(now.timestamp(), last_seen)))
            else:
                secret_writer("license_last_seen", str(now.timestamp()))
        return True, "Lisans geçerli.", payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, base64.binascii.Error):
        return False, "Lisans anahtarı okunamadı. Anahtarı eksiksiz kopyaladığınızdan emin olun.", None


def license_summary(payload: dict) -> str:
    expires = datetime.fromtimestamp(int(payload["exp"]), timezone.utc).astimezone()
    days_left = max(0, (expires.date() - datetime.now().astimezone().date()).days)
    name = payload.get("customer", "Kullanıcı")
    return f"Lisans: {name} · {days_left} gün kaldı · Bitiş: {expires:%d.%m.%Y %H:%M}"
