"""Static regression guard for nginx security headers (docker/nginx templates).

Pure text assertions against the templates as shipped — no nginx runtime or
envsubst rendering required. Locks in the Content-Security-Policy header added
per docs/dev-guide/design/security.md §3.2 and ensures the four pre-existing
security headers are not lost.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = REPO_ROOT / "docker" / "nginx" / "conf.d"

SERVER_TEMPLATES = [
    TEMPLATE_DIR / "http-server.conf.template",
    TEMPLATE_DIR / "https-server.conf.template",
]

EXISTING_SECURITY_HEADERS = [
    "add_header X-Frame-Options DENY always;",
    "add_header X-Content-Type-Options nosniff always;",
    'add_header X-XSS-Protection "1; mode=block" always;',
    "add_header Referrer-Policy strict-origin-when-cross-origin always;",
]

REQUIRED_CSP_DIRECTIVES = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' https: data:",
    "connect-src 'self'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
]


@pytest.fixture(params=SERVER_TEMPLATES, ids=lambda p: p.name)
def template_text(request) -> str:
    path: Path = request.param
    assert path.is_file(), f"nginx template missing: {path}"
    return path.read_text(encoding="utf-8")


def _csp_header_line(template_text: str) -> str:
    for line in template_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("add_header Content-Security-Policy"):
            return stripped
    pytest.fail("add_header Content-Security-Policy not found in template")


def test_csp_header_present(template_text):
    assert _csp_header_line(template_text)


@pytest.mark.parametrize("directive", REQUIRED_CSP_DIRECTIVES)
def test_csp_directives_present(template_text, directive):
    assert directive in _csp_header_line(template_text)


def test_csp_applies_to_error_responses(template_text):
    assert _csp_header_line(template_text).endswith('" always;')


def test_csp_is_a_hardcoded_constant(template_text):
    assert "${" not in _csp_header_line(template_text)


@pytest.mark.parametrize("header", EXISTING_SECURITY_HEADERS)
def test_existing_security_headers_retained(template_text, header):
    assert header in template_text
