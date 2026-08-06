"""Application-wide constants shared across services."""

from uuid import UUID

# The system tenant owns global shared data (seeded categories/sources). Fixed UUID,
# matching the seed in db/init_db.py.
# Fix: several places compared hardcoded strings (even "system") against UUID columns,
# which asyncpg cannot encode, causing queries to fail.
SYSTEM_TENANT_ID: UUID = UUID("00000000-0000-0000-0000-000000000000")

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
