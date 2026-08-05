# Al Hadath → Iran News → Telegram (Persian)

Monitors Al Hadath (الحدث, alhadath.net — Saudi/Al Arabiya Network) for
Iran-related stories, translates them to Persian, and posts them to a
Telegram channel with rich formatting.

## How it works

- A GitHub Actions workflow runs `bot.py` every **5 minutes**.
- The script fetches alhadath.net directly — the homepage plus every
  section page (News, Yemen, Syria, Egypt, Iraq, Maghreb). Al Hadath has
  no working RSS feed and no dedicated Iran section, so Iran stories can
  land under any of these; checking all of them every run is what makes
  this reliable. (An earlier version tried Google News' index of the
  site instead — it turned out to be too sparse/delayed and missed real
  stories, so this version goes straight to the source.)
- **Proxy fallback**: alhadath.net blocks requests coming from GitHub
  Actions' IP ranges (403 Forbidden) — a datacenter-IP block on their
  end that no amount of header-tweaking gets around. So every fetch
  tries a direct request first, and if that's blocked, falls back to
  fetching the same page through a reader/proxy service
  ([r.jina.ai](https://r.jina.ai), then
  [allorigins.win](https://allorigins.win) as a second fallback) whose
  IPs aren't blocked. This is all handled by `fetch_url()` in `bot.py` —
  nothing to configure, but worth knowing if you ever see `[fetch] ...`
  lines in the logs.
- For each candidate article link, it first checks the headline text
  already visible on the listing page against the Iran keyword list. If
  that text is missing or looks relevant, it fetches the full article
  page's `og:title` / `og:description` for an accurate title + summary
  before deciding for sure.
- Articles are filtered by an Iran-related keyword list (`IRAN_KEYWORDS`
  in `bot.py` — edit this list to tune what counts as "Iran-related").
- Already-posted links are tracked in `state/seen.json`, which the
  workflow commits back to the repo after every run, so nothing gets
  posted twice.
- Each post is translated to Persian and sent to Telegram using HTML
  formatting:
  - Bold translated headline
  - Translated lead/summary
  - A hyperlink reading **🇸🇦 🚨الـحــــدث** pointing at the original article
  - A fixed footer:
    ```
    🤖@secretollah

    #الحدث
    #فوری
    ```

## Setup

1. **Create the repo on GitHub** and push this code to it.

2. **Create/verify your Telegram bot**
   - Talk to [@BotFather](https://t.me/BotFather) to get a bot token
     (skip this if you already have a bot).
   - Add the bot to your channel as an **admin** (needs permission to
     post messages).
   - Get your channel ID — for a public channel this is just
     `@your_channel_username`; for a private channel you'll need the
     numeric ID (starts with `-100...`), which you can get by forwarding
     a channel message to [@userinfobot](https://t.me/userinfobot) or
     similar.

3. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `TELEGRAM_BOT_TOKEN` — your bot token from BotFather
   - `TELEGRAM_CHANNEL_ID` — your channel's `@username` or numeric ID

4. **Enable Actions** on the repo if it's not on by default, and make
   sure Actions has **write permission** to push commits (Settings →
   Actions → General → Workflow permissions → "Read and write
   permissions"). This is needed so the workflow can commit the updated
   `state/seen.json` after each run.

5. That's it — the workflow will start running every 5 minutes. You can
   also trigger a run manually from the **Actions** tab
   ("Monitor Al Hadath for Iran news" → Run workflow) to test it right
   away instead of waiting for the schedule.

## Tuning

- **Which pages get scraped**: `SCRAPE_PAGES` in `bot.py`. Add more
  section URLs here if you find Iran coverage showing up somewhere not
  already on the list.

- **Keyword list**: `IRAN_KEYWORDS` in `bot.py`. Add terms (e.g. more
  officials' names, "الاتفاق النووي", etc.) as needed.
- **Poll frequency**: the `cron` line in
  `.github/workflows/monitor.yml`. GitHub Actions' schedule isn't
  perfectly precise (a few minutes of jitter is normal), which is fine
  given the requested 5-10 min delay.
- **Message format**: `build_message()` in `bot.py`.
- **How many old links to remember for dedup**: `MAX_SEEN_KEEP` in
  `bot.py` (default 1500).
- **How many full articles to fetch per run**: `MAX_ARTICLE_FETCHES_PER_RUN`
  in `bot.py` (default 40) — a safety cap so one run never fetches an
  unreasonable number of article pages; raise it if you have a lot of
  Iran coverage landing at once and some is getting skipped.

## Local testing

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="123:abc"
export TELEGRAM_CHANNEL_ID="@your_channel"
python bot.py
```
