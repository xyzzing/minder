"""Output helpers: redaction + plain-text tables. stdlib only."""
from memory.canonicalise import redact

TRUNCATE = 160


def safe(text, cap=TRUNCATE):
    """Redacted + truncated for any free-text field that reaches the
    terminal. Never raises."""
    try:
        return redact(str(text or ""))[:cap]
    except Exception:
        return ""


def table(rows, columns):
    """Aligned text table. rows: list of dicts; columns: (header, key)
    pairs. Empty rows -> '(none)'."""
    if not rows:
        print("(none)")
        return
    widths = {key: len(header) for header, key in columns}
    for row in rows:
        for _header, key in columns:
            widths[key] = min(60, max(widths[key],
                                      len(str(row.get(key, "")))))
    header_line = "  ".join(header.ljust(widths[key])
                            for header, key in columns)
    print(header_line)
    print("  ".join("-" * widths[key] for _header, key in columns))
    for row in rows:
        print("  ".join(str(row.get(key, "")).ljust(widths[key])
                        for _header, key in columns))


def kv(pairs):
    """Aligned key: value block."""
    width = max((len(str(k)) for k, _ in pairs), default=0)
    for key, value in pairs:
        print(f"{str(key).ljust(width)} : {value}")
