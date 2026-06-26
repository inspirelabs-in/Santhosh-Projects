"""
Google SERP HTML parser using BeautifulSoup.
Extracts organic results, ads, answer boxes, PAA, and related keywords.
Adapted from SEO codebase selectors for desktop + mobile Google SERPs.
"""
import hashlib
import logging
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from app.models import (
    AdResult,
    AnswerBoxEntry,
    OrganicResult,
    SerpData,
    SerpFeature,
)

log = logging.getLogger("geo.serp_parser")

CLASSES_TO_REMOVE = [
    "FxLDp",    # carousel items
    "h7Tj7e",   # knowledge panel noise
    "Wt5Tfe",   # video shelf
    "MTIaKb",   # image pack
]


def _clean_text(text: str) -> str:
    if not text:
        return ""
    return text.encode("ascii", "ignore").decode("utf-8").strip()


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or ""
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        parts = url.split("/")
        return parts[2] if len(parts) > 2 else ""


def _html_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()[:16]


def parse_serp_html(html: str, keyword: str, target_domain: str = "grabon.in") -> SerpData:
    if not html or len(html) < 100:
        return SerpData(keyword=keyword, raw_html_hash="")

    soup = BeautifulSoup(html, "lxml")

    for cls in CLASSES_TO_REMOVE:
        for el in soup.find_all(["div", "ul"], class_=lambda x: x and cls in (x if isinstance(x, list) else [x])):
            el.decompose()

    for el in soup.find_all("div", attrs={"data-subtrees": "mfc"}):
        el.decompose()

    organic = _extract_organic(soup, target_domain)
    ads = _extract_ads(soup)
    answer_box = _extract_answer_box(soup, html)
    paa = _extract_paa(soup)
    related = _extract_related_keywords(soup)
    results_count = _extract_results_count(soup)

    return SerpData(
        keyword=keyword,
        organic_results=organic,
        ads=ads,
        answer_box=answer_box,
        people_also_ask=paa,
        related_keywords=related,
        results_count=results_count,
        raw_html_hash=_html_hash(html),
    )


def _extract_organic(soup: BeautifulSoup, target_domain: str) -> list[OrganicResult]:
    results = []
    seen_urls: set[str] = set()
    rank = 1

    rso = soup.find("div", attrs={"id": "rso"})
    if not rso:
        rso = soup

    blocks = rso.find_all("div", class_="MjjYud")
    if not blocks:
        blocks = rso.find_all("div", class_="g")

    for block in blocks:
        link_el = block.find("a", href=True)
        if not link_el:
            continue

        url = link_el.get("href", "")
        if not url or url.startswith("#"):
            continue
        if url.startswith("/url?"):
            qs = parse_qs(urlparse(url).query)
            url = qs.get("q", [url])[0]
        if "google.com" in url:
            continue
        if url in seen_urls:
            continue

        title_el = (
            block.find("h3")
            or block.find("div", class_=re.compile(r"F0FGWb|vvjwJb|BNeawe"))
        )
        title = _clean_text(title_el.get_text()) if title_el else ""
        if not title:
            continue

        snippet_el = block.find("div", class_=re.compile(r"VwiC3b|lEBKkf|yXK7lf"))
        if not snippet_el:
            snippet_el = block.find("span", class_=re.compile(r"aCOpRe|st"))
        snippet = _clean_text(snippet_el.get_text()) if snippet_el else None

        has_table = block.find("table") is not None
        domain = _extract_domain(url)
        is_target = target_domain.lower() in domain.lower()

        results.append(OrganicResult(
            rank_position=rank,
            title=title,
            snippet=snippet,
            url=url,
            domain=domain,
            has_table=has_table,
            is_target=is_target,
        ))
        seen_urls.add(url)
        rank += 1

    return results


