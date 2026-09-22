"""Reference fix for t3_assertion_expectation — overlay this directory
with `minder-op benchmark run --overlay` to produce a verified run.
The off-by-one is removed.
"""


def totals(items):
    return sum(items)
