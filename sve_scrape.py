#!/usr/bin/env python3
import csv
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, urlencode


BASE = "https://shadowverse-evolve.com"
INDEX_URL = f"{BASE}/cardlist/"
SEARCH_URL = f"{BASE}/cardlist/cardsearch/"


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def session_with_retries() -> requests.Session:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "ja,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": INDEX_URL,
        }
    )

    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s


def get_soup(s: requests.Session, url: str, *, timeout: float = 15.0) -> BeautifulSoup:
    r = s.get(url, timeout=timeout)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def extract_expansions(soup: BeautifulSoup) -> List[str]:
    """Extract expansion codes from the search form's select[name=expansion_name]."""
    codes: List[str] = []
    sel = soup.select_one('select[name="expansion_name"]')
    if not sel:
        return codes
    for opt in sel.find_all("option"):
        val = (opt.get("value") or "").strip()
        if val and val.upper() != "ALL":
            codes.append(val)
    return codes


# Include lowercase and underscore as some cardnos use suffixes like 'a'/'b'
CARDNO_RE = re.compile(r"cardno=([A-Za-z0-9_\-]+)")


def extract_cardnos_from_html(html: str) -> Set[str]:
    found = set(CARDNO_RE.findall(html))
    return found


def find_next_url(soup: BeautifulSoup, current_url: str) -> Optional[str]:
    # 1) rel=next
    a = soup.find("a", attrs={"rel": re.compile("next", re.I)})
    if a and a.get("href"):
        return urljoin(current_url, a["href"])

    # 2) text contains 次 or Next or »
    for a in soup.find_all("a"):
        txt = (a.get_text(strip=True) or "")
        if any(t in txt for t in ["次へ", "次", "Next", "»", ">>"]):
            href = a.get("href")
            if href:
                return urljoin(current_url, href)

    # 3) pagination container heuristic
    nav = soup.find(class_=re.compile("page|pager|pagination", re.I))
    if nav:
        for a in nav.find_all("a"):
            txt = (a.get_text(strip=True) or "")
            if any(t in txt for t in ["次へ", "次", "Next", "»", ">>"]):
                href = a.get("href")
                if href:
                    return urljoin(current_url, href)

    return None


def crawl_cardnos_for_expansion(s: requests.Session, expansion: str, delay: float = 0.8) -> Set[str]:
    params = {"expansion_name": expansion, "class[]": "all"}
    url = f"{SEARCH_URL}?{urlencode(params, doseq=True)}"
    cardnos: Set[str] = set()

    while url:
        soup = get_soup(s, url)
        html = str(soup)
        found = extract_cardnos_from_html(html)
        cardnos.update(found)
        nxt = find_next_url(soup, url)
        if nxt and nxt != url:
            url = nxt
            time.sleep(delay)
        else:
            break

    return cardnos


def crawl_all_cardnos(s: requests.Session, delay: float = 0.8) -> Set[str]:
    # Load index to get expansions
    soup = get_soup(s, INDEX_URL)
    expansions = extract_expansions(soup)
    if not expansions:
        # Fallback: try an unfiltered search page
        expansions = [""]

    all_cardnos: Set[str] = set()
    for exp in expansions:
        try:
            batch = crawl_cardnos_for_expansion(s, exp, delay=delay)
            all_cardnos.update(batch)
        except Exception as e:
            print(f"[warn] expansion {exp}: {e}", file=sys.stderr)
    return all_cardnos


