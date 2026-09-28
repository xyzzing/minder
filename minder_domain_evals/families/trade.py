"""Trade-finance oracles: UCP 600 presentation/tolerance checks and
Incoterms 2020 risk allocation."""
from datetime import timedelta

from ..rulebook import RULES
from ..util import D, add_banking_days, d, need, rand_date, s

BULK = ("MT", "KG", "L")


def make(rng):
    return _lc(rng) if rng.random() < 0.6 else _inco(rng)


def _lc(rng):
    issue = rand_date(rng, "2025-01-06", "2026-10-30")
    latest_ship = issue + timedelta(days=rng.randint(20, 60))
    expiry = latest_ship + timedelta(days=rng.choice([10, 15, 21, 30]))
    ship = latest_ship + timedelta(days=rng.choice([-25, -10, -3, 0, 2]))
    present = ship + timedelta(days=rng.choice([5, 14, 20, 21, 22, 27]))
    unit = rng.choice(["MT", "MT", "KG", "cartons", "pieces"])
    qty = rng.randint(10, 900) * (100 if unit in BULK else 10)
    about = rng.random() < 0.3
    price = D(rng.randint(80, 900))
    amount = D(qty) * price
    shipped = int(qty * rng.choice([0.88, 0.94, 0.96, 1.0, 1.0, 1.04, 1.06, 1.12]))
    draw = min(D(shipped) * price, amount * D("1.15"))
    hol = [(present + timedelta(days=k)).isoformat() for k in (-2, 1, 3, 8)
           if rng.random() < 0.3]
    inputs = {
        "kind": "ucp600_lc", "credit_amount_usd": s(amount),
        "amount_wording": "about" if about else "exact",
        "quantity": qty, "quantity_unit": unit,
        "quantity_wording": "about" if about else "exact",
        "unit_price_usd": s(price), "partial_shipments": "prohibited",
        "latest_shipment_date": latest_ship.isoformat(),
        "expiry_date": expiry.isoformat(),
        "presentation_period_days": None if rng.random() < 0.7 else 15,
        "on_board_date": ship.isoformat(), "presentation_date": present.isoformat(),
        "quantity_shipped": shipped, "drawing_amount_usd": s(draw),
        "bank_holidays": sorted(hol),
        "documents": "full set of original on-board bills of lading, "
                     "commercial invoice, packing list",
    }
    task = ("Examine this presentation under a credit subject to UCP 600. A "
            "null presentation_period_days means the credit states none. Report "
            "the latest permissible presentation date, the bank's examination "
            "deadline (the fifth banking day following presentation; weekends "
            "and listed holidays are not banking days), and the set of "
            "discrepancies from: late_shipment, late_presentation, "
            "presented_after_expiry, amount_exceeds_tolerance, "
            "quantity_outside_tolerance. The quantity check is two-sided "
            "because partial shipments are prohibited.")
    grading = {"latest_presentation_date": {"type": "date"},
               "examination_deadline": {"type": "date"},
               "discrepancies": {"type": "set"},
               "complying": {"type": "bool"}}
    return {"task": task, "inputs": inputs, "grading": grading,
            "rules": ["ucp600.presentation", "ucp600.tolerance"],
            "droppable": ["on_board_date", "expiry_date"]}


def _roll(day, holidays):
    closed = {d(h) for h in holidays}
    while day.weekday() >= 5 or day in closed:
        day += timedelta(days=1)
    return day


