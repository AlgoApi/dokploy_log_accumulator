import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import ENCRYPTION_KEY


def _fernet() -> Fernet:
    digest = hashlib.sha256(ENCRYPTION_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Failed to decrypt stored API key") from exc
