"""Offline OHIF study capability primitive; not a clinical HTTP service.

No routes, listeners, source access, or operational secrets. The future HTTP
gateway must authenticate the user independently and validate its study scope.
Do not place capability tokens in URLs, browser history or logs.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time

MAX_TTL = 300
UID_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*\Z")


def _uid(value):
    if not isinstance(value, str) or len(value) > 100 or not UID_RE.fullmatch(value):
        raise ValueError("Invalid study identifier")
    return value


def _identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise ValueError("Invalid authenticated identity")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _secret(key):
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("A separate random 32-byte key is required")
    return key


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise PermissionError("Invalid capability")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise PermissionError("Invalid capability") from None


def issue(key, authorized_uid, authenticated_identity, now=None, ttl=120):
    key = _secret(key)
    uid = _uid(authorized_uid)
    identity_hash = _identity(authenticated_identity)
    if type(ttl) is not int or not 1 <= ttl <= MAX_TTL:
        raise ValueError("Invalid TTL")
    current = int(time.time() if now is None else now)
    payload = {"version": 1, "scope": "dicomweb:read",
               "uid": uid, "identity": identity_hash,
               "issued": current, "expires": current + ttl,
               "nonce": secrets.token_hex(16)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    return body + "." + signature


def verify(key, capability, requested_uid, authenticated_identity, now=None):
    key = _secret(key)
    uid = _uid(requested_uid)
    identity_hash = _identity(authenticated_identity)
    if not isinstance(capability, str) or len(capability) > 2048:
        raise PermissionError("Invalid capability")
    parts = capability.split(".")
    if len(parts) != 2:
        raise PermissionError("Invalid capability")
    body, supplied = parts
    signature = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, _unb64(supplied)):
        raise PermissionError("Invalid capability")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeError):
        raise PermissionError("Invalid capability") from None
    if not isinstance(payload, dict) or set(payload) != {
        "version", "scope", "uid", "identity", "issued", "expires", "nonce"
    }:
        raise PermissionError("Invalid capability")
    issued, expires = payload["issued"], payload["expires"]
    current = int(time.time() if now is None else now)
    if type(issued) is not int or type(expires) is not int or not (
        issued <= current < expires and 1 <= expires - issued <= MAX_TTL
    ):
        raise PermissionError("Expired or invalid capability")
    if payload["version"] != 1 or payload["scope"] != "dicomweb:read":
        raise PermissionError("Invalid capability")
    if payload["uid"] != uid or not hmac.compare_digest(str(payload["identity"]), identity_hash):
        raise PermissionError("Capability does not authorize this study and identity")
    if not isinstance(payload["nonce"], str) or not re.fullmatch("[a-f0-9]{32}", payload["nonce"]):
        raise PermissionError("Invalid capability")
    return uid
