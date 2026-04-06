"""Case-insensitive substring matching of keywords against folder text."""

from __future__ import annotations

from dataclasses import dataclass


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

    @property
    def matched_keywords(self) -> list[str]:
        return [kw for kw, _ in self.keyword_hits]


def ranked_folder_matches(
    folder_text: dict[str, str],
    keywords: list[str],
) -> list[RankedFolderMatch]:
    """
    Match folders, then sort by:
    1. total_occurrences descending (more total hits = higher),
    2. distinct_match_count descending (more different keywords matched),
    3. weighted_rank_avg descending (keywords higher in the list weigh more per occurrence),
    4. folder_key alphabetically (stable tie-break).
    """
    kws = normalize_keywords(keywords)
    if not kws:
        return []

    weights = _keyword_weights(kws)
    rows: list[RankedFolderMatch] = []

    for folder, text in folder_text.items():
        hay = text.casefold()
        hits: list[tuple[str, int]] = []
        for kw in kws:
            n = _occurrences_casefold(hay, kw.casefold())
            if n > 0:
                hits.append((kw, n))
        if not hits:
            continue
        tup = tuple(hits)
        total_occ = sum(c for _, c in tup)
        distinct = len(tup)
        weighted = sum(weights[kw] * c for kw, c in tup) / total_occ
        rows.append(
            RankedFolderMatch(
                folder_key=folder,
                keyword_hits=tup,
                total_occurrences=total_occ,
                distinct_match_count=distinct,
                weighted_rank_avg=weighted,
            )
        )

    rows.sort(
        key=lambda r: (
            -r.total_occurrences,
            -r.distinct_match_count,
            -r.weighted_rank_avg,
            r.folder_key.lower(),
        ),
    )
    return rows


def match_folders(folder_text: dict[str, str], keywords: list[str]) -> dict[str, list[str]]:
    """
    folder_text: relative folder path string (posix) -> aggregated plain text
    Returns: folder path -> matched keywords (iteration order: ranked list order).
    """
    return {
        r.folder_key: r.matched_keywords
        for r in ranked_folder_matches(folder_text, keywords)
    }
