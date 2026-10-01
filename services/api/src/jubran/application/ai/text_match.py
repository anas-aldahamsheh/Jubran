"""Plain name matching for menu items (Arabic-aware), used next to semantic search.

Short, exact dish names ("شقف", "ريش") can score low against long bilingual
documents in vector search. Matching the name text directly catches them.
"""
import re

_TASHKEEL = re.compile(r"[ً-ْٰـ]")  # harakat, dagger alef, tatweel
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_LETTER_FORMS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"})


def _token(word: str) -> str:
    # "الحمص" and "حمص" are the same dish; keep short words such as "الا" intact.
    if word.startswith("ال") and len(word) > 4:
        return word[2:]
    return word


def normalize(text: str) -> str:
    text = _TASHKEEL.sub("", (text or "").casefold()).translate(_LETTER_FORMS)
    words = [_token(word) for word in _NON_WORD.sub(" ", text).split()]
    return " ".join(word for word in words if word)


def contains_name(name: str, other: str) -> bool:
    """True when ``other`` is ``name`` plus more words: its variations ("حمص" -> "حمص بالصنوبر")."""
    n, o = normalize(name), normalize(other)
    return bool(n) and n != o and f" {n} " in f" {o} "


def name_matches(query: str, *names: str) -> bool:
    """True when the dish name appears in the question, or the question is part of the name."""
    q = normalize(query)
    if not q:
        return False
    padded_query = f" {q} "
    for name in names:
        n = normalize(name)
        if not n:
            continue
        if f" {n} " in padded_query:
            return True
        if len(q) >= 3 and f" {q} " in f" {n} ":
            return True
    return False
