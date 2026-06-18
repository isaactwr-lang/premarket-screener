# Premarket Screener

Screens US equities for premarket gap-ups with news catalysts. Runs daily at 8:30am ET via GitHub Actions and delivers results to Telegram.

## How It Works

1. Fetches the day's universe from Alpaca's screener endpoint (50 movers) merged with a curated watchlist (~57 tickers)
2. Pulls live premarket prices from Yahoo Finance's chart API for each ticker
3. Filters by: **gap > 5%** and **previous session RVOL ≥ 3x** (relative to 20-day average daily volume)
4. Sorts results by market cap tier (mega → large → mid → small → micro)
5. Scrapes Benzinga for a news catalyst per ticker (Alpaca News API as fallback)
6. Delivers a formatted summary to Telegram

## Project Structure

```
├── agents/
│   └── gappers_screener.py     # core screening agent
├── connectors/
│   ├── alpaca_connector.py     # Alpaca screener + news API
│   ├── yfinance_connector.py   # Yahoo Finance chart API (prices + volume history)
│   └── web_fetcher.py          # Benzinga scraper
├── utils/
│   ├── filters.py              # gap/RVOL filtering, market cap enrichment
│   ├── telegram_notifier.py    # Telegram delivery
│   └── time_utils.py           # US Eastern timezone helpers
├── .github/workflows/
│   └── daily_screener.yml      # GitHub Actions schedule (Mon–Fri 8:30am ET)
├── main.py
└── requirements.txt
```

## Filters

| Filter | Value |
|---|---|
| Min gap | > 5% vs prior close |
| Min RVOL | ≥ 3x 20-day average daily volume (previous session proxy during premarket) |
| Derivative tickers | Warrants, rights, units excluded |
| Top N | 20 names |

## Data Sources

| Data | Source |
|---|---|
| Premarket prices | Yahoo Finance v8 chart API |
| Historical volume baseline | Yahoo Finance daily chart API |
| Extra tickers / universe expansion | Alpaca screener endpoint |
| News catalysts | Benzinga (scraped), Alpaca News API (fallback) |
| Market cap | yfinance fast_info |

## Setup

### 1. Clone and install
```bash
git clone https://github.com/YOUR_USERNAME/premarket-screener.git
cd premarket-screener
pip install -r requirements.txt
```

### 2. Environment variables
Create a `.env` file (never commit this):
```
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. GitHub Actions (automated)
Add the four values above as repository secrets under **Settings → Secrets and variables → Actions**.

The workflow fires every weekday at 12:30 UTC (8:30am EDT / 7:30am EST) and posts results to Telegram automatically — no machine needs to be running.

### 4. Run manually
```bash
python main.py
```

## Output

Results are saved to `output/gappers_YYYY-MM-DD.json` and sent to Telegram in this format:

```
🔔 Premarket Gappers — Jun 17, 2026
US Eastern: 08:31 ET  |  19 names

[SMALL CAP]
• QURE  $47.94  +77.6%  prevRVOL: 7.8x
  FDA signals accelerated approval for Huntington gene therapy.

• CLPT  $18.42  +38.1%  prevRVOL: 8.9x
  ClearPoint Neuro gets EU green light for updated brain surgery software.
```

---

Built with Python | Yahoo Finance · Alpaca Markets · Benzinga · Telegram
