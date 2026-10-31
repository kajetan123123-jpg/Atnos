from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from content.script_generator import ShortScript


POLISH_MARKERS = ("ze", "jest", "nie", "sie", "to", "jak", "albo", "ktory", "dla", "przez", "oraz")
GENERIC_MARKERS = (
    "wazne jest",
    "w dzisiejszych czasach",
    "warto pamietac",
    "rozne aspekty",
    "temat jest szeroki",
    "nalezy zwrocic uwage",
    "istnieje wiele czynnikow",
    "ogolny plan",
    "konkretny problem",
    "mierzalny efekt",
    "luznych kierunkow",
    "kolejna decyzja",
    "potraktuj ai short",
    "jak maly workflow",
    "sprawdz jeden realny przyklad",
    "rozbuduj caly proces",
    "to idealne do generowania tekstu",
)
CLICKBAIT_CTA_MARKERS = (
    "daj lajka",
    "subskryb",
    "zostaw komentarz",
    "kliknij",
    "sprobuj juz dzis",
    "sprobuj teraz",
    "sprawdz jak to dziala",
    "wideo w 2 sekundy",
)
UNSUPPORTED_CERTAINTY_MARKERS = (
    "na pewno",
    "zawsze",
    "nigdy",
    "gwarantuje",
    "100%",
    "brak oplat",
    "brak opoznien",
    "bez oplat",
    "bez opoznien",
    "bez ryzyka",
    "bez kompromisow",
    "szybciej niz w chmurze",
    "nie musisz sie martwic",
    "nie musisz sie obawiac",
    "wycieki danych",
    "wieczna wartosc",
    "to przyszlosc",
)
UNSUPPORTED_METRIC_PATTERN = re.compile(r"\b\d{1,3}\s*%")
SOURCE_LEAK_PATTERNS = (
    (r"#\w+", "Skrypt zawiera surowe hashtagi z researchu zamiast gotowej narracji."),
    (r"\b\d[\d\s,.]{3,}\s*(views|wyświetleń|wyswietlen)\b", "Skrypt zawiera surowe metryki z publicznego źródła."),
    (r"\b(fashion dream|catshorts|cutecat|kitten#kittens)\b", "Skrypt przecieka elementy tytułu/hashtagów źródłowego shorta."),
)
LANGUAGE_ERROR_PATTERNS = (
    (r"\btwoja\s+kanal\b", "Błąd językowy: powinno być „twój kanał”, nie „twoja kanał”."),
    (r"\btwoja\s+short\b", "Błąd językowy: niepoprawny rodzaj gramatyczny przy „short”."),
    (r"\btwoja\s+material\b", "Błąd językowy: niepoprawny rodzaj gramatyczny przy „materiał”."),
    (r"\bnie\s+odpowiedzisz\s+na\s+komentarze\b", "Nienaturalne sformułowanie: chodzi o brak odpowiadania, nie przyszły tryb."),
)
SEMANTIC_ERROR_PATTERNS = (
    (r"\busuniecie\s+znieczulenia\b", "Nonsensowny przykład: „usunięcie znieczulenia” nie pasuje do edycji shortów."),
    (r"\bdodanie\s+efektu\s+do\s+klatki\b", "Nieprecyzyjny przykład narzędzia: „efekt do klatki” brzmi jak przypadkowy filler."),
    (r"\bsubmagic\b.{0,160}\bopis\s+do\s+zdjecia\b", "Nieprecyzyjny opis Submagic: to głównie napisy/social polish, nie opis zdjęcia."),
    (r"\bklap\b.{0,180}\busuniecie\b", "Nieprecyzyjny opis Klap: narzędzie wybiera klipy, nie jest głównym edytorem efektów."),
)
UNREALISTIC_PROMISE_PATTERNS = (
    (r"\bwideo\s+w\s+\d+\s+sekund", "Obietnica jest nierealistyczna: wideo nie powstaje realnie w kilka sekund."),
    (r"\bgotowy\s+film\s+w\s+\d+\s+sekund", "Obietnica jest nierealistyczna: gotowy film wymaga review i montażu."),
)
TOPIC_CONTENT_CREATION_MARKERS = (
    "short",
    "rolk",
    "reel",
    "tiktok",
    "youtube",
    "render",
    "montaz",
    "napis",
    "hook",
    "storyboard",
    "qa",
    "pipeline",
    "faceless",
    "klip",
)
SCRIPT_CONTENT_DRIFT_MARKERS = (
    "short wyglada jak slop",
    "kolejnym renderem",
    "popraw hook",
    "sceny albo qa",
    "duplikaty napisow",
    "storyboard",
    "renderuje",
    "automatyzacja tylko szybciej produkuje chaos",
)


