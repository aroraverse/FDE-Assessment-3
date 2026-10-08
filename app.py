from __future__ import annotations

"""Decision-oriented Streamlit UI for the Procurement Request Copilot (Architecture A).

The panel is built for a procurement decision-maker, not an engineer. It shows,
for each request: the recommendation, grounded evidence, required approvals,
missing information, risk flags, the next step, and the human-review status - so
the user can understand WHY the copilot made its recommendation and act on it.
"""

import json
from pathlib import Path

import streamlit as st

from src.solution import handle_request

ROOT = Path(__file__).resolve().parent
REQUESTS = json.loads((ROOT / "data" / "requests.json").read_text(encoding="utf-8"))
BY_ID = {r["request_id"]: r for r in REQUESTS}

st.set_page_config(page_title="Procurement Request Copilot", layout="wide")
st.title("AI Procurement Request Copilot")
st.caption(
    "Architecture A - single-agent baseline. Deterministic policy engine + Groq reasoning. "
    "Recommendations are advisory; humans own all approvals."
)

request_id = st.sidebar.selectbox(
    "Request",
    list(BY_ID.keys()),
    format_func=lambda rid: f"{rid} - {BY_ID[rid]['product_name']}",
)
architecture = st.sidebar.radio("Architecture", ["single", "staged"], horizontal=True)
run = st.sidebar.button("Run analysis", type="primary", use_container_width=True)
req = BY_ID[request_id]


# ----------------------------- Risk flag styling -----------------------------
_FLAG_LABELS = {
    "budget_insufficient": "Budget insufficient",
    "budget_unavailable": "Budget unavailable",
    "existing_tool_overlap": "Existing tool overlap",
    "security_review_required": "Security review required",
    "privacy_review_required": "Privacy review required",
    "legal_review_required": "Legal review required",
    "vendor_review_expired": "Vendor review expired",
    "conflicting_vendor_evidence": "Conflicting vendor evidence",
    "vendor_risk_unavailable": "Vendor risk unavailable",
    "data_residency_risk": "Data residency risk",
    "prompt_injection_detected": "Prompt injection detected",
    "missing_information": "Missing information",
}


def flag_label(flag: str) -> str:
    return _FLAG_LABELS.get(flag, flag.replace("_", " ").title())


# --------------------------------- Layout ------------------------------------
st.subheader(f"1. Request details - {request_id}")
c1, c2, c3 = st.columns(3)
c1.metric("Product", req.get("product_name", "-"))
c2.metric("Vendor", req.get("vendor_name", "-"))
cost = req.get("annual_cost_usd")
c3.metric("Annual cost (USD)", f"${cost:,}" if isinstance(cost, (int, float)) else "Not provided")
c4, c5, c6 = st.columns(3)
c4.metric("Users / licenses", req.get("user_count") if req.get("user_count") is not None else "Not provided")
c5.metric("Data access", req.get("data_access_level", "-"))
c6.metric("Urgency", req.get("urgency", "-"))
with st.expander("Raw request JSON"):
    st.json(req)

st.divider()

if not run:
    st.info("Select a request in the sidebar and click **Run analysis**.")
    st.stop()

try:
    result = handle_request(request_id, architecture=architecture)
except Exception as exc:  # pragma: no cover - UI safety net
    st.error("The copilot failed to produce a decision.")
    st.exception(exc)
    st.stop()

decision = result.model_dump() if hasattr(result, "model_dump") else result

# 2. Recommendation + 8. Human review status
st.subheader("2. Recommendation")
st.success(decision["recommendation"])
if decision.get("human_review_required", True):
    st.warning("Human review required - the copilot does not approve spend or purchase software.")
else:
    st.info("No human review flagged by policy.")

left, right = st.columns([1.1, 0.9], gap="large")

with left:
    st.subheader("3. Evidence")
    st.caption("Every item is attributable to a tool or policy section.")
    for item in decision.get("evidence", []):
        ref = f"  \n`{item['reference']}`" if item.get("reference") else ""
        st.markdown(f"- **{item['source']}** - {item['finding']}{ref}")

with right:
    st.subheader("4. Approvals required")
    approvals = decision.get("required_approvals", [])
    if approvals:
        for a in approvals:
            st.markdown(f"- {a}")
    else:
        st.markdown("_None determined._")

    st.subheader("5. Missing information")
    missing = decision.get("missing_information", [])
    if missing:
        for m in missing:
            st.markdown(f"- :red[{m}]")
    else:
        st.markdown("_None._")

    st.subheader("6. Risk flags")
    flags = decision.get("risk_flags", [])
    if flags:
        for f in flags:
            st.markdown(f"- :orange[{flag_label(f)}]  (`{f}`)")
    else:
        st.markdown("_None._")

st.divider()
st.subheader("7. Next step")
st.info(decision["next_step"])

# Observability
tel = decision.get("telemetry") or {}
st.caption(
    f"Telemetry - LLM calls: {tel.get('llm_calls', 0)} | "
    f"tool calls: {tel.get('tool_calls', 0)} | "
    f"tools: {', '.join(tel.get('tool_names', []))}"
)

with st.expander("Full ProcurementDecision JSON"):
    st.json(decision)

st.divider()
st.caption("Recommendations are advisory. Human approval remains required for purchasing decisions.")
