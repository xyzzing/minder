"""Legal oracles: Singapore PDPA notifiable-breach deadlines, Limitation
Act 1959 contract/tort limitation, Employment Act salary payment timing.
Every kind is closed-form date/logic arithmetic off rules the prompt
supplies, so grading is exact and a wrong answer is always a wrong rule
application, not a judgment call."""
from datetime import timedelta

from ..rulebook import RULES
from ..util import d, need

KINDS = ("pdpa_deadline", "limitation_expiry", "ea_salary_deadline")


def make(rng):
    return _MAKERS[rng.choice(KINDS)](rng)


def _pdpa(rng):
    inputs = {
        "kind": "pdpa_deadline",
        "assessment_date": rng.choice([
            "2026-01-20", "2026-03-10", "2026-06-30", "2026-11-05"]),
        "individuals_affected": rng.choice([120, 200, 499, 500, 1200, 9000]),
        "significant_harm_likely": rng.choice([False, False, True]),
    }
    task = ("A data breach was assessed on assessment_date. Under the "
            "supplied PDPA rule, determine whether the breach is notifiable "
            "(scale limb: individuals_affected at or above the rule's "
            "threshold; or significant harm limb), and if so give the date "
            "by which the PDPC must be notified and whether the affected "
            "individuals must be notified.")
    return {"task": task, "inputs": inputs,
            "grading": {"notifiable": {"type": "bool"},
                        "notify_pdpc_by": {"type": "date_or_null"},
                        "notify_individuals": {"type": "bool"}},
            "rules": ["pdpa.breach"],
            "droppable": ["assessment_date", "significant_harm_likely"]}


def _pdpa_solve(inputs):
    need(inputs, "assessment_date", "individuals_affected",
         "significant_harm_likely")
    v = RULES["pdpa.breach"]["values"]
    if (inputs["individuals_affected"] < v["scale"]
            and not inputs["significant_harm_likely"]):
        return {"notifiable": False, "notify_pdpc_by": None,
                "notify_individuals": False}
    deadline = d(inputs["assessment_date"]) + timedelta(days=v["days"])
    return {"notifiable": True, "notify_pdpc_by": deadline.isoformat(),
            "notify_individuals": True}


def _limitation(rng):
    accrual = rng.choice([
        "2019-08-12", "2020-03-10", "2020-02-29", "2021-12-01",
        "2023-06-15", "2025-01-05"])
    filed = rng.choice([
        "2019-01-01", "2026-03-09", "2026-03-11", "2027-01-10",
        "2024-04-01", "2026-08-14"])
    inputs = {"kind": "limitation_expiry", "cause": rng.choice(
        ["contract", "tort"]), "accrual_date": accrual,
        "claim_filed_date": filed}
    task = ("A claim founded on the stated cause of action accrued on "
            "accrual_date and the writ was filed on claim_filed_date. "
            "Using the supplied limitation rule, give the last date the "
            "claim could have been brought and whether this claim is "
            "in_time or barred.")
    return {"task": task, "inputs": inputs,
            "grading": {"limitation_expiry": {"type": "date"},
                        "status": {"type": "enum"}},
            "rules": ["limitation.contract_tort"],
            "droppable": ["accrual_date", "claim_filed_date"]}


def _add_years(day, n):
    # Feb 29 has no counterpart in most target years; roll to Mar 1.
    try:
        return day.replace(year=day.year + n)
    except ValueError:
        return day.replace(year=day.year + n, day=1, month=3)


def _limitation_solve(inputs):
    need(inputs, "accrual_date", "claim_filed_date")
    expiry = _add_years(d(inputs["accrual_date"]),
                        RULES["limitation.contract_tort"]["values"]["years"])
    return {"limitation_expiry": expiry.isoformat(),
            "status": "in_time"
            if d(inputs["claim_filed_date"]) <= expiry else "barred"}


def _ea_salary(rng):
    inputs = {
        "kind": "ea_salary_deadline",
        "salary_period_end": rng.choice([
            "2026-01-31", "2026-02-28", "2026-05-31", "2026-07-15",
            "2026-12-31"]),
        "salary_paid_on": rng.choice([
            "2026-06-01", "2026-06-07", "2026-06-08", "2026-06-20"]),
    }
    task = ("An employee's salary period ended on salary_period_end. Using "
            "the supplied Employment Act rule, give the last date the "
            "salary could be paid in time, and whether the actual payment "
            "(salary_paid_on) was on time.")
    return {"task": task, "inputs": inputs,
            "grading": {"payment_deadline": {"type": "date"},
                        "paid_on_time": {"type": "bool"}},
            "rules": ["ea.salary_payment"],
            "droppable": ["salary_period_end", "salary_paid_on"]}


def _ea_salary_solve(inputs):
    need(inputs, "salary_period_end", "salary_paid_on")
    deadline = d(inputs["salary_period_end"]) + timedelta(
        days=RULES["ea.salary_payment"]["values"]["days"])
    paid = inputs["salary_paid_on"]
    return {"payment_deadline": deadline.isoformat(),
            "paid_on_time": d(paid) <= deadline}


_MAKERS = {"pdpa_deadline": _pdpa, "limitation_expiry": _limitation,
           "ea_salary_deadline": _ea_salary}
_SOLVERS = {"pdpa_deadline": _pdpa_solve,
            "limitation_expiry": _limitation_solve,
            "ea_salary_deadline": _ea_salary_solve}


def solve(inputs):
    return _SOLVERS[inputs["kind"]](inputs)


def variants(inputs, rng):
    """Metamorphic twins: correct answers survive the transformation."""
    out = [("irrelevant_rename", dict(inputs))]
    kind = inputs["kind"]
    if kind == "limitation_expiry":
        out.append(("date_shift", dict(
            inputs, accrual_date="2019-08-12",
            claim_filed_date="2026-08-11")))
        out.append(("cause_flip", dict(inputs, cause=(
            "tort" if inputs["cause"] == "contract" else "contract"))))
    elif kind == "ea_salary_deadline":
        out.append(("date_shift", dict(
            inputs, salary_period_end="2026-01-31",
            salary_paid_on=None)))
    else:
        out.append(("harm_flip_false_twin", dict(
            inputs, individuals_affected=50,
            significant_harm_likely=False)))
    return out
