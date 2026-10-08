# Architecture B - Staged 2-Agent Procurement Copilot

## Workflow

```text
User request (request_id)
      |
  get_request_context                      (deterministic tool)
      |
  ┌─ Agent 1: Procurement Analyst ───────────────────────────┐
  │  gather evidence via shared tools:                        │
  │    - get_budget_and_history   (deterministic)             │
  │    - search_software_catalog  (deterministic)             │
  │    - get_vendor_registry      (deterministic)             │
  │    - get_vendor_risk          (external API, retries)     │
  │  annotate evidence pack with data-quality observations    │
  │  (missing / conflicting / stale) - NO decisions           │
  └───────────────────────────────────────────────────────────┘
      |
  prompt-injection screen (deterministic, on untrusted text)
      |
  deterministic policy engine   (AUTHORITATIVE: approvals, flags, missing info)
      |
  ┌─ Agent 2: Policy/Risk Reviewer ──────────────────────────┐
  │  input = request + evidence pack + AUTHORITATIVE policy    │
  │  synthesise recommendation + next step, explain           │
  │  cannot change approvals/flags/missing info               │
  │  cannot approve, cannot invent evidence                   │
  └───────────────────────────────────────────────────────────┘
      |
  ProcurementDecision (+ telemetry)   <-- identical schema to Architecture A
      |
  Human review (always required for sensitive outcomes)
```

## Relationship to Architecture A

B **reuses A's entire deterministic core** via `src/pipeline.py`:
tools, the policy engine, the vendor-risk integration (incl. the in-process
fallback and 503 handling), prompt-injection screening, evidence assembly, and
the deterministic narrative fallback. A and B are byte-identical on the
authoritative fields; they differ only in how LLM reasoning is organised.

| | Architecture A (single) | Architecture B (staged) |
|---|---|---|
| LLM calls (Groq available) | 1 | 2 (analyst + reviewer) |
| LLM role | one narrative pass | analyst observations + reviewer narrative |
| Tools | same 5 | same 5 |
| Policy engine | shared, authoritative | shared, authoritative |
| Output schema | `ProcurementDecision` | identical |

## Agent boundaries (reliability invariants)

**Agent 1 - Procurement Analyst** (`agents.run_analyst`)
- Produces only *observations* about data quality; makes no decisions.
- A **deterministic observation floor** is always included, so a failed or empty
  LLM response can never *remove* a real signal (e.g. "vendor-risk unavailable").
- The LLM may only *add* grounded commentary; it is advisory and non-authoritative.
- On LLM failure/absence it returns the deterministic observations - no fabrication.

**Deterministic policy engine** (`pipeline.run_policy`)
- The single source of truth for required approvals, risk flags, missing
  information, budget arithmetic, thresholds, staleness, and conflicts.
- Runs on the raw evidence pack, not on any LLM output.

**Agent 2 - Policy/Risk Reviewer** (`agents.run_reviewer`)
- Receives request + evidence pack + the **final** policy results only.
- Is told the policy results are authoritative; the final decision object takes
  approvals/flags/missing-info **directly from the policy engine**, so even a
  misbehaving Agent 2 cannot change them (verified by tests).
- Any output containing "approved", or malformed/empty output, is rejected and
  replaced by the deterministic narrative.

## Preserved reliability properties (same as A)

- Vendor-risk 503 → `vendor_risk_unavailable`, no favourable status inferred.
- Transport failure (server down) → in-process fallback to the real mock app;
  **not** conflated with a per-vendor 503.
- Missing fields itemised; no guessing.
- Registry vs API conflict → `conflicting_vendor_evidence` + Security.
- Prompt injection → flagged, ignored, policy enforced.
- LLM unavailable → deterministic narrative; contract stays valid.
- Every evidence item is attributable to a tool/source or policy section. Agent 1
  observations are attributed to `procurement_analyst` and are explicitly
  non-authoritative.

## How to run

```powershell
python evals/run_public_evals.py --architecture staged
python -m pytest tests/ -q
```

(Groq config is identical to Architecture A - see `docs/architecture_A.md`.)
