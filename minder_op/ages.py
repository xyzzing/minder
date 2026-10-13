"""One age formatter for every operator surface that says how old
something is (doctor, summary, scorecard). A duration rendered three ways
reads as three different severities."""


def age_text(secs):
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 48 * 3600:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"
