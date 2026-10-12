"""Job costing: what a clean costs us, what we charge, and the profit in between.

A costing is stored as JSON on the quote. All money is in cents. The same formulas run in the
browser (static/js/site.js, "costing") for live totals; this module is the one that counts when
the costing is saved.

    direct costs   = sum of lines (quantity × rate), grouped by category
    contingency    = direct × contingency %
    overhead       = (direct + contingency) × overhead %
    our cost       = direct + contingency + overhead
    charge excl VAT= our cost × (1 + markup %)            (or set by hand)
    charge         = charge excl VAT + VAT, rounded up    (this is the funding goal)
    card fees      = charge × card fee %
    profit         = charge excl VAT − our cost − card fees
"""
import json
import math

CATEGORIES = [
    ("labour", "Labour", "Crew labour"),
    ("materials", "Materials", "Materials and consumables"),
    ("equipment", "Equipment and fuel", "Equipment and fuel"),
    ("transport", "Transport", "Transport"),
    ("disposal", "Dumping and disposal", "Rubble removal and dumping"),
    ("other", "Other", "Other costs"),
]
CAT_LABEL = {k: label for k, label, _ in CATEGORIES}
CAT_PUBLIC = {k: public for k, _, public in CATEGORIES}
VAT_PCT = 15.0

DEFAULT_RATE_CARD = """labour | General worker | worker-day | 350
labour | Team supervisor | day | 600
equipment | Brush cutter (fuel and line) | machine-day | 180
equipment | Power brush or blower | machine-day | 250
materials | Refuse bags (pack of 20) | pack | 120
materials | Safety cones and signage | job | 150
transport | Bakkie and trailer | km | 6
disposal | Garden refuse dumping | load | 250
disposal | Rubble dumping | load | 450"""

SETTING_DEFAULTS = {
    "cost_contingency_pct": "5",
    "cost_overhead_pct": "10",
    "cost_markup_pct": "30",
    "cost_vat": "0",
    "cost_fee_pct": "0",
    "cost_round_to": "10",
    "cost_monthly_factor_pct": "60",
    "cost_rate_card": DEFAULT_RATE_CARD,
}


def _num(value, default=0.0, lo=0.0, hi=1e9):
    try:
        v = float(str(value).replace(",", ".").replace("R", "").replace(" ", "").replace("\xa0", "") or default)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return max(lo, min(hi, v))


