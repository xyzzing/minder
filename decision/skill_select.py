"""Skill-select contract helpers (Phase 5.5 slice F, still no hook
change).

The shortlist (<= LIMIT ids) comes from the EXISTING memory skill index
via deterministic trigger matching — never invented by a model — plus
"none" as the always-legal answer. A client response only leads to a
body load when: any_skill_applies >= MIN_APPLIES, the choice is a
shortlist id (not "none"), and the gate did not override to human. At
most ONE body is ever loaded. The body itself is loaded through
memory.skill_load.load_skill, which returns instructions=None when the
body file is missing — that means NO attach, not a crash.
"""
from . import contracts as dcontracts
from .types import InvalidDistribution

MIN_APPLIES = 0.5          # noul below this -> never load a body
NONE = "none"
LIMIT = 3


def build_skill_shortlist(event, index_path=None):
    """<= LIMIT live skill ids from the deterministic index matcher."""
    try:
        from memory.skill_load import list_skill_metadata
        advertised = list_skill_metadata(event, index_path=index_path)
        names = [str(entry.get("name")) for entry in advertised
                 if entry.get("name")][:LIMIT]
        return tuple(names)
    except Exception:
        return ()


def skill_select_response_contract(shortlist):
    return dcontracts.skill_select_contract(shortlist)


def decide_skill(response, shortlist, contract=None):
    """The recommended skill id, or None when the model may not load:
    noul < MIN_APPLIES, choice "none", or a choice outside the
    shortlist. Invalid distributions are refused (None), matching the
    gate's conservative failure."""
    shortlist = tuple(shortlist)
    contract = contract or dcontracts.skill_select_contract(shortlist)
    try:
        response.validate(contract)
    except (InvalidDistribution, Exception):
        return None
    applies = response.noul_probs.get("any_skill_applies")
    try:
        applies = float(applies)
    except (TypeError, ValueError):
        return None
    if applies < MIN_APPLIES:
        return None
    best_probs = response.choice_probs.get("best_skill") or {}
    from .types import top_two_margin
    if top_two_margin(best_probs) <= 0.0:
        return None  # a tied/uniform distribution is no opinion at all
    best = response.top_choice("best_skill")
    if not best or best == NONE or best not in shortlist:
        return None
    return best


def load_selected_skill(name, index_path=None):
    """At most one body, through the Phase 2 loader. Returns the skill
    dict, or None when the body is missing (no attach, no crash)."""
    if not name:
        return None
    try:
        from memory.skill_load import load_skill
        skill = load_skill(name, index_path=index_path)
        if not skill or not skill.get("instructions"):
            return None
        return skill
    except Exception:
        return None
