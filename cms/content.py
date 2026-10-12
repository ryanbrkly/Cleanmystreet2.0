"""Editable wording and pictures for the About and Sponsors pages.

Every piece of text on those pages is listed here with its default. Admins change
it in Admin → Page content; changes are stored in the `page_content` table and the
defaults below are used for anything not changed. Leaving a heading or title empty
hides that section or card.
"""
from flask import g
from markupsafe import Markup, escape

from .db import execute, now_iso, query

T, A, L, IMG = "text", "area", "lines", "image"  # one line, paragraphs, one item per line, picture


def _items(prefix, label, defaults):
    out = []
    for i, (title, text) in enumerate(defaults, 1):
        out.append((f"{prefix}{i}_title", f"{label} {i}: title", T, title))
        out.append((f"{prefix}{i}_text", f"{label} {i}: text", A, text))
    return out


PAGES = {
    "about": {
        "label": "About page",
        "endpoint": "public.about",
        "sections": [
            ("Top of the page", [
                ("about.hero_title", "Heading", T, "Reshaping communities, restoring value"),
                ("about.hero_lede", "Introduction", A,
                 "Clean My Street is a street cleaning and maintenance company. We make neighbourhoods look better, "
                 "feel safer and hold their value, with crews that come back to keep them that way."),
                ("about.hero_button", "Photo story button", T, "See how a street gets done"),
            ]),
            ("Getting to know us", [
                ("about.intro_heading", "Heading", T, "Getting to know us"),
                ("about.intro_body", "Text (leave a blank line between paragraphs)", A,
                 "We started in January 2024 because too many streets were falling apart. Neighbourhoods were "
                 "degrading, municipal services weren’t keeping up, and neglected verges and pavements were dragging "
                 "down the homes and businesses around them.\n\n"
                 "So we do the work ourselves: grass cutting, weed removal, clearing drains and kerbs, and the regular "
                 "streetscape maintenance that keeps a road looking cared for. Most of our clients are property owners "
                 "and business owners who know a clean street is good for everyone on it.\n\n"
                 "We proudly hire South Africans. Every clean creates paid local work for crews who know these "
                 "communities, trained in safe roadside work and kitted out in uniform and protective gear."),
                ("about.intro_image", "Picture", IMG, "static:img/gallery/island-rebuild.jpg"),
                ("about.intro_caption", "Note under the picture", T, "Rebuilding a neglected traffic island."),
            ]),
            ("Vision, mission and values", [
                ("about.vmv1_title", "Box 1: label", T, "Our vision"),
                ("about.vmv1_text", "Box 1: text", A,
                 "To make the biggest and most significant impact on communities: clean streets and restored "
                 "neighbourhoods."),
                ("about.vmv2_title", "Box 2: label", T, "Our mission"),
                ("about.vmv2_text", "Box 2: text", A,
                 "To deliver clean, well-maintained streets and public spaces, so parks stay clean, walkways stay "
                 "usable and the whole area becomes a source of community pride."),
                ("about.vmv3_title", "Box 3: label", T, "Our values"),
                ("about.vmv3_text", "Box 3: text", A,
                 "Professionalism, reliability and care for the communities we serve. We do the job properly and take "
                 "accountability for every one we finish."),
            ]),
            ("Photo story", [
                ("about.story_heading", "Heading", T, "How a street gets done"),
                ("about.story_intro", "Introduction", T, "From cones going up to the final sweep. Tap any photo to see it full size."),
                ("about.home_button", "Button on the homepage that links to the photo story", T, "See how a street gets done"),
            ]),
            ("Why a clean community matters", [
                ("about.why_heading", "Heading", T, "Why a clean community matters"),
            ] + _items("about.why", "Point", [
                ("Safer spaces for everyone", "Clearing litter and debris removes tripping hazards. Well-kept public "
                 "spaces bring neighbours together and discourage vandalism and crime."),
                ("Looking after the environment", "Less litter and pollution protects people, wildlife and rivers. "
                 "Clear drains keep stormwater flowing and help prevent flooding."),
                ("Restoring property value", "Buyers notice neglected streets. A well-maintained road makes an area "
                 "more attractive and helps protect what homes and businesses are worth."),
                ("Continuous care, not just a clean-up", "A one-off clean fades. Regular maintenance keeps a street "
                 "clean and welcoming month after month."),
            ])),
            ("Quote", [
                ("about.quote", "Quote (leave empty to hide)", A,
                 "“The more we neglect what lies beyond our walls, the smaller our space becomes.”"),
            ]),
            ("Going green and how we work", [
                ("about.green_heading", "Left heading", T, "Going green"),
                ("about.green_text", "Left text", A,
                 "We want to help bring urban spaces back to life, with work that’s good for the community, "
                 "responsible towards the environment and always getting better."),
                ("about.work_heading", "Right heading", T, "How we work with neighbours"),
            ] + _items("about.work", "Point", [
                ("Transparent", "Every rand is shown on the project page, held for that street only, with photos of the work."),
                ("Fair", "If a street doesn’t reach its goal by the deadline, everyone is refunded in full."),
            ])),
            ("Numbers", [
                ("about.stats_heading", "Heading (optional)", T, ""),
                ("about.stat_streets", "Label: streets cleaned", T, "streets cleaned"),
                ("about.stat_raised", "Label: money raised", T, "raised by neighbours"),
                ("about.stat_contributors", "Label: people who chipped in", T, "people have chipped in"),
                ("about.stat_active", "Label: streets raising funds", T, "streets raising funds now"),
            ]),
            ("Where we work", [
                ("about.where_heading", "Heading", T, "Where we work"),
                ("about.where_text", "Text ({areas} is replaced by the service areas in Site settings)", A,
                 "We currently serve {areas}. Not on the list? Request a clean anyway. Demand from your area helps us "
                 "decide where to grow next."),
                ("about.where_button", "Button", T, "Request a clean"),
            ]),
        ],
    },
    "sponsors": {
        "label": "Sponsors page",
        "endpoint": "public.sponsors",
        "sections": [
            ("Top of the page", [
                ("sponsors.hero_title", "Heading", T, "Support the businesses that support your street"),
                ("sponsors.hero_lede", "Introduction", A,
                 "These local businesses and associations put their own money into cleaner verges, drains and "
                 "pavements in the neighbourhoods they serve. When you shop with them, you help keep that going."),
            ]),
            ("Three reasons", _items("sponsors.why", "Reason", [
                ("They’re your neighbours", "Every sponsor here trades in the areas they help clean. The money stays on your streets."),
                ("Their support is real", "Each sponsor is linked to the actual projects they fund, with photos of the "
                 "work, so you can see where it went."),
                ("Your custom keeps it going", "Businesses that see a return from caring about the area keep "
                 "sponsoring. Choosing them is the easiest way to say thanks."),
            ])),
            ("Sponsor directory", [
                ("sponsors.dir_heading", "Heading", T, "Find a sponsor near you"),
                ("sponsors.dir_empty", "When there are no sponsors yet", T, "Your business could be the first name on this page."),
            ]),
            ("Ways to back them", [
                ("sponsors.ways_heading", "Heading", T, "Four easy ways to back them"),
                ("sponsors.ways_intro", "Introduction", T, "None of these cost you extra, and all of them help."),
            ] + _items("sponsors.ways", "Step", [
                ("Shop local first", "Before you buy online or drive across town, check whether a sponsor nearby sells it."),
                ("Mention Clean My Street", "Tell them you found them here. It shows them their sponsorship is noticed, "
                 "and some have a thank-you offer."),
                ("Leave a review", "A quick Google review helps a small business more than almost anything else, and it’s free."),
                ("Share them", "Post a sponsor’s page in your street or estate WhatsApp group when someone asks for a recommendation."),
            ])),
            ("For businesses", [
                ("sponsors.pitch_heading", "Heading", T, "Run a business that cares about your area?"),
                ("sponsors.pitch_lede", "Introduction", A,
                 "Clean streets bring people back. Sponsoring puts your name on the work your customers see every day."),
            ] + _items("sponsors.tier", "Option", [
                ("Top up a street", "Close the gap on a project near you that’s almost funded. Your logo goes on that project page."),
                ("Adopt a street", "Fund the monthly upkeep of a road outside your business, with your logo on the "
                 "project page and the site sign."),
                ("Area partner", "Support a set number of cleans across a suburb each quarter, with homepage placement "
                 "and a listing in this directory."),
            ]) + [
                ("sponsors.gets_heading", "Box heading", T, "What sponsors get"),
                ("sponsors.gets_list", "Box list (one item per line)", L,
                 "Your own page with your logo, story, website and neighbour offer\n"
                 "Your logo on every project you support, with before-and-after photos\n"
                 "A listing in this directory under your area\n"
                 "Optional placement on the homepage"),
                ("sponsors.gets_text", "Box text", A, "Tell us your area and budget and we’ll suggest streets that would benefit most."),
                ("sponsors.cta_button", "Button", T, "Email us about sponsoring"),
            ]),
            ("Each sponsor’s own page", [
                ("sponsor.thanks_heading", "Heading ({name} is replaced by the sponsor’s name)", T, "Say thanks to {name}"),
                ("sponsor.thanks_lede", "Introduction", A,
                 "Businesses keep sponsoring when they can see it matters to the people who live nearby."),
                ("sponsor.thanks_list", "List (one item per line)", L,
                 "Choose them next time you need what they offer\n"
                 "Tell them you saw them on Clean My Street\n"
                 "Leave them a quick online review\n"
                 "Share this page in your street or estate group"),
                ("sponsor.offer_note", "Under a thank-you offer ({name} is replaced)", T,
                 "Mention Clean My Street when you visit {name}."),
            ]),
        ],
    },
}

