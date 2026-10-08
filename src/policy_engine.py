"""Deterministic procurement policy engine.

This module is intentionally **non-LLM**. Every rule here is encoded as
deterministic Python so that budget arithmetic, approval thresholds, and
security/privacy/legal triggers are reproducible and auditable.

Design principle (execute.md):
    CODE  = deterministic rules, thresholds, arithmetic, validation.
    AI    = interpretation / synthesis / explanation (handled elsewhere).
    HUMAN = sensitive approvals and exceptions.

Policy source of truth: ``data/procurement_policy.md``
Reference date for all date-based checks: ``2026-09-30`` (never the machine date).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# --------------------------------------------------------------------------
# Policy constants (mirroring data/procurement_policy.md)
# --------------------------------------------------------------------------

# Data snapshot / evaluation reference date. Do NOT use the machine's date.
POLICY_REFERENCE_DATE = date(2026, 9, 30)

# A vendor security assessment is considered current for this many days.
SECURITY_ASSESSMENT_VALID_DAYS = 365

# New-vendor legal review spend threshold.
LEGAL_NEW_VENDOR_SPEND_THRESHOLD = 10_000.0

# Required request fields (policy section 1). ``data_access_level`` and
# ``requested_integrations`` are part of the policy's required set; integrations
# may legitimately be an empty list, so emptiness is handled separately.
REQUIRED_FIELDS = (
    "requester",
    "department",
    "product_name",
    "vendor_name",
    "annual_cost_usd",
    "user_count",
    "business_justification",
    "data_access_level",
)

# Data-access tokens that imply a security review (policy section 5).
SECURITY_SENSITIVE_DATA_TOKENS = (
    "source_code",
    "source code",
    "confidential",
    "employee_pii",
    "customer_pii",
    "pii",
    "credential",
    "secret",
    "production",
    "cloud",
)

# Integration tokens that imply production/cloud access.
SECURITY_SENSITIVE_INTEGRATION_TOKENS = (
    "production",
    "prod ",
    "cloud account",
    "cloud",
    "git repositor",
    "repositor",
)

# Data-access / integration tokens that imply PII handling (privacy, section 6).
PII_TOKENS = (
    "employee_pii",
    "customer_pii",
    "pii",
    "personal",
)

# Vendor-risk security_review_status values that are NOT acceptable.
UNACCEPTABLE_SECURITY_STATUSES = (
    "expired",
    "not_completed",
    "pending",
    "unknown",
    "failed",
)


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------


@dataclass
class PolicyResult:
    """Deterministic policy outcome. Consumed by the agent / LLM layer.

    All lists are de-duplicated while preserving insertion order.
    """

    required_approvals: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    # Human-readable deterministic findings, each attributable to policy.
    findings: list[dict[str, str]] = field(default_factory=list)
    human_review_required: bool = True

    def add_approval(self, name: str) -> None:
        if name not in self.required_approvals:
            self.required_approvals.append(name)

    def add_missing(self, name: str) -> None:
        if name not in self.missing_information:
            self.missing_information.append(name)

    def add_flag(self, flag: str) -> None:
        if flag not in self.risk_flags:
            self.risk_flags.append(flag)

    def add_finding(self, finding: str, reference: str) -> None:
        self.findings.append({"finding": finding, "reference": reference})

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_approvals": list(self.required_approvals),
            "missing_information": list(self.missing_information),
            "risk_flags": list(self.risk_flags),
            "findings": list(self.findings),
            "human_review_required": self.human_review_required,
        }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def _contains_any(haystack: str, tokens: tuple[str, ...]) -> bool:
    low = haystack.lower()
    return any(tok in low for tok in tokens)


def parse_review_date(value: Any) -> date | None:
    """Parse an ISO date string; return None if missing/invalid."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def is_assessment_current(
    review_date: date | None,
    reference_date: date = POLICY_REFERENCE_DATE,
    valid_days: int = SECURITY_ASSESSMENT_VALID_DAYS,
) -> bool:
    """A security assessment is current for ``valid_days`` from its review date."""
    if review_date is None:
        return False
    age_days = (reference_date - review_date).days
    return 0 <= age_days <= valid_days


