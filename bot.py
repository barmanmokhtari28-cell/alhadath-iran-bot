#!/usr/bin/env python3
"""
Al Hadath (الحدث) -> Iran-related news monitor -> Telegram channel (Persian)

What this does, every time it runs:
  1. Pulls the latest articles directly off alhadath.net by scraping the
     homepage and every section page (News, Yemen, Syria, Egypt, Iraq,
     Maghreb) -- Al Hadath doesn't publish a working RSS feed and does not
     have a dedicated "Iran" section, so Iran stories show up scattered
     across all of these, and Google News' index of the site is too sparse
     and delayed to rely on, so this goes straight to the source instead.
  2. Filters for Iran-related stories (keyword match on the link text
     shown in the listing, then double-checked against the full article
     page).
  3. Skips anything already posted before (tracked in state/seen.json).
  4. Translates the headline + a short lead into Persian.
  5. Posts to the configured Telegram channel with Telegram HTML formatting.
  6. Saves the updated "seen" list so the same story is never posted twice.

Meant to be run on a schedule (see .github/workflows/monitor.yml), every 5 minutes.
That polling interval is what gives you the "5-10 min after publish" delay --
there's no way to get truly instant delivery without Al Hadath pushing to us,
so polling often is the practical way to get close to real-time.
"""

import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")  # e.g. "@your_channel" or "-100xxxxxxxxxx"

STATE_FILE = Path(__file__).parent / "state" / "seen.json"
MAX_SEEN_KEEP = 1500  # how many old links to remember, so the file doesn't grow forever

# Pages scraped every run. Al Hadath has no dedicated Iran section, so Iran
# stories can land under any of these -- we check them all, every run.
SCRAPE_PAGES = [
    "https://www.alhadath.net/",
    "https://www.alhadath.net/News",
    "https://www.alhadath.net/yemen",
    "https://www.alhadath.net/syria",
    "https://www.alhadath.net/egypt",
    "https://www.alhadath.net/iraq",
    "https://www.alhadath.net/maghreb",
]

# How many articles (max) to fetch full meta for for translation on a single
# run. Keeps a run fast even if a section page is unusually link-heavy.
MAX_ARTICLE_FETCHES_PER_RUN = 40

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.8,en;q=0.6",
    "Referer": "https://www.google.com/",
}

# Iran-related keywords to filter on (Arabic). Add/remove terms as needed.
IRAN_KEYWORDS = [
    "إيران", "إيراني", "إيرانية", "الإيراني", "الإيرانية", "الإيرانيين",
    "طهران", "خامنئي", "بزشكيان", "عراقجي", "الحرس الثوري", "روحاني",
    "ذوالقدر", "مضيق هرمز", "هرمز", "النووي الإيراني", "وزير الخارجية الإيراني",
]

CAPTION_LINK_TEXT = "🇸🇦 🚨الـحــــدث"
FOOTER = "🤖@secretollah\n\n#الحدث\n#فوری"


# ---------------------------------------------------------------------------
# Fetching with proxy fallback
# ---------------------------------------------------------------------------
# alhadath.net (and sites on the same network) return 403 Forbidden to
# requests coming straight from GitHub Actions' IP ranges -- that's a
# datacenter-IP block on their end, not something fixable with headers.
# So: try a direct request first (works fine for local/manual runs), and if
# that gets blocked, fall back to fetching the page through a proxy/reader
# service instead, whose IPs aren't in that blocklist.
#
# Returns (content, mode) where mode is "html" (raw HTML, parse with
# BeautifulSoup as normal) or "text" (already-extracted readable text/
# markdown from a reader proxy -- parse with the *_from_text() helpers).
# Returns (None, None) if every method failed.

