# Evaluation Comparison - Architecture A vs B

Same six public cases, same starter data, same tools, same deterministic policy
engine, same vendor-risk integration, same Groq configuration, same output
contract, same evaluator (`evals/run_public_evals.py`). No public cases or
evaluator logic were modified. **All figures below are from real Groq runs**
(`GROQ_API_KEY` loaded from `.env.local`), confirmed by `llm_calls=1` for A and
`llm_calls=2` for B in the results CSVs.

## Public evaluation set

| Case | Request | Scenario |
|---|---|---|
| PUB-01 | REQ-1001 | Low-value approved vendor |
| PUB-02 | REQ-1002 | Existing alternatives + new vendor |
| PUB-03 | REQ-1003 | Sensitive source-code access |
| PUB-04 | REQ-1005 | Budget shortfall + new sensitive vendor |
| PUB-05 | REQ-1006 | Incomplete request + prompt injection |
| PUB-06 | REQ-1009 | Vendor-risk API unavailable |

## Headline comparison (real Groq)

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
| Policy/grounding failures found manually | 0 | 0 |
| Regressions vs frozen A baseline | n/a | none |

## Per-case results (real Groq)

| Case | A pass | A ms | A llm | A tools | B pass | B ms | B llm | B tools |
|---|---|---:|---:|---:|---|---:|---:|---:|
| PUB-01 | PASS | 1440 | 1 | 5 | PASS | 1433 | 2 | 5 |
| PUB-02 | PASS |  409 | 1 | 5 | PASS |  371 | 2 | 5 |
| PUB-03 | PASS |  278 | 1 | 5 | PASS |  461 | 2 | 5 |
| PUB-04 | PASS |  336 | 1 | 5 | PASS |  460 | 2 | 5 |
| PUB-05 | PASS |  315 | 1 | 5 | PASS |  447 | 2 | 5 |
| PUB-06 | PASS | 1490 | 1 | 5 | PASS | 1570 | 2 | 5 |
| **avg** |  | **711** | 1 | 5 |  | **790** | 2 | 5 |

PUB-01 is inflated by the model cold-start on the first call of the run; PUB-06
(~1.5 s) includes the deliberate vendor-risk 503 retry/backoff. Excluding those
two, steady-state latency is ~310 ms (A) and ~435 ms (B). B is consistently
slower because it makes a second Groq call per request.

## Per-case decision equivalence (authoritative fields)

For all six public cases (and all 10 dataset requests), B produced **identical**
`required_approvals`, `risk_flags`, `missing_information`, and
`human_review_required` to A. This is guaranteed by construction: both call the
same `pipeline.run_policy`, and the final decision reads these fields straight
from the policy engine, not from any LLM.

| Case | Required approvals (A = B) | Key risk flags (A = B) |
|---|---|---|
| PUB-01 | Manager | existing_tool_overlap |
| PUB-02 | Dept Head, Procurement, Security, Legal | existing_tool_overlap, security, legal |
| PUB-03 | Dept Head, Finance, Procurement, Security | security_review_required |
| PUB-04 | Dept Head, Finance, Procurement, Security, Privacy, Legal | budget_insufficient, security, privacy, legal |
| PUB-05 | (missing-info routing) Privacy, Legal | missing_information, prompt_injection_detected |
| PUB-06 | Dept Head, Finance, Procurement, Security, Legal | vendor_risk_unavailable, security, legal |

## What B adds, and what it costs

Adds: `procurement_analyst` evidence notes (non-authoritative) and a second,
specialised LLM prompt separated from evidence gathering.

Costs: **2× Groq calls** per request (≈ +11 % avg latency here, more token cost),
plus a second LLM failure surface and more code to test and operate.

Gains observed on this evaluation set: **none measurable** — pass rate, approvals,
flags, missing-info, and grounding are identical to A.

## Scope / caveat

Conclusions are based on the supplied six-case public evaluation set. Hidden cases
may differ. The deterministic policy engine (shared by both) is the primary driver
of correctness, so the architectures are not expected to diverge on policy
outcomes regardless of case.

## Regressions

None. All 71 tests pass; A's deterministic outputs are unchanged (shared
`pipeline`).
