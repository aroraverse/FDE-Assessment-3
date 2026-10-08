from __future__ import annotations

import os
from urllib.parse import quote

import requests

# Cached in-process client bound to the real mock app (built at most once).
_INPROCESS_CLIENT = None
_INPROCESS_TRIED = False

# Cached reachability decision for the configured HTTP endpoint. We probe the
# service once; if it is not reachable we route all subsequent calls through the
# in-process app so we do not pay a slow failing HTTP round-trip per vendor.
# Keyed by base_url so a changed env var is re-probed.
_HTTP_REACHABLE: dict[str, bool] = {}

# Short timeout for the one-time reachability probe (seconds).
_PROBE_TIMEOUT = 0.5


class VendorRiskServiceError(RuntimeError):
    """Raised when the vendor-risk service cannot be reached at all (transport failure).

    Distinct from an HTTP error raised BY the service (404 unknown vendor, 503
    outage for a specific vendor). A transport failure means the environment is
    not wired up, not that a given vendor is risky or unavailable.
    """


def _get_inprocess_client():
    """Lazily build a TestClient bound to the real mock FastAPI app.

    Runs the actual mock_api handlers in-process (including the NimbusAI 503
    force_error path), so behaviour is identical to the HTTP server. Used only as
    a fallback when the HTTP endpoint is not reachable.
    """
    global _INPROCESS_CLIENT, _INPROCESS_TRIED
    if _INPROCESS_CLIENT is not None:
        return _INPROCESS_CLIENT
    if _INPROCESS_TRIED:
        return None
    _INPROCESS_TRIED = True
    try:
        from fastapi.testclient import TestClient

        from mock_api.app import app

        _INPROCESS_CLIENT = TestClient(app, raise_server_exceptions=False)
    except Exception:
        _INPROCESS_CLIENT = None
    return _INPROCESS_CLIENT


def _http_reachable(base_url: str) -> bool:
    """Probe the HTTP service once (cached) using the /health endpoint."""
    if base_url in _HTTP_REACHABLE:
        return _HTTP_REACHABLE[base_url]
    reachable = False
    try:
        resp = requests.get(f"{base_url}/health", timeout=_PROBE_TIMEOUT)
        reachable = resp.ok
    except requests.RequestException:
        reachable = False
    _HTTP_REACHABLE[base_url] = reachable
    return reachable


def _raise_for_status_like(status_code: int, url: str) -> None:
    """Raise a requests.HTTPError carrying a response with the given status code."""
    resp = requests.Response()
    resp.status_code = status_code
    resp.url = url
    raise requests.HTTPError(f"{status_code} Error for url: {url}", response=resp)


def _inprocess_call(vendor_name: str, path: str, url: str, transport_exc: Exception | None = None) -> dict:
    client = _get_inprocess_client()
    if client is None:
        raise VendorRiskServiceError(
            f"Vendor-risk service unreachable and no in-process fallback available: {transport_exc}"
        ) from transport_exc
    resp = client.get(path)
    if resp.status_code >= 400:
        _raise_for_status_like(resp.status_code, url)
    return resp.json()


def get_vendor_risk(vendor_name: str, timeout_seconds: float = 3.0) -> dict:
    """Low-level vendor-risk API client.

    Primary transport is HTTP against ``VENDOR_RISK_BASE_URL``. The endpoint is
    health-probed once; if it is not reachable, calls are routed through the real
    mock app in-process so evaluation and the copilot stay reliable even when a
    separate server was not launched. HTTP errors returned *by the service*
    (404/503) are always propagated unchanged - per-vendor outage handling is
    never weakened.
    """
    base_url = (os.getenv("VENDOR_RISK_BASE_URL") or "http://127.0.0.1:8001").rstrip("/")
    path = f"/vendor-risk/{quote(vendor_name, safe='')}"
    url = f"{base_url}{path}"

    if not _http_reachable(base_url):
        # Service not up over HTTP - use the in-process app directly (fast path).
        return _inprocess_call(vendor_name, path, url)

    try:
        response = requests.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError:
        # Meaningful signal from the service (404 unknown, 503 outage) - propagate.
        raise
    except requests.RequestException as transport_exc:
        # The service became unreachable after the probe; retry in-process.
        _HTTP_REACHABLE[base_url] = False
        return _inprocess_call(vendor_name, path, url, transport_exc)
