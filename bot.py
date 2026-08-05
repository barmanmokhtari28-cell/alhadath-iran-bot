#!/usr/bin/env python3
"""
Al Hadath (الحدث) -> Iran-related news monitor -> Telegram channel (Persian)
"""

import calendar
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
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

STATE_FILE = Path(__file__).parent / "state" / "seen.json"
MAX_SEEN_KEEP = 500

# Hours back to check for articles (Set to 48 for testing)
HOURS_BACK = 48
MAX_POSTS_PER_RUN = 15  # Safety limit per execution

# Google News RSS for Al Hadath (bypasses Cloudflare 403 WAF blocks)
RSS_CANDIDATES = [
    "https://news.google.com/rss/search?q=site:alhadath.net&hl=ar&gl=SA&ceid=SA:ar",
    "https://news.google.com/rss/search?q=site:alarabiya.net&hl=ar&gl=SA&ceid=SA:ar",
    "https://www.alhadath.net/.mrss/alhadath.xml",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

IRAN_KEYWORDS = [
    "إيران", "إيراني", "إيرانية", "الإيراني", "الإيرانية", "الإيرانيين",
    "طهران", "خامنئي", "بزشكيان", "عراقجي", "الحرس الثوري", "روحاني",
    "ذوالقدر", "مضيق هرمز", "هرمز", "النووي الإيراني", "وزير الخارجية الإيراني",
]

CAPTION_LINK_TEXT = "🇸🇦 🚨الـحــــدث"
FOOTER = "🤖 @secretollah\n\n#الحدث\n#فوری"


# ---------------------------------------------------------------------------
# State (dedup) handling
# ---------------------------------------------------------------------------

def load_seen() -> set:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    save_seen(set())
    return set()


def save_seen(seen: set) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    trimmed = list(seen)[-MAX_SEEN_KEEP:]
    STATE_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Date + Fetching
# ---------------------------------------------------------------------------

def is_within_timeframe(entry, hours=HOURS_BACK) -> bool:
    """Check if the article was published within the last N hours."""
    published_parsed = entry.get("published_parsed")
    if not published_parsed:
        return True  # Include if no publish date is specified
    
    pub_timestamp = calendar.timegm(published_parsed)
    cutoff = time.time() - (hours * 3600)
    return pub_timestamp >= cutoff


def clean_title(title: str) -> str:
    """Remove source suffix added by Google News RSS (e.g. '- الحدث')."""
    return re.sub(r"\s*-\s*(الحدث|العربية).*$", "", title).strip()


def fetch_from_rss() -> list[dict]:
    articles = []
    seen_links = set()

    for url in RSS_CANDIDATES:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200 or not resp.content:
                continue

            feed = feedparser.parse(resp.content)
            if not feed.entries:
                continue

            for entry in feed.entries:
                link = entry.get("link", "").strip()
                if not link or link in seen_links:
                    continue

                if not is_within_timeframe(entry, hours=HOURS_BACK):
                    continue

                raw_title = entry.get("title", "").strip()
                cleaned_title = clean_title(raw_title)

                summary_raw = entry.get("summary", "") or entry.get("description", "")
                cleaned_summary = re.sub("<[^<]+?>", "", summary_raw).strip()

                articles.append({
                    "title": html.unescape(cleaned_title),
                    "summary": html.unescape(cleaned_summary),
                    "link": link,
                })
                seen_links.add(link)

            if articles:
                print(f"[rss] fetched {len(articles)} entries within last {HOURS_BACK}h from {url}")
                return articles

        except requests.RequestException as e:
            print(f"[rss] failed for {url}: {e}")
            continue

    return articles


# ---------------------------------------------------------------------------
# Filtering + Translation
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
        print(f"[translate] failed, using Arabic original: {e}")
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
        print("[telegram] missing token/channel ID")
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
    articles = fetch_from_rss()

    if not articles:
        print(f"[main] no articles found from last {HOURS_BACK} hours")
        return

    new_iran_articles = [
        a for a in articles
        if a["link"] and a["link"] not in seen and is_iran_related(a)
    ]

    # Post oldest first so timeline is chronological
    new_iran_articles.reverse()

    if not new_iran_articles:
        print("[main] no new Iran-related articles matching criteria")
        return

    # Cap posts per execution to avoid Telegram API rate limits
    to_post = new_iran_articles[:MAX_POSTS_PER_RUN]
    print(f"[main] posting {len(to_post)} Iran-related article(s) from last {HOURS_BACK}h...")

    posted = 0
    for article in to_post:
        title_fa = translate_to_persian(article["title"])
        summary_fa = translate_to_persian(article["summary"]) if article["summary"] else ""

        message = build_message(article, title_fa, summary_fa)

        if send_to_telegram(message):
            print(f"[main] posted: {article['title']}")
            seen.add(article["link"])
            posted += 1
            time.sleep(2)  # Gentle delay for Telegram API
        else:
            print(f"[main] failed to post: {article['title']}")

    save_seen(seen)
    print(f"[main] completed: posted {posted}/{len(to_post)} article(s)")


if __name__ == "__main__":
    sys.exit(main())
