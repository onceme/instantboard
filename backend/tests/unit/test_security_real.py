"""Real (unmocked) bcrypt round-trip regression tests for app.core.security.

passlib 1.7.4 is incompatible with bcrypt>=5: its backend self-check probes
with a >72-byte password, which bcrypt 5 rejects with ValueError instead of
truncating, crashing hash_password/verify_password at runtime. The tests in
test_core_security.py mock pwd_context and cannot detect this, so this module
deliberately exercises the actually installed bcrypt version on every run.
If the dependency constraints regress, these tests fail immediately in CI.
"""

from app.core.security import hash_password, verify_password

PASSWORD = "correct-horse-battery-staple"
WRONG_PASSWORD = "wrong-horse-battery-staple"


class TestRealPasswordHashing:
    def test_hash_produces_bcrypt_hash(self):
        hashed = hash_password(PASSWORD)
        assert hashed.startswith("$2b$")

    def test_verify_correct_password(self):
        hashed = hash_password(PASSWORD)
        assert verify_password(PASSWORD, hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password(PASSWORD)
        assert verify_password(WRONG_PASSWORD, hashed) is False
