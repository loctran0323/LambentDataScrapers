"""Central config for the VAIntage Pathways Engine 1 / Engine 2 scraping pipeline.

All source URLs live here so a non-engineer can update a link without touching parser code.
The cadence field drives the Azure Functions cron schedule (Phase 5).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
VECTOR_DIR = DATA_DIR / "vector"
DIFF_DIR = DATA_DIR / "diffs"

for _d in (RAW_DIR, PROCESSED_DIR, VECTOR_DIR, DIFF_DIR):
    _d.mkdir(parents=True, exist_ok=True)

Cadence = Literal["monthly", "quarterly", "bi-annually", "annually", "ad-hoc"]
Engine = Literal["engine1", "engine2"]


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    url: str
    cadence: Cadence
    engine: Engine
    # Hint for the scraper dispatcher. Real fetches sometimes need Selenium for JS-heavy
    # pages (AHCA), or a PDF parser. "html" is the default.
    fetch_mode: Literal["html", "pdf", "json-api", "selenium"] = "html"
    notes: str = ""


# ---------- Engine 1: Financial & Billing Rule Matrix sources ----------
ENGINE1_SOURCES: list[Source] = [
    Source(
        key="cms_pub_100_04_ch39",
        name="CMS IOM Pub 100-04, Medicare Claims Processing Manual, Ch 39 (OTP)",
        url="https://www.cms.gov/files/document/chapter-39-opioid-treatment-programs-otps.pdf",
        cadence="monthly",
        engine="engine1",
        fetch_mode="pdf",
        notes="Federal baseline: weekly G-code bundle thresholds (G2067-G2075).",
    ),
    Source(
        key="cms_pub_100_02_ch17",
        name="CMS IOM Pub 100-02, Medicare Benefit Policy Manual, Ch 17 (OTP)",
        url="https://www.cms.gov/files/document/chapter-17-opioid-treatment-programs-otps.pdf",
        cadence="monthly",
        engine="engine1",
        fetch_mode="pdf",
        notes="Federal baseline: medical necessity + IOP thresholds (G0137 = 9 services / 7 days).",
    ),
    Source(
        key="cms_mln_otp_booklet",
        name="CMS MLN Booklet — OTP Medicare Billing & Payment (MLN8296732)",
        url="https://www.cms.gov/sites/default/files/2021-12/2021_12_MLN8296732_OTP_Billing_Payment_FINAL_0.pdf",
        cadence="bi-annually",
        engine="engine1",
        fetch_mode="pdf",
    ),
    Source(
        key="fl_ahca_cbh_handbook",
        name="Florida AHCA — Community Behavioral Health Services Handbook",
        url="https://ahca.myflorida.com/medicaid/medicaid-policy-quality-and-operations/medicaid-policy-and-quality/medicaid-policy/medical-and-behavioral-health-coverage-policy/behavioral-health-and-health-facilities/community-behavioral-health-services",
        cadence="quarterly",
        engine="engine1",
        fetch_mode="selenium",
        notes="JS-rendered listing of handbooks + fee schedules. AHCA returns 403 to "
        "headless requests, so use Selenium with a real UA.",
    ),
    Source(
        key="fl_mac_fcso_otp",
        name="FCSO (Medicare FL MAC) — OTP Specialty Page",
        # FCSO hosts an HTML landing page (not a single PDF). The scraper
        # harvests G-code mentions + outbound CMS PDF references from the HTML.
        url="https://medicare.fcso.com/specialties/otp",
        cadence="bi-annually",
        engine="engine1",
        fetch_mode="html",
    ),
    Source(
        key="cms_ncci_edits",
        name="CMS NCCI PTP Edits — Practitioner",
        url="https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits",
        cadence="quarterly",
        engine="engine1",
        notes="Landing page links out to quarterly ZIPs containing PTP edit CSVs.",
    ),
    Source(
        key="sunshine_provider_manual",
        name="Sunshine Health (Centene MCO) — Provider Manuals",
        url="https://www.sunshinehealth.com/providers/resources/provider-manuals.html",
        cadence="monthly",
        engine="engine1",
    ),
    Source(
        key="simply_provider_resources",
        name="Simply Healthcare — Provider Resources",
        url="https://provider.simplyhealthcareplans.com/florida-provider/home",
        cadence="quarterly",
        engine="engine1",
    ),
]

# ---------- Engine 2: Clinical / SDOH NLP corpus sources ----------
ENGINE2_SOURCES: list[Source] = [
    Source(
        key="ecfr_42_part_8",
        name="42 CFR Part 8 — Medications for Treatment of Opioid Use Disorder",
        # eCFR API root. The scraper resolves the latest issue date and pulls
        # XML for Part 8 from /full/{date}/title-42.xml.
        url="https://www.ecfr.gov",
        cadence="ad-hoc",
        engine="engine2",
        fetch_mode="json-api",
        notes="Federal SAMHSA OTP take-home & certification rules.",
    ),
    Source(
        key="samhsa_tip_63",
        name="SAMHSA Treatment Improvement Protocol (TIP) 63 — MAT",
        url="https://store.samhsa.gov/sites/default/files/pep21-02-01-002.pdf",
        cadence="bi-annually",
        engine="engine2",
        fetch_mode="pdf",
    ),
    Source(
        key="cdc_icd10_z_codes",
        name="CDC ICD-10-CM — Z-Codes (SDOH)",
        url="https://www.cdc.gov/nchs/icd/icd-10-cm/files.html",
        cadence="annually",
        engine="engine2",
    ),
    Source(
        key="asam_criteria",
        name="ASAM Criteria, 4th Edition — Patient Placement / Step-down",
        url="https://www.asam.org/asam-criteria",
        cadence="ad-hoc",
        engine="engine2",
        notes="Subscription-gated. Phase 1 task: confirm institutional access; phase 2 wires it in.",
    ),
]

ALL_SOURCES: list[Source] = ENGINE1_SOURCES + ENGINE2_SOURCES


def by_key(key: str) -> Source:
    for s in ALL_SOURCES:
        if s.key == key:
            return s
    raise KeyError(f"Unknown source key: {key}")


# ---------- Pipeline knobs ----------
# Plain Chrome UA. The earlier "VAIntagePathwaysBot/0.1" prefix tripped
# Cloudflare-style bot detection on store.samhsa.gov (403). Once we have a
# stable IP allowlist with each agency, swap back to a signed bot UA.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECS = 30
MAX_RETRIES = 3
# Engine 1's stated SLA. The diff-checker logs a warning if upstream payloads
# get large enough to threaten this budget downstream.
ENGINE1_LATENCY_BUDGET_MS = 200
