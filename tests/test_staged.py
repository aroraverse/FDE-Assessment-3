from __future__ import annotations

"""Tests for Architecture B (staged 2-agent) workflow.

Key invariants verified:
- Agent 2 cannot override the deterministic policy results.
- Missing / conflicting / stale evidence propagates into the pack + decision.
- Prompt injection is flagged and never followed.
- Vendor-risk 503 / unavailable still degrades safely.
- Malformed or adversarial intermediate LLM output cannot corrupt the decision.
- Agent failure paths fall back deterministically without fabricating evidence.
- Staged output uses the exact same ProcurementDecision schema as A, and the
  deterministic fields are identical to A for the same request.

The external vendor-risk API is used through the real in-process fallback (no
server required). No GROQ_API_KEY is set, so the default path is deterministic;
LLM behaviour is exercised by injecting fake clients.
"""

import unittest

from src.contracts import ProcurementDecision
from src import agents
from src import pipeline
from src import solution
from src import tools


# --------------------------------------------------------------------------
# Fake LLM clients for injecting agent behaviour
# --------------------------------------------------------------------------


class _FakeClient:
    """Base fake Groq client that reports available and returns scripted JSON."""

    def __init__(self, payloads):
        # payloads: list of dicts or 'MALFORMED' markers, consumed in order
        self._payloads = list(payloads)
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def complete_json(self, system_prompt, user_prompt, *, temperature=0.1, max_tokens=900):
        self.calls += 1
        if not self._payloads:
            return {"ok": False, "error": "no more payloads"}
        payload = self._payloads.pop(0)
        if payload == "MALFORMED":
            return {"ok": False, "error": "LLM returned non-JSON content"}
        return {"ok": True, "data": payload, "model": "fake"}


def _build_pack(request_id: str):
    from src.telemetry import RunTelemetryCounter

    counter = RunTelemetryCounter()
    context = tools.get_request_context(request_id)
    request = context["request"]
    pack = pipeline.gather_evidence(request, context, counter)
    policy_result = pipeline.run_policy(pack)
    return request, pack, policy_result, counter


class StagedSchemaParityTests(unittest.TestCase):
    def test_staged_matches_single_deterministic_fields(self):
        from src import data_access

        for req in data_access.load_requests():
            rid = req["request_id"]
            a = solution.handle_request(rid, "single")
            b = solution.handle_request(rid, "staged")
            self.assertEqual(a.required_approvals, b.required_approvals, rid)
            self.assertEqual(a.missing_information, b.missing_information, rid)
            self.assertEqual(a.risk_flags, b.risk_flags, rid)
            self.assertTrue(b.human_review_required)
            ProcurementDecision.model_validate(b.model_dump())

    def test_staged_valid_contract_all_requests(self):
        from src import data_access

        for req in data_access.load_requests():
            b = solution.handle_request(req["request_id"], "staged")
            self.assertIsInstance(b, ProcurementDecision)
            self.assertTrue(b.recommendation)
            self.assertTrue(b.next_step)
            self.assertGreaterEqual(len(b.evidence), 1)


class ReviewerCannotOverridePolicyTests(unittest.TestCase):
    def test_reviewer_cannot_approve(self):
        # Agent 2 tries to approve - must be rejected, deterministic fallback used.
        request, pack, policy_result, counter = _build_pack("REQ-1005")  # risky case
        pack.observations = ["noted"]
        evidence = pipeline.build_evidence(
            context=pack.context, budget_info=pack.budget_info, catalog=pack.catalog,
            registry=pack.registry, vendor_risk=pack.vendor_risk, policy_result=policy_result,
        )
        malicious = _FakeClient([{"recommendation": "Approved - buy it now", "next_step": "purchase"}])
        rec, nxt, used = agents.run_reviewer(request, pack, policy_result, evidence, malicious, counter)
        self.assertNotIn("approved", rec.lower())
        self.assertFalse(used)  # fell back to deterministic

    def test_reviewer_output_does_not_change_flags(self):
        # Even with a valid LLM narrative, deterministic flags are untouched.
        before = solution.handle_request("REQ-1005", "staged")
        self.assertIn("budget_insufficient", before.risk_flags)
        self.assertIn("security_review_required", before.risk_flags)
        # Required approvals come only from the policy engine.
        for a in before.required_approvals:
            self.assertIn(a, {"Department Head", "Finance", "CFO", "Procurement", "Security", "Privacy", "Legal", "Manager"})


