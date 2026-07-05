import os
import base64
import hashlib
import hmac

_ALGO = "sha256"
_ITERS = 200_000
_SALT_LEN = 16
_DKLEN = 32


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_LEN)
    dk = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"), salt, _ITERS, dklen=_DKLEN)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iters_s, salt_b64, dk_b64 = password_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = base64.b64decode(salt_b64)
        dk_expected = base64.b64decode(dk_b64)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"), salt, iters, dklen=len(dk_expected))
    return hmac.compare_digest(dk, dk_expected)
