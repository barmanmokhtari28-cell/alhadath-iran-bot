#!/usr/bin/env python3
"""
Al Hadath (الحدث) -> Iran-related news monitor -> Telegram channel (Persian)

What this does, every time it runs:
  1. Pulls the latest articles from Al Hadath (RSS first, HTML scrape as fallback).
  2. Filters for Iran-related stories (keyword match on title/summary).
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
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")  # e.g. "@your_channel" or "-100xxxxxxxxxx"

STATE_FILE = Path(__file__).parent / "state" / "seen.json"
MAX_SEEN_KEEP = 500  # how many old links to remember, so the file doesn't grow forever

# Candidate RSS feeds to try, in order. Al Hadath doesn't advertise an RSS page
# the way alarabiya.net does, but it runs on the same CMS, so these are the
# most likely paths. The scraper below is the guaranteed fallback if none work.
RSS_CANDIDATES = [
    "https://www.alhadath.net/feed/rss2/ar.xml",
    "https://www.alhadath.net/feed/rss2/ar/News.xml",
    "https://www.alhadath.net/rss.xml",
]

# Pages to scrape as a fallback / supplement to RSS (homepage + main news listing)
SCRAPE_PAGES = [
    "https://www.alhadath.net/",
    "https://www.alhadath.net/News",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Iran-related keywords to filter on (Arabic). Add/remove terms as needed.
IRAN_KEYWORDS = [
    "إيران", "إيراني", "إيرانية", "الإيراني", "الإيرانية", "الإيرانيين",
    "طهران", "خامنئي", "بزشكيان", "عراقجي", "الحرس الثوري", "ترامب",
    "ذوالقدر", "مضيق هرمز", "هرمز", "النووي الإيراني", "وزير الخارجية الإيراني",
]

CAPTION_LINK_TEXT = "🇸🇦 🚨الـحــــدث"
FOOTER = "🤖 @secretollah\n\n#الحدث\n#فوری"


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

def fetch_from_rss() -> list[dict]:
    articles = []
    for url in RSS_CANDIDATES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200 or not resp.content:
                continue
            feed = feedparser.parse(resp.content)
            if not feed.entries:
                continue
            for entry in feed.entries:
                articles.append({
                    "title": html.unescape(entry.get("title", "").strip()),
                    "summary": html.unescape(re.sub("<[^<]+?>", "", entry.get("summary", ""))).strip(),
                    "link": entry.get("link", "").strip(),
                })
            if articles:
                print(f"[rss] got {len(articles)} entries from {url}")
                return articles
        except requests.RequestException as e:
            print(f"[rss] failed for {url}: {e}")
            continue
    return articles


def fetch_from_scrape() -> list[dict]:
    """Fallback: scrape article links off the homepage / news listing page,
    then pull title+description from each article's meta tags."""
    articles = []
    seen_links = set()

    for page_url in SCRAPE_PAGES:
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[scrape] failed to load {page_url}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(page_url, a["href"])
            # Article URLs on alhadath.net look like:
            # https://www.alhadath.net/2026/08/04/<slug>
            if re.search(r"/\d{4}/\d{2}/\d{2}/", href) and href not in seen_links:
                seen_links.add(href)

    for link in seen_links:
        meta = fetch_article_meta(link)
        if meta:
            articles.append(meta)

    print(f"[scrape] found {len(articles)} candidate article links")
    return articles


def fetch_article_meta(url: str) -> dict | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[scrape] failed to load article {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    def meta_content(prop):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return tag["content"].strip() if tag and tag.get("content") else ""

    title = meta_content("og:title") or (soup.title.string.strip() if soup.title else "")
    summary = meta_content("og:description") or meta_content("description")

    if not title:
        return None

    return {"title": html.unescape(title), "summary": html.unescape(summary), "link": url}


def get_latest_articles() -> list[dict]:
    articles = fetch_from_rss()
    if not articles:
        print("[main] RSS returned nothing usable, falling back to scraping")
        articles = fetch_from_scrape()
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
    articles = get_latest_articles()

    if not articles:
        print("[main] no articles fetched this run (RSS + scrape both empty)")
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
