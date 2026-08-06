"""Application-wide constants shared across services."""

from uuid import UUID

# The system tenant owns global shared data (seeded categories/sources). Fixed UUID,
# matching the seed in db/init_db.py.
# Fix: several places compared hardcoded strings (even "system") against UUID columns,
# which asyncpg cannot encode, causing queries to fail.
SYSTEM_TENANT_ID: UUID = UUID("00000000-0000-0000-0000-000000000000")

# Provider identifier for the local (non-SSO) admin login. Admin identities live in the
# system tenant and are fully isolated from SSO users, even when the email matches
# (see docs/design/admin-login.md).
LOCAL_SSO_PROVIDER = "local"

# Brute-force protection for local admin login: fixed-window counters and locks.
ADMIN_LOGIN_MAX_FAILURES = 5
ADMIN_LOGIN_WINDOW_SECONDS = 15 * 60
ADMIN_LOGIN_LOCK_SECONDS = 15 * 60
ADMIN_LOGIN_IP_MAX_FAILURES = 20
ADMIN_LOGIN_IP_WINDOW_SECONDS = 60 * 60
ADMIN_LOGIN_IP_LOCK_SECONDS = 60 * 60

# Browser-like request headers for Yahoo Finance: staging tests showed the default client
# fingerprint gets anti-bot rate-limited with HTTP 429 (TLS itself works fine).
# Masquerading as a browser improves the success rate. Shared by services/finance.py and
# the yfinance collector.
YAHOO_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
