"""Recognize explicit nearby category searches without choosing a particular place."""
import re

from prompting import fold


# Categories are ordinary vocabulary, not named POIs or evaluated utterances.
_CATEGORIES = [
    ("편의점", "other", {"ko": ("편의점",), "en": ("convenience store",), "es": ("tienda de conveniencia",)}),
    ("주유소", "gas_station", {"ko": ("주유소",), "en": ("gas station", "petrol station"), "es": ("gasolinera",)}),
    ("충전소", "other", {"ko": ("충전소", "전기차 충전소"), "en": ("charging station", "EV charging station"),
                     "es": ("estación de carga", "punto de recarga")}),
    ("주차장", "other", {"ko": ("주차장",), "en": ("parking lot", "car park"), "es": ("estacionamiento", "aparcamiento")}),
    ("약국", "other", {"ko": ("약국",), "en": ("pharmacy",), "es": ("farmacia",)}),
    ("세탁소", "other", {"ko": ("세탁소",), "en": ("laundromat", "laundry"), "es": ("lavandería",)}),
]
_ALIASES = {
    lang: {fold(alias).casefold(): (korean, category) for korean, category, names in _CATEGORIES
           for alias in names[lang]}
    for lang in ("ko", "en", "es")
}
_GRAMMARS = {
    "ko": [r"(?:(?:이\s*)?근처|가까운)\s*{category}(?:을|를)?\s*(?:좀\s*)?"
           r"찾아\s*(?:줘(?:요)?|주세요|주시겠어요|주실래요|주십시오)"],
    "en": [r"(?:please\s+)?(?:find|locate|search\s+for)\s+(?:an?\s+)?{category}\s+"
           r"(?:near\s+here|nearby)(?:,?\s+please)?",
           r"(?:please\s+)?(?:find|locate|search\s+for)\s+(?:(?:a|an|the)\s+)?"
           r"(?:nearby|nearest|closest)\s+{category}(?:,?\s+please)?"],
    "es": [r"(?:por\s+favor,?\s+)?(?:busque|busca|encuentre)\s+(?:un|una)\s+{category}\s+"
           r"cerca(?:\s+de\s+aqui)?(?:,?\s+por\s+favor)?"],
}
_PATTERNS = {
    lang: [re.compile(grammar.format(category="(?P<category>" + "|".join(
        re.escape(alias) for alias in sorted(_ALIASES[lang], key=len, reverse=True)) + ")"), re.I)
           for grammar in grammars]
    for lang, grammars in _GRAMMARS.items()
}


def nearby_category_request(text: str, locale: str) -> dict | None:
    """Only complete affirmative search requests match; names and past/negated clauses do not."""
    lang = locale.split("-", 1)[0]
    utterance = text.strip().rstrip(".!?。？！").rstrip()
    for pattern in _PATTERNS.get(lang, ()):
        match = pattern.fullmatch(fold(utterance))
        if match:
            korean, category = _ALIASES[lang][match["category"].casefold()]
            return {"intent": "set_destination",
                    "query_original": utterance[match.start("category"):match.end("category")],
                    "query_ko_candidate": korean, "region_hint": None,
                    "category_hint": category, "needs_confirmation": True}
    return None