# --------------------------------------------------------------------------
# Individual deterministic checks
# --------------------------------------------------------------------------


def check_required_information(request: dict, result: PolicyResult) -> None:
    """Policy section 1 - required request information."""
    # requester + department are derived from context; treat requester_id presence.
    if not request.get("requester_id") and not request.get("requester"):
        result.add_missing("requester")
    if not request.get("department"):
        result.add_missing("department")
    if not request.get("product_name"):
        result.add_missing("product/vendor")
    if not request.get("vendor_name"):
        result.add_missing("product/vendor")

    cost = request.get("annual_cost_usd")
    if cost is None:
        result.add_missing("annual cost or estimate")

    users = request.get("user_count")
    if users is None:
        result.add_missing("number of users/licenses")

    if not _as_text(request.get("business_justification")).strip():
        result.add_missing("business purpose")

    data_access = _as_text(request.get("data_access_level")).strip().lower()
    if not data_access or data_access in {"unknown", "unspecified", "n/a"}:
        result.add_missing("intended data-access level")

    if result.missing_information:
        result.add_flag("missing_information")
        result.add_finding(
            "One or more required request fields are missing or unspecified: "
            + ", ".join(result.missing_information),
            "Policy section 1",
        )


def check_budget(request: dict, budget: dict | None, result: PolicyResult) -> None:
    """Policy section 2 - budget check against available software budget."""
    cost = request.get("annual_cost_usd")
    if cost is None:
        # Cannot evaluate budget without a cost; handled by required-info check.
        return
    if budget is None or budget.get("available_usd") is None:
        result.add_flag("budget_unavailable")
        result.add_finding(
            "Department budget could not be determined; cannot verify affordability.",
            "Policy section 2",
        )
        result.add_approval("Finance")
        return

    available = float(budget["available_usd"])
    if float(cost) > available:
        result.add_flag("budget_insufficient")
        result.add_approval("Finance")
        result.add_finding(
            f"Requested annual cost ${float(cost):,.0f} exceeds available department "
            f"software budget ${available:,.0f}.",
            "Policy section 2",
        )
    else:
        result.add_finding(
            f"Requested annual cost ${float(cost):,.0f} is within available department "
            f"software budget ${available:,.0f}.",
            "Policy section 2",
        )


def approvals_for_amount(amount: float) -> list[str]:
    """Policy section 4 - deterministic approval thresholds (annualized)."""
    if amount <= 1_000.0:
        return ["Manager"]
    if amount <= 10_000.0:
        return ["Department Head", "Procurement"]
    if amount <= 25_000.0:
        return ["Department Head", "Finance", "Procurement"]
    return ["Department Head", "Finance", "CFO", "Procurement"]


def check_approval_threshold(request: dict, result: PolicyResult) -> None:
    """Policy section 4 - add minimum business approvals for the amount."""
    cost = request.get("annual_cost_usd")
    if cost is None:
        # Without a cost we cannot determine the threshold tier deterministically.
        result.add_finding(
            "Approval tier cannot be determined because annual cost is missing.",
            "Policy section 4",
        )
        return
    approvals = approvals_for_amount(float(cost))
    for name in approvals:
        result.add_approval(name)
    result.add_finding(
        f"Annual amount ${float(cost):,.0f} requires minimum business approvals: "
        + ", ".join(approvals) + ".",
        "Policy section 4",
    )


