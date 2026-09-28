"""Grounded extraction: read a generated supplier sustainability report,
return reporting-year figures with a verbatim evidence quote each.

Deterministic grading of a reading task: the value must equal the
planted fact, the quote must be a verbatim substring of the document
that contains the value, and fields the document never discloses must
come back null with quote null. Distractors (prior year, targets,
peer figures, restatements) are what a skimming model grabs."""
from ..util import need

FIELDS = {
    "scope1_tco2e": "Scope 1 emissions (tCO2e)",
    "scope2_tco2e": "Scope 2 emissions, location-based (tCO2e)",
    "energy_mwh": "total energy consumption (MWh)",
    "women_workforce_pct": "women as a percentage of total workforce",
    "lost_time_injuries": "number of lost-time injuries",
    "water_withdrawal_m3": "total water withdrawal (m3)",
}


def _n(x):
    return f"{x:,}"


def make(rng):
    fy = rng.choice([2023, 2024, 2025])
    co = rng.choice(["Meridian Packaging", "Lotus Cold Chain", "Harbourline Metals"])
    v = {"scope1_tco2e": rng.randint(1200, 90000),
         "scope2_tco2e": rng.randint(800, 60000),
         "energy_mwh": rng.randint(2000, 400000),
         "women_workforce_pct": rng.randint(12, 58),
         "lost_time_injuries": rng.randint(0, 14),
         "water_withdrawal_m3": rng.randint(5000, 900000)}
    undisclosed = set(rng.sample(sorted(FIELDS), rng.randint(1, 2)))
    prior = {k: int(val * rng.uniform(0.8, 1.25)) + 1 for k, val in v.items()}
    s = []
    s.append(f"{co} Sustainability Report FY{fy}.")
    if "scope1_tco2e" not in undisclosed:
        s.append(f"In FY{fy}, our Scope 1 emissions were {_n(v['scope1_tco2e'])} "
                 f"tCO2e, compared with {_n(prior['scope1_tco2e'])} tCO2e in "
                 f"FY{fy - 1}.")
    s.append(f"We aim to cut Scope 1 emissions to {_n(v['scope1_tco2e'] // 2)} "
             f"tCO2e by 2030.")
    if "scope2_tco2e" not in undisclosed:
        s.append(f"Location-based Scope 2 emissions totalled "
                 f"{_n(v['scope2_tco2e'])} tCO2e (market-based: "
                 f"{_n(int(v['scope2_tco2e'] * 0.7))} tCO2e).")
    if "energy_mwh" not in undisclosed:
        s.append(f"FY{fy - 1} energy use has been restated to "
                 f"{_n(prior['energy_mwh'])} MWh. Total energy consumption "
                 f"in FY{fy} reached {_n(v['energy_mwh'])} MWh.")
    if "women_workforce_pct" not in undisclosed:
        s.append(f"Women made up {v['women_workforce_pct']}% of our total "
                 f"workforce and {min(v['women_workforce_pct'] + 7, 70)}% of "
                 f"new hires.")
    if "lost_time_injuries" not in undisclosed:
        s.append(f"We recorded {v['lost_time_injuries']} lost-time injuries "
                 f"across our sites; the industry average was "
                 f"{v['lost_time_injuries'] + 3}.")
    if "water_withdrawal_m3" not in undisclosed:
        s.append(f"Total water withdrawal was {_n(v['water_withdrawal_m3'])} m3.")
    s.append("Figures for FY2030 targets are subject to board approval.")
    head, body = s[:1], s[1:]
    rng.shuffle(body)
    doc = " ".join(head + body)
    inputs = {"kind": "grounded_extraction", "reporting_year": f"FY{fy}",
              "document": doc, "fields": FIELDS,
              "_truth": {k: (None if k in undisclosed else v[k]) for k in FIELDS},
              "_sentences": head + body}
    task = ("Extract each field for the reporting year only. For each field "
            "return {\"value\": number or null, \"quote\": verbatim sentence "
            "fragment from the document containing the value, or null}. If the "
            "document does not disclose the reporting-year figure, return null "
            "for both. Never estimate.")
    grading = {k: {"type": "evidence"} for k in FIELDS}
    return {"task": task, "inputs": inputs, "grading": grading, "rules": [],
            "droppable": ["reporting_year"]}


def solve(x):
    need(x, "reporting_year", "document")
    out = {}
    for k, val in x["_truth"].items():
        if val is None:
            out[k] = {"value": None, "quote": None}
            continue
        needle = _n(val) if k not in ("women_workforce_pct",
                                      "lost_time_injuries") else str(val)
        out[k] = {"value": val, "quote": _locate(x["document"], k, needle)}
    return out


_ANCHORS = {"scope1_tco2e": "our Scope 1 emissions were",
            "scope2_tco2e": "Location-based Scope 2 emissions totalled",
            "energy_mwh": "Total energy consumption",
            "women_workforce_pct": "Women made up",
            "lost_time_injuries": "We recorded",
            "water_withdrawal_m3": "Total water withdrawal was"}


def _locate(doc, key, needle):
    i = doc.index(_ANCHORS[key])
    j = doc.index(needle, i) + len(needle)
    return doc[i:j]


def variants(x, rng):
    sents = x["_sentences"]
    order = sents[:1] + list(reversed(sents[1:]))
    return [("reorder_sentences", dict(x, document=" ".join(order),
                                       _sentences=order))]
