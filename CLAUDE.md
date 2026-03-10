# GovBot

AI-assisted equity trading bot that exploits government data publication lag.

## Quick Start
- Run locally: `python main.py`
- Run via Docker: `docker compose up -d`
- Deploy to Pi: `bash deploy.sh`

## Project Structure
- `feeds/` -- 5 data feed pollers + 1 news fetcher:
  - `sam_gov.py` -- SAM.gov federal contract awards (API key from api.data.gov)
  - `sec_edgar.py` -- SEC EDGAR material filings (8-K, Form 4, SC 13D). Needs User-Agent w/ real email.
  - `federal_register.py` -- Federal Register rules & presidential documents (no key needed)
  - `congress.py` -- Congress.gov legislation (API key from api.congress.gov)
  - `congress_trading.py` -- QuiverQuant Congress member stock trades (API token from api.quiverquant.com)
- `brokerages/` -- Alpaca adapter (paper + live)
- `core/` -- signal scanning, company resolver (fuzzy match + market cap filter), sector ETF mapper (macro signals), position manager, live trader
- `ai/` -- LLM analysis with multi-model ensemble, news fetcher (RSS, 5 feeds + ticker search)
- `utils/` -- config, Telegram notifier (with /status /positions /pause /resume /help), helpers
- `scripts/` -- `test_feeds.py` for isolated feed testing
- `data/` -- persisted state (portfolio.json, trades.csv, signals.csv, company_map.json)

## Signal Routing
- Company signals (SAM.gov, EDGAR) -> fuzzy company resolver -> ticker
- Macro signals (FedReg, Congress bills) -> sector ETF mapper -> ETF ticker (ITA, XLV, XLK, etc.)
- Pre-resolved signals (Congress trades, EDGAR w/ tickers) -> skip resolver, pass through directly

## Deployment
- Target: `polypi@polypi.local:/home/polypi/govbot`
- Venv: `/home/polypi/govbot/.venv`
- Systemd service: `govbot.service` (currently disabled pending API keys)
- Env file on Pi: `/home/polypi/govbot/.env`
- Logs: `/home/polypi/govbot/govbot.log`

## Rules
- PAPER_MODE=true by default. Never switch to live without explicit user instruction.
- All gov API calls must respect rate limits (SEC EDGAR: 10 req/sec, SAM.gov: varies).
- Company name to ticker mapping lives in data/company_map.json. Update it, don't hardcode.
- Always update CHANGELOG.md and DEVLOG.md at the end of every conversation.
