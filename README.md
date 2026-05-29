# LambentDataScrapers — VAIntage Pathways™ "Brain"

Automated ingestion pipeline that scrapes federal & Florida-specific MAT billing
rules and clinical guidelines, transforms them into the **Engine 1 JSON Rule
Matrix** (financial guard) and **Engine 2 RAG corpus** (clinical guard) used by
the VAIntage Desktop Agent.

> Phase 2–5 of the *VAIntage Pathways Accelerated Summer Internship Project Timeline*

---

## Architecture

```
   ┌─────────────────────────────────────────────────────────┐
   │  SOURCES (public + subscription-gated)                  │
   │   • CMS IOM Pub 100-02 Ch 17 (OTP benefit policy)       │
   │   • CMS IOM Pub 100-04 Ch 39 (OTP claims processing)    │
   │   • CMS MLN OTP Booklet (MLN8296732)                    │
   │   • CMS NCCI PTP Edits (quarterly ZIP)                  │
   │   • FL AHCA Community Behavioral Health Services        │
   │   • FCSO (FL Medicare MAC) OTP Specialty Page           │
   │   • eCFR Title 42 Part 8 (federal OTP regulation)       │
   │   • SAMHSA TIP 63 (MAT clinical guidelines)             │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼  scrapers/  (BeautifulSoup, lxml, pdfplumber, Selenium)
   ┌─────────────────────────────────────────────────────────┐
   │  RAW PAYLOADS → data/raw/<source_key>/<sha12>.{pdf,xml,html}
   │  PARSED       → data/processed/<source_key>.json        │
   └─────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
   transformers/json_matrix.py       transformers/vector_chunker.py
   ┌─────────────────────────┐       ┌──────────────────────────┐
   │ Engine 1 Rule Matrix    │       │ Engine 2 RAG chunks      │
   │ (federal → FL overrides)│       │ (windowed, stable IDs)   │
   └─────────────────────────┘       └──────────────────────────┘
              │                                │
              ▼                                ▼
        qa/diff_checker.py            data/vector/engine2_chunks.json
   ┌─────────────────────────┐               (→ vector DB)
   │ Diff vs live matrix     │
   │ (alert-fatigue gate)    │
   └─────────────────────────┘
              │
              ▼
   data/processed/engine1_matrix.{candidate,live}.json  (→ Desktop Agent)
```

> **Cloud retention note (Zero-PHI).** This repo only produces *public policy
> data* — federal/state rules and de-identified guideline text — so nothing it
> emits is PHI. Downstream, the Engine 2 RAG chunks feed an **Azure Cosmos DB
> vector database that enforces a strict 30-day TTL (Time-To-Live)** on
> admission-risk records. The TTL is the cloud-side guarantee behind our
> Zero-PHI retention policy: any patient-derived context Engine 2 caches for
> longitudinal analysis auto-expires within 30 days of capture.

## Quick start

```bash
# 1. Install
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Run all scrapers
python main.py

# 3. Run only one source
python main.py --only ecfr_42_part_8

# 4. See a human summary
python inspect_run.py

# 5. Drill into one source's parsed payload
python inspect_run.py cms_pub_100_04_ch39

# 6. Generate manager-friendly CSV + Markdown reports
python report.py
```

After a run, the **`reports/`** directory will contain:

| File | Purpose |
|---|---|
| `run_summary.md` | One-page digest — email or paste into Slack |
| `run_summary.csv` | Per-source status, byte counts, SHA hashes |
| `engine1_rules.csv` | Flat table of every generated billing rule |
| `engine2_chunks.csv` | All RAG chunks with previews |

## Promoting a candidate matrix to "live"

The pipeline writes every run as a `*.candidate.json` and never auto-overwrites
the live matrix. To promote (after reviewing the diff report):

```bash
python main.py --promote
```

This copies `engine1_matrix.candidate.json` → `engine1_matrix.live.json` **only
if** the diff report flagged a material change. Prevents silent rule drift.

## Project layout

```
Engine1Scraper/
├── config.py                  # All source URLs + cadences (single source of truth)
├── main.py                    # End-to-end orchestrator
├── inspect_run.py             # Human-readable run summary
├── report.py                  # CSV + Markdown report generator
├── scrapers/
│   ├── base.py                # Retries, caching, content hashing
│   ├── cms_scraper.py         # Pub 100-02, Pub 100-04, MLN booklet
│   ├── samhsa_scraper.py      # eCFR 42 CFR Part 8 + TIP 63
│   ├── ahca_scraper.py        # FL AHCA handbook (with Selenium fallback)
│   ├── ncci_scraper.py        # NCCI quarterly PTP edits
│   └── fl_mac_scraper.py      # FCSO OTP specialty page
├── transformers/
│   ├── json_matrix.py         # Scrape → Engine 1 rule matrix
│   └── vector_chunker.py      # Scrape → Engine 2 RAG chunks
├── qa/
│   └── diff_checker.py        # Candidate vs live matrix diff
├── azure_functions/
│   └── timer_trigger/         # Phase 5 — Azure Functions cron wrapper
├── data/                      # (gitignored) raw + processed payloads
└── reports/                   # CSV + MD outputs for stakeholders
```

