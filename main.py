"""End-to-end orchestrator for the "Brain" pipeline.

Steps:
  1. Run every scraper in `config.ALL_SOURCES` (filterable via --only).
  2. Persist raw payloads + parsed JSON to data/.
  3. Build the Engine 1 rule matrix from Engine 1 sources.
  4. Chunk Engine 2 sources for the RAG vector DB.
  5. Diff the new matrix against `data/processed/engine1_matrix.live.json`.
  6. If `--promote` is passed and the diff is material, write the new matrix as
     the new "live" matrix.

This file is also the entry point the Azure Functions wrapper calls — see
`azure_functions/timer_trigger/__init__.py`.
"""
from __future__ import annotations

import argparse
import json
import logging

from config import (
    ALL_SOURCES,
    DIFF_DIR,
    ENGINE1_SOURCES,
    ENGINE2_SOURCES,
    PROCESSED_DIR,
    VECTOR_DIR,
    Source,
)
from qa import diff_matrix
from scrapers import (
    AHCAHandbookScraper,
    CDCICD10ZCodesScraper,
    CMSManualScraper,
    ECFRPart8Scraper,
    FCSOFactSheetScraper,
    MedicaidNCCIScraper,
    MLNBookletScraper,
    NCCIScraper,
    SamhsaTIP63Scraper,
    SimplyProviderManualScraper,
    SunshineProviderManualScraper,
)
from scrapers.base import BaseScraper, ScrapeResult
from transformers import build_rule_matrix, chunk_for_rag

log = logging.getLogger("engine1.brain")

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "cms_pub_100_04_ch39": CMSManualScraper,
    "cms_pub_100_02_ch17": CMSManualScraper,
    "cms_mln_otp_booklet": MLNBookletScraper,
    "fl_ahca_cbh_handbook": AHCAHandbookScraper,
    "fl_mac_fcso_otp": FCSOFactSheetScraper,
    "cms_ncci_edits": NCCIScraper,
    "cms_ncci_medicaid": MedicaidNCCIScraper,
    "ecfr_42_part_8": ECFRPart8Scraper,
    "samhsa_tip_63": SamhsaTIP63Scraper,
    "cdc_icd10_z_codes": CDCICD10ZCodesScraper,
    "sunshine_provider_manual": SunshineProviderManualScraper,
    "simply_provider_resources": SimplyProviderManualScraper,
}

LIVE_MATRIX_PATH = PROCESSED_DIR / "engine1_matrix.live.json"
CANDIDATE_MATRIX_PATH = PROCESSED_DIR / "engine1_matrix.candidate.json"
VECTOR_CHUNKS_PATH = VECTOR_DIR / "engine2_chunks.json"


def _run_one(source: Source) -> ScrapeResult | None:
    cls = SCRAPER_REGISTRY.get(source.key)
    if cls is None:
        log.warning("No scraper registered for %s — skipping", source.key)
        return None
    try:
        return cls(source).run()
    except Exception as exc:  # noqa: BLE001 — one source failing shouldn't take down the run
        log.exception("Scraper failed: %s", source.key)
        return ScrapeResult(
            source_key=source.key,
            source_name=source.name,
            fetched_at="",
            content_sha256="",
            raw_path="",
            warnings=[f"scrape_failed: {exc}"],
        )


def run(only: list[str] | None = None, promote: bool = False) -> dict:
    sources = [s for s in ALL_SOURCES if not only or s.key in only]
    log.info("Running %d sources", len(sources))

    results: list[ScrapeResult] = []
    for s in sources:
        r = _run_one(s)
        if r is not None:
            results.append(r)
            (PROCESSED_DIR / f"{s.key}.json").write_text(r.to_json())

    # Engine 1 matrix
    engine1_results = [r for r in results if r.source_key in {s.key for s in ENGINE1_SOURCES}]
    matrix = build_rule_matrix(engine1_results)
    CANDIDATE_MATRIX_PATH.write_text(json.dumps(matrix, indent=2))
    log.info("Candidate matrix written with %d rules", matrix["rule_count"])

    # Diff vs live
    report = diff_matrix(
        matrix, LIVE_MATRIX_PATH if LIVE_MATRIX_PATH.exists() else None
    )
    (DIFF_DIR / "latest.json").write_text(report.to_json())
    log.info(
        "Diff: +%d / -%d / ~%d (material=%s)",
        len(report.added_rule_ids),
        len(report.removed_rule_ids),
        len(report.changed_rules),
        report.is_material,
    )

    # Engine 2 RAG chunks
    engine2_results = [r for r in results if r.source_key in {s.key for s in ENGINE2_SOURCES}]
    chunks = chunk_for_rag(engine2_results)
    VECTOR_CHUNKS_PATH.write_text(json.dumps(chunks, indent=2))
    log.info("Wrote %d RAG chunks", len(chunks))

    if promote and report.is_material:
        LIVE_MATRIX_PATH.write_text(json.dumps(matrix, indent=2))
        log.info("Promoted candidate -> live matrix")

    return {
        "rule_count": matrix["rule_count"],
        "chunk_count": len(chunks),
        "diff_material": report.is_material,
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="Subset of source keys to run")
    ap.add_argument("--promote", action="store_true",
                    help="If diff is material, write candidate as the new live matrix")
    args = ap.parse_args()
    summary = run(only=args.only, promote=args.promote)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
