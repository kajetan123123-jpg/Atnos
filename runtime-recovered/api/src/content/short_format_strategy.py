from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class ShortFormatStrategy:
    key: str
    name: str
    hook_patterns: tuple[str, ...]
    beat_timeline: tuple[str, ...]
    retention_devices: tuple[str, ...]
    visual_direction: str
    caption_style: str
    cta_guidance: str
    risk_notes: tuple[str, ...]

    def to_prompt_section(self) -> str:
        return "\n".join(
            (
                "SHORT FORMAT STRATEGY:",
                f"- format: {self.name} ({self.key})",
                "- hook patterns:",
                *[f"  - {item}" for item in self.hook_patterns],
                "- beat timeline:",
                *[f"  - {item}" for item in self.beat_timeline],
                "- retention devices:",
                *[f"  - {item}" for item in self.retention_devices],
                f"- visual direction: {self.visual_direction}",
                f"- caption style: {self.caption_style}",
                f"- CTA guidance: {self.cta_guidance}",
                "- risk notes:",
                *[f"  - {item}" for item in self.risk_notes],
            )
        )


@dataclass(frozen=True, slots=True)
class ShortStrategyBrief:
    topic: str
    niche: str
    audience: str
    strategy: ShortFormatStrategy
    viewer_promise: str
    viewer_objection: str

    def to_prompt_section(self) -> str:
        return "\n\n".join(
            (
                "SHORT CREATIVE BRIEF:",
                f"- topic: {self.topic}",
                f"- niche: {self.niche or 'general'}",
                f"- audience: {self.audience or 'curious viewer'}",
                f"- viewer promise: {self.viewer_promise}",
                f"- likely objection: {self.viewer_objection}",
                self.strategy.to_prompt_section(),
                "SCRIPTING RULES:",
                "- first 2 seconds must make a clear promise or expose a concrete mistake",
                "- every 4-7 seconds change the beat: claim, example, contrast, consequence, next action",
                "- keep one idea per sentence; avoid essay wording",
                "- make on-screen text short enough for mobile captions",
                "- do not invent facts, metrics, tool features, earnings, or platform rules",
                "- end with a useful next action, not generic like/subscribe filler",
            )
        )


