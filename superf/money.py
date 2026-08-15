"""Spec §3.8 — the canonical money calculation.

Everything here is integer **sen** (§3.8.1). Percentages produce fractions
(70% of RM135 is RM94.50) and floating-point ringgit drifts until the zero-sum
assertion fails for reasons nobody can find. Convert to RM only for display.

The one deviation from the literal §3.8.2 signature is deliberate and is
documented in ``distribute`` below: the stake is a per-manager mapping rather
than a single ``stake_sen`` × ``N``, because §3.8.6 requires a month bucket
spanning a mid-season join to be computed gameweek by gameweek. With uniform
stakes the two are identical.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Iterable, Mapping, Sequence

SEN_PER_RM = 100


class LedgerError(AssertionError):
    """Raised when a pot does not balance. Never publish through this."""


def rm_to_sen(rm: float | int | str) -> int:
    """RM -> sen, exactly. Accepts ints, floats and decimal strings."""
    return int(Fraction(str(rm)) * SEN_PER_RM)


def sen_to_rm(sen: int) -> float:
    """sen -> RM for display. Whole ringgit stay ints; split pots keep 2dp."""
    if sen % SEN_PER_RM == 0:
        return sen // SEN_PER_RM
    return round(sen / SEN_PER_RM, 2)


def round_half_up(value: Fraction) -> int:
    """Round a non-negative Fraction to the nearest integer, .5 going up."""
    if value < 0:
        return -round_half_up(-value)
    return int((value * 2 + 1) // 2)


def share_amounts(pot_sen: int, shares: Sequence[float]) -> list[int]:
    """Split a pot into one amount per share.

    The **last share absorbs the residue** (§3.8.2). Rounding each share
    independently leaves the pot a sen or two short; handing the remainder to
    the final (smallest) share keeps the headline prizes on their advertised
    round numbers.
    """
    if not shares:
        return []
    amounts: list[int] = []
    allocated = 0
    last = len(shares) - 1
    for i, share in enumerate(shares):
        if i == last:
            amount = pot_sen - allocated
        else:
            amount = round_half_up(Fraction(pot_sen) * Fraction(str(share)))
            allocated += amount
        amounts.append(amount)
    return amounts


def distribute(
    stakes: Mapping[str, int],
    shares: Sequence[float],
    ranking: Sequence[Sequence[str]],
    order_key: Mapping[str, int],
) -> dict[str, int]:
    """The one shared routine every pot uses (§3.8.2).

    Args:
        stakes: ``{manager: stake_sen}``. Everyone in this mapping pays in, and
            the pot is the sum. Uniform values reproduce §3.8.2's
            ``stake_sen × N`` exactly; differing values express §3.8.6's
            per-gameweek ``N`` inside a single month bucket.
        shares: e.g. ``[0.60, 0.25, 0.15]``. Ordered, summing to 1.
        ranking: finishing order as groups, e.g. ``[["a"], ["b", "c"], ["d"]]``.
            An inner list longer than one is a tie that survived all four
            tiebreak levels (§3.5), so those managers **split** the combined
            money of the places they jointly occupy — the only reading that
            keeps the pot zero-sum.
        order_key: ``{manager: entry_id}``. Leftover sen in a split go to the
            earliest entry_id, so a rerun is byte-identical (§3.8.2).

    Returns:
        ``{manager: net_sen}``, net of the payee's own stake, summing to zero.
    """
    ledger: dict[str, int] = {m: -s for m, s in stakes.items()}
    if not ranking:
        # No winner — a postponed or cancelled round pays no pot (§10), and
        # therefore charges no stake either.
        return {m: 0 for m in stakes}

    for group in ranking:
        for m in group:
            if m not in ledger:
                raise LedgerError(f"{m!r} is ranked but paid no stake")
            if m not in order_key:
                raise LedgerError(f"{m!r} has no entry_id, so a split pot could not be ordered")

    pot = sum(stakes.values())
    ranked_count = sum(len(g) for g in ranking)
    # Truncate to the number of places actually contested, so the residue rule
    # still lands on a share somebody can be paid.
    usable = list(shares)[: min(len(shares), ranked_count)]
    amounts = share_amounts(pot, usable)

    idx = 0
    for group in ranking:
        if idx >= len(amounts):
            break
        claimed = amounts[idx : idx + len(group)]
        idx += len(group)
        amount = sum(claimed)
        if amount == 0:
            continue
        tied = sorted(group, key=lambda m: order_key[m])
        base, extra = divmod(amount, len(tied))
        for j, m in enumerate(tied):
            ledger[m] += base + (1 if j < extra else 0)

    total = sum(ledger.values())
    if total != 0:
        raise LedgerError(
            f"pot does not balance: residue {total} sen "
            f"(pot={pot}, stakes={dict(stakes)}, ranking={list(ranking)})"
        )
    return ledger


def advertised_net(stake_sen: int, n: int, shares: Sequence[float]) -> list[int]:
    """The headline prize for each place, net of the winner's own stake.

    Derived by running :func:`distribute` on a synthetic field of ``n`` managers
    with no ties, so the numbers printed in "How the money works" (§7.2F) can
    never disagree with the engine that actually pays them out.
    """
    if n <= 0:
        return []
    managers = [f"m{i}" for i in range(n)]
    stakes = {m: stake_sen for m in managers}
    order = {m: i for i, m in enumerate(managers)}
    ledger = distribute(stakes, shares, [[m] for m in managers], order)
    return [ledger[m] for m in managers[: len(shares)]]


def minimum_transfers(balances: Mapping[str, int]) -> list[dict]:
    """§3.9.3 — the minimal payment set that squares the book.

    The book is zero-sum, so debtors owe exactly what creditors are owed. Do not
    make eight people pay eight people: match the largest debt against the
    largest credit, which never exceeds N-1 payments.

    Sorting by amount *then id* makes the output deterministic, so the
    settlement sheet is reproducible run to run.
    """
    total = sum(balances.values())
    if total != 0:
        raise LedgerError(f"cannot settle a book that does not balance: residue {total} sen")

    debtors = sorted(
        ([m, -v] for m, v in balances.items() if v < 0), key=lambda kv: (-kv[1], kv[0])
    )
    creditors = sorted(
        ([m, v] for m, v in balances.items() if v > 0), key=lambda kv: (-kv[1], kv[0])
    )

    payments: list[dict] = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        if amount > 0:
            payments.append({"from": debtors[i][0], "to": creditors[j][0], "amount": amount})
        debtors[i][1] -= amount
        creditors[j][1] -= amount
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return payments


def assert_zero_sum(ledger: Mapping[str, int], label: str) -> None:
    """§3.8.5. A ledger that does not balance is never published."""
    total = sum(ledger.values())
    if total != 0:
        raise LedgerError(f"{label} does not balance: residue {total} sen")


def combine(ledgers: Iterable[Mapping[str, int]], managers: Iterable[str]) -> dict[str, int]:
    """Sum several ledgers into one, keeping every manager present at zero."""
    out = {m: 0 for m in managers}
    for ledger in ledgers:
        for m, v in ledger.items():
            out[m] = out.get(m, 0) + v
    return out