def crawl_cardnos_from_search_url(s: requests.Session, url: str, delay: float = 0.8) -> Set[str]:
    """Crawl a full search URL (cardlist or cardsearch) and collect cardnos across pagination.

    Supports both classic pagination and the site's infinite scroll (cardsearch_ex) endpoints.
    """
    cardnos: Set[str] = set()

    # Fetch first page
    soup = get_soup(s, url)
    html = str(soup)
    cardnos.update(extract_cardnos_from_html(html))

    # Detect infinite-scroll style (cardsearch) with ajax subpages
    parsed = urlparse(url)
    if "/cardlist/cardsearch/" in parsed.path:
        # Try to find max_page from inline script
        m = re.search(r"max_page\s*=\s*(\d+)", html)
        max_page = int(m.group(1)) if m else 1
        # Build base ex URL with same query
        qs = parse_qs(parsed.query, keep_blank_values=True)
        # Normalize keys like class[] that sometimes appear as class[0]
        normalized_qs = {}
        for k, v in qs.items():
            nk = k.replace("class[0]", "class[]").replace("cost[0]", "cost[]").replace("card_kind[0]", "card_kind[]")
            normalized_qs.setdefault(nk, v)
        base_ex = urljoin(url, "/cardlist/cardsearch_ex")
        for page in range(2, max_page + 1):
            normalized_qs["page"] = [str(page)]
            ex_url = f"{base_ex}?{urlencode(normalized_qs, doseq=True)}"
            try:
                ex_soup = get_soup(s, ex_url)
                ex_html = str(ex_soup)
                cardnos.update(extract_cardnos_from_html(ex_html))
                time.sleep(delay)
            except Exception as e:
                print(f"[warn] search_ex page {page}: {e}", file=sys.stderr)
        return cardnos

    # Otherwise, try classic pagination links
    next_url = find_next_url(soup, url)
    while next_url and next_url != url:
        soup = get_soup(s, next_url)
        html = str(soup)
        cardnos.update(extract_cardnos_from_html(html))
        url = next_url
        next_url = find_next_url(soup, url)
        if next_url and next_url != url:
            time.sleep(delay)
    return cardnos


def _all_links_and_datacardnos(soup: BeautifulSoup) -> Dict[str, List[str]]:
    """Collect all hrefs and any data-cardno attributes on the page for diagnostics.

    Returns a dict with keys:
    - hrefs: list of href strings
    - data_cardnos: list of values from any [data-cardno] attributes
    """
    hrefs: List[str] = []
    for a in soup.find_all('a', href=True):
        hrefs.append(a['href'])
    data_cardnos: List[str] = []
    for el in soup.select('[data-cardno]'):
        val = (el.get('data-cardno') or '').strip()
        if val:
            data_cardnos.append(val)
    return {"hrefs": hrefs, "data_cardnos": data_cardnos}


def inspect_search_url(s: requests.Session, url: str, delay: float = 0.8, sample: int = 5) -> Dict[str, object]:
    """Inspect a search URL and report examples of:
    - Duplicate cardno occurrences across listing entries
    - Listing links that do not contain a `cardno=` parameter

    Returns a dict with summary and example lists for presentation.
    """
    results: Dict[str, object] = {
        "url": url,
        "duplicates": [],  # list of tuples (cardno, count, sample_hrefs[:3])
        "no_cardno_links": [],  # list of href samples without cardno=
        "pages": 0,
    }

    # Fetch first page
    soup = get_soup(s, url)
    html = str(soup)
    parsed = urlparse(url)

    # Helper to process a single page
    def process_page(soup: BeautifulSoup, base_url: str):
        html = str(soup)
        hrefs_data = _all_links_and_datacardnos(soup)
        hrefs = [urljoin(base_url, h) for h in hrefs_data["hrefs"]]
        found_cardnos = list(CARDNO_RE.findall(html))

        # Count occurrences per cardno and retain sample hrefs for that cardno
        hrefs_by_cardno: Dict[str, List[str]] = {}
        for h in hrefs:
            m = CARDNO_RE.search(h)
            if m:
                hrefs_by_cardno.setdefault(m.group(1), []).append(h)

        from collections import Counter
        cnt = Counter(found_cardnos)
        dups = [(cn, n, hrefs_by_cardno.get(cn, [])[:3]) for cn, n in cnt.items() if n > 1]

        # Links under cardlist domain without cardno param
        no_param = [h for h in hrefs if "/cardlist/" in h and "cardno=" not in h]

        return dups, no_param

    # Determine pagination style
    duplicates_accum: List[tuple] = []
    no_param_accum: List[str] = []

    if "/cardlist/cardsearch/" in parsed.path:
        m = re.search(r"max_page\s*=\s*(\d+)", html)
        max_page = int(m.group(1)) if m else 1
        # First page
        d, np = process_page(soup, url)
        duplicates_accum.extend(d)
        no_param_accum.extend(np)
        # Subsequent pages via cardsearch_ex
        qs = parse_qs(parsed.query, keep_blank_values=True)
        normalized_qs = {}
        for k, v in qs.items():
            nk = k.replace("class[0]", "class[]").replace("cost[0]", "cost[]").replace("card_kind[0]", "card_kind[]")
            normalized_qs.setdefault(nk, v)
        base_ex = urljoin(url, "/cardlist/cardsearch_ex")
        for page in range(2, max_page + 1):
            normalized_qs["page"] = [str(page)]
            ex_url = f"{base_ex}?{urlencode(normalized_qs, doseq=True)}"
            try:
                ex_soup = get_soup(s, ex_url)
                d, np = process_page(ex_soup, ex_url)
                duplicates_accum.extend(d)
                no_param_accum.extend(np)
                time.sleep(delay)
            except Exception:
                pass
        results["pages"] = max_page
    else:
        # Classic pagination
        current_url = url
        d, np = process_page(soup, current_url)
        duplicates_accum.extend(d)
        no_param_accum.extend(np)
        next_url = find_next_url(soup, current_url)
        pages = 1
        while next_url and next_url != current_url:
            soup = get_soup(s, next_url)
            d, np = process_page(soup, next_url)
            duplicates_accum.extend(d)
            no_param_accum.extend(np)
            pages += 1
            current_url = next_url
            next_url = find_next_url(soup, current_url)
            if next_url and next_url != current_url:
                time.sleep(delay)
        results["pages"] = pages

    # Prepare samples
    # Deduplicate duplicate entries by cardno (keep highest count and some href samples)
    best_by_cardno: Dict[str, tuple] = {}
    for cn, n, hrefs in duplicates_accum:
        cur = best_by_cardno.get(cn)
        if not cur or n > cur[1]:
            best_by_cardno[cn] = (cn, n, hrefs)
    dup_samples = sorted(best_by_cardno.values(), key=lambda x: -x[1])[:sample]

    # Unique no-cardno links, sample
    seen = set()
    uniq_no_param = []
    for h in no_param_accum:
        if h not in seen:
            seen.add(h)
            uniq_no_param.append(h)
        if len(uniq_no_param) >= sample:
            break

    results["duplicates"] = dup_samples
    results["no_cardno_links"] = uniq_no_param
    return results

