"""The one storage error the read side raises (issue #20 extraction).

`minder_op/queries.py` used to own it. The injection-ledger reads moved
into their own module and both need the same exception, so the class
lives in a module neither imports from the other. A cycle here would be
the cheapest possible way to make a read surface unimportable.
"""


class DBError(Exception):
    """Missing or unreadable/corrupt DB."""
