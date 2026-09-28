"""CFO-office oracles: GST invoicing, NPV, loan annuity, IFRS 16 initial
measurement, budget variance. Every formula is closed-form; every
convention a unique answer needs is stated in the task text."""
from ..rulebook import RULES
from ..util import D, need, q, rand_date, s

KINDS = ("gst_invoice", "npv", "loan", "ifrs16", "variance")


def gst_rate(iso):
    v = RULES["sg_gst.rate"]["values"]
    return v["2024+"] if iso >= "2024-01-01" else \
        v["2023"] if iso >= "2023-01-01" else v["2022"]


def make(rng):
    return _MAKERS[rng.choice(KINDS)](rng)


def _gst(rng):
    lines = [{"desc": f"item-{i}", "amount": s(D(rng.randint(500, 900_000)) / 100),
              "price_basis": rng.choice(["exclusive", "exclusive", "inclusive"]),
              "supply": rng.choice(["standard", "standard", "zero_rated", "exempt"])}
             for i in range(rng.randint(2, 5))]
    inputs = {"kind": "gst_invoice",
              "invoice_date": rand_date(rng, "2022-06-01", "2026-06-30").isoformat(),
              "lines": lines}
    task = ("Compute GST on this Singapore tax invoice. Determine the rate from "
            "the invoice date. For inclusive prices extract GST as amount x "
            "r/(100+r). Compute GST per line and round each line half-up to "
            "cents, then sum. Report the rate applied (percent), net total "
            "excluding GST, total GST and gross total.")
    grading = {"rate_pct": {"type": "int"},
               "net_total_sgd": {"type": "decimal", "tol": "0.00"},
               "gst_total_sgd": {"type": "decimal", "tol": "0.00"},
               "gross_total_sgd": {"type": "decimal", "tol": "0.00"}}
    return {"task": task, "inputs": inputs, "grading": grading,
            "rules": ["sg_gst.rate", "convention.rounding"],
            "droppable": ["invoice_date"]}


def _npv(rng):
    flows = [-rng.randint(50, 500) * 1000] + \
        [rng.randint(-20, 160) * 1000 for _ in range(rng.randint(3, 7))]
    inputs = {"kind": "npv", "discount_rate_pct": s(D(rng.randint(40, 140)) / 10, 1),
              "cash_flows_sgd": flows}
    task = ("Cash flow t=0 occurs today; cash flow t occurs at the end of year "
            "t. Compute NPV at the annual discount rate, round to cents, and "
            "decide accept if NPV > 0 else reject.")
    return {"task": task, "inputs": inputs,
            "grading": {"npv_sgd": {"type": "decimal", "tol": "0.05"},
                        "decision": {"type": "enum"}},
            "rules": ["convention.rounding"], "droppable": ["discount_rate_pct"]}


def _loan(rng):
    inputs = {"kind": "loan", "principal_sgd": rng.randint(20, 900) * 1000,
              "annual_rate_pct": s(D(rng.randint(150, 900)) / 100),
              "years": rng.choice([3, 5, 7, 10, 15]),
              "repayment": "monthly_in_arrears_level"}
    task = ("Level monthly repayments in arrears; monthly rate = annual rate / "
            "12. Compute the monthly instalment (round to cents) and total "
            "interest = instalment x months - principal, using the rounded "
            "instalment.")
    return {"task": task, "inputs": inputs,
            "grading": {"monthly_instalment_sgd": {"type": "decimal", "tol": "0.01"},
                        "total_interest_sgd": {"type": "decimal", "tol": "1.00"}},
            "rules": ["convention.rounding"], "droppable": ["annual_rate_pct"]}


def _ifrs16(rng):
    inputs = {"kind": "ifrs16", "annual_payment_sgd": rng.randint(24, 600) * 1000,
              "payments_timing": "annual_in_arrears",
              "term_years": rng.randint(2, 10),
              "incremental_borrowing_rate_pct": s(D(rng.randint(25, 80)) / 10, 1),
              "initial_direct_costs_sgd": rng.choice([0, 5000, 12000]),
              "lease_incentives_received_sgd": rng.choice([0, 0, 10000]),
              "restoration_cost_estimate_sgd": rng.choice([0, 0, 20000]),
              "rate_implicit_in_lease": "not readily determinable"}
    task = ("Measure this lease at commencement under IFRS 16. No payments are "
            "made at or before commencement. Report the initial lease "
            "liability and right-of-use asset, each rounded to cents.")
    return {"task": task, "inputs": inputs,
            "grading": {"lease_liability_sgd": {"type": "decimal", "tol": "1.00"},
                        "rou_asset_sgd": {"type": "decimal", "tol": "1.00"}},
            "rules": ["ifrs16.initial", "convention.rounding"],
            "droppable": ["incremental_borrowing_rate_pct"]}