@dataclass(frozen=True, slots=True)
class ScriptQualityReport:
    hook_score: float
    clarity_score: float
    polish_quality_score: float
    retention_score: float
    subtitle_readability_score: float
    cta_score: float
    overall_score: float
    passed: bool
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "hook_score": self.hook_score,
            "clarity_score": self.clarity_score,
            "polish_quality_score": self.polish_quality_score,
            "retention_score": self.retention_score,
            "subtitle_readability_score": self.subtitle_readability_score,
            "cta_score": self.cta_score,
            "overall_score": self.overall_score,
            "passed": self.passed,
            "issues": list(self.issues),
        }


class ScriptQualityGate:
    def __init__(self, min_score: float = 7.0) -> None:
        self.min_score = min_score

    def evaluate(self, *, script: ShortScript, topic: str) -> ScriptQualityReport:
        text = " ".join((script.hook, *script.body, script.cta, script.voiceover)).strip()
        voiceover_text = script.voiceover.strip() or text
        normalized_text = _normalize_for_matching(text)
        normalized_voiceover = _normalize_for_matching(voiceover_text)
        normalized_hook = _normalize_for_matching(script.hook)
        normalized_cta = _normalize_for_matching(script.cta)
        normalized_topic = _normalize_for_matching(topic)
        words = re.findall(r"\w+", voiceover_text, flags=re.UNICODE)
        word_count = len(words)
        issues: list[str] = []
        critical_failure = False

        hook_score = 10.0
        hook_words = len(re.findall(r"\w+", script.hook, flags=re.UNICODE))
        if hook_words < 5:
            hook_score -= 3
            issues.append("Hook jest za krótki.")
        if hook_words > 18:
            hook_score -= 2
            issues.append("Hook jest za długi jak na pierwsze 2 sekundy.")
        if "przy temacie" in normalized_hook or "temat \"" in normalized_hook:
            hook_score -= 5
            critical_failure = True
            issues.append("Hook cytuje temat zamiast otwierac konkretny short.")
        if not any(char in script.hook for char in ("?", "!", "„", "\"")) and not any(
            token in normalized_hook for token in ("blad", "naj", "dlaczego", "zobacz", "przestan", "nie rob")
        ):
            hook_score -= 2
            issues.append("Hook nie ma wystarczającego napięcia.")

        if normalized_hook and not normalized_voiceover.startswith(normalized_hook[: min(len(normalized_hook), 40)]):
            hook_score -= 3
            critical_failure = True
            issues.append("Hook nie znajduje siÄ™ na poczÄ…tku voiceoveru.")

        clarity_score = 10.0
        topic_tokens = [token for token in re.findall(r"\w+", _normalize_for_matching(topic)) if len(token) > 3]
        if topic_tokens and not any(token in normalized_text for token in topic_tokens[:4]):
            clarity_score -= 4
            issues.append("Skrypt nie trzyma się konkretnego tematu.")
        if "przekupienie" in normalized_text:
            clarity_score -= 6
            critical_failure = True
            issues.append("Skrypt zawiera blad semantyczny: przekupienie nie pasuje do inwestowania.")
        if _topic_is_asset_ranking(normalized_topic) and not _has_asset_ranking_specificity(normalized_text):
            clarity_score -= 6
            critical_failure = True
            issues.append("Ranking aktywow musi wymienic konkretne typy aktywow, nie ogolne rady.")
        if not _topic_is_content_creation(normalized_topic):
            drift_hits = [marker for marker in SCRIPT_CONTENT_DRIFT_MARKERS if marker in normalized_text]
            if len(drift_hits) >= 2:
                clarity_score -= 6
                critical_failure = True
                issues.append("Skrypt odpływa w temat produkcji shortów zamiast trzymać się tematu.")
        generic_hits = [marker for marker in GENERIC_MARKERS if marker in normalized_text]
        if generic_hits:
            clarity_score -= min(7.0, 2.0 + len(generic_hits))
            if len(generic_hits) >= 3:
                critical_failure = True
            issues.append("Skrypt brzmi zbyt ogólnie.")
        if len(script.body) < 2:
            clarity_score -= 2
            issues.append("Brakuje logicznej struktury body.")
        if any(marker in normalized_text for marker in UNSUPPORTED_CERTAINTY_MARKERS):
            clarity_score -= 2.5
            critical_failure = True
            issues.append("Skrypt używa zbyt pewnych twierdzeń bez dowodu.")
        if UNSUPPORTED_METRIC_PATTERN.search(normalized_text):
            clarity_score -= 3.0
            critical_failure = True
            issues.append("Skrypt uzywa nieuzasadnionej metryki/procentu bez zrodla.")
        source_leaks = _matching_pattern_messages(normalized_text, SOURCE_LEAK_PATTERNS)
        if source_leaks:
            clarity_score -= min(8.0, 3.0 + 2.0 * len(source_leaks))
            critical_failure = True
            issues.extend(source_leaks)

        polish_quality_score = 10.0
        if sum(1 for marker in POLISH_MARKERS if marker in normalized_text) < 3:
            polish_quality_score -= 4
            issues.append("Skrypt może nie być naturalnie po polsku.")
        if re.search(r"\b(the|and|you|with|should|because)\b", normalized_text):
            polish_quality_score -= 3
            issues.append("Skrypt zawiera angielskie wtręty.")
        language_errors = _matching_pattern_messages(normalized_text, LANGUAGE_ERROR_PATTERNS)
        if language_errors:
            polish_quality_score -= min(8.0, 3.0 + 2.0 * len(language_errors))
            critical_failure = True
            issues.extend(language_errors)

        semantic_errors = _matching_pattern_messages(normalized_text, SEMANTIC_ERROR_PATTERNS)
        if semantic_errors:
            clarity_score -= min(8.0, 3.0 + 2.0 * len(semantic_errors))
            critical_failure = True
            issues.extend(semantic_errors)

        unrealistic_promises = _matching_pattern_messages(normalized_text, UNREALISTIC_PROMISE_PATTERNS)
        if unrealistic_promises:
            clarity_score -= min(6.0, 2.5 + len(unrealistic_promises))
            issues.extend(unrealistic_promises)

        retention_score = 10.0
        if word_count < 65:
            retention_score -= 3
            critical_failure = True
            issues.append("Skrypt jest za krótki jak na pełnego shorta.")
        if word_count > 150:
            retention_score -= 3
            issues.append("Skrypt jest za długi i napisy mogą być nieczytelne.")
        if re.search(r"\b[1-5]\s*[\).:]", voiceover_text):
            retention_score -= 1.5
            issues.append("Voiceover brzmi jak lista zamiast narracji.")
        if _has_repeated_sentence(voiceover_text):
            retention_score -= 3
            critical_failure = True
            issues.append("Voiceover powtarza to samo zdanie.")
        avg_sentence_len = _average_sentence_words(voiceover_text)
        if avg_sentence_len > 20:
            retention_score -= 2
            issues.append("Zdania są za długie dla czytelnych napisów.")

        subtitle_readability_score = 10.0
        max_sentence_len = _max_sentence_words(voiceover_text)
        if avg_sentence_len > 16:
            subtitle_readability_score -= 2
            issues.append("Średnia długość zdań utrudni czytanie napisów.")
        if max_sentence_len > 28:
            subtitle_readability_score -= 3
            issues.append("Najdłuższe zdanie jest za długie dla shorta.")

        cta_score = 10.0
        if unrealistic_promises:
            cta_score = min(cta_score, 5.0)
        if any(marker in normalized_cta for marker in CLICKBAIT_CTA_MARKERS):
            cta_score -= 3
            issues.append("CTA brzmi sztucznie.")
        if normalized_cta and not _cta_mentions_topic_or_action(normalized_cta, topic_tokens):
            cta_score -= 1.5
            issues.append("CTA jest zbyt oderwane od tematu.")
        if len(re.findall(r"\w+", script.cta, flags=re.UNICODE)) > 18:
            cta_score -= 1.5
            issues.append("CTA jest za długie.")

        scores = tuple(
            max(0.0, min(10.0, value))
            for value in (
                hook_score,
                clarity_score,
                polish_quality_score,
                retention_score,
                subtitle_readability_score,
                cta_score,
            )
        )
        overall = round(
            (
                scores[0] * 0.22
                + scores[1] * 0.22
                + scores[2] * 0.16
                + scores[3] * 0.18
                + scores[4] * 0.14
                + scores[5] * 0.08
            ),
            2,
        )
        if critical_failure:
            overall = min(overall, round(self.min_score - 0.1, 2))
        return ScriptQualityReport(
            hook_score=scores[0],
            clarity_score=scores[1],
            polish_quality_score=scores[2],
            retention_score=scores[3],
            subtitle_readability_score=scores[4],
            cta_score=scores[5],
            overall_score=overall,
            passed=overall >= self.min_score and not critical_failure,
            issues=tuple(issues),
        )