## Engine 1 rules currently generated

| Rule ID | Payer | Code | Modifier | Logic | Source |
|---|---|---|---|---|---|
| `R-FED-01` | Medicare Advantage | G2067 | — | BundleValidation | CMS Pub 100-04 Ch 39 |
| `R-FL-02` | Managed Medicaid | H0020 | POS-58 | PointOfCareBlock | FL AHCA |
| `R-FL-03` | AHCA Medicaid | All SUD | HF | SubstanceAbuseModifier | FL AHCA |
| `R-FL-04` | AHCA Medicaid | H0020 | HD→HG | ModifierSequencer | FL AHCA |
| `R-FLMAC-01` | Medicare | G2067 | — | PayerBlocker | FCSO |
| `R-SIMPLY-01` | Simply MCO | COUNSELING | — | CounselingTimeMinimum | Simply Provider Manual |
| `R-FL-05` | AHCA Medicaid | H0020 | — | DosingCounselingRatio | FL AHCA |
| `R-FED-03` | Federal SAMHSA | ADMISSION | — | AdmissionEligibilityGuard | 42 CFR 8.12(e)(1) |
| `R-FED-04` | Federal SAMHSA | TAKEHOME | — | TakeHomeStabilityGuard | 42 CFR 8.12(i)(2) |
| `R-FED-05` | Federal SAMHSA | TAKEHOME | — | InitialTakeHomeWindow | 42 CFR 8.12(i) |
| `R-FED-06` | Federal DEA | VAULT | — | ControlledSubstanceReconciliation | 21 CFR 1304.21–.22 |

Federal rules execute **before** Florida-specific overrides, per the addendum.

The `R-FED-03..06` and `R-FL-05` gates implement the **point-of-care compliance
blocks** the training masterclass assigns to Engine 1 (1-year addiction-history
admission mandate, take-home stability/housing block, Day-1 take-home warning,
zero-variance methadone vault reconciliation, dosing-to-counseling ratio). They
are deterministic — no AI consent, all 50 states. Each carries a
`params.regulatory_basis` cite and a `params.maturity` flag: `established` for
stable statute, `evolving` for rules mid-rulemaking (e.g. the 2024 SAMHSA
take-home discretion change, and the FL ratio not yet enumerated in the scraped
PDF) — `evolving` gates should be treated as warnings, not hard blocks, until
confirmed, consistent with the Rule 32 caution.

## Source matrix

| Source key | Type | Cadence | Engine | Status |
|---|---|---|---|---|
| `cms_pub_100_04_ch39` | PDF | monthly | 1 | ✅ live |
| `cms_pub_100_02_ch17` | PDF | monthly | 1 | ✅ live |
| `cms_mln_otp_booklet` | PDF | bi-annually | 1 | ✅ live |
| `cms_ncci_edits` | ZIP (full table) | quarterly | 1 | ✅ live (full PTP table; 0 OTP-code edits — see limitations) |
| `cms_ncci_medicaid` | ZIP | quarterly | 1 | 🛠 configured, scraper TBD (H0020 edits) |
| `fl_ahca_cbh_handbook` | HTML | quarterly | 1 | ✅ live |
| `fl_mac_fcso_otp` | HTML | bi-annually | 1 | ✅ live |
| `ecfr_42_part_8` | JSON/XML API | ad-hoc | 1 + 2 | ✅ live (now also emits Engine-1 gates R-FED-03..06) |
| `samhsa_tip_63` | PDF | bi-annually | 2 | ✅ live |
| `sunshine_provider_manual` | PDF | monthly | 1 | ✅ live |
| `simply_provider_resources` | PDF | quarterly | 1 | ✅ live |
| `cdc_icd10_z_codes` | PDF | annually | 2 | ✅ live |
| `cfr_42_part_2` | JSON/XML API | annually | 2 | 🛠 configured, scraper TBD |
| `fl_ahca_carf_ai_consent` | HTML (Selenium) | quarterly | 1 | 🛠 configured, scraper TBD |
| `asam_criteria` | gated | ad-hoc | 2 | 🔒 subscription required |

The two newest targets come from the README review against the Developer Master
Matrix:
- **`cfr_42_part_2`** — 42 CFR Part 2, Subpart C (§2.31). Engine 2 needs the
  **9 mandatory elements of a valid consent** so a referral can't be authorized
  without a conforming, active Release of Information on file.
- **`fl_ahca_carf_ai_consent`** — FL AHCA / CARF **Rule 32**: advance patient
  consent for AI utilization. Engine 1 blocks admission until a time-stamped
  AI-consent form is signed. This mandate is still evolving — the configured URL
  is a monitoring entry point, not a stable deep link, and needs validation
  before a parser is wired (consistent with our general source-URL caveat).

