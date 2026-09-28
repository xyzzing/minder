"""Sustainability oracles: Singapore carbon tax and GHG Scope 2."""
from ..rulebook import RULES
from ..util import D, Missing, need, q, s

FUELS = [  # (fuel, unit, tCO2e per unit) — illustrative factors, stated in-task
    ("natural_gas", "GJ", "0.0561"), ("diesel", "kL", "2.6798"),
    ("fuel_oil", "t", "3.1135"), ("lpg", "t", "2.9846")]


def make(rng):
    year = rng.choice([2024, 2025, 2026, 2027, 2027, 2028])
    scale = rng.choice([200, 3_000, 20_000, 80_000, 150_000])
    lines = []
    for fuel, unit, ef in rng.sample(FUELS, rng.randint(1, 3)):
        qty = s(D(rng.uniform(0.1, 1.0)) * scale / D(ef) / 2, 3)
        lines.append({"fuel": fuel, "quantity": qty, "unit": unit,
                      "ef_tco2e_per_unit": ef})
    inputs = {
        "kind": "carbon_tax", "facility": f"Facility-{rng.randint(100, 999)}",
        "calendar_year": year, "fuel_combustion": lines,
        "purchased_electricity_kwh": rng.randint(1, 90) * 100_000,
        "grid_ef_kgco2e_per_kwh": rng.choice(["0.4080", "0.4168", "0.4057"]),
        "icc_credits_available_tco2e": rng.choice([0, 500, 1500, 5000]),
    }
    task = ("Compute this Singapore facility's Scope 1 (direct combustion) "
            "and location-based Scope 2 emissions in tCO2e, classify it under "
            "the Carbon Pricing Act thresholds (using Scope 1 only), and "
            "compute carbon tax payable after the maximum permitted offset "
            "from the ICCs available. Assume no transition allowance. Round "
            "tCO2e to 3 dp and tax to cents only at the end. Classification "
            "is one of none|reportable|taxable. Tax is 0 unless taxable.")
    grading = {"scope1_tco2e": {"type": "decimal", "tol": "0.01"},
               "scope2_tco2e": {"type": "decimal", "tol": "0.01"},
               "classification": {"type": "enum"},
               "icc_offset_tco2e": {"type": "decimal", "tol": "0.01"},
               "carbon_tax_sgd": {"type": "decimal", "tol": "1.00"}}
    return {"task": task, "inputs": inputs, "grading": grading,
            "rules": ["sg_carbon_tax.rate", "sg_carbon_tax.thresholds",
                      "sg_carbon_tax.icc_offset", "ghg.scope2_location"],
            "droppable": ["calendar_year", "grid_ef_kgco2e_per_kwh",
                         "icc_credits_available_tco2e"]}


def solve(inputs):
    need(inputs, "fuel_combustion", "purchased_electricity_kwh",
         "grid_ef_kgco2e_per_kwh", "calendar_year",
         "icc_credits_available_tco2e")
    s1 = sum((D(x["quantity"]) * D(x["ef_tco2e_per_unit"])
              for x in inputs["fuel_combustion"]), D(0))
    s2 = D(inputs["purchased_electricity_kwh"]) * \
        D(inputs["grid_ef_kgco2e_per_kwh"]) / 1000
    th = RULES["sg_carbon_tax.thresholds"]["values"]
    cls = ("taxable" if s1 >= th["taxable"] else
           "reportable" if s1 >= th["reportable"] else "none")
    offset, tax = D(0), D(0)
    if cls == "taxable":
        rate = RULES["sg_carbon_tax.rate"]["values"].get(
            inputs["calendar_year"])
        if rate is None:
            raise Missing(["carbon_tax_rate_for_year"])
        cap = s1 * D(RULES["sg_carbon_tax.icc_offset"]["values"]["max_share"])
        offset = min(D(inputs["icc_credits_available_tco2e"]), cap)
        tax = (s1 - offset) * rate
    return {"scope1_tco2e": s(s1, 3), "scope2_tco2e": s(s2, 3),
            "classification": cls, "icc_offset_tco2e": s(offset, 3),
            "carbon_tax_sgd": s(q(tax, 2))}


def variants(inputs, rng):
    """Metamorphic twins whose correct answer is identical to the base."""
    lines = inputs["fuel_combustion"]
    first = dict(lines[0])  # restate line 1 in a unit 1000x smaller
    first["quantity"] = s(D(first["quantity"]) * 1000, 3)
    first["unit"] = {"GJ": "MJ", "kL": "L", "t": "kg"}[first["unit"]]
    first["ef_tco2e_per_unit"] = format(
        D(first["ef_tco2e_per_unit"]) / 1000, "f")
    return [
        ("reorder_lines", dict(inputs, fuel_combustion=list(reversed(lines)))),
        ("unit_rescale", dict(inputs, fuel_combustion=[first] + lines[1:])),
        ("irrelevant_rename", dict(inputs, facility="Plant " + inputs["facility"])),
    ]
