"""CMS NCCI PTP edits — pulls the quarterly practitioner edit ZIP.

We:
  1. Pull the landing HTML and find the most recent practitioner-PTP zip link.
  2. Download the zip and extract every PTP edit it contains:
       - the *tab-delimited* additions/deletions files (NCCI calls them .txt but
         they're TSV, not CSV), and
       - any .xlsx workbooks, parsed with a streaming reader.
  3. Filter rows to HCPCS codes Engine 1 evaluates (OTP G/H-codes).

Files inside the delta ZIP look like:
    MCR_NCCI_Additions_Eff_<QTR>.txt
    MCR_NCCI_Deletions_Eff_<QTR>.txt
    MCR_NCCI_CCMIChgs_Eff_<QTR>.txt
    MCR_NCCI_Changes_Eff_<QTR>.xlsx

xlsx handling (review action: "ingest the full PTP universe"): the delta zip only
carries that quarter's additions/deletions, so Engine 1 would miss historical
unbundling edits (e.g. the H0020 + 80305 conflict). The *full* quarterly PTP
tables CMS publishes are xlsx workbooks of ~2.5M rows. We read those with
openpyxl in read_only mode and stream row-by-row via iter_rows() so memory stays
flat regardless of table size — never loading the whole sheet into RAM. The same
code path also reads the delta zip's Changes.xlsx; rows are de-duplicated against
the .txt additions/deletions so we don't double-count.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from urllib.parse import urljoin

import openpyxl
from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapeResult

# HCPCS codes Engine 1 evaluates. Expanded to cover the full OTP weekly bundle
# range (G2067–G2080) plus take-home naloxone codes (G1028, G2215) and the
# core FL Medicaid H-codes.
RELEVANT_CODES = {
    # OTP weekly bundle G-codes (CMS Pub 100-04 Ch 39)
    "G2067", "G2068", "G2069", "G2070", "G2071", "G2072", "G2073",
    "G2074", "G2075", "G2076", "G2077", "G2078", "G2079", "G2080",
    # OTP intake / add-on / take-home
    "G2086", "G2087", "G2088",
    "G1028", "G2215",          # take-home naloxone
    "G0137",                   # IOP threshold trigger
    # FL Medicaid MAT H-codes
    "H0001", "H0004", "H0005", "H0006", "H0020", "H0033", "H0047",
    "H0050", "H2010", "H2017",
}

RE_PRACTITIONER_ZIP = re.compile(r"practitioner.*\.zip$", re.I)


class NCCIScraper(BaseScraper):
    def parse(self) -> ScrapeResult:
        html = self.fetch_text(self.source.url)
        soup = BeautifulSoup(html, "lxml")
        zip_urls = [
            urljoin(self.source.url, a["href"])
            for a in soup.find_all("a", href=True)
            if RE_PRACTITIONER_ZIP.search(a["href"])
        ]
        if not zip_urls:
            return ScrapeResult(
                source_key=self.source.key,
                source_name=self.source.name,
                fetched_at="",
                content_sha256="",
                raw_path="",
                parsed={"doc": "NCCI", "edits": []},
                warnings=["No practitioner PTP zip link found on landing page"],
            )

        zip_url = zip_urls[0]
        payload = self.fetch_bytes(zip_url)
        raw_path, digest = self._persist_raw(payload, suffix=".zip")

        edits = self._extract_relevant_edits(payload)
        return ScrapeResult(
            source_key=self.source.key,
            source_name=self.source.name,
            fetched_at="",
            content_sha256=digest,
            raw_path=str(raw_path),
            parsed={
                "doc": "NCCI PTP Edits (practitioner)",
                "source_zip": zip_url,
                "edit_count_relevant": len(edits),
                "edits": edits,
            },
        )

    @classmethod
    def _extract_relevant_edits(cls, payload: bytes) -> list[dict]:
        edits: list[dict] = []
        # De-dup across files: the delta zip ships the same edits as both .txt and
        # the Changes.xlsx, and full PTP tables can repeat a pair across quarters.
        seen: set[tuple[str, str, str]] = set()
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for name in zf.namelist():
                low = name.lower()
                if low.endswith(".txt") and ("additions" in low or "deletions" in low):
                    kind = "addition" if "additions" in low else "deletion"
                    with zf.open(name) as fh:
                        text = io.TextIOWrapper(fh, encoding="latin-1", errors="ignore")
                        # NCCI .txt files are TAB-delimited, not comma. First row is
                        # a copyright blurb, then a header row, then data rows:
                        # <Column1>\t<Column2>\t<ModifierIndicator>\t<...>.
                        cls._collect(
                            csv.reader(text, delimiter="\t"), name, kind, edits, seen
                        )
                elif low.endswith(".xlsx"):
                    cls._collect_xlsx(zf.read(name), name, edits, seen)
        return edits

    @staticmethod
    def _collect(rows, source_file, default_kind, edits, seen) -> None:
        """Filter an iterable of rows to relevant OTP edits, de-duplicating."""
        for row in rows:
            if row is None or len(row) < 2:
                continue
            c1 = "" if row[0] is None else str(row[0]).strip()
            c2 = "" if row[1] is None else str(row[1]).strip()
            # Skip header / copyright rows (don't look like HCPCS codes).
            if not _looks_like_hcpcs(c1) or not _looks_like_hcpcs(c2):
                continue
            if c1 not in RELEVANT_CODES and c2 not in RELEVANT_CODES:
                continue
            key = (c1, c2, default_kind)
            if key in seen:
                continue
            seen.add(key)
            modifier = ""
            if len(row) > 2 and row[2] is not None:
                # xlsx modifier-indicator headers carry embedded newlines; keep
                # just the leading token (the actual 0/1/9 value on data rows).
                modifier = str(row[2]).strip().split("\n")[0].strip()
            edits.append(
                {
                    "column1": c1,
                    "column2": c2,
                    "modifier_indicator": modifier,
                    "edit_kind": default_kind,
                    "source_file": source_file,
                }
            )

    @classmethod
    def _collect_xlsx(cls, data: bytes, member_name: str, edits, seen) -> None:
        """Stream an xlsx workbook row-by-row (read_only) into the edit list.

        read_only=True + iter_rows() keeps memory flat for the multi-million-row
        full PTP tables; openpyxl never materializes the whole sheet.
        """
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                kind = _xlsx_sheet_kind(ws.title, member_name)
                cls._collect(
                    ws.iter_rows(values_only=True),
                    f"{member_name}#{ws.title}",
                    kind,
                    edits,
                    seen,
                )
        finally:
            wb.close()


_HCPCS_RE = re.compile(r"^[A-Z0-9]{5}$")


def _looks_like_hcpcs(code: str) -> bool:
    """HCPCS codes are 5 chars, alphanumeric (e.g., G2067, 99213, 0395T)."""
    return bool(_HCPCS_RE.match(code))


def _xlsx_sheet_kind(sheet_title: str, member_name: str) -> str:
    """Classify an xlsx sheet by its name.

    Delta workbooks name sheets NCCI_Adds_*/NCCI_Dels_*/NCCI_CCMIChgs_*. Full PTP
    tables aren't additions/deletions at all — they're the standing edit universe,
    so anything we can't classify falls back to "ptp_edit".
    """
    name = f"{sheet_title} {member_name}".lower()
    if "add" in name:
        return "addition"
    if "del" in name:
        return "deletion"
    if "ccmi" in name or "chg" in name or "change" in name:
        return "ccmi_change"
    return "ptp_edit"
