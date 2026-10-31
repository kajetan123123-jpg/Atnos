from __future__ import annotations

from dataclasses import dataclass
import json
import re
import unicodedata
from typing import Any

import requests

from atnos.core.config import Settings
from content.short_format_strategy import build_short_strategy_brief


_MOJIBAKE_REPLACEMENTS = {
    "Ä…": "ą",
    "Ä‡": "ć",
    "Ä™": "ę",
    "Ĺ‚": "ł",
    "Ĺ„": "ń",
    "Ăł": "ó",
    "Ĺ›": "ś",
    "Ĺş": "ź",
    "ĹĽ": "ż",
    "Ä„": "Ą",
    "Ä†": "Ć",
    "Ä": "Ę",
    "Ĺ": "Ł",
    "Ĺš": "Ś",
    "Ĺ»": "Ż",
    "â€ž": "„",
    "â€ť": "”",
    "â€œ": "“",
    "â€“": "–",
    "â€”": "—",
    "â€™": "’",
    "â€¦": "…",
}


def _repair_mojibake(text: str) -> str:
    repaired = str(text or "")
    for broken, fixed in _MOJIBAKE_REPLACEMENTS.items():
        repaired = repaired.replace(broken, fixed)
    return repaired


def _enforce_voiceover_contract(
    *,
    topic: str,
    hook: str,
    body: tuple[str, ...],
    cta: str,
    voiceover: str,
) -> str:
    normalized_hook = _normalize_for_contract(hook)
    normalized_voiceover = _normalize_for_contract(voiceover)
    if normalized_hook and not normalized_voiceover.startswith(normalized_hook[: min(len(normalized_hook), 40)]):
        voiceover = " ".join((hook, *body, cta)).strip()

    for sentence in _supporting_sentences(topic):
        if len(re.findall(r"\w+", voiceover, flags=re.UNICODE)) >= 65:
            break
        voiceover = " ".join((voiceover.rstrip(), sentence)).strip()
    return _repair_mojibake(voiceover)


def _supporting_sentences(topic: str) -> tuple[str, ...]:
    clean_topic = _clean_topic_for_script(topic)
    normalized = _strip_accents(clean_topic)
    if _looks_like_finance_topic(normalized):
        return (
            "W finansach najpierw pokaz koszt zachowania, potem prosta zmiane decyzji.",
            "Nie dawaj porady kup albo sprzedaj; pokaz zasade, ktora widz moze sprawdzic u siebie.",
            "Zakoncz jedna decyzja, ktora widz podejmie przed kolejnym wydatkiem.",
        )
    if _looks_like_local_ai_topic(normalized):
        return (
            "Najpierw pokaz jeden prywatny plik, potem wynik i ograniczenie modelu.",
            "Nie obiecuj magii; pokaz kiedy lokalny runtime realnie pomaga.",
            "Zakoncz checkpointem, ktory widz moze przetestowac na swoim komputerze.",
            "Dopiero po takim tescie warto dodawac automatyzacje i kolejne modele.",
        )
    if not _looks_like_content_creation_topic(normalized):
        return (
            f"Pokaz jeden konkretny mechanizm zwiazany z tematem: {clean_topic}.",
            "Najpierw nazwij problem, potem zmiane, potem efekt albo ograniczenie.",
            "Usun wszystko, czego nie da sie wyjasnic jednym zdaniem.",
        )
    return (
        f"Pokaż jeden konkretny problem i naprawę w kadrze: {clean_topic}.",
        "Najpierw pokaż błąd, potem zmianę, potem efekt po QA.",
        "Usuń wszystko, czego nie da się zobaczyć na ekranie albo wyjaśnić jednym zdaniem.",
    )


def _normalize_for_contract(text: str) -> str:
    return " ".join(text.casefold().split())


