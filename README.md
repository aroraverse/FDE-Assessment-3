# AI Procurement Request Copilot — FDE Assessment 3

An internal copilot that inspects a software/service purchase request, gathers
evidence with tools, applies **deterministic** procurement policy, and recommends
the next action — while keeping every sensitive approval with a human.

Two architectures are implemented and evaluated on the same public cases:
- **Architecture A** — single-agent baseline (recommended to ship).
- **Architecture B** — staged 2-agent variant (evaluated alternative).

> All companies, vendors, products, employees, prices, and risk signals are
> synthetic assessment material.

---

## 1. Project purpose

Procurement must check, for every request: existing-tool overlap, department
budget, vendor status/risk, security/privacy/legal requirements, and approval
thresholds. The copilot gathers that evidence, runs the deterministic policy
rules, and produces a structured recommendation plus a clear human hand-off. It
**recommends**; it never purchases, approves, or alters budgets.

---

## 2. Setup

Prerequisites: Python 3.11 or 3.12.

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

```bash
# macOS / Linux
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

Preflight (no LLM key needed):

```bash
python verify_setup.py        # expect: PRE-FLIGHT PASSED
```

---

## 3. Environment variables

Copy the example and add your own key (never commit a real key):

```powershell
Copy-Item .env.example .env         # or create .env.local for secrets
```

| Variable | Purpose | Default |
|---|---|---|
| `GROQ_API_KEY` | Groq API key (LLM reasoning). Required for live LLM. | — |
| `GROQ_MODEL` / `MODEL_NAME` | Model id | `llama-3.3-70b-versatile` |
| `GROQ_BASE_URL` | Optional custom endpoint | — |
| `VENDOR_RISK_BASE_URL` | Mock vendor-risk API base URL | `http://127.0.0.1:8001` |

**Loading order:** `.env` (shared defaults) is loaded first, then `.env.local`
(developer secrets) overrides it; neither overrides variables already set in the
OS environment. Both `.env` and `.env.local` are git-ignored; only `.env.example`
(placeholders) is committed.

If no key is configured, the system still runs end-to-end using a deterministic,
policy-grounded narrative — the business decision is identical because the LLM is
narrative-only (see the authority boundary below).

---

## 4. One-command local start

```bash
python run_local.py
```

Starts:
- Vendor-risk API → `http://127.0.0.1:8001`
- Streamlit UI → `http://127.0.0.1:8501`

(The copilot also works without the server running: the vendor-risk client falls
back to the real mock app in-process, so evaluations and tests need no server.)

---

## 5. Product workflow

```text
Purchase request
  → understand request
  → gather evidence (tools)
  → deterministic policy checks        (authoritative)
  → policy/risk reasoning (Groq)       (narrative only)
  → structured recommendation
  → human review / approval
```

The UI shows, for each request: request details, recommendation, evidence,
approvals required, missing information, risk flags, next step, and the
human-review status — so a procurement user can see **why** a recommendation was
made and act on it.

---

## 6. Architecture A — single-agent baseline (recommended)

```text
Request
  → get_request_context
  → tools: budget+history, software catalog, vendor registry, vendor-risk API
  → deterministic policy engine        (AUTHORITATIVE: approvals, flags, missing info)
  → single Groq reasoning step         (recommendation + next step)
  → structured ProcurementDecision
  → human review
```
One LLM call per request. Details: `docs/architecture_A.md`.

## 7. Architecture B — staged 2-agent variant (evaluated alternative)

```text
Request
  → Agent 1: Procurement Analyst  — gather evidence + flag missing/conflicting/stale
  → evidence pack
  → deterministic policy engine        (AUTHORITATIVE: approvals, flags, missing info)
  → Agent 2: Policy/Risk Reviewer — synthesize recommendation from request + pack + policy
  → structured ProcurementDecision
  → human review
```
Two LLM calls per request. Details: `docs/architecture_B.md`.

**Both architectures share the identical deterministic policy engine, tools,
vendor-risk integration, Groq configuration, and output contract.** They differ
only in how LLM reasoning is organised.