def fetch_url(url: str):
    # 1) Direct request.
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.text, "html"
        print(f"[fetch] direct request to {url} returned {resp.status_code}, trying proxy")
    except requests.RequestException as e:
        print(f"[fetch] direct request to {url} failed ({e}), trying proxy")

    # Proxy fallbacks, tried in order. Free proxy/reader services are often
    # rate-limited *per IP*, and GitHub Actions runners share IP pools with
    # a huge number of other users -- so a method that worked a minute ago
    # can be silently throttled on the next run. To catch that, we don't
    # just check for HTTP 200: we require the response to actually look
    # like the real page (mentions alhadath.net, isn't suspiciously short),
    # since a rate-limit notice or interstitial page can also come back as
    # a "successful" 200 response with a small, useless body.
    proxies = [
        ("r.jina.ai", f"https://r.jina.ai/{url}", {}),
        ("allorigins/raw", f"https://api.allorigins.win/raw?url={quote(url, safe='')}", {}),
        ("corsproxy.io", f"https://corsproxy.io/?url={quote(url, safe='')}", {}),
    ]

    for name, proxy_url, extra_headers in proxies:
        for attempt in (1, 2):
            try:
                resp = requests.get(proxy_url, headers=extra_headers, timeout=25)
            except requests.RequestException as e:
                print(f"[fetch] {name} attempt {attempt} for {url} failed: {e}")
                continue

            body = resp.text or ""
            looks_valid = resp.status_code == 200 and len(body) > 500 and "alhadath" in body.lower()

            if looks_valid:
                print(f"[fetch] {name} succeeded for {url} ({len(body)} chars)")
                mode = "text" if name == "r.jina.ai" else "html"
                return body, mode

            print(
                f"[fetch] {name} attempt {attempt} for {url} looked invalid "
                f"(status={resp.status_code}, len={len(body)}, "
                f"preview={body[:120]!r})"
            )
            time.sleep(2)

    print(f"[fetch] all methods failed for {url}")
    return None, None


def extract_links_from_html(base_url: str, html_text: str) -> dict:
    """Returns {absolute_url: link_text} for every alhadath.net article
    link found in a page of HTML."""
    links = {}
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if "alhadath.net" not in href:
            continue
        if not re.search(r"/\d{4}/\d{2}/\d{2}/", href):
            continue
        link_text = a.get_text(strip=True)
        if not link_text:
            img = a.find("img", alt=True)
            link_text = img["alt"].strip() if img else ""
        if href not in links or len(link_text) > len(links[href]):
            links[href] = link_text
    return links


def extract_links_from_text(text: str) -> dict:
    """Returns {absolute_url: link_text} for article links found in
    markdown/plain-text page content (e.g. from the r.jina.ai proxy).

    Listing pages render article links as nested image-links:
        [![caption](https://vid.alarabiya.net/images/2026/08/04/...jpg)](https://www.alhadath.net/2026/08/04/slug)
    A naive "[text](url)" regex matches the *inner* image link first (its
    URL also happens to contain a date pattern, so it slips past the date
    filter) and never reaches the real article URL. Collapsing the inner
    image markdown down to its caption text first fixes that.
    """
    # ![caption](img_url)  ->  caption
    cleaned = re.sub(r"!\[([^\]]*)\]\([^\)]*\)", r"\1", text)

    links = {}
    for match in re.finditer(r"\[([^\]]*)\]\((https?://[^\s\)]+)\)", cleaned):
        link_text, href = match.group(1).strip(), match.group(2).strip()
        # Restrict to alhadath.net itself -- excludes any stray links to
        # vid.alarabiya.net, social share links, etc. that might otherwise
        # match the date pattern below.
        if "alhadath.net" not in href:
            continue
        if not re.search(r"/\d{4}/\d{2}/\d{2}/", href):
            continue
        if href not in links or len(link_text) > len(links[href]):
            links[href] = link_text
    return links


def parse_article_from_html(url: str, html_text: str) -> dict | None:
    soup = BeautifulSoup(html_text, "html.parser")

    def meta_content(prop):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return tag["content"].strip() if tag and tag.get("content") else ""

    title = meta_content("og:title") or (soup.title.string.strip() if soup.title else "")
    summary = meta_content("og:description") or meta_content("description")

    if not title:
        return None
    return {"title": html.unescape(title), "summary": html.unescape(summary), "link": url}


