"""FCSO (Florida Medicare Administrative Contractor) — OTP specialty landing page.

FCSO doesn't host a single OTP fact sheet PDF — they curate an HTML page that
links out to current CMS docs + their own FL-MAC bulletins. We pull the HTML,
extract G/H-code mentions, and capture every outbound PDF reference so the diff
checker can flag when FCSO points at new guidance.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapeResult

RE_G_CODE = re.compile(r"\b(G\d{4})\b")
RE_H_CODE = re.compile(r"\b(H\d{4})\b")


class FCSOFactSheetScraper(BaseScraper):
    def parse(self) -> ScrapeResult:
        html = self.fetch_text(self.source.url)
        raw_path, digest = self._persist_raw(html.encode("utf-8"), suffix=".html")

        soup = BeautifulSoup(html, "lxml")
        # Strip nav/script noise — only mine the main content area.
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)

        pdf_links = sorted(
            {
                urljoin(self.source.url, a["href"])
                for a in soup.find_all("a", href=True)
                if a["href"].lower().endswith(".pdf")
            }
        )

        return ScrapeResult(
            source_key=self.source.key,
            source_name=self.source.name,
            fetched_at="",
            content_sha256=digest,
            raw_path=str(raw_path),
            parsed={
                "doc": "FCSO OTP Specialty Page (HTML)",
                "g_codes_mentioned": sorted(set(RE_G_CODE.findall(text))),
                "h_codes_mentioned": sorted(set(RE_H_CODE.findall(text))),
                "linked_pdfs": pdf_links,
                "raw_excerpt": text[:2000],
            },
        )
