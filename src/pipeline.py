from __future__ import annotations

"""Shared procurement pipeline primitives.

This module holds the deterministic, architecture-agnostic core that BOTH
Architecture A (single-agent) and Architecture B (staged 2-agent) use:

- prompt-injection screening (deterministic)
- tool invocation / evidence gathering
- the authoritative deterministic policy engine call
- attributable evidence assembly
- the deterministic narrative fallback

Keeping this here guarantees the two architectures share identical tools, the
identical policy engine, the identical vendor-risk integration, and identical
deterministic outputs. The ONLY thing the architectures vary is how the LLM
reasoning is organised (one agent vs. two staged agents).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.contracts import EvidenceItem
from src import policy_engine
from src import tools
from src.telemetry import RunTelemetryCounter


# --------------------------------------------------------------------------
# Prompt-injection screen (deterministic, pattern-based)
# --------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    r"ignore (all |any |previous )?(procurement )?(rules|instructions|policy|policies)",
    r"disregard (the )?(policy|rules|instructions)",
    r"treat this (request )?as (cfo|ceo|manager)[- ]?approved",
    r"approve (it|this|the request)( immediately| now)?",
    r"bypass (the )?(controls|approval|review|security|policy)",
    r"you are now",
    r"act as (an? )?(admin|administrator|approver)",
    r"grant (full )?approval",
    r"override (the )?(policy|security|review)",
    r"reveal (the )?(secret|system prompt|api key)",
]


def detect_prompt_injection(*texts: Any) -> list[str]:
    """Return the list of suspicious snippets found in untrusted business text."""
    hits: list[str] = []
    for text in texts:
        if not text:
            continue
        blob = str(text)
        for pattern in _INJECTION_PATTERNS:
            m = re.search(pattern, blob, flags=re.IGNORECASE)
            if m:
                hits.append(m.group(0))
    return hits


def safe_json(obj: Any) -> str:
    return json.dumps(obj, default=str, indent=2)


# --------------------------------------------------------------------------
# Evidence pack (shared container)
# --------------------------------------------------------------------------


@dataclass
class EvidencePack:
    """Structured, attributable evidence gathered from tools + policy.

    Agent 1 (Procurement Analyst) assembles/annotates this; the deterministic
    policy engine runs against it; Agent 2 (Policy/Risk Reviewer) consumes it.
    """

    request: dict
    context: dict
    budget_info: dict
    catalog: dict
    registry: dict
    vendor_risk: dict
    injection_hits: list[str] = field(default_factory=list)
    # Analyst-identified data-quality observations (missing/conflicting/stale).
    observations: list[str] = field(default_factory=list)
    evidence_items: list[EvidenceItem] = field(default_factory=list)

    def tool_sources_available(self) -> dict[str, bool]:
        return {
            "request_context": bool(self.context.get("available")),
            "budget_and_history": bool(self.budget_info.get("available")),
            "software_catalog": bool(self.catalog.get("available")),
            "vendor_registry": bool(self.registry.get("available")),
            "vendor_risk_api": bool(self.vendor_risk.get("available")),
        }


# --------------------------------------------------------------------------
# Evidence assembly (identical for both architectures)
# --------------------------------------------------------------------------


def build_evidence(
    *,
    context: dict,
    budget_info: dict,
    catalog: dict,
    registry: dict,
    vendor_risk: dict,
    policy_result: policy_engine.PolicyResult,
) -> list[EvidenceItem]:
    """Build attributable evidence items from tool outputs + policy findings."""
    evidence: list[EvidenceItem] = []

    # Budget evidence
    if budget_info.get("available") and budget_info.get("budget"):
        b = budget_info["budget"]
        evidence.append(
            EvidenceItem(
                source="budget_and_history",
                finding=(
                    f"{b['department']} available software budget is "
                    f"${b['available_usd']:,} (annual ${b['annual_software_budget_usd']:,} "
                    f"- committed ${b['committed_usd']:,})."
                ),
                reference=b["department"],
            )
        )
    elif budget_info.get("error"):
        evidence.append(
            EvidenceItem(source="budget_and_history", finding=budget_info["error"], reference=None)
        )

    # Catalog / overlap evidence
    if catalog.get("available"):
        exact = catalog.get("same_product_or_vendor") or []
        cat = catalog.get("same_category") or []
        if exact:
            evidence.append(
                EvidenceItem(
                    source="software_catalog",
                    finding="Existing catalog entry for same product/vendor: "
                    + ", ".join(f"{m['product_name']} ({m['software_id']})" for m in exact),
                    reference=exact[0]["software_id"],
                )
            )
        elif cat:
            evidence.append(
                EvidenceItem(
                    source="software_catalog",
                    finding="Catalog products in the same category: "
                    + ", ".join(f"{m['product_name']} ({m['software_id']})" for m in cat),
                    reference=cat[0]["software_id"],
                )
            )
        else:
            evidence.append(
                EvidenceItem(
                    source="software_catalog",
                    finding="No overlapping product/vendor/category found in the approved catalog.",
                    reference=None,
                )
            )

    # Internal registry evidence
    if registry.get("available") and registry.get("found") and registry.get("registry"):
        r = registry["registry"]
        evidence.append(
            EvidenceItem(
                source="vendor_registry",
                finding=(
                    f"Internal registry: procurement={r['procurement_status']}, "
                    f"security={r['security_status']} (reviewed {r['security_review_date']}), "
                    f"legal_terms={r['legal_terms_status']}."
                ),
                reference=r["vendor_id"],
            )
        )
    elif registry.get("available") and registry.get("found") is False:
        evidence.append(
            EvidenceItem(
                source="vendor_registry",
                finding="Vendor not present in the internal procurement registry (treated as new/unknown).",
                reference=None,
            )
        )

    # External vendor-risk evidence
    if vendor_risk.get("available"):
        evidence.append(
            EvidenceItem(
                source="vendor_risk_api",
                finding=(
                    f"External risk: level={vendor_risk.get('risk_level')}, "
                    f"security_review_status={vendor_risk.get('security_review_status')}, "
                    f"last_review={vendor_risk.get('last_review_date')}, "
                    f"processes_personal_data={vendor_risk.get('processes_personal_data')}, "
                    f"stores_data_outside_region={vendor_risk.get('stores_data_outside_region')}."
                ),
                reference=vendor_risk.get("vendor_name"),
            )
        )
    else:
        evidence.append(
            EvidenceItem(
                source="vendor_risk_api",
                finding=vendor_risk.get("error", "Vendor-risk evidence unavailable."),
                reference=None,
            )
        )

    # Deterministic policy findings (attributable to policy sections)
    for f in policy_result.findings:
        evidence.append(
            EvidenceItem(source="policy_engine", finding=f["finding"], reference=f["reference"])
        )

    return evidence


# --------------------------------------------------------------------------
# Shared gather + deterministic policy (the authoritative core)
# --------------------------------------------------------------------------


def gather_evidence(request: dict, context: dict, counter: RunTelemetryCounter) -> EvidencePack:
    """Call every tool and assemble raw evidence. Deterministic, no LLM.

    ``context`` is the already-fetched request_context result (so the caller
    controls the request_context tool-call telemetry). All other tool calls are
    recorded here so both architectures incur identical tool-call counts.
    """
    department = request.get("department")
    vendor_name = request.get("vendor_name")
    product_name = request.get("product_name")
    category = request.get("category")

    counter.record_tool_call("get_budget_and_history")
    budget_info = tools.get_budget_and_history(department, vendor_name)

    counter.record_tool_call("search_software_catalog")
    catalog = tools.search_software_catalog(product_name, vendor_name, category)

    counter.record_tool_call("get_vendor_registry")
    registry = tools.get_vendor_registry(vendor_name)

    counter.record_tool_call("get_vendor_risk")
    vendor_risk = tools.get_vendor_risk(vendor_name)

    injection_hits = detect_prompt_injection(
        request.get("business_justification"),
        registry.get("registry", {}).get("notes") if registry.get("registry") else None,
    )

    return EvidencePack(
        request=request,
        context=context,
        budget_info=budget_info,
        catalog=catalog,
        registry=registry,
        vendor_risk=vendor_risk,
        injection_hits=injection_hits,
    )


def run_policy(pack: EvidencePack) -> policy_engine.PolicyResult:
    """Run the AUTHORITATIVE deterministic policy engine against the evidence pack."""
    policy_result = policy_engine.evaluate_policy(
        pack.request,
        budget=pack.budget_info.get("budget"),
        catalog_matches=pack.catalog if pack.catalog.get("available") else None,
        vendor_registry=pack.registry.get("registry"),
        vendor_risk=pack.vendor_risk,
    )
    if pack.injection_hits:
        policy_result.add_flag("prompt_injection_detected")
        policy_result.add_finding(
            "Untrusted business text attempted to override procurement rules "
            f"(e.g. '{pack.injection_hits[0]}'). Instruction ignored; policy enforced.",
            "Policy section 9",
        )
    return policy_result


# --------------------------------------------------------------------------
# Deterministic narrative fallback (shared)
# --------------------------------------------------------------------------


def deterministic_narrative(policy_result: policy_engine.PolicyResult) -> tuple[str, str]:
    """Policy-grounded recommendation + next step when the LLM is unavailable."""
    if policy_result.missing_information:
        rec = (
            "Request clarification before review: required information is missing "
            f"({', '.join(policy_result.missing_information)})."
        )
        nxt = (
            "Return the request to the requester to supply the missing fields, then re-run the checks."
        )
        return rec, nxt

    review_bodies = [
        a
        for a in policy_result.required_approvals
        if a in {"Security", "Privacy", "Legal", "Finance", "CFO"}
    ]
    if review_bodies:
        rec = (
            "Route for required human reviews before any approval ("
            + ", ".join(policy_result.required_approvals) + ")."
        )
        nxt = (
            "Send the evidence package to "
            + ", ".join(review_bodies)
            + " and Procurement for human review."
        )
        return rec, nxt

    rec = (
        "Route for standard human approval ("
        + ", ".join(policy_result.required_approvals or ["Manager"]) + ")."
    )
    nxt = (
        "Send the request to "
        + ", ".join(policy_result.required_approvals or ["Manager"])
        + " for approval. The copilot does not approve spend."
    )
    return rec, nxt
