"""RFC 7523 ES256 assertions and RFC 9449 DPoP using the OS credential store."""

import base64
import hashlib
import json
import platform
import time
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .errors import CompanionError


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def public_jwk(private_key) -> dict:
    n = private_key.public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": b64(n.x.to_bytes(32, "big")), "y": b64(n.y.to_bytes(32, "big"))}


def sign_jwt(private_key, header, claims) -> str:
    chunks = [b64(json.dumps(value, separators=(",", ":"), sort_keys=True).encode()) for value in (header, claims)]
    signing_input = ".".join(chunks).encode()
    signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    return ".".join([*chunks, b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))])


def assertion(private_key, subject, audience) -> str:
    now = int(time.time())
    return sign_jwt(
        private_key,
        {"alg": "ES256", "typ": "JWT"},
        {"iss": subject, "sub": subject, "aud": audience, "iat": now, "exp": now + 60, "jti": str(uuid.uuid4())},
    )


def dpop(private_key, method, url, access_token) -> str:
    return sign_jwt(
        private_key,
        {"alg": "ES256", "typ": "dpop+jwt", "jwk": public_jwk(private_key)},
        {
            "htm": method.upper(),
            "htu": url.split("?", 1)[0].split("#", 1)[0],
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
            "ath": b64(hashlib.sha256(access_token.encode("ascii")).digest()),
        },
    )


class SecureKeyStore:
    """Explicit native backends; never keyrings.alt or environment-selected fallbacks."""

    SERVICE = "io.myhermes.installation.v1"

    def __init__(self):
        try:
            if platform.system() == "Darwin":
                from keyring.backends.macOS import Keyring
            elif platform.system() == "Linux":
                from keyring.backends.SecretService import Keyring
            else:
                raise CompanionError("unsupported_os", "This release supports macOS and Ubuntu only.", 3)
            self.backend = Keyring()
            if self.backend.priority <= 0:
                raise RuntimeError("Unavailable backend")
        except CompanionError:
            raise
        except Exception:
            raise CompanionError(
                "secure_store_unavailable",
                "Unlock macOS Keychain or configure Ubuntu Secret Service. No plaintext fallback is supported.",
                3,
            ) from None

    def load(self, key_id: str):
        try:
            value = self.backend.get_password(self.SERVICE, key_id)
            if value is None:
                return None
            key = serialization.load_pem_private_key(value.encode(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
                raise ValueError("Invalid key")
            return key
        except Exception:
            raise CompanionError(
                "secure_store_unavailable", "The installation key could not be read from the OS credential store.", 3
            ) from None

    def create(self, key_id: str):
        key = self.load(key_id)
        if key is not None:
            return key
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()
        try:
            self.backend.set_password(self.SERVICE, key_id, pem)
            if self.load(key_id) is None:
                raise RuntimeError("Readback failed")
        except Exception:
            raise CompanionError(
                "secure_store_unavailable",
                "The installation key could not be saved in the OS credential store; enrollment was not sent.",
                3,
            ) from None
        return key