def _variance(rng):
    names = rng.sample(["Revenue - Projects", "Revenue - Services", "COGS",
                        "Staff costs", "Travel", "IT", "Rent", "Marketing"], 5)
    lines = []
    for n in names:
        b = rng.randint(50, 900) * 1000
        lines.append({"line": n, "type": "revenue" if n.startswith("Revenue")
                      else "cost", "budget_sgd": b,
                      "actual_sgd": int(b * rng.uniform(0.8, 1.2))})
    inputs = {"kind": "variance", "lines": lines,
              "materiality": {"pct": 10, "abs_sgd": 25000}}
    task = ("For each line compute variance = actual - budget and label it F "
            "(favourable) or U (unfavourable): revenue above budget is F, cost "
            "above budget is U; zero variance is F. A line is material if "
            "|variance| >= pct% of budget AND |variance| >= abs_sgd. Return "
            "the set of material lines and the set of U lines.")
    return {"task": task, "inputs": inputs,
            "grading": {"material_lines": {"type": "set"},
                        "unfavourable_lines": {"type": "set"},
                        "net_profit_variance_sgd": {"type": "decimal", "tol": "0.00"}},
            "rules": [], "droppable": ["materiality"]}


_MAKERS = {"gst_invoice": _gst, "npv": _npv, "loan": _loan,
           "ifrs16": _ifrs16, "variance": _variance}


def solve(inputs):
    k = inputs["kind"]
    if k == "gst_invoice":
        need(inputs, "invoice_date", "lines")
        r = D(gst_rate(inputs["invoice_date"]))
        net = gst = D(0)
        for ln in inputs["lines"]:
            amt = D(ln["amount"])
            rr = r if ln["supply"] == "standard" else D(0)
            if ln["price_basis"] == "inclusive":
                g = q(amt * rr / (100 + rr))
                net += amt - g
            else:
                g = q(amt * rr / 100)
                net += amt
            gst += g
        return {"rate_pct": int(r), "net_total_sgd": s(net),
                "gst_total_sgd": s(gst), "gross_total_sgd": s(net + gst)}
    if k == "npv":
        need(inputs, "discount_rate_pct", "cash_flows_sgd")
        r = D(inputs["discount_rate_pct"]) / 100
        npv = sum((D(cf) / (1 + r) ** t
                   for t, cf in enumerate(inputs["cash_flows_sgd"])), D(0))
        return {"npv_sgd": s(npv), "decision": "accept" if q(npv) > 0 else "reject"}
    if k == "loan":
        need(inputs, "principal_sgd", "annual_rate_pct", "years")
        i = D(inputs["annual_rate_pct"]) / 100 / 12
        n = inputs["years"] * 12
        pmt = q(D(inputs["principal_sgd"]) * i / (1 - (1 + i) ** -n))
        return {"monthly_instalment_sgd": s(pmt),
                "total_interest_sgd": s(pmt * n - inputs["principal_sgd"])}
    if k == "ifrs16":
        need(inputs, "annual_payment_sgd", "term_years",
             "incremental_borrowing_rate_pct")
        r = D(inputs["incremental_borrowing_rate_pct"]) / 100
        liab = sum((D(inputs["annual_payment_sgd"]) / (1 + r) ** t
                    for t in range(1, inputs["term_years"] + 1)), D(0))
        rou = liab + inputs["initial_direct_costs_sgd"] - \
            inputs["lease_incentives_received_sgd"] + \
            inputs["restoration_cost_estimate_sgd"]
        return {"lease_liability_sgd": s(liab), "rou_asset_sgd": s(rou)}
    if k == "variance":
        need(inputs, "lines", "materiality")
        m = inputs["materiality"]
        mat, unf, profit = [], [], 0
        for ln in inputs["lines"]:
            v = ln["actual_sgd"] - ln["budget_sgd"]
            fav = v >= 0 if ln["type"] == "revenue" else v <= 0
            profit += v if ln["type"] == "revenue" else -v
            if not fav:
                unf.append(ln["line"])
            if abs(v) * 100 >= m["pct"] * ln["budget_sgd"] and abs(v) >= m["abs_sgd"]:
                mat.append(ln["line"])
        return {"material_lines": sorted(mat), "unfavourable_lines": sorted(unf),
                "net_profit_variance_sgd": s(profit)}
    raise ValueError(k)


def variants(inputs, rng):
    k = inputs["kind"]
    if k in ("gst_invoice", "variance"):
        return [("reorder_lines", dict(inputs, lines=list(reversed(inputs["lines"]))))]
    if k == "npv":  # appending a zero flow changes nothing
        return [("append_zero_flow",
                 dict(inputs, cash_flows_sgd=inputs["cash_flows_sgd"] + [0]))]
    return [("irrelevant_note", dict(inputs, note="Prepared by the FP&A team."))]
