from __future__ import annotations

"""Unit tests for the deterministic policy engine.

These tests pin down the business rules in data/procurement_policy.md so the
LLM layer can never silently change them. No network or LLM is used.
"""

import unittest
from datetime import date

from src import policy_engine as pe


def base_request(**overrides):
    req = {
        "requester_id": "E004",
        "requester": "Noah Williams",
        "department": "Finance",
        "product_name": "SomeTool",
        "vendor_name": "SomeVendor",
        "category": "Utility",
        "annual_cost_usd": 500,
        "user_count": 3,
        "business_justification": "A valid business reason.",
        "data_access_level": "none",
        "requested_integrations": [],
    }
    req.update(overrides)
    return req


class ApprovalThresholdTests(unittest.TestCase):
    def test_tier_manager(self):
        self.assertEqual(pe.approvals_for_amount(1000.0), ["Manager"])
        self.assertEqual(pe.approvals_for_amount(1.0), ["Manager"])

    def test_tier_dept_head_procurement(self):
        self.assertEqual(
            pe.approvals_for_amount(1000.01), ["Department Head", "Procurement"]
        )
        self.assertEqual(
            pe.approvals_for_amount(10000.0), ["Department Head", "Procurement"]
        )

    def test_tier_finance(self):
        self.assertEqual(
            pe.approvals_for_amount(10000.01),
            ["Department Head", "Finance", "Procurement"],
        )
        self.assertEqual(
            pe.approvals_for_amount(25000.0),
            ["Department Head", "Finance", "Procurement"],
        )

    def test_tier_cfo(self):
        self.assertEqual(
            pe.approvals_for_amount(25000.01),
            ["Department Head", "Finance", "CFO", "Procurement"],
        )
        self.assertEqual(
            pe.approvals_for_amount(1_000_000),
            ["Department Head", "Finance", "CFO", "Procurement"],
        )

    def test_threshold_applied_in_full_evaluation(self):
        res = pe.evaluate_policy(base_request(annual_cost_usd=800), budget={"available_usd": 29000})
        self.assertIn("Manager", res.required_approvals)


class BudgetTests(unittest.TestCase):
    def test_within_budget_no_flag(self):
        res = pe.evaluate_policy(base_request(annual_cost_usd=500), budget={"available_usd": 1000})
        self.assertNotIn("budget_insufficient", res.risk_flags)

    def test_over_budget_flags_and_finance(self):
        res = pe.evaluate_policy(base_request(annual_cost_usd=5000), budget={"available_usd": 1000})
        self.assertIn("budget_insufficient", res.risk_flags)
        self.assertIn("Finance", res.required_approvals)

    def test_budget_unavailable_flagged(self):
        res = pe.evaluate_policy(base_request(annual_cost_usd=5000), budget=None)
        self.assertIn("budget_unavailable", res.risk_flags)


class MissingInformationTests(unittest.TestCase):
    def test_missing_cost_and_users_and_data(self):
        req = base_request(annual_cost_usd=None, user_count=None, data_access_level="unknown")
        res = pe.evaluate_policy(req, budget={"available_usd": 1000})
        self.assertIn("missing_information", res.risk_flags)
        joined = " ".join(res.missing_information).lower()
        self.assertIn("cost", joined)
        self.assertIn("user", joined)
        self.assertIn("data", joined)

    def test_complete_request_no_missing_flag(self):
        res = pe.evaluate_policy(base_request(), budget={"available_usd": 1000})
        self.assertNotIn("missing_information", res.risk_flags)
        self.assertEqual(res.missing_information, [])


class SecurityTests(unittest.TestCase):
    def test_source_code_triggers_security(self):
        req = base_request(data_access_level="source_code")
        res = pe.evaluate_policy(req, budget={"available_usd": 100000})
        self.assertIn("security_review_required", res.risk_flags)
        self.assertIn("Security", res.required_approvals)

    def test_customer_pii_triggers_security(self):
        req = base_request(data_access_level="customer_pii")
        res = pe.evaluate_policy(req, budget={"available_usd": 100000})
        self.assertIn("security_review_required", res.risk_flags)

    def test_production_integration_triggers_security(self):
        req = base_request(
            data_access_level="production_telemetry",
            requested_integrations=["Production cloud account"],
        )
        res = pe.evaluate_policy(req, budget={"available_usd": 100000})
        self.assertIn("security_review_required", res.risk_flags)

    def test_expired_api_status_triggers_security(self):
        req = base_request()
        risk = {
            "available": True,
            "security_review_status": "expired",
            "last_review_date": "2025-07-01",
            "processes_personal_data": False,
            "stores_data_outside_region": False,
        }
        res = pe.evaluate_policy(req, budget={"available_usd": 100000}, vendor_risk=risk)
        self.assertIn("security_review_required", res.risk_flags)