FIELDS = {key: (label, kind, default) for page in PAGES.values()
          for _title, fields in page["sections"] for key, label, kind, default in fields}
MAX_LEN = {T: 200, A: 3000, L: 2000, IMG: 200}


def all_values():
    if "page_content" not in g:
        values = {k: d for k, (_l, _k, d) in FIELDS.items()}
        try:
            for row in query("SELECT key, value FROM page_content"):
                if row["key"] in values or row["key"] in ("about.layout", "about.stats_show"):
                    values[row["key"]] = row["value"]
        except Exception:  # noqa: BLE001 - table appears after the next start
            pass
        g.page_content = values
    return g.page_content


def get(key, **fill):
    value = all_values().get(key, "")
    for name, repl in fill.items():
        value = value.replace("{" + name + "}", str(repl))
    return value


def save(key, value):
    label, kind, default = FIELDS[key]
    value = (value or "").replace("\r\n", "\n").strip()[:MAX_LEN[kind]]
    if value == default.strip():
        execute("DELETE FROM page_content WHERE key=?", (key,))
    else:
        execute("INSERT INTO page_content(key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now_iso()))
    g.pop("page_content", None)


def customised():
    try:
        return {r["key"] for r in query("SELECT key FROM page_content")}
    except Exception:  # noqa: BLE001
        return set()


def paragraphs(text):
    """Blank-line separated text → <p> paragraphs; single line breaks kept."""
    parts = [p.strip() for p in str(text or "").replace("\r\n", "\n").split("\n\n") if p.strip()]
    return Markup("".join(f"<p>{escape(p)}</p>".replace("\n", "<br>") for p in parts))


def lines(text):
    return [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]


# ---------------------------------------------------------------- About page layout (blocks)

import json  # noqa: E402

ABOUT_BLOCKS = [
    ("intro", "Getting to know us"),
    ("vmv", "Vision, mission and values"),
    ("story", "Photo story"),
    ("why", "Why a clean community matters"),
    ("quote", "Quote"),
    ("green", "Going green and how we work"),
    ("where", "Where we work"),
    ("stats", "Numbers (streets cleaned, money raised…)"),
]
STATS = [
    ("streets", "Streets cleaned"),
    ("raised", "Money raised"),
    ("contributors", "People who chipped in"),
    ("active", "Streets raising funds now"),
]


def custom_blocks(page="about"):
    try:
        return {r["id"]: dict(r) for r in query("SELECT * FROM page_blocks WHERE page=? ORDER BY id", (page,))}
    except Exception:  # noqa: BLE001
        return {}


def about_layout():
    """Ordered blocks for the About page: [{id, label, on, block?}]. Saved as JSON in page_content."""
    names = dict(ABOUT_BLOCKS)
    blocks = custom_blocks()
    try:
        saved = json.loads(all_values().get("about.layout") or "[]")
    except ValueError:
        saved = []
    out, seen = [], set()
    for item in saved if isinstance(saved, list) else []:
        bid = str(item.get("id", ""))
        if bid in seen:
            continue
        if bid in names:
            out.append({"id": bid, "label": names[bid], "on": bool(item.get("on", True))})
        elif bid.startswith("block:") and bid[6:].isdigit() and int(bid[6:]) in blocks:
            b = blocks[int(bid[6:])]
            out.append({"id": bid, "label": b["heading"] or "Untitled block", "on": bool(item.get("on", True)), "block": b})
        else:
            continue
        seen.add(bid)
    for bid, label in ABOUT_BLOCKS:  # new built-in blocks appear switched on
        if bid not in seen:
            out.append({"id": bid, "label": label, "on": True})
    for b in blocks.values():  # blocks never placed yet go at the end
        bid = f"block:{b['id']}"
        if bid not in seen:
            out.append({"id": bid, "label": b["heading"] or "Untitled block", "on": True, "block": b})
    return out


def save_layout(items):
    value = json.dumps([{"id": i["id"], "on": bool(i["on"])} for i in items])
    execute("INSERT INTO page_content(key, value, updated_at) VALUES ('about.layout', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (value, now_iso()))
    g.pop("page_content", None)


def stats_shown():
    raw = all_values().get("about.stats_show")
    if raw is None:
        return [k for k, _ in STATS]
    return [k for k in raw.split(",") if k in dict(STATS)]