FORMAT_STRATEGIES: tuple[ShortFormatStrategy, ...] = (
    ShortFormatStrategy(
        key="mistake_warning",
        name="Mistake warning",
        hook_patterns=(
            "Najwiekszy blad przy <topic> to...",
            "Jesli robisz <topic> w ten sposob, tracisz...",
            "Przestan zaczynac <topic> od...",
        ),
        beat_timeline=(
            "0-2s: nazwij blad i jego koszt",
            "2-7s: pokaz jak widz zwykle robi to zle",
            "7-15s: pokaz prostszy, poprawny mechanizm",
            "15-28s: daj jeden konkretny przyklad",
            "28-40s: szybka checklista naprawy",
            "40-45s: naturalny CTA do zapisania checklisty",
        ),
        retention_devices=("open loop", "contrast before/after", "visible checklist", "micro payoff"),
        visual_direction="bold red/orange warning card, before-after split, 3-step repair checklist",
        caption_style="short punchy lines, max 1-2 lines on screen, key words highlighted",
        cta_guidance="ask viewer to save the fix or compare it with their current workflow",
        risk_notes=("avoid fearmongering", "do not claim guaranteed results"),
    ),
    ShortFormatStrategy(
        key="before_after",
        name="Before / after transformation",
        hook_patterns=(
            "Przed: <bad state>. Po: <better state>.",
            "Tak wyglada slaby <topic>. A tak wyglada lepszy.",
            "Jedna zmiana, ktora poprawia <topic> od razu...",
        ),
        beat_timeline=(
            "0-2s: pokaz kontrast przed/po",
            "2-8s: nazwij przyczyne roznicy",
            "8-18s: rozbij zmiane na 2-3 decyzje",
            "18-32s: pokaz efekt na konkretnym przykladzie",
            "32-42s: powiedz kiedy nie stosowac tej metody",
            "42-45s: CTA do testu jednej zmiany",
        ),
        retention_devices=("visual contrast", "progress reveal", "specific example", "anti-rule"),
        visual_direction="split screen, progress meter, clean transformation cards",
        caption_style="comparison captions: BEFORE / AFTER / WHY IT WORKS",
        cta_guidance="ask viewer to test one change and review the result",
        risk_notes=("do not fake transformation quality", "show limitation if result depends on tools"),
    ),
    ShortFormatStrategy(
        key="how_to_micro",
        name="Micro how-to",
        hook_patterns=(
            "Zrob <topic> w trzech ruchach.",
            "Najprostszy workflow do <topic> wyglada tak...",
            "Jesli chcesz <topic>, zacznij od tego kroku.",
        ),
        beat_timeline=(
            "0-2s: wynik, ktory widz chce uzyskac",
            "2-8s: krok 1 i dlaczego jest pierwszy",
            "8-16s: krok 2 z konkretnym ustawieniem",
            "16-27s: krok 3 z weryfikacja wyniku",
            "27-38s: typowy blad do unikniecia",
            "38-45s: CTA do zapisania workflow",
        ),
        retention_devices=("numbered beats visually, not in voiceover", "checkpoint", "mistake guard"),
        visual_direction="clean workflow cards, arrows, tool icons, final artifact preview",
        caption_style="verb-first captions: wybierz / ustaw / sprawdz",
        cta_guidance="invite saving the workflow, not generic engagement bait",
        risk_notes=("avoid turning narration into a dry numbered list",),
    ),
    ShortFormatStrategy(
        key="myth_bust",
        name="Myth bust",
        hook_patterns=(
            "Mit: <topic> wymaga...",
            "To nieprawda, ze <topic> musi...",
            "Wiekszosc osob myli sie w jednej rzeczy o <topic>.",
        ),
        beat_timeline=(
            "0-2s: mit albo bledne zalozenie",
            "2-8s: dlaczego ten mit jest kuszacy",
            "8-18s: poprawna zasada",
            "18-30s: konkretny przyklad",
            "30-40s: kiedy mit czesciowo bywa prawdziwy",
            "40-45s: CTA do sprawdzenia zalozenia",
        ),
        retention_devices=("pattern interrupt", "correction", "nuance", "example"),
        visual_direction="myth vs fact cards, stamp overlays, simple evidence strip",
        caption_style="MYTH / REALITY / CHECK THIS",
        cta_guidance="ask viewer to check one assumption in their own setup",
        risk_notes=("do not overstate the counterclaim", "include nuance when needed"),
    ),
    ShortFormatStrategy(
        key="tool_stack",
        name="Tool stack explainer",
        hook_patterns=(
            "Te narzedzia nie robia tego samego.",
            "Nie wrzucaj wszystkiego do jednego AI. Podziel workflow tak...",
            "Jesli uzywasz <topic>, ustaw role narzedzi jasno.",
        ),
        beat_timeline=(
            "0-2s: rozdziel role narzedzi",
            "2-9s: narzedzie 1 i jego prawdziwa rola",
            "9-16s: narzedzie 2 i granica uzycia",
            "16-26s: narzedzie 3 i punkt kontroli jakosci",
            "26-38s: pokaz kolejke outputow",
            "38-45s: CTA do zapisania stacku",
        ),
        retention_devices=("role labels", "tool contrast", "workflow map", "QA checkpoint"),
        visual_direction="stack diagram, tool cards, pipeline arrows, final output preview",
        caption_style="tool name + one role, no long descriptions",
        cta_guidance="ask viewer to map their own stack before automating",
        risk_notes=("do not invent tool features", "separate API access from subscription UI"),
    ),
    ShortFormatStrategy(
        key="case_study",
        name="Mini case study",
        hook_patterns=(
            "Zobacz, co zmienilo wynik w tym przypadku...",
            "Ten maly przypadek pokazuje, dlaczego <topic> dziala.",
            "Przed decyzja bylo X. Po decyzji bylo Y.",
        ),
        beat_timeline=(
            "0-2s: wynik albo zaskakujacy kontrast",
            "2-8s: kontekst w jednym zdaniu",
            "8-18s: decyzja, ktora zmienila wynik",
            "18-30s: dowod albo obserwacja",
            "30-40s: lekcja do zastosowania",
            "40-45s: CTA do skopiowania zasady",
        ),
        retention_devices=("curiosity gap", "specific situation", "payoff", "lesson"),
        visual_direction="case card, timeline, one metric if verified, lesson card",
        caption_style="context -> decision -> lesson",
        cta_guidance="invite applying the principle, not copying blindly",
        risk_notes=("only use metrics if provided", "do not fabricate case details"),
    ),
    ShortFormatStrategy(
        key="checklist",
        name="Checklist",
        hook_patterns=(
            "Zanim zrobisz <topic>, sprawdz te 3 rzeczy.",
            "Ta checklista ratuje <topic> przed chaosem.",
            "Jesli brakuje jednego punktu, <topic> bedzie slaby.",
        ),
        beat_timeline=(
            "0-2s: obiecaj konkretna kontrole jakosci",
            "2-10s: punkt 1 i jego test",
            "10-18s: punkt 2 i jego test",
            "18-28s: punkt 3 i jego test",
            "28-38s: najczestszy brak",
            "38-45s: CTA do zapisania checklisty",
        ),
        retention_devices=("visible checklist", "tick marks", "last item reveal", "failure guard"),
        visual_direction="large readable checklist, green/red states, minimal background clutter",
        caption_style="one checkpoint per caption, no paragraphs",
        cta_guidance="ask viewer to use it before publishing/exporting",
        risk_notes=("avoid generic checklist items",),
    ),
    ShortFormatStrategy(
        key="comparison",
        name="Comparison",
        hook_patterns=(
            "<A> czy <B>? Zalezy od jednego kryterium.",
            "Nie wybieraj <A> albo <B> zanim sprawdzisz...",
            "Najprostsze porownanie <topic> wyglada tak.",
        ),
        beat_timeline=(
            "0-2s: decyzja, ktora widz chce podjac",
            "2-9s: kryterium 1",
            "9-16s: kryterium 2",
            "16-26s: kiedy wybrac pierwsza opcje",
            "26-36s: kiedy wybrac druga opcje",
            "36-45s: CTA do wyboru wedlug kryterium",
        ),
        retention_devices=("decision tension", "side-by-side labels", "clear rule", "edge case"),
        visual_direction="side-by-side comparison, score chips, final recommendation card",
        caption_style="short criteria labels with clear winner per use case",
        cta_guidance="ask viewer to choose based on their constraint",
        risk_notes=("avoid fake universal winner",),
    ),
)


