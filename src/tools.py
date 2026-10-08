"""Procurement copilot tools.

Four tools expose business evidence to the agent. Each returns a **structured,
attributable** result (``source`` + payload + ``available`` flag). Tools never
fabricate values: when evidence is missing or a service is down, they say so
explicitly so the deterministic policy engine and the LLM can degrade safely.

Tools
-----
1. get_request_context      (deterministic)  - request + requester + department
2. get_budget_and_history   (deterministic)  - department budget + purchase history
3. search_software_catalog  (deterministic)  - overlap detection in approved catalog
4. get_vendor_risk          (external API)   - mock vendor-risk service w/ retries

At least one tool is deterministic/non-LLM (in fact the first three are).
"""
from __future__ import annotations

import time
from typing import Any

import requests

from src import data_access
from src import vendor_client


# --------------------------------------------------------------------------
# Tool 1: request context (deterministic)
# --------------------------------------------------------------------------


def get_request_context(request_id: str) -> dict[str, Any]:
    """Return the request joined with requester + department context.

    Deterministic. Raises nothing for a bad id; instead reports availability.
    """
    source = "request_context"
    try:
        request = data_access.get_request(request_id)
    except KeyError:
        return {
            "source": source,
            "available": False,
            "error": f"Unknown request_id: {request_id}",
        }

    requester_id = request.get("requester_id")
    requester_name = None
    department = None
    manager_id = None
    country = None

    try:
        employees = data_access.load_employees()
        match = employees[employees["employee_id"] == requester_id]
        if not match.empty:
            row = match.iloc[0]
            requester_name = str(row["name"])
            department = str(row["department"])
            manager_id = str(row["manager_id"]) if row["manager_id"] == row["manager_id"] else None
            country = str(row["country"])
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "source": source,
            "available": False,
            "error": f"Failed to load employee context: {exc}",
        }

    enriched = dict(request)
    enriched["requester"] = requester_name
    enriched["department"] = department
    enriched["requester_country"] = country
    enriched["manager_id"] = manager_id

    return {
        "source": source,
        "available": True,
        "request": enriched,
    }


# --------------------------------------------------------------------------
# Tool 2: budget + purchase history (deterministic)
# --------------------------------------------------------------------------


def get_budget_and_history(department: str | None, vendor_name: str | None = None) -> dict[str, Any]:
    """Return the department's available software budget + relevant purchase history."""
    source = "budget_and_history"
    if not department:
        return {
            "source": source,
            "available": False,
            "error": "No department provided; cannot look up budget.",
        }

    try:
        budgets = data_access.load_budgets()
        row = budgets[budgets["department"] == department]
        if row.empty:
            budget = None
        else:
            r = row.iloc[0]
            budget = {
                "department": department,
                "annual_software_budget_usd": int(r["annual_software_budget_usd"]),
                "committed_usd": int(r["committed_usd"]),
                "available_usd": int(r["available_usd"]),
            }
    except Exception as exc:  # pragma: no cover - defensive
        return {"source": source, "available": False, "error": f"Budget load failed: {exc}"}

    history: list[dict[str, Any]] = []
    try:
        hist_df = data_access.load_purchase_history()
        subset = hist_df[hist_df["department"] == department]
        if vendor_name:
            vendor_hits = hist_df[hist_df["vendor_name"] == vendor_name]
            subset = (
                vendor_hits
                if not vendor_hits.empty
                else subset
            )
        for _, h in subset.iterrows():
            history.append(
                {
                    "purchase_id": str(h["purchase_id"]),
                    "purchase_date": str(h["purchase_date"]),
                    "vendor_name": str(h["vendor_name"]),
                    "product_name": str(h["product_name"]),
                    "annual_amount_usd": int(h["annual_amount_usd"]),
                    "status": str(h["status"]),
                    "notes": str(h["notes"]),
                }
            )
    except Exception as exc:  # pragma: no cover - defensive
        return {"source": source, "available": False, "error": f"History load failed: {exc}"}

    return {
        "source": source,
        "available": budget is not None,
        "budget": budget,
        "purchase_history": history,
    }


# --------------------------------------------------------------------------
# Tool 3: software catalog overlap (deterministic)
# --------------------------------------------------------------------------


