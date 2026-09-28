"""Governance oracles: procurement controls (delegation of authority,
three-way match, segregation of duties) and PDPA breach notification.
Control parameters are supplied as the organisation's own policy, so the
test is rule application, not recall."""
from datetime import timedelta

from ..rulebook import RULES
from ..util import D, d, need, rand_date

KINDS = ("doa", "three_way_match", "sod", "pdpa_breach")
ROLES = [("Manager", 10_000), ("Head of Dept", 50_000), ("CFO", 250_000),
         ("CEO", 1_000_000)]


def make(rng):
    return _MAKERS[rng.choice(KINDS)](rng)


def _doa(rng):
    vendor = rng.choice(["Acme Logistics", "Kaya Print", "Delta IT"])
    day = rand_date(rng, "2026-01-05", "2026-09-25").isoformat()
    pos = [{"po": f"PO-{rng.randint(1000, 9999)}", "vendor": rng.choice(
        [vendor, vendor, "Other Co"]), "date": rng.choice([day, day, "2026-10-01"]),
        "amount_sgd": rng.randint(20, 900) * 100, "category": "opex"}
        for _ in range(rng.randint(2, 4))]
    target = {"po": "PO-NEW", "vendor": vendor, "date": day,
              "amount_sgd": rng.choice([8_000, 9_900, 45_000, 60_000, 300_000,
                                        1_200_000]),
              "category": rng.choice(["opex", "opex", "capex"])}
    inputs = {"kind": "doa", "authority_matrix": [
        {"role": r, "max_sgd": m} for r, m in ROLES],
        "policy": "Lowest role whose limit >= amount approves. Capex always "
                  "needs at least CFO. Amounts above the highest limit go to "
                  "the Board. POs to the same vendor on the same date are "
                  "aggregated for the limit test and flagged potential_split "
                  "when the new PO alone is within a lower role's limit than "
                  "the aggregate.",
        "existing_pos": pos, "new_po": target}
    task = ("Apply the delegation-of-authority policy to new_po. Return the "
            "required approver role (a matrix role or Board), the aggregate "
            "amount used for the limit test, and potential_split true/false.")
    return {"task": task, "inputs": inputs,
            "grading": {"required_approver": {"type": "enum"},
                        "aggregate_sgd": {"type": "int"},
                        "potential_split": {"type": "bool"}},
            "rules": [], "droppable": ["authority_matrix"]}


def _approver(amount, matrix, capex):
    order = [m["role"] for m in matrix]
    role = next((m["role"] for m in matrix if m["max_sgd"] >= amount), "Board")
    if capex and role != "Board" and order.index(role) < order.index("CFO"):
        role = "CFO"
    return role


def _3wm(rng):
    qty = rng.randint(10, 500)
    price = D(rng.randint(100, 9000)) / 100
    grn = None if rng.random() < 0.1 else qty - rng.choice([0, 0, 0, 2, 5])
    inputs = {"kind": "three_way_match",
              "po": {"qty": qty, "unit_price_sgd": str(price)},
              "grn": None if grn is None else {"qty_received": grn},
              "invoice": {"qty": qty - rng.choice([0, 0, 1, 5]) if grn else qty,
                          "unit_price_sgd": str(price * D(rng.choice(
                              ["1", "1", "1.01", "1.02", "1.03", "0.97"])))},
              "tolerance": {"price_pct": 2, "qty": "invoice qty must not exceed "
                            "received qty"}}
    task = ("Perform a three-way match. A null grn means no goods receipt was "
            "posted. Price passes if |invoice price - PO price| <= price_pct% of "
            "PO price. Return outcome approve|hold and the set of reason codes "
            "from: no_grn, qty_exceeds_received, price_variance.")
    return {"task": task, "inputs": inputs,
            "grading": {"outcome": {"type": "enum"}, "reasons": {"type": "set"}},
            "rules": [], "droppable": ["tolerance"]}


def _sod(rng):
    perms = ["create_vendor", "approve_vendor", "raise_po", "approve_po",
             "post_grn", "post_invoice", "release_payment"]
    conflicts = [["create_vendor", "release_payment"], ["raise_po", "approve_po"],
                 ["post_invoice", "release_payment"], ["approve_vendor", "raise_po"]]
    users = {f"u{i:02d}": sorted(rng.sample(perms, rng.randint(1, 3)))
             for i in range(rng.randint(4, 8))}
    inputs = {"kind": "sod", "conflicting_pairs": conflicts, "user_permissions": users}
    task = ("Identify segregation-of-duties violations: return the set of users "
            "holding both permissions of any conflicting pair, and the count of "
            "(user, pair) violations.")
    return {"task": task, "inputs": inputs,
            "grading": {"users_in_violation": {"type": "set"},
                        "violation_count": {"type": "int"}},
            "rules": [], "droppable": ["conflicting_pairs"]}