LABEL_MAP = {
    # Japanese -> canonical column keys
    "カード番号": "cardno",
    "カード名": "name",
    "名称": "name",
    "クラス": "class",
    "タイトル": "title",
    "収録商品": "expansion",
    "商品": "expansion",
    "カード種類": "kind",
    "種類": "kind",
    "レアリティ": "rarity",
    "コスト": "cost",
    "パワー": "power",
    "攻撃力": "power",
    "体力": "hp",
    "タイプ": "type",
    "能力": "ability",
    "キーワード": "keywords",
    "イラストレーター": "illustrator",
}


CANON_COLS = [
    "cardno",
    "name",
    "class",
    "title",
    "expansion",
    "kind",
    "rarity",
    "cost",
    "power",
    "hp",
    "type",
    "keywords",
    "ability",
    "illustrator",
    "image_url",
    "url",
]


def extract_details_from_detail_page(soup: BeautifulSoup) -> Dict[str, str]:
    data: Dict[str, str] = {}

    # Name candidates
    name_candidates = [
        soup.select_one(".cardlist-Detail .txt > h1.ttl"),
        soup.select_one(".cardlist-Detail h1.ttl"),
        soup.select_one(".card-Detail_Name"),
        soup.select_one(".cardDetail-Name"),
        soup.select_one(".CardDetail_Name"),
        soup.select_one("h1"),
        soup.select_one(".Detail_Title"),
    ]
    for el in name_candidates:
        if el and el.get_text(strip=True):
            data.setdefault("name", el.get_text(strip=True))
            break

    # Main image (avoid site logo and unrelated images)
    img = None
    for sel in [
        ".card-Detail_Image img",
        ".CardDetail_Image img",
        ".cardlist-Card_Image img",
        "img.card-image",
        "main img",
    ]:
        img = soup.select_one(sel)
        if img and img.get("src"):
            src = img.get("src")
            # Skip common logo assets
            if "logo" in src or "assets/images/common/logo" in src:
                continue
            data["image_url"] = urljoin(BASE, src)
            break

    # Definition lists (dt/dd)
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        if not dts or len(dts) != len(dds):
            continue
        for dt, dd in zip(dts, dds):
            label = (dt.get_text(strip=True) or "").replace("：", ":").strip()
            value = dd.get_text(" ", strip=True)
            # If value is empty, sometimes numbers are expressed via <img alt="5">, etc.
            if not value:
                alts = [i.get("alt", "").strip() for i in dd.find_all("img")]
                alts = [a for a in alts if a]
                if alts:
                    value = " ".join(alts)
            key = LABEL_MAP.get(label)
            if not key:
                # Try simplified matching without punctuation/spaces
                simple = re.sub(r"\s|:|：", "", label)
                for jp, en in LABEL_MAP.items():
                    if simple == re.sub(r"\s|:|：", "", jp):
                        key = en
                        break
            if key and value:
                # If multiple entries exist, join with / but avoid duplicates
                if key in data and value not in data[key]:
                    data[key] = f"{data[key]} / {value}"
                else:
                    data[key] = value

    # Ability from primary detail block
    detail_block = soup.select_one('.detail')
    if detail_block:
        # Replace icons with their alt text and preserve <br> as line breaks only
        for img in detail_block.find_all('img'):
            alt = (img.get('alt') or '').strip()
            img.replace_with(alt if alt else '')
        for br in detail_block.find_all('br'):
            br.replace_with('\n')
        text = detail_block.get_text()
        # Normalize spaces but keep newlines inserted by <br>
        text = re.sub(r'[\t\r\f\v ]+', ' ', text)
        # Trim spaces around each line
        lines = [ln.strip() for ln in text.split('\n')]
        text = '\n'.join(lines)
        if text:
            data['ability'] = text

    # Ability might be in other blocks (fallback)
    if "ability" not in data:
        ability_block = soup.select_one(".Ability, .CardText, .card-Ability, .cardtext")
        if ability_block:
            for img in ability_block.find_all('img'):
                alt = (img.get('alt') or '').strip()
                img.replace_with(alt if alt else '')
            for br in ability_block.find_all('br'):
                br.replace_with('\n')
            text = ability_block.get_text()
            text = re.sub(r'[\t\r\f\v ]+', ' ', text)
            lines = [ln.strip() for ln in text.split('\n')]
            text = '\n'.join(lines)
            if text:
                data["ability"] = text

    # Status block for cost/power/hp (outside of dl)
    status = soup.select_one('.status')
    if status:
        def _extract_number(sel: str) -> Optional[str]:
            el = status.select_one(sel)
            if not el:
                return None
            txt = el.get_text(strip=True)
            m = re.search(r"(\d+)", txt)
            return m.group(1) if m else None

        cost = _extract_number('.status-Item-Cost')
        power = _extract_number('.status-Item-Power')
        hp = _extract_number('.status-Item-Hp')
        if cost and not data.get('cost'):
            data['cost'] = cost
        if power and not data.get('power'):
            data['power'] = power
        if hp and not data.get('hp'):
            data['hp'] = hp

    return data


