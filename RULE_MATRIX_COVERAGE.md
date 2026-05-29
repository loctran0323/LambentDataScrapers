# 37-Rule Master Matrix — Engine Coverage

Maps every rule in *VAIntage Pathways™: The Rule Master Matrix* to its status in
this repo. **Engine 1** = the deterministic JSON rule matrix this repo builds
(`transformers/json_matrix.py`). **Engine 2** = the clinical/NLP RAG corpus
(`transformers/vector_chunker.py`) — it reads note *context*, it does **not**
emit deterministic JSON gates, so Engine-2 rules are tracked as corpus *sources*,
not matrix rules.

Status legend:
- ✅ **live** — rule emits from a source that actually scrapes today.
- 🟡 **defined** — rule builder + config source exist; **no scraper yet**, so it
  is skipped at runtime until its source is wired (same staging as `asam_criteria`).
- ⛔ **deferred** — config source registered but **no rule builder yet**, by team
  decision, pending legal/policy sign-off.
- 📚 **corpus** — Engine-2 RAG source registered; consumed by the clinical engine,
  not the matrix.

## Engine 1 — deterministic matrix (16 rules)

| # | Rule focus | Rule ID(s) | Source key | Status | Maturity |
|---|---|---|---|---|---|
| 1 | Take-home dispensing criteria | `R-FED-04`, `R-FED-05` | `ecfr_42_part_8` | ✅ live | established / evolving |
| 2 | Medicare/Medicaid unbundling | `R-FED-01`, `R-NCCI-*`, `R-NCCIMCD-*` | `cms_pub_100_04_ch39`, `cms_ncci_edits`, `cms_ncci_medicaid` | ✅ live | — |
| 3 | POS-58 facility modifier | `R-FL-02` | `fl_ahca_cbh_handbook` | ✅ live | — |
| 4 | 'HF' substance-abuse modifier | `R-FL-03` | `fl_ahca_cbh_handbook` | ✅ live | — |
| 5 | Dosing-to-counseling ratio | `R-FL-05` | `fl_ahca_cbh_handbook` | ✅ live | evolving |
| 11 | "No-Pay, No Take-Home" check | _(deferred)_ | `internal_policy` | ⛔ deferred | **policy — pending sign-off** |
| 13 | SDOH housing → take-home block | `R-FED-04` | `ecfr_42_part_8` | ✅ live | established |
| 28 | FASAMS intake data validation | `R-FASAMS-01` | `fl_dcf_fasams` | 🟡 defined | evolving |
| 29 | Financial eligibility re-assess | `R-FASAMS-02` | `fl_dcf_fasams` | 🟡 defined | evolving |
| 30 | Regional MSO modifier routing | `R-MSO-01` | `fl_mso_contracts` | 🟡 defined | contract |
| 32 | AI patient consent gate | _(deferred)_ | `fl_ahca_carf_ai_consent` | ⛔ deferred | **not enacted law — pending sign-off** |
| 33 | 1-year opioid-history admission | `R-FED-03` | `ecfr_42_part_8` | ✅ live | established |
| 34 | DEA vault reconciliation | `R-FED-06` | `ecfr_42_part_8` | ✅ live | established |
| 35 | Mandatory PDMP (E-FORCSE) query | `R-PDMP-01` | `fl_eforcse_pdmp` | 🟡 defined | established |
| 36 | Clinical supervision / credentialing | `R-CRED-01` | `fl_65d30_fac` | 🟡 defined | established |
| 37 | Treatment-plan expiration | `R-TPLAN-01` | `fl_65d30_fac` | 🟡 defined | established |

**Engine 1: 8 live, 6 defined (awaiting scrapers), 2 deferred. 0 missing.**

> ⛔ **Two gates are intentionally NOT implemented yet** (team decision). Rule 11
> is internal corporate policy (withholding a *medication* dose over a debt is an
> ethics/DEA risk), and Rule 32's AI-consent mandate is not yet enacted law. Their
> config sources are tracked, but no rule is emitted until legal/policy sign-off.

## Engine 2 — clinical / governance RAG corpus (21 rules)

These are **not** matrix rules. Each is registered as an Engine-2 source so the
RAG corpus can ingest it; the clinical engine scores note context against them.

| # | Rule focus | Source key | Status |
|---|---|---|---|
| 6 | Patient rights & responsibilities | `corporate_clinical_policy` | 📚 corpus |
| 7 | Code of ethics (objective tone) | `corporate_clinical_policy` | 📚 corpus |
| 8 | Admin discharge / retention | `aatod_guidelines` | 📚 corpus |
| 9 | Medicare SDOH documentation | `aatod_guidelines`, `cdc_icd10_z_codes` | 📚 corpus |
| 10 | 90-day efficacy / dropout risk | `nida_principles` | 📚 corpus |
| 12 | SDOH: transportation insecurity | `cdc_icd10_z_codes` | 📚 corpus (live source) |
| 14 | SDOH: food / financial insecurity | `cdc_icd10_z_codes` | 📚 corpus (live source) |
| 15 | FL Marchman Act — impaired/AMA | `fl_marchman_act` | 📚 corpus |
| 16 | FL Baker Act — psych vs. substance | `fl_baker_act` | 📚 corpus |
| 17 | FDA REMS — cardiac/EKG | `fda_rems_methadone` | 📚 corpus |
| 18 | Polysubstance / xylazine alerts | `nida_principles` | 📚 corpus |
| 19 | Co-occurring medical (Hep-C/HIV) | `aatod_guidelines` | 📚 corpus |
| 20 | Note cloning / copy-paste fraud | `hhs_oig_compliance` | 📚 corpus |
| 21 | Post-relapse plan modification | `nida_principles` | 📚 corpus |
| 22 | Integrated primary care | `carf_standards` | 📚 corpus (gated) |
| 23 | Naloxone / Narcan distribution | `samhsa_sor_grant` | 📚 corpus (grant add-on) |
| 24 | Rural SDOH barriers | `hrsa_rcorp` | 📚 corpus (grant add-on) |
| 25 | 48-hour priority admission | `samhsa_sabg` | 📚 corpus (grant add-on) |
| 26 | Suboxone precipitated withdrawal | `asam_criteria` | 📚 corpus (gated) |
| 27 | Mobile OTP dispensing security | `dea_mobile_otp` | 📚 corpus (expansion) |

> Rules 1 and 13 are tagged "Engine 1 & 2" in the matrix: the deterministic
> take-home block is the Engine-1 half (above), the Z-code revenue/SDOH capture
> is the Engine-2 half (`cdc_icd10_z_codes`).

## What's left to make the 🟡 rules go live

Each needs a scraper wired to its registered source (and, for gated sources,
institutional access):
- `fl_dcf_fasams`, `fl_mso_contracts` — FL DCF / Managing Entity pages.
- `fl_eforcse_pdmp`, `fl_65d30_fac` — statute/FAC text (rules are hardcoded from
  the citation; the scraper only diff-tracks source changes).
- `fl_ahca_carf_ai_consent` — blocked on the AI-consent rule actually being
  promulgated; keep as warn-only until then.
- `internal_policy` — no scrape target; values come from clinic config.