def _extract_ads(soup: BeautifulSoup) -> list[AdResult]:
    results = []
    rank = 1

    ad_blocks = soup.find_all("div", class_="uEierd")
    if not ad_blocks:
        ad_blocks = soup.find_all("div", attrs={"data-text-ad": True})

    for block in ad_blocks:
        try:
            url_el = block.find("a", href=True)
            title_el = block.find("div", class_=re.compile(r"q8U8x|yUTMj|CCgQ5"))
            if not title_el:
                title_el = block.find("span", class_=re.compile(r"q8U8x|yUTMj"))
            snippet_el = block.find("div", class_=re.compile(r"MUxGbd|w1C3Le|yDYNvb"))

            if url_el and title_el:
                url = url_el.get("href", "")
                title = _clean_text(title_el.get_text())
                snippet = _clean_text(snippet_el.get_text()) if snippet_el else None
                domain = _extract_domain(url)

                results.append(AdResult(
                    rank_position=rank,
                    title=title,
                    snippet=snippet,
                    url=url,
                    domain=domain,
                ))
                rank += 1
        except Exception:
            continue

    return results


def _extract_answer_box(soup: BeautifulSoup, raw_html: str) -> list[AnswerBoxEntry]:
    results = []

    ab_soup = BeautifulSoup(raw_html, "lxml")
    mfc_block = ab_soup.find("div", attrs={"data-subtrees": "mfc"}) or ab_soup.find("div", attrs={"data-subtree": "mfc"})

    if mfc_block:
        for link in mfc_block.find_all("a", class_="uVhVib"):
            href = link.get("href", "")
            if href:
                results.append(AnswerBoxEntry(
                    url=href,
                    domain=_extract_domain(href),
                    position="main",
                ))

        for link in mfc_block.find_all("a", class_="KEVENd"):
            href = link.get("href", "")
            if href:
                results.append(AnswerBoxEntry(
                    url=href,
                    domain=_extract_domain(href),
                    position="side",
                ))

    if not results:
        for cls in ["ifM9O", "V3FYCf", "Xv4xee"]:
            for el in soup.find_all("div", class_=cls):
                link = el.find("a", href=True)
                if link:
                    href = link.get("href", "")
                    title_el = el.find("h3") or el.find("div", class_=re.compile(r"yuRUbf"))
                    results.append(AnswerBoxEntry(
                        url=href,
                        domain=_extract_domain(href),
                        title=_clean_text(title_el.get_text()) if title_el else None,
                        position="main",
                    ))

    return results


def _extract_paa(soup: BeautifulSoup) -> list[str]:
    questions = []

    paa_blocks = soup.find_all("div", attrs={"jsname": "N760b"})
    if not paa_blocks:
        paa_blocks = soup.find_all("div", class_=lambda c: c and all(x in c for x in ["AuVD", "wHYlTd", "cUnQKe"]))

    for block in paa_blocks:
        try:
            q_el = block.find("div", class_="z9gcx")
            if q_el and q_el.get("data-q"):
                questions.append(q_el["data-q"])
            elif q_el:
                text = _clean_text(q_el.get_text())
                if text and text.endswith("?"):
                    questions.append(text)
        except Exception:
            continue

    if not questions:
        for el in soup.find_all("div", attrs={"data-q": True}):
            q = el.get("data-q", "")
            if q:
                questions.append(q)

    return questions


def _extract_related_keywords(soup: BeautifulSoup) -> list[str]:
    keywords = []

    for block in soup.find_all("div", attrs={"jsname": "Cpkphb"}):
        try:
            kw_el = block.find("div", class_=lambda c: c and all(x in c for x in ["s75CSd", "u60jwe"]))
            if kw_el:
                text = _clean_text(kw_el.get_text())
                if text:
                    keywords.append(text)
        except Exception:
            continue

    if not keywords:
        for block in soup.find_all("a", class_=re.compile(r"k8XOCe|gL9Hy")):
            text = _clean_text(block.get_text())
            if text and not text.startswith("http"):
                keywords.append(text)

    return keywords


def _extract_results_count(soup: BeautifulSoup) -> str:
    el = soup.find("div", attrs={"id": "result-stats"})
    if el:
        return _clean_text(el.get_text())
    return ""
