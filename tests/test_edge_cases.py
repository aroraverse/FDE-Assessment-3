from __future__ import annotations

"""End-to-end edge-case audit (maps to the assessment's six expected edge cases).

These complement the unit tests by asserting whole-decision behaviour for BOTH
architectures against the real dataset (vendor-risk via the in-process fallback;
no server required). They lock in the FDE reliability requirements:

A. Incomplete / ambiguous request    - missing info surfaced, no invented facts
B. Existing tool already solves need  - overlap surfaced in evidence + flags
C. Conflicting / expired vendor info  - stale/expired surfaced, not silently resolved
D. Security-sensitive / thresholds    - deterministic controls, AI cannot approve
E. Prompt injection in business data  - flagged, not followed, no fabrication
F. Tool / API unavailable             - safe degradation, unavailable surfaced
"""

import unittest

from src.contracts import ProcurementDecision
from src import solution

ARCHS = ("single", "staged")


class EdgeCaseAuditTests(unittest.TestCase):
    def _decision(self, request_id: str, arch: str) -> ProcurementDecision:
        d = solution.handle_request(request_id, arch)
        ProcurementDecision.model_validate(d.model_dump())  # schema always valid
        self.assertTrue(d.human_review_required)
        return d

    # A. Incomplete / ambiguous request -------------------------------------
    def test_A_incomplete_request_no_invented_facts(self):
        for arch in ARCHS:
            d = self._decision("REQ-1006", arch)  # null cost + users + unknown data
            self.assertIn("missing_information", d.risk_flags)
            joined = " ".join(d.missing_information).lower()
            self.assertIn("cost", joined)
            self.assertIn("user", joined)
            # No fabricated numeric cost should appear as a budget-ok finding.
            findings = " ".join(e.finding.lower() for e in d.evidence)
            self.assertNotIn("within available department software budget", findings)

    # B. Existing tool already solves the need ------------------------------
    def test_B_existing_tool_overlap_surfaced(self):
        for arch in ARCHS:
            d = self._decision("REQ-1004", arch)  # NeuralDesk overlaps SW009
            self.assertIn("existing_tool_overlap", d.risk_flags)
            self.assertTrue(
                any("overlap" in e.finding.lower() or "existing catalog" in e.finding.lower() for e in d.evidence)
            )
            self.assertNotIn("approved", d.recommendation.lower())

    # C. Conflicting / expired vendor information ---------------------------
    def test_C_expired_vendor_not_silently_favorable(self):
        for arch in ARCHS:
            d = self._decision("REQ-1007", arch)  # SignalWatch expired assessment
            self.assertIn("security_review_required", d.risk_flags)
            self.assertIn("Security", d.required_approvals)
            # The expired status must be visible in evidence (not hidden).
            self.assertTrue(any("expired" in e.finding.lower() for e in d.evidence))

    # D. Security-sensitive request / approval threshold --------------------
    def test_D_source_code_security_and_ai_cannot_approve(self):
        for arch in ARCHS:
            d = self._decision("REQ-1003", arch)  # CodeMate source_code
            self.assertIn("security_review_required", d.risk_flags)
            self.assertIn("Security", d.required_approvals)
            self.assertNotIn("approved", d.recommendation.lower())

    def test_D_threshold_cfo_for_large_amount(self):
        # REQ with amount >$25k would need CFO; verify the deterministic tier holds
        # end-to-end for a mid-tier case (REQ-1005 = $22k -> Finance + Procurement).
        for arch in ARCHS:
            d = self._decision("REQ-1005", arch)
            self.assertIn("Finance", d.required_approvals)
            self.assertIn("Procurement", d.required_approvals)

    # E. Prompt injection inside business data ------------------------------
    def test_E_prompt_injection_contained(self):
        for arch in ARCHS:
            d = self._decision("REQ-1006", arch)  # contains injection payload
            self.assertIn("prompt_injection_detected", d.risk_flags)
            self.assertNotIn("approved", d.recommendation.lower())
            # Injection attempt is surfaced as evidence, instruction not followed.
            self.assertTrue(any("untrusted" in e.finding.lower() or "override" in e.finding.lower() for e in d.evidence))

    # F. Tool / API unavailable ---------------------------------------------
    def test_F_api_unavailable_safe_degradation(self):
        for arch in ARCHS:
            d = self._decision("REQ-1009", arch)  # NimbusAI -> 503
            self.assertIn("vendor_risk_unavailable", d.risk_flags)
            self.assertIn("security_review_required", d.risk_flags)
            self.assertTrue(any("unavailable" in e.finding.lower() for e in d.evidence))
            # Must NOT infer a favorable/approved status from the outage.
            self.assertNotIn("approved", d.recommendation.lower())


if __name__ == "__main__":
    unittest.main()
