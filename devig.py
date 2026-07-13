"""
devig.py  --  strip the bookmaker's margin out of odds to get TRUE probability.

A bookmaker's odds always imply probabilities that sum to MORE than 100%
(the extra is their margin / "vig" / "overround"). To find the true probability
we must remove that margin.

   Raw implied:  p_raw_i = 1 / odds_i        (these sum to > 1)
   Overround  :  sum(p_raw_i)                (e.g. 1.05 = 5% margin)

We support two de-vig methods:

   multiplicative : p_i = p_raw_i / overround     (simple, fast, default)
   power          : p_i = p_raw_i ** k, solve k so sum == 1
                    (more accurate -- favourites are less over-priced than
                     longshots, and the power method captures that skew)

The power method is what sharp bettors prefer for h2h markets.
"""
from __future__ import annotations


def _raw_inverse(odds: dict[str, float]) -> dict[str, float]:
    """1/odds for each valid outcome."""
    return {k: 1.0 / v for k, v in odds.items() if v and v > 1.0}


def overround(odds: dict[str, float]) -> float:
    """How much over 100% the book's implied probabilities sum to."""
    return sum(_raw_inverse(odds).values())


def multiplicative(odds: dict[str, float]) -> dict[str, float]:
    """Normalise raw implied probabilities so they sum to 1."""
    inv = _raw_inverse(odds)
    s = sum(inv.values())
    if s <= 0:
        return {}
    return {k: v / s for k, v in inv.items()}


def power(odds: dict[str, float], tol: float = 1e-9, max_iter: int = 100) -> dict[str, float]:
    """
    Power de-vig: find exponent k such that sum(p_raw_i ** k) == 1.
    Solved by bisection. Falls back to multiplicative if it can't converge.
    """
    inv = _raw_inverse(odds)
    if not inv:
        return {}
    probs = list(inv.values())

    def total(k: float) -> float:
        return sum(p ** k for p in probs)

    # k=1 gives the overround (>1). Larger k shrinks the sum. Bracket the root.
    lo, hi = 0.5, 5.0
    if total(hi) > 1.0:           # margin so large even k=5 won't reach 1
        return multiplicative(odds)

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        s = total(mid)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    return {key: (p ** k) for key, p in inv.items()}


METHODS = {"multiplicative": multiplicative, "power": power}


def fair_probs(odds: dict[str, float], method: str = "power") -> dict[str, float]:
    """Public entry point. Returns {outcome: true_probability}."""
    fn = METHODS.get(method, power)
    return fn(odds)


def fair_odds(odds: dict[str, float], method: str = "power") -> dict[str, float]:
    """True (no-margin) decimal odds = 1 / fair_prob."""
    return {k: (1.0 / p if p > 0 else float("inf"))
            for k, p in fair_probs(odds, method).items()}