def parse_article_from_text(url: str, text: str) -> dict | None:
    """Parse the reader-proxy output format:
        Title: ...
        URL Source: ...
        Markdown Content:
        <article body>
    """
    title_match = re.search(r"^Title:\s*(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else ""

    idx = text.find("Markdown Content:")
    body = text[idx + len("Markdown Content:"):].strip() if idx != -1 else text
    # First non-empty paragraph as the summary/lead.
    summary = ""
    for para in body.split("\n"):
        para = para.strip().lstrip("#").strip()
        if len(para) > 20:
            summary = para[:400]
            break

    if not title:
        return None
    return {"title": title, "summary": summary, "link": url}


# ---------------------------------------------------------------------------
# State (dedup) handling
# ---------------------------------------------------------------------------

def load_seen() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_seen(seen: set) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Keep only the most recent MAX_SEEN_KEEP entries (order isn't meaningful
    # for a set, so we just cap the size to bound file growth).
    trimmed = list(seen)[-MAX_SEEN_KEEP:]
    STATE_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fetching articles
# ---------------------------------------------------------------------------

def collect_candidate_links() -> dict:
    """Fetch every page in SCRAPE_PAGES (direct, falling back to a proxy if
    blocked) and collect article links along with whatever visible link
    text is sitting right there in the listing. Returns {link: text}."""
    candidates = {}

    for page_url in SCRAPE_PAGES:
        content, mode = fetch_url(page_url)
        if content is None:
            continue

        page_links = (
            extract_links_from_html(page_url, content) if mode == "html"
            else extract_links_from_text(content)
        )
        for href, text in page_links.items():
            if href not in candidates or len(text) > len(candidates[href]):
                candidates[href] = text

    print(f"[scrape] found {len(candidates)} candidate article links across {len(SCRAPE_PAGES)} pages")
    return candidates


def fetch_article_meta(url: str) -> dict | None:
    content, mode = fetch_url(url)
    if content is None:
        return None
    if mode == "html":
        return parse_article_from_html(url, content)
    return parse_article_from_text(url, content)


def get_latest_articles(seen: set) -> list[dict]:
    """Collect candidate links, pre-filter by the listing-page text so we
    don't waste requests on obviously-unrelated stories, then fetch full
    meta (title + description) only for links that look promising or that
    we can't judge from the listing text alone."""
    candidates = collect_candidate_links()
    articles = []
    fetched = 0

    for link, listing_text in candidates.items():
        if link in seen:
            continue

        looks_relevant = any(kw in listing_text for kw in IRAN_KEYWORDS)
        # If the listing text is too short/empty to judge, or it does look
        # relevant, fetch the full article to get an accurate title +
        # summary (and to double check on the full text, since listing
        # captions are sometimes trimmed and can miss a keyword).
        if not listing_text or looks_relevant:
            if fetched >= MAX_ARTICLE_FETCHES_PER_RUN:
                continue
            meta = fetch_article_meta(link)
            fetched += 1
            if meta:
                articles.append(meta)
        # else: listing text is present and clearly not Iran-related --
        # skip fetching it at all, saves a request.

    return articles


# ---------------------------------------------------------------------------
# Filtering + translation
# ---------------------------------------------------------------------------

def is_iran_related(article: dict) -> bool:
    text = f"{article.get('title', '')} {article.get('summary', '')}"
    return any(kw in text for kw in IRAN_KEYWORDS)


def translate_to_persian(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    try:
        return GoogleTranslator(source="ar", target="fa").translate(text)
    except Exception as e:
        print(f"[translate] failed, posting original Arabic text instead: {e}")
        return text


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def build_message(article: dict, title_fa: str, summary_fa: str) -> str:
    parts = [f"<b>{escape_html(title_fa)}</b>"]
    if summary_fa:
        parts.append(escape_html(summary_fa))
    parts.append(f'<a href="{escape_html(article["link"])}">{CAPTION_LINK_TEXT}</a>')
    parts.append(FOOTER)
    return "\n\n".join(parts)


def send_to_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHANNEL_ID:
        print("[telegram] missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID, cannot send")
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(api_url, data=payload, timeout=15)
        if resp.status_code != 200:
            print(f"[telegram] send failed: {resp.status_code} {resp.text}")
            return False
        return True
    except requests.RequestException as e:
        print(f"[telegram] send error: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    articles = get_latest_articles(seen)

    if not articles:
        print("[main] no candidate articles fetched this run")
        return

    new_iran_articles = [
        a for a in articles
        if a["link"] and a["link"] not in seen and is_iran_related(a)
    ]

    # Oldest first, so the channel timeline reads chronologically.
    new_iran_articles.reverse()

    if not new_iran_articles:
        print("[main] no new Iran-related articles this run")
        return

    posted = 0
    for article in new_iran_articles:
        title_fa = translate_to_persian(article["title"])
        summary_fa = translate_to_persian(article["summary"]) if article["summary"] else ""

        message = build_message(article, title_fa, summary_fa)

        if send_to_telegram(message):
            print(f"[main] posted: {article['title']}")
            seen.add(article["link"])
            posted += 1
            time.sleep(2)  # be gentle with Telegram's rate limits
        else:
            print(f"[main] failed to post, will retry next run: {article['title']}")

    save_seen(seen)
    print(f"[main] done, posted {posted}/{len(new_iran_articles)} new article(s)")


if __name__ == "__main__":
    sys.exit(main())
