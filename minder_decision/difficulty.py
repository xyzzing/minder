"""The difficulty router: laya's fast decision layer output -> request
parameters. Pure code — laya is never a solver and never decides anything
by itself. It only returns a typed difficulty label + calibrated score +
confidence; this module maps that onto a minder level, reasoning effort,
thinking budget, output-token ceiling, and a spending guardrail.

Everything here is fail-open: any malformed input yields None (no opinion)
and the caller falls through to the mode/scheduler path.
"""
from .types import InvalidDistribution, ScoreQuestion

# The four difficulty labels, in ascending order of required reasoning.
LABELS = ("mechanical", "routine", "complex", "expert_or_ambiguous")

# Score -> label buckets (the score is an expected value in 0..3).
SCORE_LABELS = (
    (0.5, "mechanical"),
    (1.5, "routine"),
    (2.5, "complex"),
    (float("inf"), "expert_or_ambiguous"),
)

# Default bands: label -> (level, effort, thinking_budget, max_tokens,
# guardrail). level 0 = exec (no escalation), 1 = think (Standard),
# 2 = deep (Deep). Effort names are semantic — the proxy translates them
# onto the measured vocabulary. max_tokens is a CEILING only (never
# raised above the preset).
DEFAULT_BANDS = {
    "mechanical": {"level": 0, "effort": "off", "budget": None,
                   "max_tokens": 2048, "guardrail": None},
    "routine": {"level": 1, "effort": "low", "budget": 2048,
                "max_tokens": 8192, "guardrail": None},
    "complex": {"level": 2, "effort": "high", "budget": 10240,
                "max_tokens": 32768, "guardrail": "spend"},
    "expert_or_ambiguous": {"level": 2, "effort": "xhigh", "budget": 12000,
                            "max_tokens": 32768, "guardrail": "spend"},
}


def score_label(score):
    """Bucket an expected score (0..3) into a difficulty label."""
    try:
        score = float(score)
    except (TypeError, ValueError):
        return None
    for upper, label in SCORE_LABELS:
        if score < upper:
            return label
    return LABELS[-1]


def _label_rank(label):
    try:
        return LABELS.index(label)
    except ValueError:
        return None


def resolve_difficulty(response, contract, cfg):
    """Validate a DecisionResponse against the task-difficulty contract and
    return (label, band) or None.

    Conservative fail-safe: the final label is the HIGHER of the choice
    label and the score label (more thinking, never less). Below
    `laya_min_confidence` the router has no opinion. Any malformed input
    (missing/invalid distribution, score out of range) -> None."""
    try:
        if response is None or contract is None:
            return None
        response.validate(contract)
    except (InvalidDistribution, Exception):
        return None
    try:
        min_conf = float(cfg.get("laya_min_confidence", 0.7))
    except (TypeError, ValueError):
        min_conf = 0.7
    confidence = float(response.confidence or 0.0)
    if confidence < min_conf:
        return None
    label = response.top_choice("difficulty")
    if label not in LABELS:
        return None
    score = None
    for question in contract.questions:
        if isinstance(question, ScoreQuestion) and question.id == \
                "difficulty_score":
            score = response.score_values.get(question.id)
    if score is not None:
        score_label_ = score_label(score)
        if score_label_ is not None:
            # conservative: never de-escalate on the score
            if _label_rank(score_label_) > _label_rank(label):
                label = score_label_
    band = _band_for_label(label, cfg)
    return label, band


def _band_for_label(label, cfg):
    bands = dict(DEFAULT_BANDS)
    overrides = cfg.get("difficulty_bands") or {}
    if isinstance(overrides, dict):
        override = overrides.get(label)
        if isinstance(override, dict):
            merged = dict(bands[label])
            for key in ("level", "effort", "budget", "max_tokens",
                        "guardrail"):
                if key in override and override[key] is not None:
                    merged[key] = override[key]
            bands[label] = merged
    band = dict(bands[label])
    band["label"] = label
    band["band"] = label
    return band


def band_for(label, cfg):
    """Public accessor for a label's band (used by tests)."""
    return _band_for_label(label, cfg)


def apply_band(req, band, cfg):
    """Apply a band to the request in place (the proxy has already run
    adapter.apply_auto with the band's effort, so this only sets the
    budget/ceiling). Returns the applied params for logging.
    max_tokens is a ceiling: min(current, band ceiling) — never raised."""
    applied = {"effort": band.get("effort"), "budget": band.get("budget"),
               "max_tokens": None, "guardrail": band.get("guardrail")}
    budget = band.get("budget")
    if budget is not None:
        try:
            budget = int(budget)
        except (TypeError, ValueError):
            budget = None
        if budget is not None:
            ctk = dict(req.get("chat_template_kwargs") or {})
            ctk["thinking_budget"] = budget
            req["chat_template_kwargs"] = ctk
            applied["budget"] = budget
    ceiling = band.get("max_tokens")
    if ceiling is not None:
        try:
            ceiling = int(ceiling)
        except (TypeError, ValueError):
            ceiling = None
        if ceiling is not None:
            current = req.get("max_tokens")
            if current is None:
                # no client ceiling: take the band ceiling as the cap
                req["max_tokens"] = ceiling
            else:
                try:
                    req["max_tokens"] = min(int(current), ceiling)
                except (TypeError, ValueError):
                    req["max_tokens"] = ceiling
            applied["max_tokens"] = req["max_tokens"]
    return applied