def _clean_topic_for_script(topic: str) -> str:
    clean = " ".join(_repair_mojibake(str(topic or "")).split()).strip()
    clean = re.sub(r"(?i)^\s*(ai\s+short|short|rolka|reels?)\s*:\s*", "", clean).strip()
    clean = _extract_original_short_goal(clean)
    clean = re.sub(r"https?://\S+", "", clean)
    clean = re.sub(r"#\S+", "", clean)
    clean = re.sub(r"\b\d[\d\s,.]{3,}\s*(views|wyświetleń|wyswietlen)\b", "", clean, flags=re.IGNORECASE)
    clean = _drop_symbol_noise(clean)
    clean = " ".join(clean.split()).strip(" -.,;:")
    clean = _truncate_topic(clean)
    return clean or "ten temat"


def _extract_original_short_goal(topic: str) -> str:
    for marker in ("Zrób oryginalny short:", "Zrob oryginalny short:"):
        if marker in topic:
            candidate = topic.split(marker, maxsplit=1)[1]
            candidate = re.split(r"\bHook:|\bSygnał:|\bSygnal:|\bNie kopiuj", candidate, maxsplit=1)[0]
            if candidate.strip():
                return candidate.strip()
    without_source_title = re.sub(
        r"\bz filmu\s+['\"„”].{20,}?['\"„”]\s*",
        "",
        topic,
        flags=re.IGNORECASE,
    )
    return without_source_title.strip() or topic


def _drop_symbol_noise(text: str) -> str:
    cleaned_chars: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category in {"So", "Cs"}:
            continue
        cleaned_chars.append(char)
    return "".join(cleaned_chars)


def _truncate_topic(text: str, *, max_chars: int = 180) -> str:
    if len(text) <= max_chars:
        return text
    boundary = max(text.rfind(". ", 0, max_chars), text.rfind("; ", 0, max_chars), text.rfind(", ", 0, max_chars))
    if boundary >= 60:
        return text[: boundary + 1].strip()
    return text[:max_chars].rsplit(" ", maxsplit=1)[0].rstrip(" -.,;:") + "."


def _looks_like_finance_topic(normalized: str) -> bool:
    markers = (
        "finans",
        "pieniadz",
        "pieniedz",
        "pienie",
        "dlug",
        "akty",
        "pasyw",
        "inflac",
        "podwyz",
        "wypl",
        "bogac",
        "biedn",
        "majat",
        "inwest",
        "oszczedz",
        "dochod",
    )
    return any(marker in _strip_accents(normalized) for marker in markers)


def _looks_like_asset_ranking_topic(normalized: str) -> bool:
    stripped = _strip_accents(normalized)
    return "akty" in stripped and (
        "ranking" in stripped
        or "robi pieniadze" in stripped
        or "robia pieniadze" in stripped
        or "cashflow" in stripped
    )


