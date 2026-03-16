# GovBot

AI-assisted equity trading bot that exploits government data publication lag. Monitors federal contracts, SEC filings, regulatory actions, congressional legislation, and Congress member stock trades -- then identifies affected companies, runs AI ensemble analysis, and executes paper trades via Alpaca.

> **Paper mode by default.** Proof of concept -- validating signal quality before committing real capital.

## How It Works

```
Every 15 minutes:
  1. Poll 6 data feeds (SAM.gov, SEC EDGAR, Federal Register, Congress.gov, QuiverQuant, RSS news)
  2. Filter for significant signals (large contracts, material filings, committee-relevant trades)
  3. Resolve to tickers:
     - Company signals -> fuzzy name matching (50+ seeded contractors)
     - Macro signals (FedReg, Congress bills) -> sector ETF mapper
     - Congress trades / EDGAR w/ tickers -> pass through directly
  4. Filter by market cap ($500M-unlimited default)
  5. Fetch recent news headlines for priced-in detection
  6. Run AI ensemble analysis (Anthropic, DeepSeek, OpenAI) for trade conviction
  7. Execute paper trades via Alpaca with position sizing and risk management
  8. Send Telegram alerts for signals, trades, and position updates
```

## Data Feeds

| Feed | Source | API Key Required | Signal Types |
|------|--------|-----------------|--------------|
| SAM.gov | Federal contract awards | Yes (free, api.data.gov) | Contract awards > $50M |
| SEC EDGAR | Material filings | No (User-Agent w/ email) | 8-K, Form 4, SC 13D, 10-K/A, 10-Q/A |
| Federal Register | Rules & executive orders | No | Economically significant regulations |
| Congress.gov | Legislation | Yes (free, api.congress.gov) | Bills with significant actions (passed, signed) |
| QuiverQuant | Congress member stock trades | Yes (free tier available) | Individual buys, cluster buys, committee-relevant trades |
| News (RSS) | Yahoo Finance, Reuters, etc. | No | Enrichment context for AI analysis |

## Project Structure

```
govbot/
  main.py                       # Async scan loop + signal pipeline
  feeds/
    sam_gov.py                  # SAM.gov federal contract awards
    sec_edgar.py                # SEC EDGAR material filings (8-K, Form 4, SC 13D)
    federal_register.py         # Federal Register rules & presidential documents
    congress.py                 # Congress.gov legislation tracking
    congress_trading.py         # QuiverQuant Congress member stock trades
  brokerages/
    alpaca.py                   # Alpaca REST adapter (paper + live)
    base.py                     # Abstract brokerage interface
  core/
    signal_scanner.py           # Unified scanner across all feeds
    company_resolver.py         # Fuzzy company name -> ticker mapping + market cap filter
    sector_etf_mapper.py        # Macro signal -> sector ETF routing (ITA, XLV, XLK, etc.)
    position_manager.py         # Position tracking, stop loss, take profit, trailing stop
    live_trader.py              # Order execution facade
  ai/
    analyzer.py                 # LLM analysis prompt tuned for gov signals
    ensemble.py                 # Multi-model consensus runner
    news_fetcher.py             # RSS news fetcher (5 feeds + ticker-specific search)
  utils/
    config.py                   # Config from .env
    notifier.py                 # Telegram notifications + /status /positions /pause /resume /help
    utils.py                    # Retry decorator, rate limiter, helpers
  scripts/
    test_feeds.py               # Test each feed in isolation
  data/
    company_map.json            # Company name -> ticker seed data (50+ contractors)
    portfolio.json              # Live position state
    trades.csv                  # Trade history
    signals.csv                 # Signal log
```

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Required API keys:
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` -- paper trading account
- `SAM_GOV_API_KEY` -- free from api.data.gov
- `CONGRESS_API_KEY` -- free from api.congress.gov
- `SEC_USER_AGENT` -- your real email (SEC requirement)
- `QUIVERQUANT_API_TOKEN` -- free tier from api.quiverquant.com
- At least one AI provider key (Anthropic, OpenAI, or DeepSeek)

Optional:
- `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` -- for alerts and /status commands

### 3. Run

```bash
# Local
python main.py

# Docker
docker compose up -d

# Deploy to Raspberry Pi
bash deploy.sh
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Uptime, mode, trade stats, P&L |
| `/positions` | Open positions with unrealized P&L |
| `/pause` | Pause scanning |
| `/resume` | Resume scanning |
| `/help` | List commands |

## Deployment

Deploys to Raspberry Pi via rsync:

```bash
bash deploy.sh  # rsync + pip install + restart systemd service
```

Target: `polypi@polypi.local:/home/polypi/govbot`
Service: `govbot.service` (systemd)

## Current Status

- v0.5.0 (2026-03-16)
- All 6 data feeds implemented and functional
- Sector ETF mapping for macro signals
- News-enriched AI analysis with priced-in detection
- Congress member trade tracking with committee relevance scoring
- Deployed to Pi (service disabled pending API key setup)

### Recent Changes (v0.5.0)

- Startup config validation -- missing/invalid env vars caught before the scan loop starts
- Market cap filter fails closed -- if the market cap lookup errors out, the signal is rejected rather than passed through
- Trailing stop fix for short positions -- trailing stop logic now correctly handles short-side P&L
- Unit tests being added for core modules
