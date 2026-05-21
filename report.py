"""Manager-friendly CSV + Markdown reports of the latest pipeline run.

Generates four artifacts in `reports/`:
  - run_summary.csv        : one row per source — what succeeded, what failed, byte counts
  - engine1_rules.csv      : the Engine 1 rule matrix as a flat table
  - engine2_chunks.csv     : Engine 2 RAG chunks (id, source, label, char_count, preview)
  - run_summary.md         : the same summary as an emailable markdown digest

Why CSV: opens in Excel/Sheets directly, easy to filter/share. The MD digest is
what to paste into Slack or a status email.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROCESSED = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"
VECTOR = ROOT / "data" / "vector"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)


def _raw_bytes_for(source_key: str) -> int:
    d = RAW / source_key
    if not d.exists():
        return 0
    return sum(f.stat().st_size for f in d.iterdir())


def build_run_summary() -> list[dict]:
    rows: list[dict] = []
    for f in sorted(PROCESSED.glob("*.json")):
        if f.name.startswith("engine1_matrix"):
            continue
        payload = json.loads(f.read_text())
        warns = payload.get("warnings") or []
        parsed = payload.get("parsed", {})
        failed = any("scrape_failed" in w for w in warns)
        # Decide a single "what we got" number per source type.
        if "section_count" in parsed:
            extracted = parsed["section_count"]
            unit = "sections"
        elif "edit_count_relevant" in parsed:
            extracted = parsed["edit_count_relevant"]
            unit = "NCCI edits"
        elif "g_codes_mentioned" in parsed:
            extracted = len(parsed["g_codes_mentioned"])
            unit = "G-codes mentioned"
        elif "candidate_links" in parsed:
            extracted = len(parsed["candidate_links"])
            unit = "PDF links"
        else:
            extracted = 0
            unit = ""
        rows.append(
            {
                "source_key": payload["source_key"],
                "source_name": payload["source_name"],
                "status": "FAIL" if failed else "OK",
                "fetched_at": payload.get("fetched_at", ""),
                "raw_bytes": _raw_bytes_for(payload["source_key"]),
                "content_sha256": payload.get("content_sha256", ""),
                "extracted_count": extracted,
                "extracted_unit": unit,
                "doc_label": parsed.get("doc", ""),
                "warnings": " | ".join(warns)[:300],
            }
        )
    return rows


def write_run_summary_csv(rows: list[dict]) -> Path:
    out = REPORTS / "run_summary.csv"
    if not rows:
        out.write_text("source_key,status,note\n,,no processed files yet\n")
        return out
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return out


def write_engine1_rules_csv() -> Path:
    out = REPORTS / "engine1_rules.csv"
    matrix_path = PROCESSED / "engine1_matrix.candidate.json"
    if not matrix_path.exists():
        out.write_text("rule_id,note\n,no matrix generated yet\n")
        return out
    matrix = json.loads(matrix_path.read_text())
    rules = matrix.get("rules", [])
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["rule_id", "payer_type", "code", "required_modifier",
             "logic_type", "source_key", "params_json"]
        )
        for r in rules:
            w.writerow(
                [
                    r["rule_id"],
                    r["payer_type"],
                    r["code"],
                    r.get("required_modifier") or "",
                    r["logic_type"],
                    r["source_key"],
                    json.dumps(r.get("params", {})),
                ]
            )
    return out


def write_engine2_chunks_csv() -> Path:
    out = REPORTS / "engine2_chunks.csv"
    chunks_path = VECTOR / "engine2_chunks.json"
    if not chunks_path.exists():
        out.write_text("chunk_id,note\n,no RAG chunks generated yet\n")
        return out
    chunks = json.loads(chunks_path.read_text())
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["chunk_id", "source_key", "label", "char_count", "preview"])
        for c in chunks:
            preview = c["text"][:200].replace("\n", " ")
            w.writerow([c["chunk_id"], c["source_key"], c["label"], c["char_count"], preview])
    return out


def write_markdown_digest(rows: list[dict]) -> Path:
    out = REPORTS / "run_summary.md"
    matrix_path = PROCESSED / "engine1_matrix.candidate.json"
    chunks_path = VECTOR / "engine2_chunks.json"
    matrix = json.loads(matrix_path.read_text()) if matrix_path.exists() else {}
    chunks = json.loads(chunks_path.read_text()) if chunks_path.exists() else []
    ok = sum(1 for r in rows if r["status"] == "OK")
    fail = sum(1 for r in rows if r["status"] == "FAIL")

    lines: list[str] = [
        "# VAIntage Brain — Scrape Run Summary",
        f"_Generated: {datetime.now(timezone.utc).isoformat()}_",
        "",
        f"**Sources fetched OK:** {ok}  |  **Sources failed:** {fail}",
        f"**Engine 1 rules built:** {matrix.get('rule_count', 0)}",
        f"**Engine 2 RAG chunks:** {len(chunks)}",
        "",
        "## Per-source results",
        "",
        "| Source | Status | Got | Raw bytes | SHA-256 (first 12) |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        sha = (r["content_sha256"] or "")[:12]
        got = f"{r['extracted_count']} {r['extracted_unit']}".strip()
        lines.append(
            f"| `{r['source_key']}` | **{r['status']}** | {got} | {r['raw_bytes']:,} | `{sha}` |"
        )

    if matrix.get("rules"):
        lines += ["", "## Engine 1 rules generated", "",
                  "| Rule ID | Payer | Code | Modifier | Logic |",
                  "|---|---|---|---|---|"]
        for rule in matrix["rules"]:
            lines.append(
                f"| {rule['rule_id']} | {rule['payer_type']} | {rule['code']} "
                f"| {rule.get('required_modifier') or '-'} | {rule['logic_type']} |"
            )

    # Sample a few real RAG chunks — proof that text was actually extracted.
    if chunks:
        lines += ["", "## Sample Engine 2 chunks (proof of extraction)", ""]
        for c in chunks[:3]:
            lines.append(f"**{c['source_key']} — {c['label']}** ({c['char_count']} chars)")
            lines.append("")
            lines.append(f"> {c['text'][:400]}{'…' if len(c['text']) > 400 else ''}")
            lines.append("")

    out.write_text("\n".join(lines))
    return out


def main():
    rows = build_run_summary()
    paths = [
        write_run_summary_csv(rows),
        write_engine1_rules_csv(),
        write_engine2_chunks_csv(),
        write_markdown_digest(rows),
    ]
    print("Wrote:")
    for p in paths:
        print(f"  {p}")


if __name__ == "__main__":
    main()
