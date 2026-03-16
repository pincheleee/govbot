"""Unit tests for govbot core modules: position_manager, analyzer, company_resolver."""

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared mock config
# ---------------------------------------------------------------------------

@dataclass
class MockConfig:
    initial_capital: float = 10000.0
    max_position_pct: float = 0.10
    max_concurrent_positions: int = 5
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.08
    trailing_stop_pct: float = 0.02
    trailing_stop_activation_pct: float = 0.03
    max_hold_hours: int = 72
    ai_provider: str = "anthropic"
    anthropic_api_key: str = "test-key"
    deepseek_api_key: str = ""
    openai_api_key: str = ""


# ===========================================================================
# Position Manager Tests
# ===========================================================================

class TestPositionManager:
    """Tests for core/position_manager.py"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Create a PositionManager with a temp data dir."""
        from core.position_manager import PositionManager
        self.config = MockConfig()
        self.pm = PositionManager(str(tmp_path), self.config)

    def _open_long(self, entry=100.0, shares=10):
        return self.pm.open_position(
            ticker="TEST",
            side="LONG",
            entry_price=entry,
            shares=shares,
            edge=0.1,
            confidence=80,
            catalyst="SAM_CONTRACT",
            reasoning="test trade",
        )

    def _open_short(self, entry=100.0, shares=10):
        return self.pm.open_position(
            ticker="TEST",
            side="SHORT",
            entry_price=entry,
            shares=shares,
            edge=0.1,
            confidence=80,
            catalyst="SEC_8K",
            reasoning="test short trade",
        )

    # -- lifecycle --

    def test_open_position_creates_open_entry(self):
        pos = self._open_long()
        assert pos.status == "OPEN"
        assert pos.ticker == "TEST"
        assert self.pm.open_count == 1

    def test_close_position_moves_to_closed(self):
        pos = self._open_long()
        self.pm.close_position(pos, exit_price=105.0, exit_reason="TAKE_PROFIT")
        assert pos.status == "CLOSED"
        assert self.pm.open_count == 0
        assert len(self.pm.closed) == 1

    def test_close_updates_stats(self):
        pos = self._open_long()
        self.pm.close_position(pos, exit_price=105.0, exit_reason="TAKE_PROFIT")
        assert self.pm.stats["total_trades"] == 1
        assert self.pm.stats["wins"] == 1

    def test_close_losing_trade_updates_losses(self):
        pos = self._open_long()
        self.pm.close_position(pos, exit_price=90.0, exit_reason="STOP_LOSS")
        assert self.pm.stats["losses"] == 1

    # -- P&L calculation --

    def test_pnl_long_profit(self):
        pos = self._open_long(entry=100.0, shares=10)
        self.pm.close_position(pos, exit_price=110.0, exit_reason="TAKE_PROFIT")
        assert pos.pnl == pytest.approx(100.0)  # (110-100)*10

    def test_pnl_long_loss(self):
        pos = self._open_long(entry=100.0, shares=10)
        self.pm.close_position(pos, exit_price=90.0, exit_reason="STOP_LOSS")
        assert pos.pnl == pytest.approx(-100.0)

    def test_pnl_short_profit(self):
        pos = self._open_short(entry=100.0, shares=10)
        self.pm.close_position(pos, exit_price=90.0, exit_reason="TAKE_PROFIT")
        assert pos.pnl == pytest.approx(100.0)  # (100-90)*10

    def test_pnl_short_loss(self):
        pos = self._open_short(entry=100.0, shares=10)
        self.pm.close_position(pos, exit_price=110.0, exit_reason="STOP_LOSS")
        assert pos.pnl == pytest.approx(-100.0)

    # -- stop loss --

    def test_stop_loss_long(self):
        pos = self._open_long(entry=100.0)
        # 5% SL -> price at 94.99 should trigger
        result = self.pm.check_risk(pos, current_price=94.99)
        assert result == "STOP_LOSS"

    def test_stop_loss_long_not_triggered(self):
        pos = self._open_long(entry=100.0)
        result = self.pm.check_risk(pos, current_price=96.0)
        assert result is None

    def test_stop_loss_short(self):
        pos = self._open_short(entry=100.0)
        # SHORT: price rises 5%+ -> SL
        result = self.pm.check_risk(pos, current_price=105.01)
        assert result == "STOP_LOSS"

    def test_stop_loss_short_not_triggered(self):
        pos = self._open_short(entry=100.0)
        result = self.pm.check_risk(pos, current_price=104.0)
        assert result is None

    # -- take profit --

    def test_take_profit_long(self):
        pos = self._open_long(entry=100.0)
        # 8% TP -> price at 108+
        result = self.pm.check_risk(pos, current_price=108.0)
        assert result == "TAKE_PROFIT"

    def test_take_profit_short(self):
        pos = self._open_short(entry=100.0)
        # SHORT: price drops 8%+ -> TP
        result = self.pm.check_risk(pos, current_price=92.0)
        assert result == "TAKE_PROFIT"

    # -- trailing stop --

    def test_trailing_stop_long(self):
        """Price rises past activation (3%), then drops trailing_stop_pct (2%) from high."""
        pos = self._open_long(entry=100.0)
        # Price rises to 104 (above 3% activation at 103)
        self.pm.check_risk(pos, current_price=104.0)
        assert pos.high_water_mark == 104.0
        # Price drops 2% from HWM: 104 * 0.98 = 101.92
        result = self.pm.check_risk(pos, current_price=101.91)
        assert result == "TRAILING_STOP"

    def test_trailing_stop_long_not_activated(self):
        """Price rises but not past activation threshold."""
        pos = self._open_long(entry=100.0)
        self.pm.check_risk(pos, current_price=102.0)  # below 3% activation
        result = self.pm.check_risk(pos, current_price=100.5)
        assert result is None

    def test_trailing_stop_short(self):
        """SHORT: price drops past activation, then rises trailing_stop_pct from low."""
        pos = self._open_short(entry=100.0)
        # Price drops to 96 (below 97 activation = 100*(1-0.03))
        self.pm.check_risk(pos, current_price=96.0)
        assert pos.high_water_mark == 96.0
        # Price rises 2% from low-water-mark: 96 * 1.02 = 97.92
        result = self.pm.check_risk(pos, current_price=97.93)
        assert result == "TRAILING_STOP"

    def test_trailing_stop_short_not_activated(self):
        """SHORT: price drops but not past activation threshold."""
        pos = self._open_short(entry=100.0)
        self.pm.check_risk(pos, current_price=98.0)  # above 97 activation
        result = self.pm.check_risk(pos, current_price=99.0)
        assert result is None

    # -- can_open_position --

    def test_can_open_within_limits(self):
        assert self.pm.can_open_position(500.0) is True

    def test_cannot_exceed_max_positions(self):
        for _ in range(5):
            self._open_long()
        assert self.pm.can_open_position(500.0) is False

    def test_cannot_exceed_position_size(self):
        # max_position_pct=0.10, capital=10000 -> max 1000
        assert self.pm.can_open_position(1001.0) is False


