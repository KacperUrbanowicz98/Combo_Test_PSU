# auth.py
"""
Hasła do panelu ENG (hash SHA-256 z solą — w config.json nie ma jawnego hasła).

Poziomy:
  eng   — panel ENG; przyjmuje hasło ENG albo Admin
  admin — zmiana haseł; tylko hasło Admin
Domyślne (ustawiane przy pierwszym starcie): ENG = eng123, Admin = admin123.
"""
import hashlib
import hmac

LEVEL_NONE, LEVEL_ENG, LEVEL_ADMIN = 0, 1, 2


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def check_level(cfg, password: str) -> int:
    """Zwraca poziom dostępu dla hasła (LEVEL_ADMIN / LEVEL_ENG / LEVEL_NONE)."""
    h = hash_password(password, cfg.PASSWORD_SALT)
    if cfg.ADMIN_PASSWORD_HASH and hmac.compare_digest(h, cfg.ADMIN_PASSWORD_HASH):
        return LEVEL_ADMIN
    if cfg.ENG_PASSWORD_HASH and hmac.compare_digest(h, cfg.ENG_PASSWORD_HASH):
        return LEVEL_ENG
    return LEVEL_NONE


def set_password(cfg, level: int, password: str) -> bool:
    h = hash_password(password, cfg.PASSWORD_SALT)
    if level == LEVEL_ADMIN:
        cfg.ADMIN_PASSWORD_HASH = h
    else:
        cfg.ENG_PASSWORD_HASH = h
    print(f"[AUTH] Zmiana hasła {'ADMIN' if level == LEVEL_ADMIN else 'ENG'}")
    return cfg.save()