def build_short_strategy_brief(topic: str, *, niche: str = "", audience: str = "") -> ShortStrategyBrief:
    clean_topic = _clean_topic(topic)
    strategy = choose_short_format(clean_topic, niche=niche, audience=audience)
    return ShortStrategyBrief(
        topic=clean_topic,
        niche=_clean_topic(niche),
        audience=_clean_topic(audience),
        strategy=strategy,
        viewer_promise=_viewer_promise(clean_topic, strategy),
        viewer_objection=_viewer_objection(clean_topic, strategy),
    )


def choose_short_format(topic: str, *, niche: str = "", audience: str = "") -> ShortFormatStrategy:
    text = _normalize(" ".join((topic, niche, audience)))
    if _has_any(text, ("blad", "bled", "problem", "czemu nie", "dlaczego nie", "tragicz", "slaby", "napraw")):
        return _strategy("mistake_warning")
    if _has_any(text, (" vs ", "kontra", "porown", "lepszy", "wybrac", "czy ")):
        return _strategy("comparison")
    if _has_any(text, ("narzedz", "tool", "stack", "aplikac", "submagic", "klap", "capcut", "runway", "elevenlabs")):
        return _strategy("tool_stack")
    if _has_any(text, ("jak ", "workflow", "krok", "zrob", "ustaw", "automatyzac", "pipeline")):
        return _strategy("how_to_micro")
    if _has_any(text, ("mit", "prawda", "falsz", "nieprawda", "myli")):
        return _strategy("myth_bust")
    if _has_any(text, ("case", "przypadek", "historia", "wynik", "analiza")):
        return _strategy("case_study")
    if _has_any(text, ("checklist", "lista", "sprawdz", "przed publikacja", "qa")):
        return _strategy("checklist")
    return _strategy("before_after")


def _strategy(key: str) -> ShortFormatStrategy:
    for item in FORMAT_STRATEGIES:
        if item.key == key:
            return item
    raise KeyError(key)


def _viewer_promise(topic: str, strategy: ShortFormatStrategy) -> str:
    if strategy.key == "tool_stack":
        return f"Widz zrozumie, jaka role ma miec kazde narzedzie w temacie: {topic}."
    if strategy.key == "mistake_warning":
        return f"Widz rozpozna jeden blad, ktory realnie psuje wynik w temacie: {topic}."
    if strategy.key == "comparison":
        return f"Widz dostanie proste kryterium wyboru zamiast losowej opinii o: {topic}."
    if strategy.key == "how_to_micro":
        return f"Widz dostanie maly workflow, ktory da sie sprawdzic od razu: {topic}."
    return f"Widz zrozumie konkretny mechanizm i nastepny krok w temacie: {topic}."


def _viewer_objection(topic: str, strategy: ShortFormatStrategy) -> str:
    if strategy.key in {"tool_stack", "comparison"}:
        return "To brzmi jak reklama narzedzi albo przypadkowa lista bez granic uzycia."
    if strategy.key == "mistake_warning":
        return "To moze brzmiec jak straszenie bez dowodu."
    if strategy.key == "how_to_micro":
        return "To moze byc zbyt ogolne, jesli nie ma konkretnego checkpointu."
    return f"To moze byc zbyt abstrakcyjne, jesli nie pokazemy przykladu dla: {topic}."


def _clean_topic(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize(value: str) -> str:
    text = _clean_topic(value).casefold()
    replacements = str.maketrans(
        {
            "ą": "a",
            "ć": "c",
            "ę": "e",
            "ł": "l",
            "ń": "n",
            "ó": "o",
            "ś": "s",
            "ż": "z",
            "ź": "z",
        }
    )
    return re.sub(r"\s+", " ", text.translate(replacements))


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