def _lc_solve(x):
    need(x, "on_board_date", "expiry_date", "presentation_date")
    per = x["presentation_period_days"] or \
        RULES["ucp600.presentation"]["values"]["default_days"]
    ship, exp, pres = d(x["on_board_date"]), d(x["expiry_date"]), \
        d(x["presentation_date"])
    hol = x["bank_holidays"]
    # Art 29(a): a last day falling on a closed day rolls to the next
    # banking day; Art 29(c): the latest shipment date never rolls.
    exp_eff = _roll(exp, hol)
    per_eff = _roll(ship + timedelta(days=per), hol)
    latest = min(per_eff, exp_eff)
    disc = set()
    if ship > d(x["latest_shipment_date"]):
        disc.add("late_shipment")
    if pres > exp_eff:
        disc.add("presented_after_expiry")
    if pres > per_eff:
        disc.add("late_presentation")
    tol = RULES["ucp600.tolerance"]["values"]
    amt_tol = D(tol["about"]) if x["amount_wording"] == "about" else D(0)
    if D(x["drawing_amount_usd"]) > D(x["credit_amount_usd"]) * (1 + amt_tol):
        disc.add("amount_exceeds_tolerance")
    if x["quantity_wording"] == "about":
        qt = D(tol["about"])
    elif x["quantity_unit"] in BULK:
        qt = D(tol["bulk_qty"])
    else:
        qt = D(0)
    lo, hi = D(x["quantity"]) * (1 - qt), D(x["quantity"]) * (1 + qt)
    if not lo <= D(x["quantity_shipped"]) <= hi:
        disc.add("quantity_outside_tolerance")
    exam = add_banking_days(pres, RULES["ucp600.presentation"]["values"]
                            ["exam_banking_days"], hol)
    return {"latest_presentation_date": latest.isoformat(),
            "examination_deadline": exam.isoformat(),
            "discrepancies": sorted(disc), "complying": not disc}


# Incoterms 2020 --------------------------------------------------------------
# Loss events along a sea shipment, in order. RISK_AT[t] = index of the first
# event whose loss falls on the BUYER.
EVENTS = ("at_seller_premises_after_goods_placed_at_buyers_disposal",
          "alongside_vessel_at_port_of_shipment_before_loading",
          "at_sea_during_main_carriage",
          "during_unloading_at_named_destination_terminal",
          "after_unloading_awaiting_import_clearance")
RISK_AT = {"EXW": 0, "FAS": 1, "FOB": 2, "CFR": 2, "CIF": 2,
           "DAP": 3, "DDP": 3, "DPU": 4}
CARRIAGE = {"EXW": False, "FCA": False, "FAS": False, "FOB": False}
INSURANCE = {"CIF": "ICC_C", "CIP": "ICC_A"}
SEA_ONLY = {"FAS", "FOB", "CFR", "CIF"}
ALL_TERMS = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP",
             "DAP", "DPU", "DDP")


def _inco(rng):
    term = rng.choice(list(RISK_AT))
    event = rng.choice(EVENTS)
    inputs = {"kind": "incoterms2020", "term": term, "named_place": rng.choice(
        ["Singapore", "Port Klang", "Ho Chi Minh City (Cat Lai)", "Rotterdam"]),
        "loss_event": event, "incoterms_version": "2020"}
    task = ("Under the stated Incoterms 2020 rule, who bears the risk of the "
            "loss event (buyer|seller)? Also state whether the seller must "
            "contract main carriage, the seller's minimum insurance obligation "
            "(none|ICC_A|ICC_C), whether the seller clears export, whether the "
            "seller clears import, and whether the rule is sea/inland-waterway "
            "only.")
    grading = {"risk_bearer": {"type": "enum"},
               "seller_contracts_main_carriage": {"type": "bool"},
               "seller_min_insurance": {"type": "enum"},
               "seller_clears_export": {"type": "bool"},
               "seller_clears_import": {"type": "bool"},
               "sea_inland_waterway_only": {"type": "bool"}}
    return {"task": task, "inputs": inputs, "grading": grading,
            "rules": ["incoterms2020"], "droppable": ["term"]}


def _inco_solve(x):
    need(x, "term", "loss_event")
    t = x["term"]
    return {"risk_bearer": "buyer" if EVENTS.index(x["loss_event"]) >= RISK_AT[t]
            else "seller",
            "seller_contracts_main_carriage": CARRIAGE.get(t, True),
            "seller_min_insurance": INSURANCE.get(t, "none"),
            "seller_clears_export": t != "EXW",
            "seller_clears_import": t == "DDP",
            "sea_inland_waterway_only": t in SEA_ONLY}


def solve(inputs):
    return _lc_solve(inputs) if inputs["kind"] == "ucp600_lc" \
        else _inco_solve(inputs)


def variants(inputs, rng):
    if inputs["kind"] == "incoterms2020":
        return [("irrelevant_place", dict(inputs, named_place="Busan"))]
    return [("irrelevant_documents_order",
             dict(inputs, documents="commercial invoice, packing list, full set "
                  "of original on-board bills of lading"))]