# ===========================================================================
# AI Analyzer _parse_response Tests
# ===========================================================================

class TestAnalyzerParseResponse:
    """Tests for ai/analyzer.py _parse_response method."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from ai.analyzer import GovSignalAnalyzer
        self.analyzer = GovSignalAnalyzer(MockConfig())

    def test_valid_json(self):
        response = json.dumps({
            "action": "BUY",
            "confidence": 85,
            "edge": 0.12,
            "target_price": 155.0,
            "stop_loss_price": 140.0,
            "expected_hold_days": 3,
            "reasoning": "Contract not priced in yet.",
            "key_factors": ["large contract", "low volume"],
        })
        result = self.analyzer._parse_response(response, "LMT", "SAM_CONTRACT")
        assert result.action == "BUY"
        assert result.confidence == 85
        assert result.edge == pytest.approx(0.12)
        assert result.ticker == "LMT"
        assert result.catalyst == "SAM_CONTRACT"

    def test_json_in_code_block(self):
        response = '```json\n{"action":"SHORT","confidence":60,"edge":0.05,"target_price":null,"stop_loss_price":null,"expected_hold_days":2,"reasoning":"test","key_factors":[]}\n```'
        result = self.analyzer._parse_response(response, "BA", "SEC_8K")
        assert result.action == "SHORT"
        assert result.confidence == 60

    def test_malformed_json_returns_skip(self):
        result = self.analyzer._parse_response("not json at all {{{", "BA", "SEC_8K")
        assert result.action == "SKIP"
        assert result.confidence == 0
        assert "Failed to parse" in result.reasoning

    def test_missing_fields_handled_gracefully(self):
        response = json.dumps({"action": "BUY"})
        result = self.analyzer._parse_response(response, "LMT", "SAM_CONTRACT")
        assert result.action == "BUY"
        assert result.confidence == 0
        assert result.edge == 0.0
        assert result.target_price is None
        assert result.key_factors == []


# ===========================================================================
# Company Resolver Tests
# ===========================================================================

class TestCompanyResolver:
    """Tests for core/company_resolver.py"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from core.company_resolver import CompanyResolver
        self.resolver = CompanyResolver(str(tmp_path), alpaca_headers={})

    # -- fuzzy matching --

    def test_exact_match(self):
        assert self.resolver.resolve("LOCKHEED MARTIN") == "LMT"

    def test_exact_match_case_insensitive(self):
        assert self.resolver.resolve("lockheed martin") == "LMT"

    def test_fuzzy_match_above_threshold(self):
        # "LOCKHEED MARTIN CORP" should fuzzy-match to "LOCKHEED MARTIN CORPORATION"
        result = self.resolver.resolve("LOCKHEED MARTIN CORP")
        assert result == "LMT"

    def test_fuzzy_match_below_threshold(self):
        result = self.resolver.resolve("TOTALLY UNKNOWN COMPANY XYZ")
        assert result is None

    def test_private_company_returns_none(self):
        # PERATON is mapped to None (private)
        assert self.resolver.resolve("PERATON") is None

    # -- market cap filter --

    def test_market_cap_filter_unknown_returns_false(self):
        """When market cap is unavailable, fail closed (return False)."""
        self.resolver._financials_cache["FAKE"] = None
        result = asyncio.run(self.resolver.passes_market_cap_filter("FAKE", min_cap=500_000_000))
        assert result is False

    def test_market_cap_filter_zero_cap_returns_false(self):
        """When market cap is 0 (e.g., Alpaca fallback), fail closed."""
        from core.company_resolver import CompanyInfo
        self.resolver._financials_cache["FAKE"] = CompanyInfo(
            ticker="FAKE", name="Fake Co", sector="", market_cap=0, revenue=0, match_confidence=100,
        )
        result = asyncio.run(self.resolver.passes_market_cap_filter("FAKE", min_cap=500_000_000))
        assert result is False

    def test_market_cap_filter_passes(self):
        from core.company_resolver import CompanyInfo
        self.resolver._financials_cache["LMT"] = CompanyInfo(
            ticker="LMT", name="Lockheed Martin", sector="Industrials",
            market_cap=120_000_000_000, revenue=65_000_000_000, match_confidence=100,
        )
        result = asyncio.run(self.resolver.passes_market_cap_filter("LMT", min_cap=500_000_000))
        assert result is True

    def test_market_cap_filter_below_min(self):
        from core.company_resolver import CompanyInfo
        self.resolver._financials_cache["SMALL"] = CompanyInfo(
            ticker="SMALL", name="Small Co", sector="",
            market_cap=100_000_000, revenue=0, match_confidence=100,
        )
        result = asyncio.run(self.resolver.passes_market_cap_filter("SMALL", min_cap=500_000_000))
        assert result is False

    def test_market_cap_filter_no_limits_passes(self):
        """When both min and max are 0, always pass."""
        result = asyncio.run(self.resolver.passes_market_cap_filter("ANYTHING", min_cap=0, max_cap=0))
        assert result is True
