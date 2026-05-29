"""Smoke tests for the Engine 1 rule matrix builder.

Run with: python -m pytest tests/ -q  (or python -m unittest discover tests)

Why these specific assertions:
  - Every emitted rule MUST carry a non-empty friendly_message
    (2026-05-22 schema decision — the rule-preview UI relies on it).
  - R-FED-02 must fire whenever iop_threshold_services is set
    (regressed once when CMS spelled the number out as "nine").
  - Federal rules must come before Florida rules in the output
    (Engine 1 evaluation order).
"""
from __future__ import annotations

import unittest

from scrapers.base import ScrapeResult
from transformers.json_matrix import build_rule_matrix


def _result(source_key: str, parsed: dict) -> ScrapeResult:
    return ScrapeResult(
        source_key=source_key,
        source_name=source_key,
        fetched_at="2026-05-22T00:00:00Z",
        content_sha256="x" * 64,
        raw_path="",
        parsed=parsed,
    )


class FriendlyMessageContract(unittest.TestCase):
    def test_every_rule_has_non_empty_friendly_message(self):
        results = [
            _result("cms_pub_100_04_ch39", {"weekly_bundle_min_services": 1,
                                            "g_codes_mentioned": ["G2067", "G2068"]}),
            _result("cms_pub_100_02_ch17", {"iop_threshold_services": 9,
                                            "iop_window_days": 7}),
            _result("fl_ahca_cbh_handbook", {"any_HF_mentioned": True}),
            _result("fl_mac_fcso_otp", {"g_codes_mentioned": ["G2067"]}),
            _result("sunshine_provider_manual", {"requires_dx_with_h_codes": True,
                                                 "h_codes_referenced": ["H0020"]}),
            _result("simply_provider_resources", {"min_counseling_threshold_minutes": 15}),
        ]
        matrix = build_rule_matrix(results)
        self.assertGreater(matrix["rule_count"], 0)
        for rule in matrix["rules"]:
            with self.subTest(rule_id=rule["rule_id"]):
                self.assertIn("friendly_message", rule)
                self.assertTrue(rule["friendly_message"].strip(),
                                f"{rule['rule_id']} has empty friendly_message")


class IopThresholdRule(unittest.TestCase):
    def test_r_fed_02_fires_when_threshold_set(self):
        matrix = build_rule_matrix([
            _result("cms_pub_100_02_ch17", {"iop_threshold_services": 9,
                                            "iop_window_days": 7}),
        ])
        ids = [r["rule_id"] for r in matrix["rules"]]
        self.assertIn("R-FED-02", ids)

    def test_r_fed_02_silent_when_threshold_missing(self):
        matrix = build_rule_matrix([
            _result("cms_pub_100_02_ch17", {"iop_threshold_services": None}),
        ])
        ids = [r["rule_id"] for r in matrix["rules"]]
        self.assertNotIn("R-FED-02", ids)


class RuleOrdering(unittest.TestCase):
    def test_federal_rules_emitted_before_florida(self):
        # build_rule_matrix skips sources with an empty parsed dict, so seed each
        # with a sentinel field — the AHCA builder doesn't read parsed fields,
        # the rules are hardcoded from the addendum.
        results = [
            _result("fl_ahca_cbh_handbook", {"doc": "AHCA"}),
            _result("cms_pub_100_04_ch39", {"weekly_bundle_min_services": 1,
                                            "g_codes_mentioned": []}),
        ]
        matrix = build_rule_matrix(results)
        ids = [r["rule_id"] for r in matrix["rules"]]
        self.assertEqual(ids[0], "R-FED-01")
        self.assertIn("R-FL-02", ids[1:])


class FederalPointOfCareGates(unittest.TestCase):
    """42 CFR 8.12 / DEA gates the training masterclass assigns to Engine 1."""

    def test_ecfr_emits_admission_takehome_and_vault_rules(self):
        matrix = build_rule_matrix([
            _result("ecfr_42_part_8", {"doc": "42 CFR Part 8"}),
        ])
        ids = [r["rule_id"] for r in matrix["rules"]]
        for expected in ("R-FED-03", "R-FED-04", "R-FED-05", "R-FED-06"):
            self.assertIn(expected, ids)

    def test_federal_gates_carry_regulatory_basis_and_maturity(self):
        matrix = build_rule_matrix([
            _result("ecfr_42_part_8", {"doc": "42 CFR Part 8"}),
        ])
        fed = [r for r in matrix["rules"] if r["rule_id"].startswith("R-FED-0")
               and r["rule_id"] not in ("R-FED-01", "R-FED-02")]
        for rule in fed:
            with self.subTest(rule_id=rule["rule_id"]):
                self.assertIn("regulatory_basis", rule["params"])
                self.assertIn(rule["params"]["maturity"], {"established", "evolving"})

    def test_ecfr_gates_emit_before_florida(self):
        matrix = build_rule_matrix([
            _result("fl_ahca_cbh_handbook", {"doc": "AHCA"}),
            _result("ecfr_42_part_8", {"doc": "42 CFR Part 8"}),
        ])
        ids = [r["rule_id"] for r in matrix["rules"]]
        self.assertLess(ids.index("R-FED-03"), ids.index("R-FL-02"))


class MedicaidNCCIEdits(unittest.TestCase):
    """H0020 unbundling edits come from the Medicaid NCCI file, not Medicare."""

    def test_medicaid_edits_get_namespaced_rule_ids(self):
        matrix = build_rule_matrix([
            _result("cms_ncci_medicaid", {"edits": [
                {"column1": "H0020", "column2": "80305", "modifier_indicator": "0"},
            ]}),
        ])
        ids = [r["rule_id"] for r in matrix["rules"]]
        self.assertIn("R-NCCIMCD-0000", ids)
        self.assertNotIn("R-NCCI-0000", ids)  # must not collide with Medicare set

    def test_medicare_and_medicaid_edits_do_not_collide(self):
        matrix = build_rule_matrix([
            _result("cms_ncci_edits", {"edits": [
                {"column1": "G2067", "column2": "99213", "modifier_indicator": "1"},
            ]}),
            _result("cms_ncci_medicaid", {"edits": [
                {"column1": "H0020", "column2": "80305", "modifier_indicator": "0"},
            ]}),
        ])
        ids = [r["rule_id"] for r in matrix["rules"]]
        self.assertIn("R-NCCI-0000", ids)
        self.assertIn("R-NCCIMCD-0000", ids)
        self.assertEqual(len(ids), len(set(ids)))  # all rule IDs unique


class ScrapeFailureGuard(unittest.TestCase):
    def test_failed_scrape_does_not_fabricate_rules(self):
        failed = _result("cms_pub_100_04_ch39", {})
        failed.warnings.append("scrape_failed: 503 from CMS")
        matrix = build_rule_matrix([failed])
        self.assertEqual(matrix["rule_count"], 0)


if __name__ == "__main__":
    unittest.main()
