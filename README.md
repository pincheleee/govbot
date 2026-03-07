# GovBot

AI-assisted equity trading bot that exploits government data publication lag. Monitors federal contract awards and regulatory filings, identifies affected public companies, and executes paper trades via Alpaca before the market fully prices in the information.

> **Paper mode by default.** Proof of concept -- validating signal quality before committing real capital.

## How It Works

```
Every N minutes:
  1. Poll government data feeds (SAM.gov contract awards, SEC EDGAR, Federal Register, Congress.gov)
  2. Filter for significant signals (large contract values, material filings)
  3. Resolve company names to stock tickers (fuzzy matching + seed map)
  4. Run AI ensemble analysis (Anthropic, DeepSeek, OpenAI) for trade conviction
  5. Execute paper trades via Alpaca with position sizing and risk management
  6. Send Telegram alerts for new signals, trades, and position updates
```

## Project Structure

```
govbot/
  main.py                  # Async scan loop + signal pipeline
  feeds/
    sam_gov.py             # SAM.gov federal contract awards (Phase 1 -- active)
    sec_edgar.py           # SEC EDGAR filings (Phase 2 -- stub)
    federal_register.py    # Federal Register rules (Phase 3 -- stub)
    congress.py            # Congress.gov legislation (Phase 3 -- stub)
  brokerages/
    alpaca.py              # Alpaca REST adapter (paper + live)
    base.py                # Abstract brokerage interface
  core/
    signal_scanner.py      # Unified scanner across all feeds
    company_resolver.py    # Fuzzy company name -> ticker mapping
    position_manager.py    # Position tracking, stop loss, take profit, trailing stop
    live_trader.py         # Order execution facade
  ai/
    analyzer.py            # LLM analysis prompt for gov signals
    ensemble.py            # Multi-model consensus runner
    news_fetcher.py        # News enrichment (Phase 2 -- stub)
  utils/
    config.py              # Config from .env
    notifier.py            # Telegram notifications
    utils.py               # Retry decorator, rate limiter, helpers
  data/
    company_map.json       # Company name -> ticker seed data
    portfolio.json         # Live position state
    trades.csv             # Trade history
    signals.csv            # Signal log
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Key variables:
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` -- Alpaca brokerage credentials
- `PAPER_MODE=true` -- paper trading (default)
- `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` -- alert notifications
- AI provider keys (Anthropic, OpenAI, DeepSeek) for ensemble analysis

### 3. Run

```bash
# Local
python main.py

# Docker
docker compose up -d

# Deploy to Raspberry Pi
bash deploy.sh
```

## Current Status

- **Phase 1** (SAM.gov): Built and functional
- **Phase 2** (SEC EDGAR + news enrichment): Stubbed
- **Phase 3** (Federal Register + Congress.gov): Stubbed
