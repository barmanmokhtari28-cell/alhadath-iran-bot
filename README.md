# Al Hadath → Iran News → Telegram (Persian)

Monitors Al Hadath (الحدث, alhadath.net — Saudi/Al Arabiya Network) for
Iran-related stories, translates them to Persian, and posts them to a
Telegram channel with rich formatting.

## How it works

- A GitHub Actions workflow runs `bot.py` every **5 minutes**.
- The script tries Al Hadath's RSS feed first; if that doesn't return
  anything, it falls back to scraping the homepage / news listing and
  reading each article's `og:title` / `og:description` tags.
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

## Important: verify the RSS feed URL

Al Hadath doesn't publish an RSS index page the way its sister site
alarabiya.net does (`alarabiya.net/tools/mrss`), so `bot.py` guesses a
few likely RSS URLs based on the pattern Al Arabiya's CMS uses
(`RSS_CANDIDATES` near the top of the file). **Check the Actions logs
after your first run(s)** — the script prints `[rss] got N entries from
<url>` if one of the guesses worked, or `[scrape] ...` lines if it had
to fall back to scraping. If RSS never works, the scrape fallback still
covers you, but it's worth spending 2 minutes checking
`view-source:https://www.alhadath.net/` for a `<link type="application/rss+xml">`
tag, or trying feed URLs directly in a browser, and updating
`RSS_CANDIDATES` if you find the real one — RSS is more reliable and
faster than scraping.

## Tuning

- **Keyword list**: `IRAN_KEYWORDS` in `bot.py`. Add terms (e.g. more
  officials' names, "الاتفاق النووي", etc.) as needed.
- **Poll frequency**: the `cron` line in
  `.github/workflows/monitor.yml`. GitHub Actions' schedule isn't
  perfectly precise (a few minutes of jitter is normal), which is fine
  given the requested 5-10 min delay.
- **Message format**: `build_message()` in `bot.py`.
- **How many old links to remember for dedup**: `MAX_SEEN_KEEP` in
  `bot.py` (default 500).

## Local testing

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="123:abc"
export TELEGRAM_CHANNEL_ID="@your_channel"
python bot.py
```