def detail_url_for_cardno(cardno: str) -> str:
    # Minimal parameters work on the site; use only cardno
    return f"{INDEX_URL}?cardno={cardno}"


def _looks_like_detail_page(soup: BeautifulSoup) -> bool:
    # Heuristic: presence of known name blocks or definition list with expected labels
    if soup.select_one(".card-Detail_Name, .cardDetail-Name, .CardDetail_Name, .Detail_Title"):
        return True
    for dt in soup.find_all("dt"):
        txt = (dt.get_text(strip=True) or "").replace("：", ":").strip()
        if txt in LABEL_MAP or re.sub(r"\s|:|：", "", txt) in {re.sub(r"\s|:|：", "", k) for k in LABEL_MAP}:
            return True
    return False


def _find_detail_link_in_page(soup: BeautifulSoup, current_url: str, cardno: str) -> Optional[str]:
    # Prefer links that include the exact cardno
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "cardno=" in href and cardno in href:
            return urljoin(current_url, href)
    # Try common list/detail link patterns
    a = soup.select_one(".cardlist-Card a, .CardList a, a.card-link")
    if a and a.get("href"):
        return urljoin(current_url, a["href"])
    return None


def scrape_card_detail(s: requests.Session, cardno: str, delay: float = 0.6) -> Dict[str, str]:
    # Try initial guess and fallbacks; resolve real detail page if needed
    candidates = [
        detail_url_for_cardno(cardno),
        f"{SEARCH_URL}?{urlencode({'cardno': cardno, 'class[]': 'all'}, doseq=True)}",
    ]
    data: Dict[str, str] = {}
    final_url: Optional[str] = None
    for u in candidates:
        try:
            soup = get_soup(s, u)
        except Exception:
            continue
        if not _looks_like_detail_page(soup):
            # Try to resolve a more specific detail link from this page
            nxt = _find_detail_link_in_page(soup, u, cardno)
            if nxt:
                try:
                    soup = get_soup(s, nxt)
                    u = nxt
                except Exception:
                    pass
        data = extract_details_from_detail_page(soup)
        if data:  # got something
            final_url = u
            break

    if not data:
        # Last resort: hit the first candidate and attempt extraction anyway
        u = candidates[0]
        try:
            soup = get_soup(s, u)
            data = extract_details_from_detail_page(soup)
            final_url = u
        except Exception:
            data = {}
            final_url = u

    data.setdefault("cardno", cardno)
    if final_url:
        data.setdefault("url", final_url)
    # Be polite
    time.sleep(delay)
    return data


