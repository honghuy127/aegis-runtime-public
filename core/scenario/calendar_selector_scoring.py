"""
Evidence-driven selector scoreboard for calendar interactions.

Tracks which selectors work across a run, allowing future attempts
to try proven selectors first.

Design:
- Per-run in-memory scoreboard (dict-based, deterministic)
- Optional artifact persistence (storage/runs/<run_id>/artifacts/cal_selector_scores.json)
- Selector families: opener, header, nav_button, day_cell, close_button
- Scoring: +1 for success, -0.5 for failure (floor at 0)
- Ranking: Higher scores tried first
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class SelectorScoreboard:
    """
    In-memory selector scoring for current run.

    Tracks success/failure of selectors per family, allowing
    evidence-driven ranking of future attempts.
    """

    site_key: str
    locale: str = "en"

    # {family: {selector: score}}
    _scores: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def record_success(self, selector_family: str, selector: str) -> None:
        """
        Record successful selector use.

        Increments score by 1.0.

        Args:
            selector_family: One of: opener, header, nav_button, day_cell, close_button
            selector: CSS/XPath selector that worked
        """
        if selector_family not in self._scores:
            self._scores[selector_family] = {}

        current = self._scores[selector_family].get(selector, 0.0)
        self._scores[selector_family][selector] = current + 1.0

    def record_failure(self, selector_family: str, selector: str) -> None:
        """
        Record failed selector attempt.

        Decrements score by 0.5, with floor at 0.

        Args:
            selector_family: Family name
            selector: Selector that failed
        """
        if selector_family not in self._scores:
            self._scores[selector_family] = {}

        current = self._scores[selector_family].get(selector, 0.0)
        self._scores[selector_family][selector] = max(0.0, current - 0.5)

    def rank_selectors(self, selector_family: str, fallback_list: Optional[List[str]] = None) -> List[str]:
        """
        Get selectors for a family ranked by score (highest first).

        Returns tracked selectors + fallback_list (de-duplicated, scores first).

        Args:
            selector_family: Family name
            fallback_list: Default selectors if none tracked yet

        Returns:
            List of selectors in descending score order
        """
        fallback_list = fallback_list or []

        # Get tracked selectors for this family
        tracked = self._scores.get(selector_family, {})

        if not tracked:
            # No history; return fallback as-is
            return fallback_list

        # Sort tracked by score (descending)
        sorted_tracked = sorted(tracked.items(), key=lambda x: x[1], reverse=True)
        tracked_selectors = [sel for sel, _ in sorted_tracked]

        # Append fallback selectors not yet tracked
        result = tracked_selectors.copy()
        for sel in fallback_list:
            if sel not in result:
                result.append(sel)

        return result

    def to_dict(self) -> Dict:
        """
        Serialize scoreboard to JSON-compatible dict.

        Returns:
            Dictionary with site_key, locale, scores_by_family
        """
        return {
            "site_key": self.site_key,
            "locale": self.locale,
            "scores_by_family": self._scores,
        }

    @staticmethod
    def from_dict(data: Dict) -> "SelectorScoreboard":
        """
        Deserialize scoreboard from dict.

        Args:
            data: Dictionary from to_dict()

        Returns:
            Reconstructed SelectorScoreboard
        """
        scoreboard = SelectorScoreboard(
            site_key=data.get("site_key", "unknown"),
            locale=data.get("locale", "en"),
        )
        scoreboard._scores = data.get("scores_by_family", {})
        return scoreboard

    def get_score(self, selector_family: str, selector: str) -> float:
        """
        Get current score for a selector.

        Args:
            selector_family: Family name
            selector: Selector

        Returns:
            Score (0.0 if never seen)
        """
        return self._scores.get(selector_family, {}).get(selector, 0.0)
