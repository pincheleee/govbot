# GovBot

AI-assisted equity trading bot that exploits government data publication lag.

## Quick Start
- Run locally: `python main.py`
- Run via Docker: `docker compose up -d`
- Deploy to Pi: `bash deploy.sh`

## Project Structure
- `feeds/` -- government data feed pollers (SAM.gov, EDGAR, Fed Register, Congress)
- `brokerages/` -- brokerage adapters (Alpaca first)
- `core/` -- signal scanning, position management, order execution
- `ai/` -- LLM analysis with multi-model ensemble consensus
- `utils/` -- config, Telegram notifications, shared helpers
- `data/` -- persisted state (portfolio.json, trades.csv, signals.csv)

## Rules
- PAPER_MODE=true by default. Never switch to live without explicit user instruction.
- All gov API calls must respect rate limits (SEC EDGAR: 10 req/sec, SAM.gov: varies).
- Company name to ticker mapping lives in data/company_map.json. Update it, don't hardcode.
- Always update CHANGELOG.md and DEVLOG.md at the end of every conversation.