Both are wired into `config.py` as the diff-tracked source-of-record; no scraper
is registered yet (they're skipped gracefully at runtime, like `asam_criteria`).

## Azure Functions deployment (Phase 5)

The `azure_functions/timer_trigger/` directory contains a serverless wrapper
that invokes `main.run()` on a cron schedule. To deploy:

```bash
# Prereqs: Azure CLI + an Azure Function App in your subscription
az login
func azure functionapp publish <your-function-app-name> --python
```

### Secrets: Azure Key Vault + Managed Identity (Zero-Trust)

Per our Zero-Trust architecture, **do not store secrets in plaintext Function
App Settings** — especially the gated/subscription credentials we'll need for
sources like the ASAM Criteria. Instead:

1. **Enable a system-assigned Managed Identity** on the Function App:
   ```bash
   az functionapp identity assign --name <app> --resource-group <rg>
   ```
2. **Grant that identity read access to a Key Vault** (RBAC role
   `Key Vault Secrets User`, scoped to the vault):
   ```bash
   az role assignment create \
     --assignee <function-app-principal-id> \
     --role "Key Vault Secrets User" \
     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault>
   ```
3. **Store each secret in Key Vault** (e.g. `az keyvault secret set --vault-name
   <vault> --name asam-credential --value <…>`).
4. **Retrieve at runtime via the Managed Identity** — no secret material on disk
   or in App Settings. Either reference the secret from App Settings with a Key
   Vault reference (`@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/<name>/)`),
   or read it in-process with `DefaultAzureCredential`:
   ```python
   from azure.identity import DefaultAzureCredential
   from azure.keyvault.secrets import SecretClient

   client = SecretClient(
       vault_url="https://<vault>.vault.azure.net",
       credential=DefaultAzureCredential(),  # uses the Managed Identity in Azure
   )
   asam_credential = client.get_secret("asam-credential").value
   ```
   (`pip install azure-identity azure-keyvault-secrets`.)

### Non-secret configuration

These are plain (non-sensitive) settings and may stay in App Settings:
- `BRAIN_SOURCES` (optional, comma-separated source keys to run; default = all)
- `BRAIN_PROMOTE` (`true` to auto-promote candidate → live when diff is material)

For separate federal-vs-Florida cadences, deploy two Functions with different
`BRAIN_SOURCES` values and different `schedule` strings in their respective
`function.json` files.

## Known limitations / iterations

- **NCCI now pulls the full PTP table.** The scraper discovers the full
  practitioner PTP table on the CMS page (4 license-gated zips, ~2.6M rows
  total), unwraps the AMA-license click-through to the direct `/files/zip/` URL,
  downloads every part of the newest quarter+version, and stream-parses each via
  `openpyxl` `read_only=True` + `iter_rows()` (flat memory). It also handles the
  two different column layouts — modifier indicator at column 2 in the delta
  files vs. column 5 in the full table. De-dupes across parts and `.txt`/`.xlsx`.
  Falls back to the quarterly delta zip if the full table can't be located.
  Verified end-to-end against the live Q2-2026 files.
- **OTP edits & the Medicaid gap (important).** Even with the full table, the
  *Medicare* practitioner PTP file has **no edits for the OTP bundle G-codes**
  (G2067–G2080) and **no H-codes at all** — H-codes are Medicaid. So the
  "H0020 + 80305" unbundling conflict is **not** in this file; it lives in the
  separate **Medicaid NCCI edit file** (`cms_ncci_medicaid`, now in `config.py`).
  Next step to actually surface H0020 edits: generalize `NCCIScraper` to that
  Medicaid URL (same zip/xlsx shape).
- **AHCA modifier extraction**: the MAT-specific AHCA PDFs (Methadone Criteria,
  PT 2021-25) cover clinical criteria and drug coverage but don't enumerate
  modifier rules. The R-FL-* rules are hardcoded from the addendum spec; the
  scraped PDFs serve as diff-tracked source-of-record so changes get flagged.
- **Sunshine / Simply manuals** describe processes, not code-to-diagnosis maps.
  R-SUNSHINE-01 will only fire if the manual starts listing specific H-codes
  alongside required F11.x diagnoses.
- **ASAM Criteria** is subscription-gated; needs institutional credentials.
- **Engine 1 latency budget** is 200ms — current matrix is well under that, but
  if the rule count grows, evaluate whether the agent needs an index.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `403 Forbidden` from SAMHSA | UA tripped Cloudflare. Confirm `USER_AGENT` in `config.py` is plain Chrome (no bot prefix). |
| `404 Not Found` for a PDF | CMS/AHCA reorganized. Update the `url=` in `config.py` and validate with `curl -I "$URL"`. |
| `No module named 'selenium'` | `pip install selenium webdriver-manager`. Needed only for AHCA's JS fallback. |
| `zsh: unknown file attribute: ~` when pasting commands | Run `setopt INTERACTIVE_COMMENTS` (or just paste commands without `# comments`). |

## License & data sources

All scraped content is from public-domain U.S. federal & state government
publications, plus SAMHSA materials (public). ASAM Criteria is excluded
pending institutional licensing.
