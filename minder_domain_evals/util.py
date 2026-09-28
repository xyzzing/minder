"""Exact arithmetic and calendar helpers shared by oracles."""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 34


def D(x):
    return x if isinstance(x, Decimal) else Decimal(str(x))


def q(x, places=2):
    return D(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def s(x, places=2):
    """Decimal -> canonical string for JSON (no float drift)."""
    return format(q(x, places), "f")


def d(iso):
    return date.fromisoformat(iso)


def add_banking_days(start, n, holidays=()):
    """The n-th banking day FOLLOWING `start` (weekends + holidays off)."""
    hol = {d(h) if isinstance(h, str) else h for h in holidays}
    cur, left = start, n
    while left:
        cur += timedelta(days=1)
        if cur.weekday() < 5 and cur not in hol:
            left -= 1
    return cur


def rand_date(rng, start, end):
    a, b = d(start), d(end)
    return a + timedelta(days=rng.randint(0, (b - a).days))


class Missing(Exception):
    """A required input is absent: the only correct answer is abstention."""

    def __init__(self, fields):
        super().__init__(", ".join(fields))
        self.fields = list(fields)


def need(inputs, *keys):
    miss = [k for k in keys if inputs.get(k) is None]
    if miss:
        raise Missing(miss)
