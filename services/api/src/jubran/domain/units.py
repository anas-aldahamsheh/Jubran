"""How a dish is counted: by the plate, the glass, the box, the piece...

The admin may give a dish one unit or several, written once in any language («صحن»,
or «صحن» and «علبة» for a dish that also comes in a box). A dish without any gets an
automatic unit: a unit word in its name first («فلافل الحبة الواحدة»), then the kind of
dish (water comes in a bottle, soft drinks in a can, tea in a glass), then its menu
section, else a plate. Every unit of a dish has the dish's price.

Basket and order lines carry the unit the guest chose. Screens show it in Arabic or
English: the common units are translated here, any other unit shows as written. The
assistant translates and inflects units itself, in any language.
"""
import json
import re
from typing import Iterable, List, Optional, Tuple

MAX_UNITS = 6
MAX_UNIT_LENGTH = 40
DEFAULT_UNIT = "صحن"

# Arabic name, English name, and other ways to write it (spellings, plurals, English words).
COMMON_UNITS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("صحن", "plate", ("صحون", "طبق", "أطباق", "plate", "plates", "dish")),
    ("كاسة", "glass", ("كاسات", "كاس", "كأس", "glass", "glasses")),
    ("كوب", "cup", ("أكواب", "cup", "cups", "mug", "mugs")),
    ("فنجان", "cup", ("فناجين",)),
    ("علبة", "box", ("علب", "box", "boxes", "can", "cans", "pack", "packs")),
    ("قنينة", "bottle", ("قناني", "زجاجة", "زجاجات", "bottle", "bottles")),
    ("حبة", "piece", ("حبات", "piece", "pieces", "pc", "pcs")),
    ("قطعة", "piece", ("قطع", "شقفة", "شقف")),
    ("ساندويشة", "sandwich", ("ساندويشات", "ساندويش", "سندويشة", "سندويش", "سندويشات", "ساندويتش",
                              "sandwich", "sandwiches")),
    ("رغيف", "loaf", ("أرغفة", "loaf", "loaves")),
    ("إبريق", "pot", ("أباريق", "teapot", "teapots")),
    ("زبدية", "bowl", ("bowl", "bowls")),
    ("سيخ", "skewer", ("أسياخ", "skewer", "skewers")),
    ("وجبة", "meal", ("وجبات", "meal", "meals")),
    ("كيلو", "kilo", ("كغ", "kg", "kilo", "kilos")),
)

# What a dish is, from words in its name: (words, unit). Checked in this order, so
# sparkling water is a bottle of water, not a can of soft drink.
_KINDS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("مياه", "ماء", "مي", "water"), "قنينة"),
    (("غازي", "غازية", "غازيات", "كولا", "بيبسي", "ميرندا", "سفن", "سبرايت", "فانتا",
      "soda", "cola", "pepsi", "sprite", "fanta", "soft"), "علبة"),
    (("قهوة", "اسبريسو", "coffee", "espresso"), "فنجان"),
    (("نسكافيه", "كابتشينو", "كابوتشينو", "لاتيه", "موكا", "nescafe", "cappuccino", "latte", "mocha"), "كوب"),
    (("شاي", "عصير", "عصائر", "ليموناضة", "ليمونادا", "سحلب", "عيران", "كوكتيل", "سموذي",
      "tea", "juice", "lemonade", "milkshake", "smoothie", "cocktail", "ayran", "sahlab"), "كاسة"),
    (("خبز", "bread"), "رغيف"),
)
# Menu sections: (words, unit).
_SECTIONS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("مشروبات", "مشروب", "عصائر", "drinks", "drink", "beverages", "juices"), "كاسة"),
    (("سندويشات", "ساندويشات", "سندويش", "ساندويش", "sandwiches", "sandwich", "wraps"), "ساندويشة"),
)

_TASHKEEL = re.compile(r"[ً-ْٰـ]")
_LETTER_FORMS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي"})
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_ARABIC = re.compile(r"[؀-ۿ]")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩<>]")


def _words(text: Optional[str]) -> List[str]:
    """Words of a text, written plainly: no diacritics, one form of each letter, "ال" dropped."""
    text = _TASHKEEL.sub("", (text or "").casefold()).translate(_LETTER_FORMS)
    return [word[2:] if word.startswith("ال") and len(word) > 4 else word
            for word in _NON_WORD.sub(" ", text).split()]


def _key(text: Optional[str]) -> str:
    return " ".join(_words(text))


_UNIT_FORMS = {}
for _arabic, _english, _others in COMMON_UNITS:
    for _form in (_arabic, *_others):
        _UNIT_FORMS.setdefault(_key(_form), (_arabic, _english))
