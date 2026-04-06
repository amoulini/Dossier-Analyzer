"""Case-insensitive substring matching of keywords against folder text."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordEntry:
    """One search keyword and its positivity grade (0 = not positive … 5 = very positive)."""

    text: str
    positivity: int = 3

    def __post_init__(self) -> None:
        p = int(self.positivity)
        if p < 0:
            p = 0
        elif p > 5:
            p = 5
        object.__setattr__(self, "positivity", p)


def normalize_keywords(keywords: list[str]) -> list[str]:
    """Normalize keywords to a list of unique, case-insensitive strings (order preserved)."""
    seen: set[str] = set()
    out: list[str] = []
    for k in keywords:
        s = (k or "").strip()
        if not s:
            continue
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def normalize_keyword_entries(entries: list[KeywordEntry]) -> list[KeywordEntry]:
    """Same as keyword normalization, but keeps one positivity per unique keyword (first row wins)."""
    seen: set[str] = set()
    out: list[KeywordEntry] = []
    for e in entries:
        s = (e.text or "").strip()
        if not s:
            continue
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(KeywordEntry(s, e.positivity))
    return out


def _keyword_weights(normalized_kws: list[str]) -> dict[str, float]:
    """First keyword (top of list) has the highest weight: n, n-1, …, 1."""
    n = len(normalized_kws)
    return {normalized_kws[i]: float(n - i) for i in range(n)}


def _occurrences_casefold(haystack_cf: str, needle_cf: str) -> int:
    """Non-overlapping substring count, both arguments already casefolded."""
    if not needle_cf:
        return 0
    return haystack_cf.count(needle_cf)


@dataclass(frozen=True)
class RankedFolderMatch:
    folder_key: str
    #: (keyword, occurrence_count), only keywords with count ≥ 1, in query order
    keyword_hits: tuple[tuple[str, int], ...]
    #: Sum of occurrence counts over all matched keywords
    total_occurrences: int
    #: Number of distinct keywords with at least one hit
    distinct_match_count: int
    #: sum(weight(kw) * count) / total_occurrences — higher when top-ranked keywords dominate
    weighted_rank_avg: float
    #: sum(positivity(kw) * count) / total_occurrences — average positivity per hit (0..5)
    positivity_weighted_avg: float

    @property
    def matched_keywords(self) -> list[str]:
        return [kw for kw, _ in self.keyword_hits]


def ranked_folder_matches(
    folder_text: dict[str, str],
    entries: list[KeywordEntry],
) -> list[RankedFolderMatch]:
    """
    Match folders, then sort by:
    1. positivity_weighted_avg descending (matches on more positive keywords rank higher),
    2. total_occurrences descending,
    3. distinct_match_count descending,
    4. weighted_rank_avg descending (keywords higher in the list weigh more per occurrence),
    5. folder_key alphabetically (stable tie-break).
    """
    kws = normalize_keyword_entries(entries)
    if not kws:
        return []

    kw_texts = [e.text for e in kws]
    weights = _keyword_weights(kw_texts)
    pos_by_kw: dict[str, int] = {e.text: e.positivity for e in kws}
    rows: list[RankedFolderMatch] = []

    for folder, text in folder_text.items():
        hay = text.casefold()
        hits: list[tuple[str, int]] = []
        for e in kws:
            kw = e.text
            n = _occurrences_casefold(hay, kw.casefold())
            if n > 0:
                hits.append((kw, n))
        if not hits:
            continue
        tup = tuple(hits)
        total_occ = sum(c for _, c in tup)
        distinct = len(tup)
        weighted = sum(weights[kw] * c for kw, c in tup) / total_occ
        pos_avg = sum(pos_by_kw[kw] * c for kw, c in tup) / total_occ
        rows.append(
            RankedFolderMatch(
                folder_key=folder,
                keyword_hits=tup,
                total_occurrences=total_occ,
                distinct_match_count=distinct,
                weighted_rank_avg=weighted,
                positivity_weighted_avg=pos_avg,
            )
        )

    rows.sort(
        key=lambda r: (
            -r.positivity_weighted_avg,
            -r.total_occurrences,
            -r.distinct_match_count,
            -r.weighted_rank_avg,
            r.folder_key.lower(),
        ),
    )
    return rows


def match_folders(folder_text: dict[str, str], entries: list[KeywordEntry]) -> dict[str, list[str]]:
    """
    folder_text: relative folder path string (posix) -> aggregated plain text
    Returns: folder path -> matched keywords (iteration order: ranked list order).
    """
    return {
        r.folder_key: r.matched_keywords
        for r in ranked_folder_matches(folder_text, entries)
    }