def check_overlap(request: dict, catalog_matches: dict | None, result: PolicyResult) -> None:
    """Policy section 3 - existing software / overlap (review signal, not rejection)."""
    if not catalog_matches:
        return
    exact = catalog_matches.get("same_product_or_vendor") or []
    category = catalog_matches.get("same_category") or []
    if exact:
        result.add_flag("existing_tool_overlap")
        names = ", ".join(m.get("product_name", "?") for m in exact)
        result.add_finding(
            f"Existing catalog entry overlaps with the same product/vendor: {names}. "
            "Review scope/seats/use case before creating a new purchase.",
            "Policy section 3",
        )
    elif category:
        result.add_flag("existing_tool_overlap")
        names = ", ".join(m.get("product_name", "?") for m in category)
        result.add_finding(
            f"Existing catalog product(s) in the same category may satisfy the need: {names}. "
            "Confirm whether a credible gap justifies a new purchase.",
            "Policy section 3",
        )


def check_security(
    request: dict,
    vendor_registry: dict | None,
    vendor_risk: dict | None,
    result: PolicyResult,
) -> None:
    """Policy section 5 - security review triggers + assessment freshness + conflict."""
    data_access = _as_text(request.get("data_access_level"))
    integrations = _as_text(request.get("requested_integrations"))

    sensitive_data = _contains_any(data_access, SECURITY_SENSITIVE_DATA_TOKENS)
    sensitive_integration = _contains_any(integrations, SECURITY_SENSITIVE_INTEGRATION_TOKENS)

    triggered = False
    if sensitive_data:
        triggered = True
        result.add_finding(
            f"Requested data-access level '{data_access}' is security-sensitive.",
            "Policy section 5",
        )
    if sensitive_integration:
        triggered = True
        result.add_finding(
            f"Requested integration '{integrations}' implies production/cloud/source access.",
            "Policy section 5",
        )

    # Assessment freshness / status from the external risk API (authoritative source).
    api_status = None
    api_current = None
    if vendor_risk is not None and vendor_risk.get("available"):
        api_status = (vendor_risk.get("security_review_status") or "").lower()
        api_review_date = parse_review_date(vendor_risk.get("last_review_date"))
        api_current = is_assessment_current(api_review_date)
        if api_status in UNACCEPTABLE_SECURITY_STATUSES:
            triggered = True
            result.add_finding(
                f"Vendor-risk service reports security_review_status='{api_status}'.",
                "vendor_risk_api / Policy section 5",
            )
        elif not api_current:
            triggered = True
            result.add_flag("vendor_review_expired")
            result.add_finding(
                "Vendor security assessment is older than 365 days (not current) "
                "per the policy reference date 2026-09-30.",
                "vendor_risk_api / Policy section 5",
            )
    elif vendor_risk is not None and not vendor_risk.get("available"):
        # Unavailable evidence is material to a security decision.
        triggered = True
        result.add_flag("vendor_risk_unavailable")
        result.add_finding(
            "Vendor-risk service was unavailable; security status could not be verified. "
            "Do not assume the vendor is safe.",
            "Policy section 10",
        )

    # Conflict between internal registry and external API (policy section 5).
    if vendor_registry is not None and vendor_risk is not None and vendor_risk.get("available"):
        reg_date = parse_review_date(vendor_registry.get("security_review_date"))
        reg_current = is_assessment_current(reg_date)
        reg_status = (vendor_registry.get("security_status") or "").lower()
        # Conflict: registry says approved+current but API says expired/stale (or vice versa).
        registry_ok = reg_status in {"approved"} and reg_current
        api_ok = api_status in {"approved"} and bool(api_current)
        if registry_ok != api_ok:
            triggered = True
            result.add_flag("conflicting_vendor_evidence")
            result.add_finding(
                "Internal vendor registry and external vendor-risk service disagree on "
                f"security state (registry status='{reg_status}', date={reg_date}; "
                f"API status='{api_status}', current={api_current}). "
                "Surfacing conflict rather than selecting a source.",
                "Policy section 5",
            )

    if triggered:
        result.add_flag("security_review_required")
        result.add_approval("Security")


