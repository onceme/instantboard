#!/usr/bin/env python3
"""Generate a bcrypt hash for the ADMIN_PASSWORD_HASH environment variable.

Usage:
    python scripts/gen_admin_password_hash.py            # prompts securely on stdin
    python scripts/gen_admin_password_hash.py '<secret>' # reads the secret from argv
    echo '<secret>' | python scripts/gen_admin_password_hash.py -  # reads piped stdin

The output hash is meant to be pasted into ADMIN_PASSWORD_HASH in .env. See
docs/design/admin-login.md for the local admin login flow.

Comments are intentionally English-only, matching the project convention.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

# Make the backend importable regardless of the directory this script is invoked from.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def _read_password(argv: list[str]) -> str:
    if len(argv) > 1 and argv[1] and argv[1] != "-":
        return argv[1]

    if len(argv) > 1 and argv[1] == "-":
        data = sys.stdin.read()
    elif not sys.stdin.isatty():
        data = sys.stdin.read()
    else:
        data = getpass.getpass("Password to hash: ")

    return data.strip()


def main() -> int:
    import logging

    # Silence the benign "(trapped) error reading bcrypt version" notice that passlib
    # logs when paired with bcrypt>=4, so stdout/stderr stay clean for scripting.
    logging.getLogger("passlib").setLevel(logging.CRITICAL)

    from app.core.security import hash_password

    password = _read_password(sys.argv)
    if not password:
        print("error: empty password; nothing to hash", file=sys.stderr)
        return 1
    if len(password.encode("utf-8")) > 72:
        print(
            "warning: bcrypt only uses the first 72 bytes; the hash reflects the "
            "truncated value",
            file=sys.stderr,
        )

    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