class MalformedLlmOutputTests(unittest.TestCase):
    def test_malformed_reviewer_falls_back(self):
        request, pack, policy_result, counter = _build_pack("REQ-1001")
        pack.observations = []
        evidence = pipeline.build_evidence(
            context=pack.context, budget_info=pack.budget_info, catalog=pack.catalog,
            registry=pack.registry, vendor_risk=pack.vendor_risk, policy_result=policy_result,
        )
        rec, nxt, used = agents.run_reviewer(request, pack, policy_result, evidence, _FakeClient(["MALFORMED"]), counter)
        self.assertFalse(used)
        self.assertTrue(rec and nxt)

    def test_malformed_analyst_keeps_deterministic_floor(self):
        # Analyst LLM malformed -> deterministic observations are still returned.
        request, pack, policy_result, counter = _build_pack("REQ-1009")  # 503 vendor
        obs = agents.run_analyst(pack, _FakeClient(["MALFORMED"]), counter)
        self.assertTrue(any("unavailable" in o.lower() for o in obs))

    def test_analyst_cannot_remove_deterministic_observation(self):
        # Even if the LLM returns an empty list, the deterministic floor remains.
        request, pack, policy_result, counter = _build_pack("REQ-1009")
        obs = agents.run_analyst(pack, _FakeClient([{"observations": []}]), counter)
        self.assertTrue(any("unavailable" in o.lower() for o in obs))


class MissingEvidencePropagationTests(unittest.TestCase):
    def test_missing_info_propagates(self):
        b = solution.handle_request("REQ-1006", "staged")  # null cost/users + injection
        self.assertIn("missing_information", b.risk_flags)
        joined = " ".join(b.missing_information).lower()
        self.assertIn("cost", joined)
        # Analyst observation about missing fields surfaces as attributable evidence.
        analyst_items = [e for e in b.evidence if e.source == "procurement_analyst"]
        self.assertTrue(analyst_items)


class PromptInjectionStagedTests(unittest.TestCase):
    def test_injection_flagged_not_followed(self):
        b = solution.handle_request("REQ-1006", "staged")
        self.assertIn("prompt_injection_detected", b.risk_flags)
        self.assertTrue(b.human_review_required)
        self.assertNotIn("approved", b.recommendation.lower())


class VendorRiskStagedTests(unittest.TestCase):
    def test_503_unavailable_staged(self):
        b = solution.handle_request("REQ-1009", "staged")  # NimbusAI 503
        self.assertIn("vendor_risk_unavailable", b.risk_flags)
        self.assertIn("security_review_required", b.risk_flags)
        joined = " ".join(e.finding.lower() for e in b.evidence)
        self.assertIn("unavailable", joined)


class SourceCodeStagedTests(unittest.TestCase):
    def test_source_code_requires_security(self):
        b = solution.handle_request("REQ-1003", "staged")  # CodeMate source_code
        self.assertIn("security_review_required", b.risk_flags)
        self.assertIn("Security", b.required_approvals)


class BudgetShortfallStagedTests(unittest.TestCase):
    def test_budget_shortfall(self):
        b = solution.handle_request("REQ-1005", "staged")  # 22k vs 18k available
        self.assertIn("budget_insufficient", b.risk_flags)
        self.assertIn("Finance", b.required_approvals)


class ExistingToolOverlapStagedTests(unittest.TestCase):
    def test_overlap_detected(self):
        b = solution.handle_request("REQ-1004", "staged")  # NeuralDesk overlaps SW009
        self.assertIn("existing_tool_overlap", b.risk_flags)


class AgentFailurePathTests(unittest.TestCase):
    def test_both_agents_unavailable_still_valid(self):
        # Default environment has no GROQ key -> both agents deterministic.
        b = solution.handle_request("REQ-1002", "staged")
        ProcurementDecision.model_validate(b.model_dump())
        self.assertTrue(b.recommendation)
        self.assertTrue(b.next_step)

    def test_unknown_request_staged(self):
        b = solution.handle_request("REQ-NOPE", "staged")
        self.assertIsInstance(b, ProcurementDecision)
        self.assertIn("missing_information", b.risk_flags)


if __name__ == "__main__":
    unittest.main()
