"""Multi-model ensemble consensus for trade decisions."""

import asyncio
import logging
from dataclasses import dataclass
from typing import List

from ai.analyzer import GovSignalAnalyzer, AnalysisResult

logger = logging.getLogger(__name__)


@dataclass
class ConsensusResult:
    action: str  # Consensus action
    confidence: int  # Average confidence
    edge: float  # Average edge
    reasoning: str  # Combined reasoning
    agreement: bool  # Whether models agreed
    individual_results: List[AnalysisResult]


class EnsembleRunner:
    def __init__(self, config):
        self.config = config
        self.providers = config.ai_ensemble_providers

    async def run(
        self,
        ticker: str,
        catalyst_type: str,
        catalyst_details: str,
        current_price: float,
        daily_change: float,
        volume: int,
    ) -> ConsensusResult:
        """Run analysis across multiple AI providers and apply consensus."""

        # Create an analyzer per provider and run in parallel
        tasks = []
        for provider in self.providers:
            # Create a config copy with the specific provider
            analyzer = GovSignalAnalyzer(self.config)
            analyzer.config = type(self.config)(
                **{
                    **vars(self.config),
                    "ai_provider": provider,
                }
            )
            tasks.append(
                analyzer.analyze(
                    ticker=ticker,
                    catalyst_type=catalyst_type,
                    catalyst_details=catalyst_details,
                    current_price=current_price,
                    daily_change=daily_change,
                    volume=volume,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out errors
        valid_results: List[AnalysisResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Provider {self.providers[i]} failed: {result}")
            else:
                valid_results.append(result)

        if not valid_results:
            return ConsensusResult(
                action="SKIP",
                confidence=0,
                edge=0.0,
                reasoning="All AI providers failed",
                agreement=False,
                individual_results=[],
            )

        return self._apply_consensus(valid_results)

    def _apply_consensus(self, results: List[AnalysisResult]) -> ConsensusResult:
        """Apply consensus policy: all non-SKIP results must agree on action."""
        actions = [r.action for r in results]
        non_skip = [r for r in results if r.action != "SKIP"]

        if not non_skip:
            # All models say SKIP
            return ConsensusResult(
                action="SKIP",
                confidence=0,
                edge=0.0,
                reasoning="All models recommend SKIP: " + "; ".join(r.reasoning for r in results),
                agreement=True,
                individual_results=results,
            )

        # Check if all non-SKIP agree on direction
        non_skip_actions = set(r.action for r in non_skip)
        if len(non_skip_actions) > 1:
            # Disagreement on direction
            return ConsensusResult(
                action="SKIP",
                confidence=0,
                edge=0.0,
                reasoning=f"Models disagree: {actions}. Skipping.",
                agreement=False,
                individual_results=results,
            )

        consensus_action = non_skip[0].action
        avg_confidence = sum(r.confidence for r in non_skip) // len(non_skip)
        avg_edge = sum(r.edge for r in non_skip) / len(non_skip)
        combined_reasoning = " | ".join(r.reasoning for r in non_skip)

        return ConsensusResult(
            action=consensus_action,
            confidence=avg_confidence,
            edge=avg_edge,
            reasoning=combined_reasoning,
            agreement=True,
            individual_results=results,
        )