def check_privacy(
    request: dict,
    vendor_risk: dict | None,
    result: PolicyResult,
) -> None:
    """Policy section 6 - privacy review for PII or out-of-region storage."""
    data_access = _as_text(request.get("data_access_level"))
    integrations = _as_text(request.get("requested_integrations"))
    triggered = False

    if _contains_any(data_access, PII_TOKENS) or _contains_any(integrations, PII_TOKENS):
        triggered = True
        result.add_finding(
            f"Request involves personal data (data-access '{data_access}'); privacy review applies.",
            "Policy section 6",
        )

    if vendor_risk is not None and vendor_risk.get("available"):
        if vendor_risk.get("processes_personal_data"):
            # Only material when the request itself touches PII-capable use;
            # vendor processing personal data combined with the request raises privacy.
            if _contains_any(data_access, PII_TOKENS) or _contains_any(integrations, PII_TOKENS):
                triggered = True
        if vendor_risk.get("stores_data_outside_region"):
            triggered = True
            result.add_flag("data_residency_risk")
            result.add_finding(
                "Vendor-risk service indicates data may be stored outside the operating region.",
                "vendor_risk_api / Policy section 6",
            )

    if triggered:
        result.add_flag("privacy_review_required")
        result.add_approval("Privacy")


def check_legal(
    request: dict,
    vendor_registry: dict | None,
    vendor_risk: dict | None,
    result: PolicyResult,
) -> None:
    """Policy section 7 - legal review triggers."""
    cost = request.get("annual_cost_usd")
    triggered = False

    # New vendor + spend >= $10k.
    is_new_vendor = False
    if vendor_registry is not None:
        proc_status = (vendor_registry.get("procurement_status") or "").lower()
        is_new_vendor = proc_status in {"new", ""}
    if vendor_registry is None:
        is_new_vendor = True  # no registry record => treat as new/unknown

    if is_new_vendor and cost is not None and float(cost) >= LEGAL_NEW_VENDOR_SPEND_THRESHOLD:
        triggered = True
        result.add_finding(
            f"New/unonboarded vendor with annual spend ${float(cost):,.0f} (>= $10,000) "
            "requires legal review.",
            "Policy section 7",
        )

    # Non-standard / unapproved legal terms.
    if vendor_registry is not None:
        legal_terms = (vendor_registry.get("legal_terms_status") or "").lower()
        if legal_terms and legal_terms not in {"approved", "standard"}:
            triggered = True
            result.add_finding(
                f"Vendor legal terms are not approved/standard (status='{legal_terms}').",
                "Policy section 7",
            )

    # Material cross-region data issue.
    if vendor_risk is not None and vendor_risk.get("available") and vendor_risk.get("stores_data_outside_region"):
        triggered = True
        result.add_finding(
            "Material cross-region data-processing issue identified (data stored outside region).",
            "vendor_risk_api / Policy section 7",
        )

    if triggered:
        result.add_flag("legal_review_required")
        result.add_approval("Legal")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def evaluate_policy(
    request: dict,
    *,
    budget: dict | None = None,
    catalog_matches: dict | None = None,
    vendor_registry: dict | None = None,
    vendor_risk: dict | None = None,
) -> PolicyResult:
    """Run every deterministic policy check and return a consolidated result.

    This function performs **no** LLM calls and never mutates inputs. It is the
    single source of truth for approvals, risk flags, and missing information.
    """
    result = PolicyResult()

    check_required_information(request, result)
    check_budget(request, budget, result)
    check_approval_threshold(request, result)
    check_overlap(request, catalog_matches, result)
    check_security(request, vendor_registry, vendor_risk, result)
    check_privacy(request, vendor_risk, result)
    check_legal(request, vendor_registry, vendor_risk, result)

    # Human review is always required for sensitive outcomes. In this copilot the
    # AI never auto-approves, so human_review_required stays True whenever there
    # are approvals, risks, or missing info - which is effectively always.
    result.human_review_required = True

    return result
