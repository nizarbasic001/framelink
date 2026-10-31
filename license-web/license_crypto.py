"""Small P-256 ECDSA helper shared by FrameLink and the private license issuer."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import uuid

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
G = (GX, GY)

def device_mac_id() -> str:
    """Get the machine MAC as 12 uppercase hexadecimal characters."""
    node = uuid.getnode()
    # uuid sets the least significant bit of the first octet for a random node.
    if (node >> 40) & 1:
        raise RuntimeError("Windows could not find a hardware MAC address.")
    return f"{node:012X}"

def device_mac_hash(mac: str) -> str:
    normalized = mac.replace(":", "").replace("-", "").replace(" ", "").upper()
    if len(normalized) != 12 or any(c not in "0123456789ABCDEF" for c in normalized):
        raise ValueError("Invalid device MAC address.")
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()

def _inv(x, modulus):
    return pow(x, -1, modulus)

def _add(p1, p2):
    if p1 is None: return p2
    if p2 is None: return p1
    x1, y1 = p1; x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0: return None
    slope = ((3 * x1 * x1 + A) * _inv(2 * y1, P) if p1 == p2 else (y2 - y1) * _inv(x2 - x1, P)) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P

def _mul(k, point=G):
    result = None
    addend = point
    while k:
        if k & 1: result = _add(result, addend)
        addend = _add(addend, addend)
        k >>= 1
    return result

def public_key(private_key: int) -> bytes:
    point = _mul(private_key)
    return point[0].to_bytes(32, "big") + point[1].to_bytes(32, "big")

def _on_curve(point):
    if point is None: return False
    x, y = point
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0

def sign(message: bytes, private_key: int) -> bytes:
    digest = int.from_bytes(hashlib.sha256(message).digest(), "big")
    # Deterministic nonce (RFC 6979 style) prevents weak randomness from
    # exposing the signing key while keeping this tool dependency-free.
    key_bytes = private_key.to_bytes(32, "big")
    v, k = b"\x01" * 32, b"\x00" * 32
    digest_bytes = digest.to_bytes(32, "big")
    k = hmac.new(k, v + b"\x00" + key_bytes + digest_bytes, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + key_bytes + digest_bytes, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        nonce = int.from_bytes(v, "big")
        if 1 <= nonce < N: break
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest(); v = hmac.new(k, v, hashlib.sha256).digest()
    r = _mul(nonce)[0] % N
    s = (_inv(nonce, N) * (digest + r * private_key)) % N
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")

def verify(message: bytes, signature: bytes, key: bytes) -> bool:
    if len(signature) != 64 or len(key) != 64: return False
    r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    if not (1 <= r < N and 1 <= s < N): return False
    point = (int.from_bytes(key[:32], "big"), int.from_bytes(key[32:], "big"))
    if not _on_curve(point): return False
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    w = _inv(s, N)
    result = _add(_mul((z * w) % N), _mul((r * w) % N, point))
    return result is not None and result[0] % N == r

def encode_license(payload: dict, private_key: int) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope = raw + sign(raw, private_key)
    return base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=")

def decode_license(code: str, key: bytes) -> dict:
    padded = code.strip().replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    envelope = base64.b64decode(padded, validate=True)
    if len(envelope) < 65: raise ValueError("License code is too short.")
    raw, signature = envelope[:-64], envelope[-64:]
    if not verify(raw, signature, key): raise ValueError("This license code is invalid.")
    payload = json.loads(raw.decode("utf-8"))
    license_type = payload.get("license_type")
    valid_term = payload.get("days") in (30, 365) or (license_type == "lifetime" and payload.get("days") == 0)
    if payload.get("product") != "FrameLink" or not valid_term:
        raise ValueError("This license is not for this FrameLink version.")
    return payload
