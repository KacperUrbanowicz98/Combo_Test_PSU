# auth.py
"""
Hasło do panelu ENG - to samo co w pozostałych aplikacjach stanowiskowych.
W kodzie jest tylko hash SHA-256 z solą, nie jawne hasło.
"""
import hashlib
import hmac

_SALT = "combo-psu-reconext"
_ENG_HASH = "25f68007a6f70ce94d545608d84056fec43fe8e70860e53403869636e885a564"


def hash_password(password: str, salt: str = _SALT) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def check_password(password: str) -> bool:
    ok = hmac.compare_digest(hash_password(password), _ENG_HASH)
    print(f"[AUTH] Logowanie ENG: {'OK' if ok else 'BŁĘDNE HASŁO'}")
    return ok
