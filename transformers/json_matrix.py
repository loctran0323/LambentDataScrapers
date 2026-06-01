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
                # Kevin's revised matrix: rule 4, "FL DCF Mandatory Counseling
                # Minutes & Phase Ratios" — to be hardcoded (no scraping needed).
                "matrix_rule_number": 4,
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
                # Kevin's 2026-05 overhaul renumbers this to rule 38 AND proposes
                # reclassifying it as Engine 2 NLP (narrative-completeness check on
                # the admission note). Pending the full revised matrix — flagged for
                # Monday; the deterministic Engine-1 gate stays live until then.
                "matrix_rule_number": 38,
                "pending_reclassification": "engine2_nlp",
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
                # Kevin's 2026-05 overhaul: rule 33, Engine 1 & 2 dual-threat
                # (CMS Z59.0 housing-instability + SAMHSA safe-storage guardrail).
                "matrix_rule_number": 33,
            },
        ),
        _rule(
            "R-FED-05",
            "Federal SAMHSA",
            "TAKEHOME",
            None,
            "TakeHomeStabilityGate",
            source_key=r.source_key,
            friendly_message=(
                f"2024 SAMHSA take-home gate. Day-1 take-homes are now permitted for "
                f"stable patients, but (1) during the first "
                f"{p.get('initial_window_days', 14)} days the supply is capped at "
                f"{p.get('initial_cap_days', 7)} days (42 CFR 8.12(i)(3)(i)), and "
                f"(2) the Medical Director's risk/benefit Stability Assessment must "
                f"be signed in the chart first (42 CFR 8.12(i)(2)). Block the "
                f"dispense if days_in_treatment < {p.get('initial_window_days', 14)} "
                f"AND requested take-home days > {p.get('initial_cap_days', 7)}, OR "
                f"if the Stability Assessment is not documented."
            ),
            extra={
                # Two-part gate, per Kevin's 2026-05 update of the day-in-the-life:
                #   block if (days_in_treatment < initial_window_days AND
                #             takehome_days > initial_cap_days)
                #        OR (takehome_requested AND not stability_assessment_complete)
                "initial_window_days": p.get("initial_window_days", 14),
                "initial_cap_days": p.get("initial_cap_days", 7),
                # Tiered caps after the initial window (8.12(i)(3)).
                "cap_schedule_days": p.get(
                    "cap_schedule",
                    {"0-14": 7, "15-31": 14, "32+": 28},
                ),
                "requires_stability_assessment": True,
                "stability_flag_field": "Stability_Assessment_Complete",
                "regulatory_basis": "42 CFR 8.12(i)(2), 8.12(i)(3)(i) (2024 final rule)",
                "maturity": "established",  # confirmed 2024 final rule, not speculative
                "matrix_rule_number": 8,    # Kevin's revised matrix alignment
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
                "matrix_rule_number": 2,  # Kevin's revised matrix: DEA Zero-Variance Dispensing Log
            },
        ),
    ]