def search_software_catalog(
    product_name: str | None,
    vendor_name: str | None,
    category: str | None,
) -> dict[str, Any]:
    """Detect overlap with the approved/internal software catalog.

    Returns matches grouped by same product/vendor and same category so the
    policy engine can surface overlap as a review signal (not auto-rejection).
    """
    source = "software_catalog"
    try:
        catalog = data_access.load_software_catalog()
    except Exception as exc:  # pragma: no cover - defensive
        return {"source": source, "available": False, "error": f"Catalog load failed: {exc}"}

    def row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "software_id": str(row["software_id"]),
            "product_name": str(row["product_name"]),
            "category": str(row["category"]),
            "vendor_name": str(row["vendor_name"]),
            "status": str(row["status"]),
            "annual_cost_usd": int(row["annual_cost_usd"]),
            "licensed_seats": int(row["licensed_seats"]),
            "scope": str(row["scope"]),
            "notes": str(row["notes"]),
        }

    pname = (product_name or "").strip().lower()
    vname = (vendor_name or "").strip().lower()
    cat = (category or "").strip().lower()

    same_product_or_vendor: list[dict[str, Any]] = []
    same_category: list[dict[str, Any]] = []

    for _, row in catalog.iterrows():
        cat_product = str(row["product_name"]).lower()
        cat_vendor = str(row["vendor_name"]).lower()
        cat_category = str(row["category"]).lower()

        product_match = pname and (pname in cat_product or cat_product in pname)
        vendor_match = vname and cat_vendor == vname
        if product_match or vendor_match:
            same_product_or_vendor.append(row_to_dict(row))
        elif cat and cat_category == cat:
            same_category.append(row_to_dict(row))

    return {
        "source": source,
        "available": True,
        "same_product_or_vendor": same_product_or_vendor,
        "same_category": same_category,
    }


def get_vendor_registry(vendor_name: str | None) -> dict[str, Any]:
    """Deterministic lookup of the INTERNAL vendor registry (vendors.csv).

    Kept separate from the external risk API so conflicts can be surfaced.
    """
    source = "vendor_registry"
    if not vendor_name:
        return {"source": source, "available": False, "error": "No vendor name provided."}
    try:
        vendors = data_access.load_vendors()
        row = vendors[vendors["vendor_name"] == vendor_name]
        if row.empty:
            return {
                "source": source,
                "available": True,
                "found": False,
                "registry": None,
            }
        r = row.iloc[0]
        registry = {
            "vendor_id": str(r["vendor_id"]),
            "vendor_name": str(r["vendor_name"]),
            "procurement_status": str(r["procurement_status"]),
            "security_status": str(r["security_status"]),
            "security_review_date": (
                str(r["security_review_date"])
                if r["security_review_date"] == r["security_review_date"] and str(r["security_review_date"]).strip()
                else None
            ),
            "legal_terms_status": str(r["legal_terms_status"]),
            "notes": str(r["notes"]),
        }
        return {"source": source, "available": True, "found": True, "registry": registry}
    except Exception as exc:  # pragma: no cover - defensive
        return {"source": source, "available": False, "error": f"Registry load failed: {exc}"}


# --------------------------------------------------------------------------
# Tool 4: external vendor-risk API (with retries + graceful degradation)
# --------------------------------------------------------------------------


def get_vendor_risk(
    vendor_name: str | None,
    *,
    retries: int = 2,
    backoff_seconds: float = 0.4,
) -> dict[str, Any]:
    """Call the mock external vendor-risk API via the starter client.

    Retries transient failures (per Class 5 guidance). On persistent failure or
    a forced outage (503), returns ``available=False`` with the reason - it never
    infers a favorable status.
    """
    source = "vendor_risk_api"
    if not vendor_name:
        return {"source": source, "available": False, "error": "No vendor name provided."}

    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            data = vendor_client.get_vendor_risk(vendor_name)
            return {"source": source, "available": True, **data}
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            last_error = f"HTTP {status}: {exc}"
            # 404 = unknown vendor (not transient); 503 = forced outage (not retryable here).
            if status == 404:
                return {
                    "source": source,
                    "available": False,
                    "found": False,
                    "error": f"No vendor-risk record for '{vendor_name}'.",
                }
            if status == 503:
                # Simulated upstream outage - surface, do not retry forever.
                if attempt < retries:
                    time.sleep(backoff_seconds * (attempt + 1))
                    continue
                return {
                    "source": source,
                    "available": False,
                    "error": f"Vendor-risk service unavailable (HTTP 503) for '{vendor_name}'.",
                }
            # other HTTP errors: retry a couple times then give up
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
        except requests.RequestException as exc:
            last_error = f"Connection error: {exc}"
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
        except vendor_client.VendorRiskServiceError as exc:
            # Service genuinely unreachable and no in-process fallback available.
            # Degrade safely - never infer a favourable status.
            last_error = f"Service unreachable: {exc}"
            break

    return {
        "source": source,
        "available": False,
        "error": last_error or "Vendor-risk service unavailable.",
    }


# Tool registry for telemetry / introspection.
TOOL_NAMES = [
    "get_request_context",
    "get_budget_and_history",
    "search_software_catalog",
    "get_vendor_registry",
    "get_vendor_risk",
]
