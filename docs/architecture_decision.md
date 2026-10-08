# Architecture Decision Memo

**Maximum length: 500 words**

## Decision

**Ship Architecture A, the single-agent baseline.**

On the shared evaluation set, the staged 2-agent variant (B) produced no
measurable quality improvement over A while doubling LLM cost and adding failure
surface. Per the assessment's own guidance, a simpler architecture that performs
as well should win.

## Evidence

Same six public cases, same data, tools, deterministic policy engine, vendor-risk
integration, Groq configuration, output contract, and evaluator.

| Metric | Single agent (A) | Staged / 2-agent (B) |
|---|---:|---:|
| Cases passing minimum expectations | 6 / 6 | 6 / 6 |
| Deterministic approvals / flags / missing-info | authoritative | identical to A |
| LLM calls / request (real Groq) | 1 | 2 |
| Tool calls / request | 5 | 5 |
| Avg latency (real Groq) | ~711 ms | ~790 ms |
| Notable policy / grounding failures | 0 | 0 |
| Regressions vs frozen A baseline | n/a | none |

For all 10 dataset requests, B's `required_approvals`, `risk_flags`,
`missing_information`, and `human_review_required` are identical to A - because
both share one deterministic policy engine and read these fields directly from
it, never from an LLM.

## Trade-offs

B improves *separation of concerns* on paper: Agent 1 gathers/annotates evidence,
Agent 2 synthesises. But because the deterministic engine is authoritative, that
separation changes nothing a procurement user sees in the decision. B's only
observable addition is extra `procurement_analyst` commentary.

What B makes worse: **2× LLM calls** (double token cost and model latency), a
second JSON-validation/failure surface, and more code paths to test and operate.
Nothing became faster or more correct.

## Risks / limitations

- The live Groq path needs a network key; offline/CI runs use the deterministic
  narrative. Because the LLM is narrative-only, approvals/flags are identical in
  both modes - the business decision does not depend on model availability.
- Overlap detection is lexical, not functional-equivalence reasoning.
- Prompt-injection screening is pattern-based (defence-in-depth); the LLM also
  cannot alter deterministic decisions, so a missed pattern cannot cause approval.
- Before production: validate the policy engine against finance/legal sign-off,
  add authn/z and audit logging, and load-test the vendor-risk dependency.

## Why this is the right MVP

The client problem is trustworthy, auditable procurement recommendations with
humans owning approvals. That trust comes from the **deterministic policy engine
and attributable evidence**, not from agent count. A delivers 6/6 with grounded,
source-attributed evidence, safe degradation (503/outage, missing fields,
conflicts, injection), and mandatory human review - at half B's LLM cost.

Adding a second agent here is orchestration for its own sake. The staged variant
is retained in the codebase (`--architecture staged`) as a tested option, but on
current evidence it does not justify shipping. If future requirements add genuine
Agent-1 work the deterministic engine cannot do (e.g. interpreting unstructured
contracts), the staged split can be revisited with new evidence.
