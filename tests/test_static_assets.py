"""Root-level dist assets must be served as themselves, not as index.html.

Vite copies web/public/* to the dist ROOT — favicon.svg, icons.svg,
mascot.png — while api/main.py only mounts /assets. Before the dist-root
lookup in spa(), those paths fell through to the SPA catch-all and reached
the browser as 200 text/html: a broken <img>, not an honest 404. The login
mascot shipped that way. These tests pin the fix.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("POOLSIDE_SCHEDULER", "off")
os.environ.setdefault("DATABASE_URL", "postgresql://nouser:nopass@127.0.0.1:1/nodb")

import api.main as m  # noqa: E402

# The SPA routes only exist when a build is present; skip rather than fail
# on a checkout that has not run `npm run build`.
pytestmark = pytest.mark.skipif(
    not m._DIST.exists(), reason="web/dist not built"
)


@pytest.fixture(scope="module")
def client():
    # Bypass lifespan: it runs migrations, and these routes touch no DB.
    return TestClient(m.app)


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("mascot.png", "image/png"),
        ("favicon.svg", "image/svg+xml"),
        ("icons.svg", "image/svg+xml"),
    ],
)
def test_dist_root_asset_served_with_own_type(client, path, content_type):
    if not (m._DIST / path).is_file():
        pytest.skip(f"{path} absent from this build")
    r = client.get(f"/{path}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(content_type)
    assert not r.content.startswith(b"<!doctype html")


def test_spa_route_still_falls_through_to_index(client):
    r = client.get("/overview")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_unknown_api_path_stays_json_404(client):
    r = client.get("/api/nonexistent-endpoint")
    assert r.status_code == 404
    assert r.json()["detail"] == "Not found"


def test_traversal_cannot_escape_dist(client):
    # Whatever the client/server normalisation does with these, the one
    # unacceptable outcome is source leaking out of the repo.
    for probe in ("../api/main.py", "..%2fapi%2fmain.py", "../../etc/passwd"):
        r = client.get(f"/{probe}")
        assert b"POOLSIDE_SESSION_SECRET" not in r.content
        assert b"root:" not in r.content
