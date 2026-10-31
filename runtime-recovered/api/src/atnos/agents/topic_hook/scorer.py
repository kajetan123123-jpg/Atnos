from __future__ import annotations

import re
import unicodedata

from atnos.agents.topic_hook.models import RawTopic, ScoredTopic, TopicScore


TREND_TOKENS = {
    "ai",
    "chatgpt",
    "pieniadze",
    "money",
    "blad",
    "mistake",
    "sekret",
    "secret",
    "dlaczego",
    "why",
    "jak",
    "how",
    "2026",
}
SHARE_TOKENS = {
    "bogaci",
    "biedni",
    "wszyscy",
    "nikt",
    "przestan",
    "zniszczy",
    "uratowac",
    "lepiej",
    "prosciej",
}
CONCRETE_TOKENS = {
    "60 sekund",
    "3",
    "5",
    "7",
    "krok",
    "zasada",
    "metoda",
    "checklista",
    "example",
    "przyklad",
}


class TopicScorer:
    """Deterministic viral-potential scorer for short-form topics."""

    def score_topics(self, topics: list[RawTopic], *, niche: str, language: str) -> list[ScoredTopic]:
        scored = [self.score_topic(topic, niche=niche, language=language) for topic in topics]
        return sorted(scored, key=lambda item: (-item.viral_score, item.title.casefold()))

    def score_topic(self, topic: RawTopic, *, niche: str, language: str) -> ScoredTopic:
        title = topic.title
        normalized = _normalize(f"{title} {' '.join(topic.evidence)}")
        scores = TopicScore(
            searchability=self._searchability(normalized, topic.evidence, niche),
            shareability=self._shareability(normalized),
            simplicity=self._simplicity(title),
            competition_gap=self._competition_gap(normalized, title, niche),
        )
        return ScoredTopic(
            title=title,
            source=topic.source,
            evidence=topic.evidence[:5],
            scores=scores,
            viral_score=scores.total,
            rationale=self._rationale(title, scores, language),
        )

    def _searchability(self, normalized: str, evidence: list[str], niche: str) -> float:
        score = 0.08
        score += min(len(evidence), 4) * 0.025
        score += min(_token_hits(normalized, TREND_TOKENS), 3) * 0.025
        if any(token in normalized for token in _normalize(niche).split()[:4]):
            score += 0.035
        return _cap(score)

    def _shareability(self, normalized: str) -> float:
        score = 0.07
        score += min(_token_hits(normalized, SHARE_TOKENS), 4) * 0.035
        if "?" in normalized or normalized.startswith(("dlaczego", "why", "jak", "how")):
            score += 0.03
        return _cap(score)

    def _simplicity(self, title: str) -> float:
        words = len(title.split())
        if words <= 8:
            return 0.25
        if words <= 13:
            return 0.21
        if words <= 18:
            return 0.16
        return 0.1

    def _competition_gap(self, normalized: str, title: str, niche: str) -> float:
        score = 0.08
        score += min(_token_hits(normalized, CONCRETE_TOKENS), 3) * 0.035
        niche_words = [word for word in _normalize(niche).split() if len(word) > 3]
        if niche_words and sum(1 for word in niche_words if word in normalized) >= min(2, len(niche_words)):
            score += 0.035
        if len(title.split()) <= 14:
            score += 0.025
        return _cap(score)

    def _rationale(self, title: str, scores: TopicScore, language: str) -> str:
        if language.casefold().startswith("pl"):
            return (
                f"Temat jest konkretny i miesci sie w formacie shorta. "
                f"Najmocniejsze sygnaly: searchability {scores.searchability:.2f}, "
                f"shareability {scores.shareability:.2f}."
            )
        return (
            f"The topic is concrete and fits a short-form explanation. "
            f"Strongest signals: searchability {scores.searchability:.2f}, "
            f"shareability {scores.shareability:.2f}."
        )


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9? ]+", " ", value)
    return " ".join(value.split())


def _token_hits(value: str, tokens: set[str]) -> int:
    normalized_tokens = {_normalize(token) for token in tokens}
    return sum(1 for token in normalized_tokens if token and token in value)


def _cap(value: float) -> float:
    return round(max(0.0, min(0.25, value)), 3)