def _from_ncci(r: ScrapeResult) -> list[dict]:
    edits = r.parsed.get("edits", [])
    # Namespace the rule IDs by source so the Medicare practitioner table
    # (cms_ncci_edits) and the Medicaid H-code table (cms_ncci_medicaid) can both
    # emit edits without colliding on R-NCCI-0000.
    is_medicaid = r.source_key == "cms_ncci_medicaid"
    prefix = "R-NCCIMCD" if is_medicaid else "R-NCCI"
    payer = "Medicaid Clearinghouse" if is_medicaid else "Standard Clearinghouse"
    rules: list[dict] = []
    for i, edit in enumerate(edits):
        rules.append(
            _rule(
                f"{prefix}-{i:04d}",
                payer,
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


# ---- 37-Rule Matrix: additional Engine 1 gates (Phase 1 rules 28,29,30,35-37)
# These are hardcoded from their governing citations (like the AHCA R-FL-* rules);
# their config sources are diff-tracked source-of-record. Each stamps
# params.matrix_rule_number for 1:1 traceability to the Rule Master Matrix doc.
#
# DEFERRED (not implemented yet, per team decision): rule 11 ("No-Pay, No
# Take-Home", internal_policy) and rule 32 (AI-consent gate,
# fl_ahca_carf_ai_consent). Their config sources stay as source-of-record but no
# builder emits a rule until legal/policy sign-off.
def _from_fl_dcf_fasams(r: ScrapeResult) -> list[dict]:
    # Matrix rules 28 & 29 — FL DCF / Managing Entity (MSO) data + eligibility.
    return [
        _rule(
            "R-FASAMS-01",
            "FL DCF/MSO",
            "INTAKE",
            None,
            "IntakeFieldValidation",
            source_key=r.source_key,
            friendly_message=(
                "FL Managing Entity (MSO) invoices require the FASAMS mandatory "
                "demographic and employment fields to be complete at intake. "
                "Missing fields cause state-funded invoice rejection — block "
                "intake completion until they are filled."
            ),
            extra={
                "matrix_rule_number": 28,
                "required_field_groups": ["demographic", "employment"],
                "regulatory_basis": "FL DCF Pamphlet 155-2 (FASAMS data elements)",
                "maturity": "evolving",
            },
        ),
        _rule(
            "R-FASAMS-02",
            "FL DCF/MSO",
            "ELIGIBILITY",
            None,
            "EligibilityExpiryWarning",
            source_key=r.source_key,
            friendly_message=(
                f"Financial eligibility re-assessment is due within "
                f"{r.parsed.get('eligibility_warning_days', 30)} days. Bill against "
                f"an expired determination and the state can claw the funds back — "
                f"warn staff to re-assess before it lapses."
            ),
            extra={
                "matrix_rule_number": 29,
                "warning_window_days": r.parsed.get("eligibility_warning_days", 30),
                "regulatory_basis": "FL DCF financial eligibility / sliding-fee rules",
                "maturity": "evolving",
            },
        ),
    ]


def _from_fl_mso_contracts(r: ScrapeResult) -> list[dict]:
    # Matrix rule 30 — regional MSO modifier routing. Contract-specific, not a
    # single public statute, so maturity="contract".
    return [
        _rule(
            "R-MSO-01",
            "Regional MSO",
            "ALL_SUD",
            None,
            "RegionalModifierRouting",
            source_key=r.source_key,
            friendly_message=(
                "FL is carved into regional Managing Entities (e.g. LSF vs. Central "
                "Florida Cares) with different billing modifiers. Apply the modifier "
                "set for the patient's region before submitting, or the MSO will "
                "reject the claim."
            ),
            extra={
                "matrix_rule_number": 30,
                "regulatory_basis": "Regional MSO contracts (LSF, Central Florida Cares, etc.)",
                "maturity": "contract",
            },
        )
    ]


def _from_fl_eforcse_pdmp(r: ScrapeResult) -> list[dict]:
    # Matrix rule 35 — Florida PDMP (E-FORCSE) query mandate before prescribing.
    return [
        _rule(
            "R-PDMP-01",
            "FL E-FORCSE",
            "PRESCRIBE",
            None,
            "PdmpQueryGate",
            source_key=r.source_key,
            friendly_message=(
                "Florida law requires the prescriber to check the E-FORCSE PDMP "
                "before prescribing a controlled substance. Confirm a documented "
                "PDMP query for this patient before the prescription is finalized."
            ),
            extra={
                "matrix_rule_number": 35,
                "regulatory_basis": "FL Statute 893.055(2)(a)",
                "maturity": "established",
            },
        )
    ]


def _from_fl_65d30_fac(r: ScrapeResult) -> list[dict]:
    # Matrix rules 36 & 37 — FL Admin Code 65D-30 (personnel + treatment planning).
    return [
        _rule(
            "R-CRED-01",
            "FL DCF Medicaid",
            "COUNSELING",
            None,
            "CredentialingBillingBlock",
            source_key=r.source_key,
            friendly_message=(
                "Counseling billed under an unlicensed counselor must have a "
                "qualified supervisor of record (FL 65D-30 personnel standards). "
                "Block billing for sessions lacking documented supervision — "
                "unsupervised counselor time is a retroactive Medicaid clawback."
            ),
            extra={
                "matrix_rule_number": 36,
                "regulatory_basis": "FL Admin Code 65D-30.004 (personnel)",
                "maturity": "established",
            },
        ),
        _rule(
            "R-TPLAN-01",
            "CARF/FL",
            "TREATMENT_PLAN",
            None,
            "TreatmentPlanExpiry",
            source_key=r.source_key,
            friendly_message=(
                f"Treatment plans must be reviewed/updated on the required cadence "
                f"(CARF + FL 65D-30). Alert staff "
                f"{r.parsed.get('plan_warning_days', 14)} days before this patient's "
                f"plan expires so a CARF survey never finds a lapsed plan."
            ),
            extra={
                "matrix_rule_number": 37,
                "warning_window_days": r.parsed.get("plan_warning_days", 14),
                "regulatory_basis": "CARF Behavioral Health standards + FL 65D-30.0046",
                "maturity": "established",
            },
        ),
    ]


_DISPATCH = {
    "cms_pub_100_04_ch39": _from_pub_100_04,
    "cms_pub_100_02_ch17": _from_pub_100_02,
    "fl_ahca_cbh_handbook": _from_ahca,
    "cms_ncci_edits": _from_ncci,
    "cms_ncci_medicaid": _from_ncci,
    "fl_mac_fcso_otp": _from_fcso,
    "sunshine_provider_manual": _from_sunshine,
    "simply_provider_resources": _from_simply,
    "ecfr_42_part_8": _from_ecfr_42_part_8,
    # 37-Rule Matrix additions (Phase 1 Engine-1 gates):
    # rule 11 (internal_policy) and rule 32 (fl_ahca_carf_ai_consent) DEFERRED —
    # not dispatched until legal/policy sign-off.
    "fl_dcf_fasams": _from_fl_dcf_fasams,                # rules 28, 29
    "fl_mso_contracts": _from_fl_mso_contracts,          # rule 30
    "fl_eforcse_pdmp": _from_fl_eforcse_pdmp,            # rule 35
    "fl_65d30_fac": _from_fl_65d30_fac,                  # rules 36, 37
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