def rate_card(text):
    """Parse 'category | item | unit | rate' lines."""
    out = []
    for line in str(text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4 or not parts[1]:
            continue
        cat = parts[0].lower() if parts[0].lower() in CAT_LABEL else "other"
        out.append({"cat": cat, "desc": parts[1][:120], "unit": parts[2][:30], "rate_cents": round(_num(parts[3]) * 100)})
    return out


def defaults(settings):
    s = dict(SETTING_DEFAULTS)
    s.update({k: v for k, v in (settings or {}).items() if k in SETTING_DEFAULTS and v not in (None, "")})
    return {
        "lines": [],
        "contingency_pct": _num(s["cost_contingency_pct"], 5, 0, 100),
        "overhead_pct": _num(s["cost_overhead_pct"], 10, 0, 100),
        "markup_pct": _num(s["cost_markup_pct"], 30, 0, 500),
        "vat": s["cost_vat"] in ("1", "true", "on"),
        "fee_pct": _num(s["cost_fee_pct"], 0, 0, 20),
        "round_to": _num(s["cost_round_to"], 10, 0, 1000),
        "price_override_cents": 0,
        "monthly_on": False,
        "monthly_visits": 1,
        "monthly_factor_pct": _num(s["cost_monthly_factor_pct"], 60, 1, 300),
        "auto_public": True,
    }


def from_form(form, settings):
    """Build a costing dict from the admin form (lists of line fields + options)."""
    c = defaults(settings)
    cats, descs, qtys, units, rates = (form.getlist(k) for k in ("line_cat", "line_desc", "line_qty", "line_unit", "line_rate"))
    lines = []
    for i in range(min(len(descs), 200)):
        desc = (descs[i] or "").strip()[:120]
        qty = _num(qtys[i] if i < len(qtys) else 0, 0, 0, 1e6)
        rate = _num(rates[i] if i < len(rates) else 0, 0, 0, 1e7)
        if not desc and not (qty and rate):
            continue
        cat = cats[i] if i < len(cats) and cats[i] in CAT_LABEL else "other"
        lines.append({"cat": cat, "desc": desc or CAT_LABEL[cat], "qty": round(qty, 3),
                      "unit": (units[i] if i < len(units) else "").strip()[:30], "rate_cents": round(rate * 100)})
    c["lines"] = lines
    c["contingency_pct"] = _num(form.get("contingency_pct"), c["contingency_pct"], 0, 100)
    c["overhead_pct"] = _num(form.get("overhead_pct"), c["overhead_pct"], 0, 100)
    c["markup_pct"] = _num(form.get("markup_pct"), c["markup_pct"], 0, 500)
    c["vat"] = bool(form.get("vat"))
    c["fee_pct"] = _num(form.get("fee_pct"), c["fee_pct"], 0, 20)
    c["round_to"] = _num(form.get("round_to"), c["round_to"], 0, 1000)
    c["price_override_cents"] = round(_num(form.get("price_override"), 0, 0, 1e8) * 100)
    c["monthly_on"] = bool(form.get("monthly_on"))
    c["monthly_visits"] = _num(form.get("monthly_visits"), 1, 0, 31)
    c["monthly_factor_pct"] = _num(form.get("monthly_factor_pct"), c["monthly_factor_pct"], 1, 300)
    c["auto_public"] = bool(form.get("auto_public"))
    return c


def load(text, settings):
    try:
        data = json.loads(text or "")
    except ValueError:
        data = None
    c = defaults(settings)
    if isinstance(data, dict):
        c.update({k: v for k, v in data.items() if k in c})
    return c


def _round_up(cents, round_to_rand):
    step = int(round(round_to_rand * 100))
    if step <= 0:
        return int(round(cents))
    return int(math.ceil(cents / step - 1e-9) * step)


def _price(cost, c, override=0):
    cost = int(round(cost))
    vat_rate = VAT_PCT / 100 if c["vat"] else 0.0
    if override:
        charge = int(override)
    else:
        charge = _round_up(cost * (1 + c["markup_pct"] / 100) * (1 + vat_rate), c["round_to"])
    excl = int(round(charge / (1 + vat_rate)))
    vat = charge - excl
    fees = int(round(charge * c["fee_pct"] / 100))
    profit = excl - cost - fees
    return {
        "cost": cost, "charge": charge, "excl": excl, "vat": vat, "fees": fees, "profit": profit,
        "margin_pct": round(profit * 100 / excl, 1) if excl else 0.0,
        "markup_pct": round((excl - cost) * 100 / cost, 1) if cost else 0.0,
    }


def compute(c):
    by_cat = {k: 0 for k, _, _ in CATEGORIES}
    for line in c["lines"]:
        by_cat[line["cat"]] = by_cat.get(line["cat"], 0) + int(round(line["qty"] * line["rate_cents"]))
    direct = sum(by_cat.values())
    contingency = direct * c["contingency_pct"] / 100
    overhead = (direct + contingency) * c["overhead_pct"] / 100
    cost = direct + contingency + overhead
    once = _price(cost, c, c.get("price_override_cents") or 0)
    once.update(direct=direct, contingency=int(round(contingency)), overhead=int(round(overhead)),
                by_cat={k: v for k, v in by_cat.items() if v})
    monthly = None
    if c.get("monthly_on") and c.get("monthly_visits"):
        m_cost = cost * c["monthly_factor_pct"] / 100 * c["monthly_visits"]
        monthly = _price(m_cost, c)
    return {"once": once, "monthly": monthly, "lines": [
        dict(line, total=int(round(line["qty"] * line["rate_cents"])), cat_label=CAT_LABEL[line["cat"]])
        for line in c["lines"]]}


def public_lines(result, vat):
    """Quote lines for neighbours: category totals scaled up to the charge, so they add up exactly."""
    once = result["once"]
    target = once["excl"]
    cats = [(k, v) for k, v in once["by_cat"].items() if v]
    if not cats or not target:
        return []
    total = sum(v for _, v in cats)
    lines, running = [], 0
    for i, (k, v) in enumerate(cats):
        cents = target - running if i == len(cats) - 1 else int(round(v / total * target / 1000) * 1000)
        running += cents
        lines.append({"label": CAT_PUBLIC[k], "cents": cents})
    if vat and once["vat"]:
        lines.append({"label": f"VAT ({VAT_PCT:g}%)", "cents": once["vat"]})
    return lines