def write_tsv(rows: Iterable[Dict[str, str]], out_path: str) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CANON_COLS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for row in rows:
            # Ensure all keys present
            out = {k: row.get(k, "") for k in CANON_COLS}
            w.writerow(out)


def main():
    import argparse

    p = argparse.ArgumentParser(description="Scrape Shadowverse EVOLVE card data to TSV")
    p.add_argument("--out", default="cards.tsv", help="Output TSV path")
    p.add_argument("--delay", type=float, default=0.6, help="Delay seconds between requests")
    p.add_argument(
        "--only-expansion",
        action="append",
        default=None,
        help="Limit scraping to specific expansion code(s) (e.g., BP16). Can be repeated.")
    p.add_argument("--limit", type=int, default=0, help="Limit number of cards for quick test")
    p.add_argument(
        "--search-url",
        action="append",
        default=None,
        help="Full search URL from the site (cardlist or cardsearch). Can be repeated.",
    )
    p.add_argument(
        "--inspect-search",
        action="append",
        default=None,
        help="Inspect a search URL for duplicate cardnos and links without cardno= (diagnostic).",
    )
    p.add_argument(
        "--inspect-limit",
        type=int,
        default=5,
        help="Max number of duplicate/no-cardno samples to print during inspection.",
    )
    args = p.parse_args()
    start_ts = time.time()

    s = session_with_retries()

    # Diagnostic mode: inspect and exit
    if args.inspect_search:
        for u in args.inspect_search:
            info = inspect_search_url(s, u, delay=args.delay, sample=args.inspect_limit)
            print(f"[inspect] URL: {info['url']}")
            print(f"[inspect] Pages scanned: {info['pages']}")
            dups = info.get('duplicates', []) or []
            if dups:
                print("[inspect] Duplicate cardno samples (cardno x count):")
                for cn, n, hrefs in dups:
                    print(f"  - {cn} x {n}")
                    for h in hrefs:
                        print(f"      href: {h}")
            else:
                print("[inspect] No duplicate cardno occurrences found in samples.")
            no_params = info.get('no_cardno_links', []) or []
            if no_params:
                print("[inspect] Links without cardno= samples:")
                for h in no_params:
                    print(f"  - {h}")
            else:
                print("[inspect] No links without cardno= found in samples.")
        # Print elapsed time also in inspect mode
        elapsed = time.time() - start_ts
        print(f"Elapsed time: {elapsed:.2f}s")
        return

    # Collect cardnos based on inputs
    cardnos: Set[str] = set()
    if args.search_url:
        for u in args.search_url:
            cardnos |= crawl_cardnos_from_search_url(s, u, delay=args.delay)
    if args.only_expansion:
        for exp in args.only_expansion:
            cardnos |= crawl_cardnos_for_expansion(s, exp, delay=args.delay)
    if not args.search_url and not args.only_expansion:
        cardnos = crawl_all_cardnos(s, delay=args.delay)

    if not cardnos:
        print("No card numbers found. The site layout may have changed.", file=sys.stderr)
        sys.exit(2)

    print(f"Found {len(cardnos)} cards. Fetching details...")

    rows: List[Dict[str, str]] = []
    for i, cn in enumerate(sorted(cardnos)):
        if args.limit and i >= args.limit:
            break
        try:
            row = scrape_card_detail(s, cn, delay=args.delay)
            rows.append(row)
        except Exception as e:
            print(f"[warn] detail {cn}: {e}", file=sys.stderr)

    # De-duplicate by (name, kind), keeping the first occurrence.
    # Since rows are built in cardno ASCII ascending order, the kept one
    # is the lexicographically smallest by current ordering.
    uniq_rows: List[Dict[str, str]] = []
    seen_name_kind: Set[tuple] = set()
    for r in rows:
        key = (r.get("name", ""), r.get("kind", ""))
        if key in seen_name_kind:
            continue
        seen_name_kind.add(key)
        uniq_rows.append(r)

    write_tsv(uniq_rows, args.out)
    print(f"Wrote {len(uniq_rows)} records to {args.out}")
    elapsed = time.time() - start_ts
    print(f"Elapsed time: {elapsed:.2f}s")


if __name__ == "__main__":
    main()
