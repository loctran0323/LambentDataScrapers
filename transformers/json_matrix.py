"""Map raw ScrapeResults to the Engine 1 JSON Rule Matrix.

The matrix shape is the contract the C# Desktop Agent reads. Field names match
the addendum table in the onboarding doc 1:1 so the agent's evaluator does not
need a translation layer.

Federal rules are emitted first, Florida-specific overrides second — matches the
"baseline before override" execution order required by Engine 1.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from scrapers.base import ScrapeResult

RULE_SCHEMA_VERSION = "2026.05.22"


def _rule(
    rule_id: str,
    payer_type: str,
    code: str,
    required_modifier: str | None,
    logic_type: str,
    *,
    source_key: str,
    friendly_message: str,
    extra: dict | None = None,
) -> dict:
    # extra (-> "params") carries logic-specific config the C# agent reads.
    # For federal point-of-care gates we additionally stamp:
    #   regulatory_basis : the CFR cite the rule enforces (auditability)
    #   maturity         : "established" (stable statute) | "evolving" (rule is
    #                      mid-rulemaking; treat as a warning, not a hard block,
    #                      until confirmed — same caution we apply to Rule 32).
    # friendly_message is required per the 2026-05-22 schema bump — it is what
    # the rule-preview UI shows to a reviewer when they accept/reject a rule.
    if not friendly_message.strip():
        raise ValueError(f"{rule_id}: friendly_message is required")
    out = {
        "rule_id": rule_id,
        "payer_type": payer_type,
        "code": code,
        "required_modifier": required_modifier,
        "logic_type": logic_type,
        "source_key": source_key,
        "friendly_message": friendly_message,
    }
    if extra:
        out["params"] = extra
    return out


# ---- per-source builders --------------------------------------------------------
def _from_pub_100_04(r: ScrapeResult) -> list[dict]:
    p = r.parsed
    window = 7
    min_in_bundle = p.get("weekly_bundle_min_services", 1)
    return [
        _rule(
            "R-FED-01",
            "Medicare Advantage",
            "G2067",
            None,
            "BundleValidation",
            source_key=r.source_key,
            friendly_message=(
                f"Medicare OTP weekly bundle (G2067–G2079) must include at least "
                f"{min_in_bundle} qualifying service within a {window}-day window. "
                f"Billing the bundle without an in-window service will be denied "
                f"per CMS Pub 100-04 Ch 39."
            ),
            extra={
                "min_services_in_bundle": min_in_bundle,
                "window_days": window,
                "g_codes_in_scope": p.get("g_codes_mentioned", []),
            },
        )
    ]


def _from_pub_100_02(r: ScrapeResult) -> list[dict]:
    p = r.parsed
    threshold = p.get("iop_threshold_services")
    if not threshold:
        return []
    window = p.get("iop_window_days", 7)
    return [
        _rule(
            "R-FED-02",
            "Medicare",
            "G0137",
            None,
            "IopThresholdGuard",
            source_key=r.source_key,
            friendly_message=(
                f"Medicare IOP add-on G0137 requires at least {threshold} qualifying "
                f"services in a {window}-day window. Below this threshold the claim "
                f"does not meet IOP medical-necessity criteria in CMS Pub 100-02 Ch 17."
            ),
            extra={
                "required_services": threshold,
                "window_days": window,
            },
        )
    ]


def _from_ahca(r: ScrapeResult) -> list[dict]:
    # The AHCA scraper produces handbook + fee-schedule excerpts. We emit the two
    # rules the addendum calls out by name; the source PDFs feed the diff-checker
    # so changes in modifier requirements get flagged for human review.
    return [
        _rule(
            "R-FL-02",
            "Managed Medicaid",
            "H0020",
            "POS-58",
            "PointOfCareBlock",
            source_key=r.source_key,
            friendly_message=(
                "FL Managed Medicaid requires methadone administration (H0020) to be "
                "billed at Place-of-Service 58 (non-residential SUD facility). Other "
                "POS values will be rejected by AHCA."
            ),
        ),
        _rule(
            "R-FL-03",
            "AHCA Medicaid",
            "ALL_SUD",
            "HF",
            "SubstanceAbuseModifier",
            source_key=r.source_key,
            friendly_message=(
                "FL AHCA Medicaid SUD services must carry the HF modifier (substance "
                "abuse program). Claims missing HF on SUD codes will be denied."
            ),
        ),
        _rule(
            "R-FL-04",
            "AHCA Medicaid",
            "H0020",
            "HD>HG",  # ordering rule: HD must precede HG when pregnancy ICD present
            "ModifierSequencer",
            source_key=r.source_key,
            friendly_message=(
                "When H0020 is billed for a pregnant patient (ICD-10 prefix O…), "
                "modifier HD (pregnant/parenting program) must appear before HG "
                "(opioid addiction treatment); otherwise use HG alone."
            ),
            extra={"trigger_icd_prefix": "O", "alt_modifier": "HG"},
        ),
        _rule(
            "R-FL-05",
            "AHCA Medicaid",
            "H0020",
            None,
            "DosingCounselingRatio",
            source_key=r.source_key,
            friendly_message=(
                f"FL Medicaid requires methadone dosing (H0020) to be matched by "
                f"counseling: at least {r.parsed.get('min_counseling_per_dosing_days', 1)} "
                f"documented counseling session per {r.parsed.get('dosing_ratio_window_days', 30)}-day "
                f"window. Dosing that outruns the counseling ratio fails AHCA "
                f"medical-necessity review and is a clawback risk."
            ),
            extra={
                "min_counseling_sessions": r.parsed.get("min_counseling_per_dosing_days", 1),
                "window_days": r.parsed.get("dosing_ratio_window_days", 30),
                "regulatory_basis": "FL AHCA Community Behavioral Health Services Handbook",
                "maturity": "evolving",  # ratio not enumerated in scraped PDF; diff-tracked
            },
        ),
    ]


def _from_ecfr_42_part_8(r: ScrapeResult) -> list[dict]:
    """Federal SAMHSA/DEA point-of-care gates (Engine 1, all 50 states).

    The training masterclass puts take-home, admission, and vault enforcement in
    *Engine 1* — they are deterministic blocks that need no AI consent and fire
    before any clinical-note analysis. They are grounded in 42 CFR 8.12 (OTP
    standards) and DEA recordkeeping (21 CFR 1304), not in a payer fee schedule,
    so payer_type is the governing authority and `code` is a domain sentinel the
    agent dispatches on by logic_type. The same source still feeds Engine 2's RAG
    corpus; this builder only adds the deterministic slice.
    """
    p = r.parsed
    return [
        _rule(
            "R-FED-03",
            "Federal SAMHSA",
            "ADMISSION",
            None,
            "AdmissionEligibilityGuard",
            source_key=r.source_key,
            friendly_message=(
                "SAMHSA requires a documented 1-year history of opioid addiction "
                "before admission to methadone maintenance (42 CFR 8.12(e)(1)). "
                "Admit without it only under a documented exception (pregnancy, "
                "release from incarceration within 6 months, or prior treatment "
                "within 2 years); otherwise the admission is non-compliant."
            ),
            extra={
                "min_history_months": p.get("admission_min_history_months", 12),
                "documented_exceptions": [
                    "pregnancy",
                    "released_from_incarceration_within_6mo",
                    "prior_treatment_within_2yr",
                ],
                "regulatory_basis": "42 CFR 8.12(e)(1)",
                "maturity": "established",
            },
        ),
        _rule(
            "R-FED-04",
            "Federal SAMHSA",
            "TAKEHOME",
            None,
            "TakeHomeStabilityGuard",
            source_key=r.source_key,
            friendly_message=(
                "Unsupervised (take-home) methadone requires the practitioner to "
                "find the patient stable across the 42 CFR 8.12(i)(2) factors — "
                "including a stable home environment. Patients lacking secure "
                "housing fail this factor; block the take-home and route to "
                "supervised dosing until the determination is documented."
            ),
            extra={
                "blocking_factor": "stable_home_environment",
                "all_factors": "42 CFR 8.12(i)(2)(i)-(viii)",
                "regulatory_basis": "42 CFR 8.12(i)(2)",
                "maturity": "established",
            },
        ),
        _rule(
            "R-FED-05",
            "Federal SAMHSA",
            "TAKEHOME",
            None,
            "InitialTakeHomeWindow",
            source_key=r.source_key,
            friendly_message=(
                f"Take-home doses in the first {p.get('initial_takehome_min_days', 1)} "
                f"day(s) of treatment require an explicit documented clinical "
                f"justification under the 42 CFR 8.12(i) take-home framework. "
                f"On day 1 the patient has the least supporting history — warn the "
                f"prescriber before the dose is dispensed."
            ),
            extra={
                "initial_takehome_min_days": p.get("initial_takehome_min_days", 1),
                "regulatory_basis": "42 CFR 8.12(i)",
                # The 2024 SAMHSA final rule widened clinical discretion on early
                # take-homes, so this is a warning gate, not an absolute block.
                "maturity": "evolving",
            },
        ),
        _rule(
            "R-FED-06",
            "Federal DEA",
            "VAULT",
            None,
            "ControlledSubstanceReconciliation",
            source_key=r.source_key,
            friendly_message=(
                "The daily methadone vault must reconcile to zero variance: "
                "beginning inventory + receipts − dispensed − wasted must equal "
                "the physical count. Any non-zero variance is a DEA recordkeeping "
                "violation (21 CFR 1304.21–.22) and must block end-of-day close."
            ),
            extra={
                "allowed_variance_mg": p.get("allowed_variance_mg", 0),
                "regulatory_basis": "21 CFR 1304.21, 1304.22(c)",
                "maturity": "established",
            },
        ),
    ]


def _from_ncci(r: ScrapeResult) -> list[dict]:
    edits = r.parsed.get("edits", [])
    rules: list[dict] = []
    for i, edit in enumerate(edits):
        rules.append(
            _rule(
                f"R-NCCI-{i:04d}",
                "Standard Clearinghouse",
                edit["column1"],
                None,
                "BundleGuard",
                source_key=r.source_key,
                friendly_message=(
                    f"NCCI PTP edit: {edit['column1']} cannot be billed on the same "
                    f"day as {edit['column2']} without an appropriate modifier."
                ),
                extra={"mutually_exclusive_with": edit["column2"]},
            )
        )
    return rules


def _from_fcso(r: ScrapeResult) -> list[dict]:
    return [
        _rule(
            "R-FLMAC-01",
            "Medicare",
            "G2067",
            None,
            "PayerBlocker",
            source_key=r.source_key,
            friendly_message=(
                "FCSO (FL Medicare MAC) accepts only OTP G-codes (G2067–G2079) for "
                "OTP services. Standard HCPCS/CPT codes will be rejected on OTP claims."
            ),
            extra={
                "enforce_g_codes": r.parsed.get("g_codes_mentioned", []),
                "block_standard_hcpcs": True,
            },
        )
    ]


def _from_sunshine(r: ScrapeResult) -> list[dict]:
    # Addendum: "Write JSON constraint enforcing an F11.2x (Opioid dependence)
    # diagnosis presence whenever MAT H-codes are billed."
    p = r.parsed
    if not p.get("requires_dx_with_h_codes"):
        return []
    return [
        _rule(
            "R-SUNSHINE-01",
            "Sunshine MCO",
            "H0020",
            None,
            "DiagnosisRequired",
            source_key=r.source_key,
            friendly_message=(
                "Sunshine Health requires an F11.2x (opioid dependence) diagnosis "
                "on the claim whenever MAT H-codes are billed. Missing diagnosis "
                "will cause the claim to be denied."
            ),
            extra={
                "required_dx_prefix": "F11.2",
                "h_codes_in_scope": p.get("h_codes_referenced", [])[:25],
            },
        )
    ]


def _from_simply(r: ScrapeResult) -> list[dict]:
    # Addendum: "Implement regex scanner for txt_ClinicalNarrative time values;
    # trigger block/warning if < 15 mins is documented."
    threshold = r.parsed.get("min_counseling_threshold_minutes") or 15
    return [
        _rule(
            "R-SIMPLY-01",
            "Simply MCO",
            "COUNSELING",
            None,
            "CounselingTimeMinimum",
            source_key=r.source_key,
            friendly_message=(
                f"Simply Healthcare requires documented counseling time of at least "
                f"{threshold} minutes in the clinical narrative. Shorter sessions "
                f"will trigger a billing warning and may be denied on audit."
            ),
            extra={"min_minutes": threshold},
        )
    ]


_DISPATCH = {
    "cms_pub_100_04_ch39": _from_pub_100_04,
    "cms_pub_100_02_ch17": _from_pub_100_02,
    "fl_ahca_cbh_handbook": _from_ahca,
    "cms_ncci_edits": _from_ncci,
    "fl_mac_fcso_otp": _from_fcso,
    "sunshine_provider_manual": _from_sunshine,
    "simply_provider_resources": _from_simply,
    "ecfr_42_part_8": _from_ecfr_42_part_8,
}


def build_rule_matrix(results: Iterable[ScrapeResult]) -> dict:
    """Return the full Engine 1 matrix payload (federal first, FL second)."""
    federal_keys = {
        "cms_pub_100_04_ch39",
        "cms_pub_100_02_ch17",
        "cms_mln_otp_booklet",
        "ecfr_42_part_8",  # federal SAMHSA/DEA point-of-care gates (R-FED-03..06)
    }
    federal_rules: list[dict] = []
    florida_rules: list[dict] = []
    sources_seen: list[dict] = []

    for r in results:
        builder = _DISPATCH.get(r.source_key)
        if builder is None:
            continue
        # Don't fabricate rules from a scrape that didn't actually fetch anything.
        # If you want a permanent fallback rule when a source is unreachable, it
        # belongs in a hand-curated "baseline.json", not the auto-built matrix.
        if any("scrape_failed" in w for w in r.warnings) or not r.parsed:
            continue
        bucket = federal_rules if r.source_key in federal_keys else florida_rules
        bucket.extend(builder(r))
        sources_seen.append(
            {
                "source_key": r.source_key,
                "content_sha256": r.content_sha256,
                "fetched_at": r.fetched_at,
            }
        )

    return {
        "schema_version": RULE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules": federal_rules + florida_rules,
        "rule_count": len(federal_rules) + len(florida_rules),
        "sources": sources_seen,
    }