---

## 8. Tools

| Tool | Type | Purpose |
|---|---|---|
| `get_request_context` | deterministic | request joined with requester/department |
| `get_budget_and_history` | deterministic | department available budget + purchase history |
| `search_software_catalog` | deterministic | overlap by product/vendor/category |
| `get_vendor_registry` | deterministic | internal vendor registry (for conflict detection) |
| `get_vendor_risk` | external API | mock vendor-risk service, retries + graceful failure |

≥ 3 tools, with 4 deterministic. Every tool returns a structured result with a
`source` and an `available` flag and never fabricates a value.

---

## 9. Agents

- **Architecture A:** one procurement agent turns the grounded, already-decided
  analysis into a recommendation + next step.
- **Architecture B — Agent 1 (Procurement Analyst):** gathers evidence and flags
  data-quality issues; makes no decisions.
- **Architecture B — Agent 2 (Policy/Risk Reviewer):** synthesizes the final
  recommendation from the request + evidence pack + authoritative policy results;
  cannot change approvals/flags/missing-info and cannot approve.

---

## 10. Deterministic policy engine

`src/policy_engine.py` is **non-LLM** and authoritative for:
required-field validation, budget arithmetic, approval thresholds, security /
privacy / legal triggers, 365-day assessment staleness (reference date
**2026-09-30**), registry-vs-API conflict detection, and vendor-risk availability.

---

## 11. Human approval boundary

```text
AI    = interpret context, synthesize evidence, recommend  (narrative only)
CODE  = deterministic rules, thresholds, arithmetic, validation  (authoritative)
HUMAN = sensitive approvals, exceptions, irreversible decisions
```

The LLM is **never** the authority for budget arithmetic, approval thresholds,
security/privacy/legal requirements, vendor-risk status, or deterministic policy
flags. The final decision reads those fields straight from the policy engine, so a
misbehaving or unavailable LLM cannot approve spend or change a control.
`human_review_required` stays `True` for sensitive outcomes.

---

## 12. Assumptions

- Policy (`data/procurement_policy.md`) is the authoritative business-rule source.
- Date checks use the snapshot reference date **2026-09-30**, not the machine date.
- The vendor-risk API is intentionally external to the static internal registry;
  disagreement between them is surfaced, not silently resolved.
- Request/vendor text is untrusted business data, never instructions.
- All supplied data is synthetic.

---

## 13. Expected edge cases (and how they are handled)

| Edge case | Behaviour |
|---|---|
| Incomplete / ambiguous request | missing fields itemised; no invented values; routed for clarification |
| Existing tool solves the need | catalog overlap surfaced as evidence + `existing_tool_overlap`; not auto-rejected or auto-approved |
| Conflicting / expired vendor info | stale/expired surfaced; registry-vs-API conflict flagged; no favourable source silently chosen |
| Security-sensitive / thresholds | deterministic security + approval controls; AI cannot approve its own exception |
| Prompt injection in business data | detected, flagged `prompt_injection_detected`, ignored; policy still enforced |
| Tool / API unavailable | safe degradation; `vendor_risk_unavailable`; no favourable status inferred; escalates |

**Transport failure vs vendor 503 are distinct:** a down service (connection
refused) falls back to the in-process mock app and is *not* treated as a vendor
outage; a vendor-specific `503` (NimbusAI) is surfaced as unavailable.

---

## 14. Evaluation methodology

Same six public cases, same data/tools/policy engine/vendor-risk integration/Groq
configuration/output contract/evaluator for both architectures. The public harness
validates the schema and minimum behavioural expectations, measures end-to-end
latency, and records telemetry (LLM/tool calls) to CSV. Conclusions below are based
on this six-case public set; hidden cases may differ.

```bash
python evals/run_public_evals.py --architecture single
python evals/run_public_evals.py --architecture staged
```

---

## 15. Architecture A results (real Groq)

