# Changelog

## [0.1.0] - 2026-03-04

### Added
- Initial project scaffold (Phase 1)
- `main.py` -- async scan loop with configurable interval, signal processing pipeline
- `brokerages/alpaca.py` -- Alpaca REST adapter (paper + live), quotes, snapshots, orders
- `brokerages/base.py` -- abstract brokerage interface
- `feeds/sam_gov.py` -- SAM.gov federal contract awards poller with amount filtering
- `feeds/sec_edgar.py` -- EDGAR stub (Phase 2)
- `feeds/federal_register.py` -- Federal Register stub (Phase 3)
- `feeds/congress.py` -- Congress.gov stub (Phase 3)
- `core/signal_scanner.py` -- unified signal scanner across all feeds
- `core/company_resolver.py` -- fuzzy company name to ticker mapping with 50+ seeded contractors
- `core/position_manager.py` -- position tracking, risk rules (stop loss, take profit, trailing stop, time limit)
- `core/live_trader.py` -- order execution facade for paper and live modes
- `ai/analyzer.py` -- AI analysis prompt for gov signals, multi-provider support (Anthropic, DeepSeek, OpenAI)
- `ai/ensemble.py` -- multi-model consensus runner
- `ai/news_fetcher.py` -- news enrichment stub (Phase 2)
- `utils/config.py` -- config dataclass from .env
- `utils/notifier.py` -- Telegram notifications (signals, positions, errors)
- `utils/utils.py` -- retry decorator, rate limiter, formatting helpers
- `data/company_map.json` -- company name to ticker seed data
- Docker + docker-compose setup
- `deploy.sh` -- rsync deploy to Pi
- `.env.example` with all configuration options
- `CLAUDE.md` project instructions
