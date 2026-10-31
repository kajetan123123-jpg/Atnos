from __future__ import annotations

import math
import re

SMALL = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20), strict=True))
TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10), strict=True))
SCALES = {"thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
NUMBER_WORDS = {*SMALL, *TENS, *SCALES, "hundred", "point"}
TOKEN = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?|[a-z]+|[$%]|[^\w\s]", re.I)
UNITS = {"dollar": "dollars", "dollars": "dollars", "percent": "percent", "%": "percent",
         "zloty": "PLN", "zlotys": "PLN", "pln": "PLN",
         "week": "weeks", "weeks": "weeks", "year": "years", "years": "years"}


def spell_narration_integers(text: str) -> str:
    """Spell unambiguous English integer literals without changing their value/unit.

    Dates, decimals, signed values, identifiers and other ambiguous forms remain
    untouched for ordinary validation/repair. The raw model response is retained.

    Hyphenated unit compounds such as ``90-day`` become ``ninety-day``. Digit–digit
    ranges such as ``10-20`` stay numeric.
    """
    from atnos.production.script_adaptation import number_words

    def _spell_int(raw: str) -> str | None:
        if len(raw) > 1 and raw.startswith("0"):
            return None
        value = int(raw.replace(",", ""))
        if value >= 10**12:
            return None
        return number_words(value)

    # 90-day / 5-year → ninety-day / five-year (letter unit after hyphen, not 10-20 ranges).
    compound = (
        r"(?<![\w.,:/+\-$€£])(?P<number>\d{1,3}(?:,\d{3})+|\d+)"
        r"(?P<hyphen>-+)(?P<unit>[A-Za-z][A-Za-z-]*)"
    )

    def replace_compound(match):
        spoken = _spell_int(match["number"])
        if spoken is None:
            return match[0]
        return f"{spoken}{match['hyphen']}{match['unit']}"

    patterned = re.sub(compound, replace_compound, text)

    # Percent literals need the same treatment as dollar literals.  The
    # general integer pattern intentionally excludes a trailing percent sign,
    # so handle it before that pass and preserve the unit as spoken words.
    percent_pattern = (
        r"(?<![\w.,:/+\-$€£])(?P<number>\d{1,3}(?:,\d{3})+|\d+)"
        r"%(?=\s+(?:a|per|annual|year|rate|return|growth)\b)"
    )

    def replace_percent(match):
        spoken = _spell_int(match["number"])
        return f"{spoken} percent" if spoken is not None else match[0]

    patterned = re.sub(percent_pattern, replace_percent, patterned)

    pattern = r"(?<![\w.,:/+\-$€£])(?P<dollar>\$)?(?P<number>\d{1,3}(?:,\d{3})+|\d+)(?![\w:/%+\-]|[.,]\d)"

    def replace(match):
        spoken = _spell_int(match["number"])
        if spoken is None:
            return match[0]
        if match["dollar"]:
            # '$20 dollars' already has the unit after the number.
            suffix = patterned[match.end() :]
            if not re.match(r"\s+dollars?\b", suffix, re.I):
                spoken += " dollar" if int(match["number"].replace(",", "")) == 1 else " dollars"
        return spoken

    normalized = re.sub(pattern, replace, patterned)
    # An amount before a noun is an attributive unit: "$10 purchase" is a
    # ten-dollar purchase, not "ten dollars purchase". Preserve its numeric
    # value/currency and leave standalone amounts such as "spend ten dollars"
    # unchanged. This also cleans the same unambiguous model phrasing.
    words = "|".join(sorted(NUMBER_WORDS - {"point"}, key=len, reverse=True))
    attributive = rf"\b(?P<amount>(?:{words})(?:[ -](?:{words}|and))*) dollars (?P<noun>purchase|bill|fee|subscription|contribution|deposit|expense|payment)\b"
    normalized = re.sub(attributive, lambda match: match['amount'].replace(' ', '-') + '-dollar ' + match['noun'],
                        normalized, flags=re.I)
    try:
        before = [(item["value"], item["unit"]) for item in numeric_mentions(text)]
        after = [(item["value"], item["unit"]) for item in numeric_mentions(normalized)]
    except ValueError:
        return text
    # Any ambiguity in units/token boundaries stays with the existing strict gate.
    return normalized if before == after else text


def parse_number(words: list[str]) -> float:
    if not words or len(words) > 30:
        raise ValueError("Invalid spoken number")
    if len(words) == 1 and words[0][0].isdigit():
        value = float(words[0].replace(",", ""))
    elif "point" in words:
        index = words.index("point")
        tail = words[index + 1:]
        if not tail or any(w not in SMALL or SMALL[w] > 9 for w in tail):
            raise ValueError("Decimal speech must use individual digits")
        value = parse_number(words[:index]) + float("0." + "".join(str(SMALL[w]) for w in tail))
    else:
        total, group, previous_scale = 0, 0, math.inf
        for word in words:
            if word == "and":
                continue
            if word in SMALL:
                group += SMALL[word]
            elif word in TENS:
                group += TENS[word]
            elif word == "hundred":
                group = (group or 1) * 100
            elif word in SCALES:
                scale = SCALES[word]
                if scale >= previous_scale:
                    raise ValueError("Invalid order of number scales")
                total += (group or 1) * scale
                group, previous_scale = 0, scale
            else:
                raise ValueError("Unsupported number token")
        value = float(total + group)
    if not math.isfinite(value) or abs(value) > 1e12:
        raise ValueError("Number exceeds supported range")
    return value


def numeric_mentions(text: str) -> list[dict]:
    # A compound "one-time" is lexical, not a bare amount. Keep it as one
    # nonnumeric token before the general hyphen-to-space number parsing.
    normalized = re.sub(r"\bone\s*[-‐‑‒–—]\s*time\b", "onetime", text.casefold())
    tokens = TOKEN.findall(normalized.replace("-", " "))
    mentions, i = [], 0
    while i < len(tokens):
        word = tokens[i]
        # In these elliptical noun phrases, "one" means the previously
        # mentioned expense/item ("an optional one", "a recurring one").
        # Only exclude it when it has no following financial/count unit;
        # explicit one-dollar/percent/year and bare cardinal assertions remain.
        if (word == "one" and (i + 1 == len(tokens) or tokens[i + 1] not in UNITS)
                and i >= 2 and tuple(tokens[i - 2:i]) in {("an", "optional"), ("a", "recurring")}):
            i += 1
            continue
        # "point" is also ordinary English ("the point is ...").  It starts
        # a numeric token only when it follows another number word; otherwise
        # treating it as a decimal produces a false format error in narration.
        if word == "point" and (i == 0 or tokens[i - 1] not in NUMBER_WORDS):
            i += 1
            continue
        if word not in NUMBER_WORDS and not word[0].isdigit():
            i += 1
            continue
        start = i
        i += 1
        if not word[0].isdigit():
            while i < len(tokens) and (tokens[i] in NUMBER_WORDS or
                    (tokens[i] == "and" and i + 1 < len(tokens) and tokens[i + 1] in NUMBER_WORDS)):
                i += 1
        if start and tokens[start - 1] in {"$", "pln"}:
            unit = "dollars" if tokens[start - 1] == "$" else "PLN"
        else:
            unit = UNITS.get(tokens[i] if i < len(tokens) else "", "none")
        mentions.append({"value": parse_number(tokens[start:i]), "unit": unit, "phrase": " ".join(tokens[start:i])})
    return mentions
