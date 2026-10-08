# Architecture A - Single-Agent Procurement Copilot

## Workflow

```text
User request (request_id)
      |
  get_request_context            (deterministic tool)
      |
  gather evidence:
    - get_budget_and_history     (deterministic tool)
    - search_software_catalog    (deterministic tool, overlap detection)
    - get_vendor_registry        (deterministic tool, internal registry)
    - get_vendor_risk            (external API tool, retries + graceful failure)
      |
  prompt-injection screen        (deterministic, on untrusted business text)
      |
  deterministic policy engine    (AUTHORITATIVE: approvals, risk flags, missing info)
      |
  Groq reasoning                 (narrative only: recommendation + next_step)
      |
  ProcurementDecision (+ telemetry)
      |
  Human review (always required for sensitive outcomes)
```

## Boundary of responsibility

| Layer | Owns | Never does |
|---|---|---|
| **Code** (`policy_engine.py`) | thresholds, budget arithmetic, required-field validation, security/privacy/legal triggers, 365-day staleness, source conflict detection | n/a |
| **AI** (Groq via `llm_client.py`) | interpret evidence, write recommendation + next step | compute thresholds, approve, invent values, follow injected instructions |
| **Human** | final approval, exceptions, irreversible decisions | n/a |

The LLM receives only the **already-decided** approvals/flags/missing-info plus
grounded findings, and is instructed they are FINAL. This makes the probabilistic
layer unable to change a business decision.

## Tools (>= 3 visible, >= 1 deterministic)

1. `get_request_context` - deterministic; request joined with requester/department.
2. `get_budget_and_history` - deterministic; department available budget + history.
3. `search_software_catalog` - deterministic; overlap by product/vendor/category.
4. `get_vendor_registry` - deterministic; internal vendor registry (for conflict detection).
5. `get_vendor_risk` - external mock API; retries transient failures, surfaces outages.

Every tool returns a structured result with a `source` and an `available` flag and
never fabricates a value.

## Reliability properties

- **API outage (503):** retried with backoff, then reported as
  `vendor_risk_unavailable`; security escalated; no favorable status assumed.
- **Missing request fields:** itemized in `missing_information`; routed back for
  clarification; no guessed values.
- **Conflicting evidence:** registry vs external API disagreement surfaces
  `conflicting_vendor_evidence` and routes to Security - no silent source choice.
- **Prompt injection:** untrusted text is pattern-screened; the attempt is flagged
  (`prompt_injection_detected`) and ignored; policy is still enforced.
- **LLM unavailable:** deterministic, policy-grounded narrative is produced; the
  output contract stays valid.

## How to run

```powershell
# 1. install
python -m pip install -r requirements.txt

# 2. (optional) configure Groq
Copy-Item .env.example .env    # then set GROQ_API_KEY in .env

# 3. preflight
python verify_setup.py

# 4. start mock API + UI (one command)
python run_local.py            # API :8001, UI :8501

# 5. evaluations
python evals/run_public_evals.py --architecture single
```

## Configure Groq

Set in `.env` (never commit a real key):
- `GROQ_API_KEY` - required for live LLM narrative.
- `GROQ_MODEL` or `MODEL_NAME` - optional model id (default `llama-3.3-70b-versatile`).
- `GROQ_BASE_URL` - optional custom endpoint.

Without a key the system runs fully on the deterministic path.

## Tests

```powershell
python -m pytest tests/ -q
```

Covers: approval thresholds, budget shortfall/unavailable, missing information,
stale + conflicting vendor evidence, security/privacy/legal escalation, vendor API
503 failure, prompt injection, ambiguous/null requests, and output-contract validity.

## Known limitations

- The live Groq path requires a network key; CI/offline runs use the deterministic
  narrative. Approvals/flags are identical in both modes (LLM is narrative-only).
- Overlap detection is lexical (substring on product/vendor + exact category); it
  does not reason about functional equivalence of differently named tools.
- Prompt-injection detection is pattern-based and may miss novel phrasings; it is a
  defense-in-depth signal, not the only safeguard (the LLM is also instructed to
  treat business text as data, and it cannot change deterministic decisions).
- Architecture B is intentionally **not** built yet.