def _average_sentence_words(text: str) -> float:
    sentences = [item.strip() for item in re.split(r"[.!?]+", text) if item.strip()]
    if not sentences:
        return 0.0
    counts = [len(re.findall(r"\w+", sentence, flags=re.UNICODE)) for sentence in sentences]
    return sum(counts) / len(counts)


def _max_sentence_words(text: str) -> int:
    sentences = [item.strip() for item in re.split(r"[.!?]+", text) if item.strip()]
    if not sentences:
        return 0
    return max(len(re.findall(r"\w+", sentence, flags=re.UNICODE)) for sentence in sentences)


def _has_repeated_sentence(text: str) -> bool:
    seen: set[str] = set()
    for sentence in (item.strip() for item in re.split(r"[.!?]+", text) if item.strip()):
        if len(re.findall(r"\w+", sentence, flags=re.UNICODE)) < 7:
            continue
        normalized = _normalize_for_matching(sentence)
        if normalized in seen:
            return True
        seen.add(normalized)
    return False


def _matching_pattern_messages(text: str, patterns: tuple[tuple[str, str], ...]) -> list[str]:
    return [message for pattern, message in patterns if re.search(pattern, text)]


def _cta_mentions_topic_or_action(normalized_cta: str, topic_tokens: list[str]) -> bool:
    if any(token in normalized_cta for token in topic_tokens[:4]):
        return True
    action_markers = (
        "sprawdz",
        "porownaj",
        "zapisz",
        "wybierz",
        "pokaz",
        "przetestuj",
        "zacznij",
        "uzyj",
        "zobacz",
        "usun",
        "napraw",
    )
    return any(marker in normalized_cta for marker in action_markers)


def _topic_is_content_creation(normalized_topic: str) -> bool:
    return any(marker in normalized_topic for marker in TOPIC_CONTENT_CREATION_MARKERS)


def _topic_is_asset_ranking(normalized_topic: str) -> bool:
    return "akty" in normalized_topic and (
        "ranking" in normalized_topic
        or "robi pieniadze" in normalized_topic
        or "robia pieniadze" in normalized_topic
    )


def _has_asset_ranking_specificity(normalized_text: str) -> bool:
    asset_markers = (
        "gotowk",
        "indeks",
        "etf",
        "cashflow",
        "biznes",
        "wynajem",
        "licencj",
        "produkt cyfrow",
        "nieruchom",
        "obligac",
        "akcj",
    )
    return sum(1 for marker in asset_markers if marker in normalized_text) >= 3


def _normalize_for_matching(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
