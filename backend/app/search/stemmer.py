"""Lightweight zero-dependency Russian (Snowball/Porter2-RU) + English stemmer."""

from __future__ import annotations

import re

_VOWELS = "аеиоуыэюя"
_RV_RE = re.compile(r"^(.*?[аеиоуыэюя])(.*)$")
_R1_RE = re.compile(r"^.*?[аеиоуыэюя][^аеиоуыэюя](.*)$")

_PERFECTIVE_GROUND_1 = re.compile(r"((?<=[ая])(в|вши|вшись))$")
_PERFECTIVE_GROUND_2 = re.compile(r"(ив|ивши|ившись|ыв|ывши|ывшись)$")
_REFLEXIVE = re.compile(r"(ся|сь)$")
_ADJECTIVE = re.compile(
    r"(ее|ие|ые|ое|ими|ыми|ей|ий|ый|ой|ем|им|ым|ом|его|ого|ему|ому|их|ых|ую|юю|ая|яя|ою|ею)$"
)
_PARTICIPLE_1 = re.compile(r"((?<=[ая])(ем|нн|вш|ющ|щ))$")
_PARTICIPLE_2 = re.compile(r"(ивш|ывш|ующ)$")
_VERB_1 = re.compile(
    r"((?<=[ая])(ла|на|ете|йте|ли|й|л|ем|н|ло|но|ет|ют|ны|ть|ешь|нно))$"
)
_VERB_2 = re.compile(
    r"(ила|ыла|ена|ейте|уйте|ите|или|ыли|ей|уй|ил|ыл|им|ым|ен|ило|ыло|ено|ят|ует|уют|ит|ыт|ены|ить|ыть|ишь|ую|ю)$"
)
_NOUN = re.compile(
    r"(а|ев|ов|ие|ье|е|иями|ями|ами|еи|ии|и|ией|ей|ой|ий|й|иям|ям|ием|ем|ам|ом|о|у|ах|иях|ях|ы|ь|ию|ью|ю|ия|ья|я)$"
)
_I_END = re.compile(r"и$")
_DERIVATIONAL = re.compile(r"(ость|ост)$")
_SUPERLATIVE = re.compile(r"(ейше|ейш)$")
_NN_END = re.compile(r"нн$")
_SOFT_SIGN = re.compile(r"ь$")

_CYRILLIC_RE = re.compile(r"[а-я]")
_WORD_RE = re.compile(r"[0-9a-zа-яё]{2,}", re.IGNORECASE)

# High-frequency Russian suppletive / alternating verb roots -> canonical stem + SQL prefixes
_IRREGULAR_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("найд", ("найд", "найт", "нашел", "нашл", "наход")),
    ("пойд", ("пойд", "пойт", "пошел", "пошл")),
    ("прид", ("прид", "прийт", "пришел", "пришл", "приход")),
    ("выд", ("выйд", "выйт", "вышел", "вышл", "выход")),
    ("возьм", ("возьм", "взят", "взял")),
)


def normalize_text(text: str) -> str:
    """Lowercase and normalize Cyrillic ё -> е."""
    return (text or "").lower().replace("ё", "е")


def _match_irregular(word: str) -> str | None:
    for canonical, prefixes in _IRREGULAR_GROUPS:
        for pref in prefixes:
            if word.startswith(pref):
                return canonical
    return None


def irregular_prefixes_for(stem: str) -> tuple[str, ...]:
    """Return SQL-searchable root prefixes for irregular canonical stems."""
    for canonical, prefixes in _IRREGULAR_GROUPS:
        if stem == canonical:
            return prefixes
    return (stem,)