class StaleAndConflictTests(unittest.TestCase):
    def test_assessment_currency_boundary(self):
        ref = date(2026, 9, 30)
        # exactly 365 days old -> current
        self.assertTrue(pe.is_assessment_current(date(2025, 9, 30), ref))
        # 366 days old -> not current
        self.assertFalse(pe.is_assessment_current(date(2025, 9, 29), ref))
        self.assertFalse(pe.is_assessment_current(None, ref))

    def test_stale_registry_but_expired_api_agree_security(self):
        # SignalWatch-like: registry old date + API expired -> security triggered
        req = base_request(data_access_level="production_telemetry")
        registry = {
            "security_status": "Approved",
            "security_review_date": "2025-07-01",
            "procurement_status": "Approved",
            "legal_terms_status": "Approved",
        }
        risk = {
            "available": True,
            "security_review_status": "expired",
            "last_review_date": "2025-07-01",
            "processes_personal_data": False,
            "stores_data_outside_region": False,
        }
        res = pe.evaluate_policy(
            req, budget={"available_usd": 100000}, vendor_registry=registry, vendor_risk=risk
        )
        self.assertIn("security_review_required", res.risk_flags)

    def test_conflict_registry_approved_current_vs_api_expired(self):
        # Registry says approved+current, API says expired -> conflict surfaced
        req = base_request()
        registry = {
            "security_status": "Approved",
            "security_review_date": "2026-06-01",  # current
            "procurement_status": "Approved",
            "legal_terms_status": "Approved",
        }
        risk = {
            "available": True,
            "security_review_status": "expired",
            "last_review_date": "2025-01-01",
            "processes_personal_data": False,
            "stores_data_outside_region": False,
        }
        res = pe.evaluate_policy(
            req, budget={"available_usd": 100000}, vendor_registry=registry, vendor_risk=risk
        )
        self.assertIn("conflicting_vendor_evidence", res.risk_flags)
        self.assertIn("Security", res.required_approvals)


class PrivacyTests(unittest.TestCase):
    def test_pii_triggers_privacy(self):
        req = base_request(data_access_level="employee_pii")
        res = pe.evaluate_policy(req, budget={"available_usd": 100000})
        self.assertIn("privacy_review_required", res.risk_flags)
        self.assertIn("Privacy", res.required_approvals)

    def test_out_of_region_triggers_privacy_and_residency(self):
        req = base_request(data_access_level="customer_pii")
        risk = {
            "available": True,
            "security_review_status": "approved",
            "last_review_date": "2026-03-02",
            "processes_personal_data": True,
            "stores_data_outside_region": True,
        }
        res = pe.evaluate_policy(req, budget={"available_usd": 100000}, vendor_risk=risk)
        self.assertIn("privacy_review_required", res.risk_flags)
        self.assertIn("data_residency_risk", res.risk_flags)


class LegalTests(unittest.TestCase):
    def test_new_vendor_over_10k_triggers_legal(self):
        req = base_request(annual_cost_usd=15000)
        registry = {
            "procurement_status": "New",
            "security_status": "Unknown",
            "security_review_date": None,
            "legal_terms_status": "Unknown",
        }
        res = pe.evaluate_policy(req, budget={"available_usd": 100000}, vendor_registry=registry)
        self.assertIn("legal_review_required", res.risk_flags)
        self.assertIn("Legal", res.required_approvals)

    def test_new_vendor_under_10k_no_legal_from_spend(self):
        req = base_request(annual_cost_usd=5000)
        registry = {
            "procurement_status": "New",
            "security_status": "Pending",
            "security_review_date": None,
            "legal_terms_status": "Approved",
        }
        res = pe.evaluate_policy(req, budget={"available_usd": 100000}, vendor_registry=registry)
        self.assertNotIn("legal_review_required", res.risk_flags)

    def test_nonstandard_terms_trigger_legal(self):
        req = base_request(annual_cost_usd=500)
        registry = {
            "procurement_status": "Approved",
            "security_status": "Approved",
            "security_review_date": "2026-06-01",
            "legal_terms_status": "Draft",
        }
        res = pe.evaluate_policy(req, budget={"available_usd": 100000}, vendor_registry=registry)
        self.assertIn("legal_review_required", res.risk_flags)


class UnavailableEvidenceTests(unittest.TestCase):
    def test_vendor_risk_unavailable_escalates_security(self):
        req = base_request(data_access_level="confidential_documents")
        risk = {"available": False, "error": "503"}
        res = pe.evaluate_policy(req, budget={"available_usd": 100000}, vendor_risk=risk)
        self.assertIn("vendor_risk_unavailable", res.risk_flags)
        self.assertIn("security_review_required", res.risk_flags)
        self.assertIn("Security", res.required_approvals)


class HumanReviewTests(unittest.TestCase):
    def test_human_review_always_required(self):
        res = pe.evaluate_policy(base_request(), budget={"available_usd": 100000})
        self.assertTrue(res.human_review_required)


if __name__ == "__main__":
    unittest.main()
