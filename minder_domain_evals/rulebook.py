"""Pinned rule parameters with provenance.

Every number a domain oracle depends on lives here, once, with its
source, the date it was checked, and how strongly it was checked:

- "primary"   — read from the regulator / statute text on verified_on
- "secondary" — authoritative text paywalled or unreachable; value
                corroborated by practitioner summaries plus working
                knowledge. Re-verify before relying on it externally.
- "convention" — not a legal fact: a grading convention the task
                states to the model so the answer is unique.

Perishable parameters (tax rates, thresholds) are keyed by effective
period so a case pinned to a year stays gradeable after the law moves.
Years with no legislated value are absent ON PURPOSE: a case asking for
one expects the model to abstain.
"""

RULES = {
    "sg_carbon_tax.rate": {
        "text": "Singapore carbon tax rate per tCO2e: S$25 for 2024-2025, "
                "S$45 for 2026-2027. No rate is legislated for 2028 onward "
                "(Government has indicated S$50-80 by 2030).",
        "values": {2024: 25, 2025: 25, 2026: 45, 2027: 45},
        "source": "NCCS, Carbon Tax (Carbon Pricing Act 2018)",
        "url": "https://www.nccs.gov.sg/singapores-climate-action/"
               "mitigation-efforts/carbontax/",
        "verification": "primary", "verified_on": "2026-09-28",
    },
    "sg_carbon_tax.thresholds": {
        "text": "A facility with direct (Scope 1) GHG emissions >= 2,000 "
                "tCO2e and < 25,000 tCO2e in a calendar year is a reportable "
                "facility; >= 25,000 tCO2e is a taxable facility.",
        "values": {"reportable": 2000, "taxable": 25000},
        "source": "NEA, GHG Measurement & Reporting Guidelines Part 1B v4",
        "url": "https://www.nea.gov.sg/docs/default-source/default-document-"
               "library/ghg-mr-guideline-part-1b-introduction-to-the-ghg-"
               "measurement-and-reporting-requirements-for-the-taxable-"
               "facility-ver-4.pdf",
        "verification": "primary", "verified_on": "2026-09-28",
    },
    "sg_carbon_tax.icc_offset": {
        "text": "From 2024 a taxable facility may surrender eligible "
                "international carbon credits to offset up to 5% of its "
                "taxable emissions.",
        "values": {"max_share": "0.05"},
        "source": "NCCS, Carbon Tax",
        "url": "https://www.nccs.gov.sg/singapores-climate-action/"
               "mitigation-efforts/carbontax/",
        "verification": "primary", "verified_on": "2026-09-28",
    },
    "sg_gst.rate": {
        "text": "Singapore GST standard rate: 7% before 1 Jan 2023, 8% from "
                "1 Jan 2023, 9% from 1 Jan 2024. Zero-rated supplies bear "
                "0%; exempt supplies bear no GST.",
        "values": {"2022": 7, "2023": 8, "2024+": 9},
        "source": "IRAS, Overview of GST Rate Change",
        "url": "https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/"
               "gst-rate-change/gst-rate-change-for-business/"
               "overview-of-gst-rate-change",
        "verification": "primary", "verified_on": "2026-09-28",
        "caveat": "9% from 1 Jan 2024 confirmed on IRAS; the 7%->8% step on "
                  "1 Jan 2023 is from working knowledge.",
    },
    "pdpa.breach": {
        "text": "PDPA Part 6A: a data breach is notifiable if it results or "
                "is likely to result in significant harm to an affected "
                "individual (s26B(1)(a)), or is of significant scale, i.e. "
                "affects not fewer than 500 individuals (s26B(1)(b); PDP "
                "(Notification of Data Breaches) Regs 2021 reg 4). The "
                "Commission must be notified no later than 3 calendar days "
                "after the day the organisation assesses the breach as "
                "notifiable (s26D(1)). Affected individuals must be notified "
                "where the breach is notifiable because of significant harm, "
                "unless remedial action renders significant harm unlikely or "
                "a pre-existing technological measure does so (s26D(2),(5)).",
        "values": {"scale": 500, "days": 3},
        "source": "PDPA 2012 Part 6A; PDP (Notification of Data Breaches) "
                  "Regulations 2021",
        "url": "https://sso.agc.gov.sg/Act/PDPA2012",
        "verification": "primary",
        "caveat": "Scope of the individual-notification duty (harm limb only) "
                  "is from working knowledge; the s26D(1) deadline and reg 4 "
                  "number were read from SSO on verified_on.",
        "verified_on": "2026-09-28",
    },
    "ucp600.presentation": {
        "text": "UCP 600 Art 14(c): a presentation including an original "
                "transport document must be made not later than 21 calendar "
                "days after the date of shipment (unless the credit states "
                "another period), and in any event not later than the expiry "
                "date. Art 14(b): the bank has a maximum of five banking days "
                "following the day of presentation to examine it. Art 29(a): if the "
                "expiry date or last day for presentation falls on a day the "
                "bank is closed (other than force majeure under Art 36), it is "
                "extended to the first following banking day; Art 29(c): the "
                "latest date for shipment is not extended.",
        "values": {"default_days": 21, "exam_banking_days": 5},
        "source": "ICC Publication No. 600 (2007)",
        "url": "https://ssltglobal.com/tools/ucp-600",
        "verification": "secondary", "verified_on": "2026-09-28",
    },
    "ucp600.tolerance": {
        "text": "UCP 600 Art 30(a): 'about' or 'approximately' allows a "
                "tolerance of 10% more or less. Art 30(b): absent that, a "
                "tolerance of 5% more or less in quantity is allowed if the "
                "credit does not state quantity in packing units or "
                "individual items and total drawings do not exceed the "
                "credit amount.",
        "values": {"about": "0.10", "bulk_qty": "0.05"},
        "source": "ICC Publication No. 600 (2007)",
        "url": "https://ssltglobal.com/tools/ucp-600",
        "verification": "secondary", "verified_on": "2026-09-28",
    },
    "incoterms2020": {
        "text": "Incoterms 2020 delivery/risk points: EXW goods at buyer's "
                "disposal at seller's premises, not loaded; FAS alongside the "
                "vessel at port of shipment; FOB/CFR/CIF on board the vessel "
                "at port of shipment; DAP/DDP at buyer's disposal on the "
                "arriving means of transport ready for unloading; DPU once "
                "unloaded at the named place. Seller contracts main carriage "
                "under CPT, CIP, CFR, CIF, DAP, DPU, DDP. Seller insures under "
                "CIF (Institute Cargo Clauses C minimum) and CIP (Clauses A "
                "minimum). Seller clears export under all terms except EXW; "
                "seller clears import only under DDP. FAS, FOB, CFR, CIF are "
                "sea and inland waterway terms.",
        "source": "ICC Incoterms 2020 (Publication No. 723E)",
        "url": "https://iccwbo.org/business-solutions/incoterms-rules/",
        "verification": "secondary", "verified_on": "2026-09-28",
    },
    "ifrs16.initial": {
        "text": "IFRS 16 paras 24 and 26: the lease liability is the present "
                "value of lease payments not paid at commencement, discounted "
                "at the rate implicit in the lease or, if not readily "
                "determinable, the incremental borrowing rate. The right-of-use "
                "asset = lease liability + payments made at or before "
                "commencement - lease incentives received + initial direct "
                "costs + estimated restoration costs.",
        "source": "IFRS 16 Leases",
        "url": "https://www.ifrs.org/issued-standards/list-of-standards/"
               "ifrs-16-leases/",
        "verification": "secondary", "verified_on": "2026-09-28",
    },
    "ghg.scope2_location": {
        "text": "GHG Protocol Scope 2 location-based method: emissions = "
                "electricity consumed x grid-average emission factor.",
        "source": "GHG Protocol Scope 2 Guidance (2015)",
        "url": "https://ghgprotocol.org/scope-2-guidance",
        "verification": "secondary", "verified_on": "2026-09-28",
    },
    "limitation.contract_tort": {
        "text": "Limitation Act 1959 s 6(1)(a): an action founded on "
                "contract or on tort may not be brought after the expiration "
                "of six years from the date on which the cause of action "
                "accrued. A claim filed on or before the sixth anniversary "
                "is in time; filed after it is time-barred.",
        "values": {"years": 6},
        "source": "Limitation Act 1959 (Singapore), s 6",
        "url": "https://sso.agc.gov.sg/Act/LA1959",
        "verification": "secondary", "verified_on": "2026-10-05",
        "caveat": "Act title and the s 6 heading were read on Singapore "
                  "Statutes Online on this date, but the section body "
                  "renders progressively and the six-year figure could not "
                  "be read from the primary text in this session; "
                  "re-verify against the section before external reliance.",
    },
    "ea.salary_payment": {
        "text": "Employment Act 1968 s 11: salary must be paid to the "
                "employee no later than 7 days after the end of the salary "
                "period in which it is earned. Payment on the 7th day is "
                "in time; later is late.",
        "values": {"days": 7},
        "source": "Employment Act 1968 (Singapore), s 11; MOM guidance",
        "url": "https://www.mom.gov.sg/employment-practices/salary",
        "verification": "secondary", "verified_on": "2026-10-05",
        "caveat": "MOM returned HTTP 403 and SSO renders the Act "
                  "progressively, so the 7-day figure is corroborated from "
                  "secondary knowledge on this date; re-verify before "
                  "external reliance.",
    },
    "convention.rounding": {
        "text": "Monetary results round half-up to 2 decimal places at the "
                "step stated in the task; intermediate values are unrounded.",
        "verification": "convention",
    },
}


def excerpt(rule_ids):
    """Rule text block for an open-book prompt."""
    return "\n".join(f"[{rid}] {RULES[rid]['text']}" for rid in rule_ids)


def provenance_table():
    rows = []
    for rid, rule in RULES.items():
        rows.append({"rule": rid, "verification": rule["verification"],
                     "verified_on": rule.get("verified_on"),
                     "source": rule.get("source"), "url": rule.get("url"),
                     "caveat": rule.get("caveat")})
    return rows
