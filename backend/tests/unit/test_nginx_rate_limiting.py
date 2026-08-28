"""Static regression guard for nginx L1 rate limiting (docker/nginx).

Pure text assertions against the shipped nginx.conf and the two server
templates — no nginx runtime or envsubst rendering required (full-config
syntax was validated with `nginx -t` in the nginx container when the zones
were enabled). Locks in the three limit_req zones (auth/api/sse) and the
per-location limit_req wiring per docs/dev-guide/design/security.md §3.3
layer 1: nginx does coarse per-IP abuse protection while the FastAPI
RateLimitMiddleware keeps the fine-grained tenant-level governance.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_DIR = REPO_ROOT / "docker" / "nginx"
NGINX_CONF = NGINX_DIR / "nginx.conf"
TEMPLATE_DIR = NGINX_DIR / "conf.d"

SERVER_TEMPLATES = [
    TEMPLATE_DIR / "http-server.conf.template",
    TEMPLATE_DIR / "https-server.conf.template",
]

EXPECTED_ZONES = [
    "limit_req_zone $binary_remote_addr zone=rl_auth:10m rate=5r/s;",
    "limit_req_zone $binary_remote_addr zone=rl_api:10m rate=30r/s;",
    "limit_req_zone $binary_remote_addr zone=rl_sse:10m rate=2r/s;",
]


def _active_lines(text: str) -> list[str]:
    return [stripped for line in text.splitlines() if (stripped := line.strip()) and not stripped.startswith("#")]


def _strip_full_line_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def _location_bodies(text: str) -> dict[str, str]:
    # Drop comment lines first: doc comments mention "location ..." verbatim.
    text = _strip_full_line_comments(text)
    bodies: dict[str, str] = {}
    for match in re.finditer(r"^\s*location\s+([^{]+?)\s*\{", text, re.MULTILINE):
        selector = match.group(1).strip()
        depth = 1
        for i in range(match.end(), len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    bodies[selector] = text[match.end() : i]
                    break
        else:
            pytest.fail(f"unterminated location block: {selector}")
    return bodies


def _active_directives(block: str) -> list[str]:
    return _active_lines(block)


@pytest.fixture(scope="module")
def nginx_conf_text() -> str:
    assert NGINX_CONF.is_file(), f"nginx main config missing: {NGINX_CONF}"
    return NGINX_CONF.read_text(encoding="utf-8")


@pytest.fixture(params=SERVER_TEMPLATES, ids=lambda p: p.name)
def template_locations(request) -> dict[str, str]:
    path: Path = request.param
    assert path.is_file(), f"nginx template missing: {path}"
    return _location_bodies(path.read_text(encoding="utf-8"))


# --- nginx.conf: zones enabled -------------------------------------------


def test_all_three_zones_enabled_not_commented(nginx_conf_text):
    active = _active_lines(nginx_conf_text)
    for zone_directive in EXPECTED_ZONES:
        assert zone_directive in active, f"zone not active: {zone_directive}"


def test_exactly_three_zones_keyed_by_client_ip(nginx_conf_text):
    zone_lines = [line for line in _active_lines(nginx_conf_text) if line.startswith("limit_req_zone")]
    assert len(zone_lines) == 3, zone_lines
    assert all("$binary_remote_addr" in line for line in zone_lines)


def test_zone_rates_match_design(nginx_conf_text):
    active = "\n".join(_active_lines(nginx_conf_text))
    assert "zone=rl_auth:10m rate=5r/s" in active
    assert "zone=rl_api:10m rate=30r/s" in active
    assert "zone=rl_sse:10m rate=2r/s" in active


def test_over_limit_status_is_429(nginx_conf_text):
    assert "limit_req_status 429;" in _active_lines(nginx_conf_text)


# --- templates: limit_req wiring ------------------------------------------


def test_auth_location_uses_auth_zone(template_locations):
    directives = _active_directives(template_locations["/api/v1/auth/"])
    assert "limit_req zone=rl_auth burst=10 delay=5;" in directives
    assert not any("rl_api" in d or "rl_sse" in d for d in directives if d.startswith("limit_req"))


def test_generic_api_location_uses_api_zone(template_locations):
    directives = _active_directives(template_locations["/api/"])
    assert "limit_req zone=rl_api burst=60 delay=20;" in directives


def test_stream_location_uses_sse_zone(template_locations):
    directives = _active_directives(template_locations["/api/v1/stream/"])
    assert "limit_req zone=rl_sse burst=8 nodelay;" in directives
    assert not any("rl_auth" in d or "rl_api" in d for d in directives if d.startswith("limit_req"))


@pytest.mark.parametrize(
    "selector",
    ["/", "/api/v1/health", "= /healthz"],
)
def test_health_and_frontend_locations_exempt(template_locations, selector):
    if selector not in template_locations:
        pytest.skip(f"{selector} not present in this template")
    directives = _active_directives(template_locations[selector])
    assert not any(d.startswith("limit_req") for d in directives)


def test_static_assets_exempt(template_locations):
    static_selectors = [s for s in template_locations if s.startswith("~*")]
    assert static_selectors, "static assets regex location missing"
    for selector in static_selectors:
        directives = _active_directives(template_locations[selector])
        assert not any(d.startswith("limit_req") for d in directives)


def test_templates_do_not_override_429_status(template_locations):
    for selector, body in template_locations.items():
        directives = _active_directives(body)
        assert not any(d.startswith("limit_req_status") for d in directives), (
            f"{selector} overrides the http-level limit_req_status"
        )
