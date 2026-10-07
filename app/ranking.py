"""Turn App Store suggestion lists into one popularity scale.

Every suggestion list is Apple's own popularity order of the queries that
match a prefix. App Store matches the prefix against the start of *any* word
("music" suggests "youtube music"), so lists overlap across first letters and
chain the whole market together.

We fit a Plackett-Luce model: each query i has a strength θ_i, and a list
c_1, c_2, ... c_k for prefix p is read as "c_1 was picked first among all
queries matching p, then c_2 among the rest, ..." — queries we know match p
but that did not make the list count as losers at every step. Strengths are
estimated with Hunter's MM algorithm (2004) with a weak prior that keeps
queries seen only once finite. θ ratios estimate relative search popularity.
"""

from __future__ import annotations

import re
from collections import defaultdict

import numpy as np

FULL_PAGE = 10
PRIOR = 0.5          # virtual win + loss against a reference query of strength 1
ITERATIONS = 150
MAX_LOSERS = 400     # cap on known-but-unlisted queries per list (speed)

_TOKEN_SPLIT = re.compile(r"[\s\-–—:._/|&,+()!?\"'«»]+")


def tokens(term: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(term) if t]


def matches(term: str, prefix: str) -> bool:
    return term.startswith(prefix) or any(t.startswith(prefix) for t in tokens(term))


def fit(lists: list[tuple[str, list[str]]]) -> dict[str, float]:
    """lists: (prefix, suggestions in Apple's order). Returns term -> strength."""
    terms = sorted({t for _, ts in lists for t in ts})
    if not terms:
        return {}
    index = {t: i for i, t in enumerate(terms)}

    # Word-prefix index to find known queries that match a prefix.
    by_prefix: dict[str, set[int]] = defaultdict(set)
    longest = max((len(p) for p, _ in lists), default=1)
    for t, i in index.items():
        for word in {t, *tokens(t)}:
            for n in range(1, min(len(word), longest) + 1):
                by_prefix[word[:n]].add(i)

    prepared = []
    wins = np.zeros(len(terms))
    for prefix, ts in lists:
        if not ts:
            continue
        chosen = np.array([index[t] for t in ts])
        if len(ts) >= FULL_PAGE:
            losers = by_prefix.get(prefix, set()) - set(chosen.tolist())
            losers = np.array(sorted(losers)[:MAX_LOSERS], dtype=int)
        else:
            losers = np.array([], dtype=int)  # Apple showed every match
        prepared.append((chosen, losers))
        np.add.at(wins, chosen, 1)

    theta = np.ones(len(terms))
    for _ in range(ITERATIONS):
        denom = np.zeros(len(terms))
        for chosen, losers in prepared:
            loser_sum = theta[losers].sum() if losers.size else 0.0
            # Choice set at step s = chosen[s:] + losers.
            remaining = np.cumsum(theta[chosen][::-1])[::-1] + loser_sum
            inv = 1.0 / remaining
            np.add.at(denom, chosen, np.cumsum(inv))
            if losers.size:
                denom[losers] += inv.sum()
        new = (wins + PRIOR) / (denom + 2 * PRIOR / (theta + 1))
        if np.max(np.abs(np.log(new) - np.log(theta))) < 1e-4:
            theta = new
            break
        theta = new
    return dict(zip(terms, theta.tolist()))


def bridge_words(lists: list[tuple[str, list[str]]], probed: set[str], limit: int) -> list[str]:
    """Words that appear inside many queries (not only at the start): probing
    them yields lists that mix queries with different first letters."""
    inner: dict[str, int] = defaultdict(int)
    for _, ts in lists:
        for t in set(ts):
            for w in tokens(t)[1:]:
                if len(w) >= 3 and not w.isdigit():
                    inner[w] += 1
    ranked = sorted(inner.items(), key=lambda kv: -kv[1])
    return [w for w, n in ranked if n >= 2 and w not in probed][:limit]
