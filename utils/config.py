"""Configuration dataclass loaded from .env"""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _bool(val: str) -> bool:
    return val.lower() in ("true", "1", "yes")


def _list(val: str) -> List[str]:
    return [v.strip() for v in val.split(",") if v.strip()]


@dataclass
class Config:
    # Brokerage
    brokerage: str = os.getenv("BROKERAGE", "alpaca")
    alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "")
    alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    alpaca_paper: bool = _bool(os.getenv("ALPACA_PAPER", "true"))

    # Data feed API keys
    sam_gov_api_key: str = os.getenv("SAM_GOV_API_KEY", "")
    congress_api_key: str = os.getenv("CONGRESS_API_KEY", "")
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "GovBot research@example.com")
    quiverquant_api_token: str = os.getenv("QUIVERQUANT_API_TOKEN", "")

    # Congress trading parameters
    congress_trade_min_amount: float = float(os.getenv("CONGRESS_TRADE_MIN_AMOUNT", "15000"))

    # AI providers
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    ai_provider: str = os.getenv("AI_PROVIDER", "anthropic")
    ai_ensemble_providers: List[str] = field(
        default_factory=lambda: _list(os.getenv("AI_ENSEMBLE_PROVIDERS", "anthropic,deepseek"))
    )

    # Trading parameters
    paper_mode: bool = _bool(os.getenv("PAPER_MODE", "true"))
    initial_capital: float = float(os.getenv("INITIAL_CAPITAL", "10000"))
    max_position_pct: float = float(os.getenv("MAX_POSITION_PCT", "0.10"))
    max_concurrent_positions: int = int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))
    min_confidence: int = int(os.getenv("MIN_CONFIDENCE", "75"))
    min_edge: float = float(os.getenv("MIN_EDGE", "0.05"))
    stop_loss_pct: float = float(os.getenv("STOP_LOSS_PCT", "0.05"))
    take_profit_pct: float = float(os.getenv("TAKE_PROFIT_PCT", "0.08"))
    trailing_stop_pct: float = float(os.getenv("TRAILING_STOP_PCT", "0.02"))
    trailing_stop_activation_pct: float = float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "0.03"))
    max_hold_hours: int = int(os.getenv("MAX_HOLD_HOURS", "72"))

    # Scan parameters
    scan_interval_minutes: int = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))
    min_contract_value: int = int(os.getenv("MIN_CONTRACT_VALUE", "50000000"))
    min_market_cap: int = int(os.getenv("MIN_MARKET_CAP", "500000000"))
    max_market_cap: int = int(os.getenv("MAX_MARKET_CAP", "0"))

    # Notifications
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "bot.log")

    # Paths
    data_dir: str = os.getenv("DATA_DIR", "data")

    def __post_init__(self):
        errors = []
        if not self.sec_user_agent or "example.com" in self.sec_user_agent:
            errors.append(
                "SEC_USER_AGENT must be set to a real contact "
                "(e.g. 'BotName you@yourdomain.com'), not example.com"
            )
        if not self.anthropic_api_key and not self.deepseek_api_key:
            errors.append(
                "At least one AI provider key required: "
                "set ANTHROPIC_API_KEY or DEEPSEEK_API_KEY"
            )
        if not self.alpaca_api_key:
            errors.append("ALPACA_API_KEY must be set")
        if not self.alpaca_secret_key:
            errors.append("ALPACA_SECRET_KEY must be set")
        if errors:
            raise ValueError("Config validation failed:\n  - " + "\n  - ".join(errors))
