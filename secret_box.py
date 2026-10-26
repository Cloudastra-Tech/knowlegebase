"""
secret_box.py  —  encrypts secrets before they go into the database.

Integration tokens (Google Drive, Notion) and single sign-on client secrets are
stored ENCRYPTED with a key from INTEGRATIONS_KEY, or, if that isn't set, a key
generated once into config/integrations.key. Losing the key means reconnecting
the integrations and re-entering the sign-in secrets, so back it up — but
never in the same place as the database, or one leaked backup decrypts
everything (scripts/backup.sh keeps them apart). Or set INTEGRATIONS_KEY.
"""

import json
import os

from cryptography.fernet import Fernet

import db

KEY_PATH = os.path.join(os.path.dirname(db.DB_PATH) or ".", "integrations.key")


def _fernet():
    key = os.getenv("INTEGRATIONS_KEY")
    if not key:
        if not os.path.exists(KEY_PATH):
            os.makedirs(os.path.dirname(KEY_PATH) or ".", exist_ok=True)
            with open(KEY_PATH, "wb") as f:
                f.write(Fernet.generate_key())
            try:
                os.chmod(KEY_PATH, 0o600)
            except OSError:
                pass
        with open(KEY_PATH, "rb") as f:
            key = f.read().strip()
    return Fernet(key)


def seal(value):
    """Anything JSON can hold -> encrypted bytes."""
    return _fernet().encrypt(json.dumps(value).encode())


def unseal(blob):
    """Encrypted bytes from seal() -> the value, or None for no blob."""
    return json.loads(_fernet().decrypt(blob)) if blob else None