def _looks_like_content_creation_topic(normalized: str) -> bool:
    markers = (
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
    return any(marker in _strip_accents(normalized) for marker in markers)


def _looks_like_local_ai_topic(normalized: str) -> bool:
    markers = ("lokalne ai", "local ai", "ollama", "llm", "model", "prywatne pliki", "offline")
    return any(marker in _strip_accents(normalized) for marker in markers)


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _cat_short_fallback_parts() -> tuple[str, str, tuple[str, ...], str]:
    hook = "Ten kot odrzucil shorta bez QA."
    body = (
        "Na ekranie widzi jeden problem: za duzo tekstu, za malo scen.",
        "Agent najpierw wybiera hook, potem planuje ujecia, dopiero pozniej renderuje.",
        "Jesli QA widzi duplikaty napisow, film wraca do naprawy.",
        "To nie magia. To proces: hook, storyboard, render i kontrola.",
    )
    cta = "Zapisz te checkliste zanim wygenerujesz kolejny short."
    return "Kot kontra AI slop", hook, body, cta


def _finance_fallback_parts(topic: str) -> tuple[str, str, tuple[str, ...], str]:
    stripped = _strip_accents(topic)
    if _looks_like_asset_ranking_topic(stripped):
        hook = "Nie każde aktywo naprawdę robi pieniądze."
        body = (
            "Na trzecim miejscu jest gotówka: daje spokój, ale sama nie buduje majątku.",
            "Na drugim są szerokie indeksy albo ETF-y: mało widowiskowe, ale rozkładają ryzyko.",
            "Na pierwszym są aktywa z cashflow: biznes, wynajem, licencja albo produkt cyfrowy.",
            "Test jest prosty: czy aktywo daje dochód, większą kontrolę albo niższy koszt.",
            "To nie rekomendacja kupna. To mapa, która odróżnia majątek od drogiej ozdoby.",
        )
        cta = "Zapisz ten ranking przed kolejnym zakupem."
        title = "Ranking aktywów w 60 sekund"
    elif "podwyz" in stripped:
        hook = "Najwiekszy blad po podwyzce? Styl zycia rosnie pierwszy."
        body = (
            "To blad finansowy, bo nowy standard zycia potrafi zjesc cala roznice.",
            "Drozsze jedzenie, telefon i subskrypcje wygladaja niewinnie, ale szybko staja sie nowa norma.",
            "Lepszy ruch to najpierw podzielic podwyzke: czesc na spokoj, czesc na przyszlosc, czesc na przyjemnosc.",
            "Dopiero potem podnosisz standard zycia, zamiast oddac cala podwyzke przypadkowym wydatkom.",
            "To nadal edukacja, nie porada inwestycyjna: chodzi o kolejnosc decyzji, zanim pieniadze znikna.",
        )
        cta = "Zapisz to przed nastepna podwyzka."
        title = "Blad po pierwszej podwyzce"
    elif "dlug" in stripped:
        hook = "Dlug nie jest jeden. Bogaci dziela go na role."
        body = (
            "Najgorszy dlug kupuje status, ktory traci wartosc od razu.",
            "Lepszy dlug finansuje narzedzie, edukacje albo aktywo, ale tylko gdy liczby maja sens.",
            "Klucz jest prosty: czy po czasie masz wiecej przeplywu, czy tylko wieksza rate.",
            "Bez tej roznicy dlug nie buduje wolnosci, tylko zabiera decyzje z przyszlosci.",
        )
        cta = "Zapisz ten test przed kolejna rata."
        title = "Dobry i zly dlug"
    else:
        hook = "Najwiekszy blad finansowy zaczyna sie od zlej definicji pieniedzy."
        body = (
            "Wiekszosc ludzi liczy tylko to, ile wplywa na konto.",
            "Bogatsze myslenie pyta inaczej: ile z tego zostaje, co pracuje dalej i co znika bez sladu.",
            "Dlatego wydatek, aktywo i przyjemnosc nie powinny byc w jednej mentalnej szufladzie.",
            "Gdy rozdzielisz te trzy role, latwiej zobaczyc, gdzie uciekaja decyzje.",
        )
        cta = "Zapisz ten podzial przed kolejnym wydatkiem."
        title = f"Krotko o: {topic}"
    return title, hook, body, cta


def _should_use_finance_fallback(*, topic: str, text: str) -> bool:
    normalized_topic = _strip_accents(topic)
    if not _looks_like_finance_topic(normalized_topic):
        return False
    if _looks_like_asset_ranking_topic(normalized_topic):
        return True

    normalized_text = _strip_accents(text)
    if "przekupien" in normalized_text:
        return True

    if _looks_like_asset_ranking_topic(normalized_topic):
        asset_markers = (
            "gotowk",
            "indeks",
            "etf",
            "cashflow",
            "biznes",
            "wynajem",
            "licencj",
            "produkt cyfrow",
        )
        if sum(1 for marker in asset_markers if marker in normalized_text) < 3:
            return True
    return False


def _content_creation_fallback_parts(topic: str) -> tuple[str, str, tuple[str, ...], str]:
    hook = "Bez jednego konkretu short wyglada jak slop."
    body = (
        "Pierwsze dwie sekundy musza pokazac problem, nie wstep.",
        "Potem potrzebny jest jeden mechanizm, ktory widz rozumie od razu.",
        "Na koncu zostaje prosta decyzja: popraw hook, sceny albo QA.",
        "Jesli tego brakuje, automatyzacja tylko szybciej produkuje chaos.",
    )
    cta = "Zapisz te checkliste przed kolejnym renderem."
    return f"Krotko o: {topic}", hook, body, cta


def _local_ai_fallback_parts(topic: str) -> tuple[str, str, tuple[str, ...], str]:
    hook = "Lokalne AI ma sens dopiero przy jednym konkretnym pliku."
    body = (
        "Nie zaczynaj od wielkiego systemu, bo wtedy trudno sprawdzic jakosc.",
        "Wybierz jeden dokument, jedna prosbe i jeden wynik, ktory da sie ocenic.",
        "Jesli model pomaga bez wysylania danych do chmury, dopiero wtedy rozbuduj workflow.",
        "Tak Atnos ma rosnac: maly test, jasny wynik, potem automatyzacja.",
    )
    cta = "Zapisz ten test przed instalowaniem kolejnego modelu."
    return f"Krotko o: {topic}", hook, body, cta


def _general_fallback_parts(topic: str) -> tuple[str, str, tuple[str, ...], str]:
    hook = f"Najpierw znajdz jeden konkretny mechanizm w temacie: {topic}."
    body = (
        "Jesli temat jest szeroki, widz szybko traci punkt zaczepienia.",
        "Dlatego short powinien pokazac jeden problem, jedna zmiane i jedna konsekwencje.",
        "Najlepszy test jest prosty: czy po obejrzeniu widz pamieta jedno zdanie.",
        "Jesli nie, trzeba wyciac poboczne watki i zostawic tylko rdzen.",
    )
    cta = "Zapisz ten test przed kolejnym szkicem."
    return f"Krotko o: {topic}", hook, body, cta


def _soften_absolute_claims(text: str) -> str:
    text = _repair_mojibake(text)
    replacements = (
        ("bez opłat", "z większą kontrolą kosztów"),
        ("Brak opłat", "Większa kontrola kosztów"),
        ("brak opłat", "większa kontrola kosztów"),
        ("bez opóźnień sieciowych", "z mniejszą zależnością od opóźnień sieciowych"),
        ("bez opóźnień", "z mniejszą zależnością od opóźnień"),
        ("Brak opóźnień", "Mniejsza zależność od opóźnień"),
        ("brak opóźnień", "mniejsza zależność od opóźnień"),
        ("bez ryzyka wycieku danych", "z mniejszą potrzebą wysyłania danych poza komputer"),
        ("bez ryzyka wycieku", "z mniejszą potrzebą wysyłania danych poza komputer"),
        ("nie musisz się martwić o wycieki danych osobowych", "łatwiej ograniczyć wysyłanie danych osobowych"),
        ("nie wysyłają danych do chmu", "mogą działać bez wysyłania danych do chmury"),
        ("są bezpieczniejsze", "dają większą kontrolę nad danymi"),
        ("może zmienić Twoje życie", "może zmienić sposób pracy"),
        ("to przyszłość", "to praktyczny kierunek pracy"),
        ("To przyszłość", "To praktyczny kierunek pracy"),
        ("wieczna wartość", "wartość zależna od realnego użycia"),
        ("nie musisz się obawiać", "łatwiej ograniczyć ryzyko"),
        ("zawsze dostępny", "dostępny wtedy, gdy działa lokalny runtime"),
        ("zawsze dostępne", "dostępne wtedy, gdy działa lokalny runtime"),
    )
    softened = text
    for old, new in replacements:
        softened = softened.replace(old, new)
    return softened


@dataclass(frozen=True, slots=True)
class ShortScript:
    title: str
    hook: str
    body: tuple[str, ...]
    cta: str
    voiceover: str

    def to_markdown(self) -> str:
        title = _repair_mojibake(self.title)
        hook = _repair_mojibake(self.hook)
        body = tuple(_repair_mojibake(item) for item in self.body)
        cta = _repair_mojibake(self.cta)
        voiceover = _repair_mojibake(self.voiceover)
        lines = [
            f"# {title}",
            "",
            "## Hook",
            hook,
            "",
            "## Body",
            *[f"- {item}" for item in body],
            "",
            "## CTA",
            cta,
            "",
            "## Voiceover",
            voiceover,
            "",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": _repair_mojibake(self.title),
            "hook": _repair_mojibake(self.hook),
            "body": [_repair_mojibake(item) for item in self.body],
            "cta": _repair_mojibake(self.cta),
            "voiceover": _repair_mojibake(self.voiceover),
        }


class ScriptGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_brief(self, topic: str) -> str:
        topic = _clean_topic_for_script(topic)
        strategy = build_short_strategy_brief(topic)
        return (
            f"Temat shorta: {topic}\n"
            "Język: polski.\n"
            "Format: YouTube Shorts / Reels, 25-45 sekund.\n"
            "Cel: mocny hook, konkret, praktyczna wartość, zero lania wody.\n"
            "Styl: prosty, dynamiczny, bez sztucznego CTA.\n"
            "Zakaz: filler typu „w dzisiejszych czasach”, błędy językowe, obietnice bez dowodu."
            "\n\n"
            f"{strategy.to_prompt_section()}"
        )

    def generate(self, *, topic: str, brief: str) -> ShortScript:
        prompt = self._build_prompt(topic=topic, brief=brief)
        try:
            raw = self._ask_ollama(prompt)
        except requests.RequestException:
            return self._automation_fallback_script(topic)
        parsed = self._parse_json(raw)
        if parsed is None:
            return self._automation_fallback_script(topic)
        return self._normalize_script(parsed, topic=topic)

    def _ask_ollama(self, prompt: str) -> str:
        model = self.settings.ollama_synthesis_model or self.settings.ollama_chat_model or "qwen3:14b"
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
            },
            timeout=self.settings.ollama_timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, dict):
            return str(message.get("content") or "").strip()
        return str(payload.get("response") or "").strip() if isinstance(payload, dict) else ""

    @staticmethod
    def _build_prompt(*, topic: str, brief: str) -> str:
        return (
            "Jesteś scenarzystą krótkich polskich shortów. Zwróć WYŁĄCZNIE JSON.\n"
            f"{brief}\n\n"
            "Schema:\n"
            "{\"title\":\"...\",\"hook\":\"...\",\"body\":[\"...\",\"...\",\"...\"],\"cta\":\"...\",\"voiceover\":\"...\"}\n\n"
            "Wymagania:\n"
            "- zastosuj wskazany SHORT FORMAT STRATEGY; nie pisz generycznego eseju\n"
            "- zaplanuj narracje jako serie beatow retencyjnych, ale voiceover ma brzmiec naturalnie\n"
            "- tekst na ekranie ma byc krotki, mobilny i zgodny z caption style ze strategii\n"
            "- hook ma uderzyć w pierwszych 2 sekundach\n"
            "- body ma mieć konkretną strukturę i przykłady\n"
            "- voiceover 80-130 słów po polsku\n"
            "- używaj krótkich zdań i prostego języka mówionego\n"
            "- bez obiecywania faktów, których nie da się uzasadnić\n"
            "- nie dopowiadaj losowych funkcji narzędzi; jeśli nie wiesz, zawęź claim\n"
            "- każdy przykład musi bezpośrednio wynikać z tematu; zakaz losowych przykładów typu spam, edycja zdjęć albo tłumaczenie znaków, jeśli temat o tym nie mówi\n"
            "- przy tematach o Atnosie, lokalnym AI albo automatyzacji pokazuj workflow, pliki, approval, QA, prywatność lub koszt, a nie przypadkowe zastosowania AI\n"
            "- bez zdań meta typu: potraktuj temat jak workflow, sprawdź przykład i rozbuduj proces\n"
            "- CTA ma być naturalne albo bardzo krótkie\n"
            "- voiceover ma zaczynac sie dokladnie od hooka\n"
            "- nie uzywaj numerowanej listy w voiceoverze; pisz jak naturalna narracja\n"
            "- unikaj absolutnych claimow typu brak oplat, bez oplat, brak opoznien, bez ryzyka\n"
            f"Temat: {_clean_topic_for_script(topic)}"
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _normalize_script(parsed: dict[str, Any], *, topic: str) -> ShortScript:
        title = str(parsed.get("title") or f"Short o {topic}").strip()
        hook = str(parsed.get("hook") or "").strip()
        raw_body = parsed.get("body")
        body = tuple(str(item).strip() for item in raw_body if str(item).strip()) if isinstance(raw_body, list) else ()
        cta = str(parsed.get("cta") or "Jeśli chcesz, zapisz ten materiał i wróć do niego później.").strip()
        voiceover = str(parsed.get("voiceover") or "").strip()
        if not voiceover:
            voiceover = " ".join((hook, *body, cta)).strip()
        if not hook:
            hook = voiceover.split(".", maxsplit=1)[0].strip() or f"To jest konkretny short o: {topic}."
        if not body:
            body = tuple(sentence.strip() for sentence in re.split(r"[.!?]", voiceover) if sentence.strip())[1:5]
        title = _soften_absolute_claims(title)
        hook = _soften_absolute_claims(hook)
        body = tuple(_soften_absolute_claims(item) for item in body)
        cta = _soften_absolute_claims(cta)
        voiceover = _soften_absolute_claims(voiceover)
        if _should_use_finance_fallback(topic=topic, text=" ".join((title, hook, *body, cta, voiceover))):
            return ScriptGenerator._automation_fallback_script(topic)
        voiceover = _enforce_voiceover_contract(
            topic=topic,
            hook=hook,
            body=body,
            cta=cta,
            voiceover=voiceover,
        )
        return ShortScript(title=title, hook=hook, body=body, cta=cta, voiceover=voiceover)

    @staticmethod
    def _automation_fallback_script(topic: str) -> ShortScript:
        clean_topic = _clean_topic_for_script(topic)
        normalized = clean_topic.casefold()
        if "kot" in normalized and ("short" in normalized or "kanal" in normalized or "kanał" in normalized):
            title, hook, body, cta = _cat_short_fallback_parts()
        elif _looks_like_finance_topic(normalized):
            title, hook, body, cta = _finance_fallback_parts(clean_topic)
        elif _looks_like_content_creation_topic(normalized):
            title, hook, body, cta = _content_creation_fallback_parts(clean_topic)
        elif _looks_like_local_ai_topic(normalized):
            title, hook, body, cta = _local_ai_fallback_parts(clean_topic)
        else:
            title, hook, body, cta = _general_fallback_parts(clean_topic)
        return ShortScript(
            title=title,
            hook=hook,
            body=body,
            cta=cta,
            voiceover=_enforce_voiceover_contract(
                topic=clean_topic,
                hook=hook,
                body=body,
                cta=cta,
                voiceover=" ".join((hook, *body, cta)),
            ),
        )

    @staticmethod
    def _fallback_script(topic: str) -> ShortScript:
        clean_topic = _clean_topic_for_script(topic)
        hook = f"Największy błąd przy temacie „{clean_topic}” to myślenie, że wystarczy ogólny plan."
        body = (
            "Najpierw nazwij konkretny problem, który chcesz rozwiązać.",
            "Potem wybierz jeden mierzalny efekt, a nie pięć luźnych kierunków.",
            "Na końcu sprawdź, czy następny krok da się wykonać dzisiaj.",
        )
        cta = "Zapisz to jako prostą checklistę przed kolejną decyzją."
        return ShortScript(
            title=f"Krótko o: {clean_topic}",
            hook=hook,
            body=body,
            cta=cta,
            voiceover=_enforce_voiceover_contract(
                topic=clean_topic,
                hook=hook,
                body=body,
                cta=cta,
                voiceover=" ".join((hook, *body, cta)),
            ),
        )
