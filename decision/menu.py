"""The live action menu (Phase 5.5 slice B).

Only currently legal action ids are offered — the model may choose from
the menu and nothing else. Laws:
- `human` is always included;
- Warden L3 (fired or level >= 3) collapses the menu to {human};
- when L2 is not allowed, `frontier_consult` is OMITTED (never listed as
  a model option);
- a duplicate unchanged retry omits the same action id.
"""
from dataclasses import dataclass

BASE_ACTIONS = ("inspect", "retrieve_lesson", "think_retry",
                "environment_check")
HUMAN = "human"
FRONTIER = "frontier_consult"


@dataclass(frozen=True)
class ActionMenu:
    action_ids: tuple
    level: int = 0
    l3: bool = False
    blocked_action_ids: tuple = ()

    def options(self):
        return self.action_ids

    def __contains__(self, action_id):
        return action_id in self.action_ids


def build_menu(level=0, l3_fired=False, frontier_allowed=False,
               blocked_action_ids=()):
    """Deterministic menu from the Warden's current position."""
    try:
        level = int(level or 0)
    except (TypeError, ValueError):
        level = 0
    blocked = tuple(a for a in (blocked_action_ids or ()) if a)
    if l3_fired or level >= 3:
        return ActionMenu((HUMAN,), level=level, l3=True,
                          blocked_action_ids=blocked)
    ids = list(BASE_ACTIONS)
    if frontier_allowed:
        ids.append(FRONTIER)
    ids.append(HUMAN)
    ids = [a for a in ids if a not in set(blocked)]
    if HUMAN not in ids:  # human is always legal
        ids.append(HUMAN)
    return ActionMenu(tuple(ids), level=level, blocked_action_ids=blocked)
