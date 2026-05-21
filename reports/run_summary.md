# VAIntage Brain — Scrape Run Summary
_Generated: 2026-05-21T07:32:56.842208+00:00_

**Sources fetched OK:** 8  |  **Sources failed:** 0
**Engine 1 rules built:** 5
**Engine 2 RAG chunks:** 1011

## Per-source results

| Source | Status | Got | Raw bytes | SHA-256 (first 12) |
|---|---|---|---|---|
| `cms_mln_otp_booklet` | **OK** | 13 G-codes mentioned | 3,378,819 | `b03e94ae7ca7` |
| `cms_ncci_edits` | **OK** | 0 NCCI edits | 170,165 | `a0ce4bf2966c` |
| `cms_pub_100_02_ch17` | **OK** | 10 G-codes mentioned | 355,826 | `2c365f78a35c` |
| `cms_pub_100_04_ch39` | **OK** | 12 G-codes mentioned | 375,532 | `8c0f7934527e` |
| `ecfr_42_part_8` | **OK** | 25 sections | 104,517 | `0b136a303c87` |
| `fl_ahca_cbh_handbook` | **OK** | 0 PDF links | 55,838 | `8a7da28c11f2` |
| `fl_mac_fcso_otp` | **OK** | 0 G-codes mentioned | 172,249 | `aa29afe58719` |
| `samhsa_tip_63` | **OK** | 0 | 3,427,264 | `e7f4c2e40a5d` |

## Engine 1 rules generated

| Rule ID | Payer | Code | Modifier | Logic |
|---|---|---|---|---|
| R-FED-01 | Medicare Advantage | G2067 | - | BundleValidation |
| R-FL-02 | Managed Medicaid | H0020 | POS-58 | PointOfCareBlock |
| R-FL-03 | AHCA Medicaid | ALL_SUD | HF | SubstanceAbuseModifier |
| R-FL-04 | AHCA Medicaid | H0020 | HD>HG | ModifierSequencer |
| R-FLMAC-01 | Medicare | G2067 | - | PayerBlocker |

## Sample Engine 2 chunks (proof of extraction)

**ecfr_42_part_8 — 8.1 § 8.1 Scope.** (1200 chars)

> § 8.1 Scope. (a) Scope. This subpart and subparts B through D of this part establish the procedures by which the Secretary of Health and Human Services (the Secretary) will determine whether an applicant seeking to become an Opioid Treatment Program (OTP) is qualified under section 303(h) of the Controlled Substances Act (CSA) (21 U.S.C. 823(h)) to dispense Medications for Opioid Use Disorder (MOU…

**ecfr_42_part_8 — 8.1 § 8.1 Scope.** (932 chars)

> m an Accreditation Body that has been approved by the Secretary. This subpart and subparts B through D also establish the procedures whereby an entity can apply to become an approved Accreditation Body, and the requirements and general standards for Accreditation Bodies to ensure that OTPs are consistently evaluated for compliance with the Secretary's standards for treatment of OUD with MOUD. (b) …

**ecfr_42_part_8 — 8.2 §thnsp;8.2 Definitions.** (1200 chars)

> §thnsp;8.2 Definitions. The following definitions apply to this part: Accreditation Body or “the Body” means an organization that has been approved by the Secretary in this part to accredit OTPs dispensing MOUD. Accreditation Body application means the application filed with the Secretary for purposes of obtaining approval as an Accreditation Body, as described in § 8.3(b). Accreditation elements …
