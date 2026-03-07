"""AI analysis for government data signals."""

import json
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are analyzing a potential equity trade triggered by a government data signal.

SIGNAL SOURCE: {catalyst_type}
{catalyst_details}

COMPANY:
Ticker: {ticker}
Current price: ${current_price}
Today's change: {daily_change}%
Volume: {volume}

ANALYSIS TASK:
1. Is this government signal MATERIAL to this company's valuation?
   - For contract awards: what % of likely annual revenue does this represent?
   - Consider the company's existing government business vs this new award

2. Has the market already priced this in?
   - Check if the stock already moved today (daily change)
   - Consider if volume is elevated vs normal

3. What is the expected magnitude and timeline?
   - Contract awards: typically 2-5% move over 1-3 days for large-caps
   - Smaller companies see bigger moves

4. If you have edge, estimate target price and confidence.

IMPORTANT: Be conservative. Only recommend BUY or SHORT if there is a clear, exploitable lag.
Most signals should be SKIP -- the market is usually efficient.

RESPOND (JSON only):
{{
    "action": "BUY" | "SHORT" | "SKIP",
    "confidence": 0-100,
    "edge": 0.0-1.0,
    "target_price": float or null,
    "stop_loss_price": float or null,
    "expected_hold_days": int,
    "reasoning": "2-3 sentences: WHY has the market not priced this in yet?",
    "key_factors": ["factor1", "factor2", "factor3"]
}}"""


@dataclass
class AnalysisResult:
    action: str  # "BUY" | "SHORT" | "SKIP"
    confidence: int
    edge: float
    target_price: Optional[float]
    stop_loss_price: Optional[float]
    expected_hold_days: int
    reasoning: str
    key_factors: list
    ticker: str
    catalyst: str


class GovSignalAnalyzer:
    def __init__(self, config):
        self.config = config

    async def analyze(
        self,
        ticker: str,
        catalyst_type: str,
        catalyst_details: str,
        current_price: float,
        daily_change: float,
        volume: int,
    ) -> AnalysisResult:
        """Run AI analysis on a single signal."""
        prompt = ANALYSIS_PROMPT.format(
            catalyst_type=catalyst_type,
            catalyst_details=catalyst_details,
            ticker=ticker,
            current_price=f"{current_price:.2f}",
            daily_change=f"{daily_change:.2f}",
            volume=f"{volume:,}",
        )

        response_text = await self._call_ai(prompt)
        return self._parse_response(response_text, ticker, catalyst_type)

    async def _call_ai(self, prompt: str) -> str:
        """Call the configured AI provider."""
        provider = self.config.ai_provider

        if provider == "anthropic":
            return await self._call_anthropic(prompt)
        elif provider == "deepseek":
            return await self._call_openai_compatible(
                prompt,
                api_key=self.config.deepseek_api_key,
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
            )
        elif provider == "openai":
            return await self._call_openai_compatible(
                prompt,
                api_key=self.config.openai_api_key,
                base_url="https://api.openai.com/v1",
                model="gpt-4o",
            )
        else:
            raise ValueError(f"Unknown AI provider: {provider}")

    async def _call_anthropic(self, prompt: str) -> str:
        headers = {
            "x-api-key": self.config.anthropic_api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Anthropic API error {resp.status}: {text}")
                data = await resp.json()
                return data["content"][0]["text"]

    async def _call_openai_compatible(
        self, prompt: str, api_key: str, base_url: str, model: str
    ) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=body,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"AI API error {resp.status}: {text}")
                data = await resp.json()
                return data["choices"][0]["message"]["content"]

    def _parse_response(self, text: str, ticker: str, catalyst: str) -> AnalysisResult:
        """Parse the AI JSON response into an AnalysisResult."""
        # Extract JSON from response (may be wrapped in markdown code blocks)
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse AI response as JSON: {text[:200]}")
            return AnalysisResult(
                action="SKIP",
                confidence=0,
                edge=0.0,
                target_price=None,
                stop_loss_price=None,
                expected_hold_days=0,
                reasoning="Failed to parse AI response",
                key_factors=[],
                ticker=ticker,
                catalyst=catalyst,
            )

        return AnalysisResult(
            action=data.get("action", "SKIP"),
            confidence=int(data.get("confidence", 0)),
            edge=float(data.get("edge", 0.0)),
            target_price=data.get("target_price"),
            stop_loss_price=data.get("stop_loss_price"),
            expected_hold_days=int(data.get("expected_hold_days", 0)),
            reasoning=data.get("reasoning", ""),
            key_factors=data.get("key_factors", []),
            ticker=ticker,
            catalyst=catalyst,
        )