def stem_russian(word: str) -> str:
    """Apply Snowball Russian stemming to a normalized lowercase Russian word."""
    w = normalize_text(word)
    if len(w) <= 3:
        return w

    irreg = _match_irregular(w)
    if irreg:
        return irreg

    m_rv = _RV_RE.match(w)
    if not m_rv:
        return w
    head, rv = m_rv.group(1), m_rv.group(2)
    if not rv:
        return w

    m_r1 = _R1_RE.match(w)
    r1 = m_r1.group(1) if m_r1 else ""
    m_r2 = _R1_RE.match(r1) if r1 else None
    r2 = m_r2.group(1) if m_r2 else ""
    r2_offset = len(w) - len(r2)

    # Step 1
    new_rv = _PERFECTIVE_GROUND_1.sub("", rv)
    if new_rv == rv:
        new_rv = _PERFECTIVE_GROUND_2.sub("", rv)
    if new_rv != rv:
        rv = new_rv
    else:
        rv = _REFLEXIVE.sub("", rv)
        adj_rv = _ADJECTIVE.sub("", rv)
        if adj_rv != rv:
            rv = adj_rv
            part_rv = _PARTICIPLE_1.sub("", rv)
            if part_rv == rv:
                part_rv = _PARTICIPLE_2.sub("", rv)
            rv = part_rv
        else:
            verb_rv = _VERB_1.sub("", rv)
            if verb_rv == rv:
                verb_rv = _VERB_2.sub("", rv)
            if verb_rv != rv:
                rv = verb_rv
            else:
                rv = _NOUN.sub("", rv)

    # Step 2
    rv = _I_END.sub("", rv)

    # Step 3: derivational suffix only if fully inside R2
    full = head + rv
    if len(full) > r2_offset:
        in_r2 = full[r2_offset:]
        m_der = _DERIVATIONAL.search(in_r2)
        if m_der:
            rv = rv[: len(rv) - len(m_der.group(0))]

    # Step 4
    if _NN_END.search(rv):
        rv = rv[:-1]
    else:
        sup_rv = _SUPERLATIVE.sub("", rv)
        if sup_rv != rv:
            rv = sup_rv
            if _NN_END.search(rv):
                rv = rv[:-1]
        else:
            rv = _SOFT_SIGN.sub("", rv)

    result = head + rv
    return result if len(result) >= 2 else w


def stem_english(word: str) -> str:
    """Lightweight suffix stemmer for English words."""
    w = word.lower()
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "xes", "zes", "sses")) and len(w) > 5:
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        return w[:-1]
    if w.endswith("ing") and len(w) > 5:
        base = w[:-3]
        if len(base) >= 2 and base[-1] == base[-2]:
            base = base[:-1]
        return base
    if w.endswith("ed") and len(w) > 4:
        base = w[:-2]
        if len(base) >= 2 and base[-1] == base[-2]:
            base = base[:-1]
        return base
    return w


def stem_word(word: str) -> str:
    """Stem a single Russian or English word with ё->е normalization."""
    w = normalize_text(word)
    if not w:
        return ""
    if _CYRILLIC_RE.search(w):
        return stem_russian(w)
    if w.isascii() and w.isalpha():
        return stem_english(w)
    return w


def tokenize_and_stem(text: str) -> set[str]:
    """Return both normalized tokens and their stems for morphological matching."""
    norm = normalize_text(text)
    out: set[str] = set()
    for w in _WORD_RE.findall(norm):
        out.add(w)
        s = stem_word(w)
        if s:
            out.add(s)
    return out


def sql_like_variants(stem_or_token: str) -> list[str]:
    """
    Build SQLite-safe LIKE patterns covering Cyrillic case-sensitivity and ё/е variants.
    SQLite LIKE is case-insensitive only for ASCII, not Unicode/Cyrillic.
    """
    base = normalize_text(stem_or_token).strip()
    if not base:
        return []
    roots = list(irregular_prefixes_for(base))
    if base not in roots and len(base) >= 3:
        roots.append(base)

    variants: list[str] = []
    seen: set[str] = set()

    def _add(val: str) -> None:
        if val and val not in seen:
            seen.add(val)
            variants.append(f"%{val}%")

    for root in roots:
        if len(root) < 2:
            continue
        _add(root)
        if _CYRILLIC_RE.search(root):
            _add(root.capitalize())
            _add(root.upper())
            if "е" in root:
                yo = root.replace("е", "ё")
                _add(yo)
                _add(yo.capitalize())
    return variants