_KIND_WORDS = tuple(({_key(word) for word in words}, unit) for words, unit in _KINDS)
_SECTION_WORDS = tuple(({_key(word) for word in words}, unit) for words, unit in _SECTIONS)
_DRINK_WORDS = set().union(*(words for words, unit in _KIND_WORDS if unit != "رغيف"),
                           *(words for words, unit in _SECTION_WORDS if unit == "كاسة"))


def common_unit(unit: Optional[str]) -> Optional[Tuple[str, str]]:
    """(Arabic, English) of a common unit written any known way ("plate", "صحون"), else None."""
    return _UNIT_FORMS.get(_key(unit))


def automatic_unit(name_ar: str, name_en: str, category_ar: str = "", category_en: str = "") -> str:
    """The unit of a dish the admin gave none: from its name, then its kind, then its section."""
    name = _words(f"{name_ar} {name_en}")
    for word in name:
        if word in _UNIT_FORMS:
            return _UNIT_FORMS[word][0]
    for words, unit in _KIND_WORDS:
        if words.intersection(name):
            return unit
    section = set(_words(f"{category_ar} {category_en}"))
    for words, unit in _SECTION_WORDS:
        if section & words:
            return unit
    return DEFAULT_UNIT


def is_drink(*texts: Optional[str]) -> bool:
    """A drink, judging by its name or section (a box of a drink is a can)."""
    return bool(set(_words(" ".join(text or "" for text in texts))) & _DRINK_WORDS)


def clean_units(values: Iterable[str]) -> List[str]:
    """The admin's units: trimmed, single spaces, no hidden characters, no repeats (also not
    the same unit written another way, such as «صحن» and "plate"), and not just symbols."""
    cleaned: List[str] = []
    seen = set()
    for value in values:
        unit = " ".join(_UNSAFE.sub("", str(value or "")).split())
        common = common_unit(unit)
        key = common[0] if common else _key(unit)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(unit)
    return cleaned


def saved_units(units_json: Optional[str]) -> List[str]:
    """The units the admin saved for a dish ([] when it is automatic)."""
    try:
        values = json.loads(units_json) if units_json else []
    except ValueError:
        return []
    return clean_units(value for value in values if isinstance(value, str)) if isinstance(values, list) else []


def units_to_json(units: Iterable[str]) -> Optional[str]:
    cleaned = clean_units(units)
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def serving_units(saved_json: Optional[str], name_ar: str, name_en: str,
                  category_ar: str = "", category_en: str = "") -> List[str]:
    """The ways a dish can be ordered: the admin's units, else its automatic unit."""
    return saved_units(saved_json) or [automatic_unit(name_ar, name_en, category_ar, category_en)]


def resolve_unit(requested: Optional[str], units: List[str]) -> Optional[str]:
    """Which of the dish's units ``requested`` means, however it was written ("plate",
    "صحون", "box" for «علبة"), or None."""
    if not requested:
        return None
    if requested in units:
        return requested
    key = _key(requested)
    for unit in units:
        if _key(unit) == key:
            return unit
    wanted = common_unit(requested)
    if wanted is not None:
        for position in (0, 1):  # the same unit, else the same unit in English ("piece")
            for unit in units:
                found = common_unit(unit)
                if found is not None and found[position] == wanted[position]:
                    return unit
    return None


def choose_unit(requested: Optional[str], units: List[str]) -> Optional[str]:
    """The unit of a new line: the only unit a dish has, or the one asked for (None: unclear)."""
    return units[0] if len(units) == 1 else resolve_unit(requested, units)


def line_unit(stored: Optional[str], units: List[str]) -> str:
    """The unit of an existing line: as chosen while the dish still offers it, else its first."""
    return resolve_unit(stored, units) or units[0]


def unit_label(unit: Optional[str], language: str, drink: bool = False) -> Optional[str]:
    """A unit in Arabic or English for the screens: as written when it is already in that
    language or not a common unit, otherwise translated."""
    if not unit:
        return None
    if bool(_ARABIC.search(unit)) == (language == "ar"):
        return unit
    common = common_unit(unit)
    if common is None:
        return unit
    if language == "ar":
        return common[0]
    return "can" if common[0] == "علبة" and drink else common[1]


def unit_labels(unit: Optional[str], drink: bool = False) -> dict:
    """The display fields of a line's unit."""
    return {"unit": unit, "unit_display_ar": unit_label(unit, "ar", drink),
            "unit_display_en": unit_label(unit, "en", drink)}
