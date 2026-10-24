import os
import tempfile

os.environ.setdefault("FOLIO_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("FOLIO_TOKEN", "test")

from fastapi.testclient import TestClient  # noqa: E402

from folio_engine.server import app  # noqa: E402


def test_webview2_private_network_preflight():
    # WebView2 sends this before every call from the Windows app to 127.0.0.1;
    # a 400 here surfaces in the UI as "TypeError: Failed to fetch".
    r = TestClient(app).options("/jobs", headers={
        "Origin": "http://tauri.localhost",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-folio-token,content-type",
        "Access-Control-Request-Private-Network": "true",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-private-network"] == "true"
    assert r.headers["access-control-allow-origin"] == "http://tauri.localhost"