def _pdpa(rng):
    assessed = rand_date(rng, "2025-01-01", "2026-09-20")
    inputs = {"kind": "pdpa_breach",
              "affected_individuals": rng.choice([12, 120, 499, 500, 501, 4000]),
              "involves_prescribed_personal_data": rng.random() < 0.5,
              "assessed_notifiable_on": assessed.isoformat(),
              "remedial_action_renders_harm_unlikely": rng.random() < 0.3,
              "prior_technological_measure_renders_harm_unlikely": rng.random() < 0.2,
              "discovered_on": (assessed - timedelta(days=rng.randint(1, 20))
                                ).isoformat()}
    task = ("Treat involves_prescribed_personal_data=true as meeting the "
            "significant-harm limb. Decide whether the breach is notifiable, "
            "the latest date to notify the PDPC (null if not notifiable; the "
            "clock runs from the assessment date), and whether affected "
            "individuals must be notified.")
    return {"task": task, "inputs": inputs,
            "grading": {"notifiable": {"type": "bool"},
                        "notify_pdpc_by": {"type": "date_or_null"},
                        "notify_individuals": {"type": "bool"}},
            "rules": ["pdpa.breach"],
            "droppable": ["affected_individuals", "assessed_notifiable_on"]}


_MAKERS = {"doa": _doa, "three_way_match": _3wm, "sod": _sod, "pdpa_breach": _pdpa}


def solve(x):
    k = x["kind"]
    if k == "doa":
        need(x, "authority_matrix", "new_po")
        n = x["new_po"]
        agg = n["amount_sgd"] + sum(p["amount_sgd"] for p in x["existing_pos"]
                                    if p["vendor"] == n["vendor"]
                                    and p["date"] == n["date"])
        capex = n["category"] == "capex"
        role = _approver(agg, x["authority_matrix"], capex)
        alone = _approver(n["amount_sgd"], x["authority_matrix"], capex)
        return {"required_approver": role, "aggregate_sgd": agg,
                "potential_split": alone != role}
    if k == "three_way_match":
        need(x, "po", "invoice", "tolerance")
        reasons = set()
        if x["grn"] is None:
            reasons.add("no_grn")
        elif x["invoice"]["qty"] > x["grn"]["qty_received"]:
            reasons.add("qty_exceeds_received")
        po_p, inv_p = D(x["po"]["unit_price_sgd"]), D(x["invoice"]["unit_price_sgd"])
        if abs(inv_p - po_p) * 100 > po_p * x["tolerance"]["price_pct"]:
            reasons.add("price_variance")
        return {"outcome": "hold" if reasons else "approve",
                "reasons": sorted(reasons)}
    if k == "sod":
        need(x, "conflicting_pairs", "user_permissions")
        users, count = set(), 0
        for u, ps in x["user_permissions"].items():
            for a, b in x["conflicting_pairs"]:
                if a in ps and b in ps:
                    users.add(u)
                    count += 1
        return {"users_in_violation": sorted(users), "violation_count": count}
    if k == "pdpa_breach":
        need(x, "affected_individuals", "assessed_notifiable_on")
        v = RULES["pdpa.breach"]["values"]
        harm = x["involves_prescribed_personal_data"]
        notifiable = harm or x["affected_individuals"] >= v["scale"]
        by = (d(x["assessed_notifiable_on"]) + timedelta(days=v["days"])
              ).isoformat() if notifiable else None
        indiv = harm and not (x["remedial_action_renders_harm_unlikely"] or
                              x["prior_technological_measure_renders_harm_unlikely"])
        return {"notifiable": notifiable, "notify_pdpc_by": by,
                "notify_individuals": bool(indiv)}
    raise ValueError(k)


def variants(x, rng):
    k = x["kind"]
    if k == "sod":  # conflict pairs are unordered
        return [("reorder_pairs", dict(x, conflicting_pairs=[
            [b, a] for a, b in x["conflicting_pairs"]]))]
    if k == "doa":
        return [("reorder_existing", dict(x, existing_pos=list(
            reversed(x["existing_pos"]))))]
    if k == "pdpa_breach":
        return [("irrelevant_discovery_shift", dict(
            x, discovered_on=(d(x["assessed_notifiable_on"]) - timedelta(days=2)
                              ).isoformat()))]
    return [("irrelevant_note", dict(x, note="Vendor is a long-standing supplier."))]