| Case | Pass | Latency (ms) | LLM calls | Tool calls |
|---|---|---:|---:|---:|
| PUB-01 | PASS | 1440 | 1 | 5 |
| PUB-02 | PASS | 409 | 1 | 5 |
| PUB-03 | PASS | 278 | 1 | 5 |
| PUB-04 | PASS | 336 | 1 | 5 |
| PUB-05 | PASS | 315 | 1 | 5 |
| PUB-06 | PASS | 1490 | 1 | 5 |

6/6; avg latency ≈ **711 ms** (PUB-01 model cold-start and PUB-06 503-retry inflate the average).

## 16. Architecture B results (real Groq)

| Case | Pass | Latency (ms) | LLM calls | Tool calls |
|---|---|---:|---:|---:|
| PUB-01 | PASS | 1433 | 2 | 5 |
| PUB-02 | PASS | 371 | 2 | 5 |
| PUB-03 | PASS | 461 | 2 | 5 |
| PUB-04 | PASS | 460 | 2 | 5 |
| PUB-05 | PASS | 447 | 2 | 5 |
| PUB-06 | PASS | 1570 | 2 | 5 |

6/6; avg latency ≈ **790 ms**.

---

## 17. Side-by-side comparison

| Metric | Architecture A | Architecture B |
|---|---:|---:|
| Public evaluation | 6/6 | 6/6 |
| Groq calls / request | 1 | 2 |
| Tool calls / request | 5 | 5 |
| Avg latency (real Groq) | ~711 ms | ~790 ms |
| Deterministic approvals/flags/missing-info | authoritative | identical to A |
| Quality improvement | baseline | none demonstrated |
| Complexity | lower | higher |
| Failure surface | lower | higher (2nd LLM step) |

For all 10 dataset requests, B's `required_approvals`, `risk_flags`,
`missing_information`, and `human_review_required` are **identical** to A.
Full detail: `evals/comparison.md`.

---

## 18. Final ship decision

**Ship Architecture A.** On the shared public evaluation set both score 6/6, but A
uses half the LLM calls and has a smaller failure surface, while B demonstrates no
measurable quality improvement. The trustworthiness of this product comes from the
deterministic policy engine and attributable evidence, not from agent count — so
the simpler architecture is the stronger MVP. B remains implemented and tested
(`--architecture staged`) as a valid alternative with future value if an
analyst-stage task ever needs genuinely complex reasoning that deterministic
tooling cannot provide. Memo: `docs/architecture_decision.md`.

---

## 19. Known limitations

- Conclusions are based on the six public cases; hidden cases may differ.
- Overlap detection is lexical, not functional-equivalence reasoning.
- Prompt-injection screening is pattern-based (defence-in-depth); the LLM also
  cannot change deterministic decisions, so a missed pattern cannot cause approval.
- The live Groq path needs a network key; offline runs use the deterministic
  narrative with identical approvals/flags.
- Non-blocking warning: `StarletteDeprecationWarning` from FastAPI's `TestClient`
  (used by the in-process vendor-risk fallback). It does not affect behaviour and
  is left untouched to avoid destabilising the pinned FastAPI/Starlette stack.

---

## 20. Testing

```bash
python -m pytest tests/ -q
```

71 tests: data integrity, mock API, deterministic policy engine (thresholds,
budget, security/privacy/legal, staleness, conflict), single-architecture
behaviour, staged-workflow invariants (Agent 2 cannot override policy, malformed
LLM fallback, analyst floor), service-reachability regression (transport vs 503),
and an end-to-end edge-case audit (A–F) for both architectures.

---

## Repository map

```text
src/policy_engine.py   deterministic policy (authoritative)
src/tools.py           the 5 tools
src/pipeline.py        shared gather + policy + evidence (used by A and B)
src/agents.py          Architecture B agents
src/llm_client.py      Groq wrapper (env-configurable)
src/solution.py        handle_request adapter (single | staged)
app.py                 Streamlit UI
run_local.py           one-command launcher
evals/                 public cases, runner, results CSVs, comparison.md
docs/                  architecture_A.md, architecture_B.md, architecture_decision.md
tests/                 71 tests
```
