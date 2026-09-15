"""Bean dossier: what is known about one green coffee before it is roasted.

The profile designer needs more than the twelve flavor scores the analyzer
already fetches from find-coffee: it needs the supplier's roast-level
recommendation ("City to Full City+"), the process, the variety and
altitude (density), and any roasting notes the supplier wrote. find-coffee
stores the product URL and a blurb (`attributes`) that contains the roast
range as free text; the Sweet Maria's product page has the rest. This
module pulls both, optionally asks the model to research the coffee on the
web, and stores the result as `beans/<slug>/dossier.json`, next to the
bean's profile versions. Re-running appends a version note rather than
overwriting silently.

`dossier_block` renders one text block for prompts. It accepts a dossier or
the older bean-profile dict (same core keys), so the recommender uses it too
and finally sees the roast range and the full flavor vector it used to drop.
"""

import json
import re
from datetime import datetime
from html.parser import HTMLParser

from coffee_lookup import extract_bean_profile
from roast_profile import slugify, profile_dir, beans_dir

# Sweet Maria's roast names in order; the operator drinks nothing darker
# than Full City (he hates dark roast), so the target is capped there.
ROAST_LEVELS = ["Cinnamon", "City", "City+", "Full City", "Full City+", "Vienna", "French", "Italian"]
OPERATOR_MAX_LEVEL = "Full City"
# Product-page spec labels worth keeping (Sweet Maria's "Cupping Notes"
# block and specs table). Matched at line starts, case-insensitive.
PAGE_FIELDS = {
    "process": ("Process", "Processing", "Drying Method"),
    "region": ("Region", "Growing Region", "Origin"),
    "variety": ("Variety", "Cultivar", "Cultivar Type"),
    "altitude": ("Altitude", "Elevation", "Growing Altitude"),
    "arrival": ("Arrival", "Arrival Date"),
    "grade": ("Grade", "Appearance"),
    "roast_notes": ("Roast Recommendations", "Roast Recommendation", "Roast"),
}
PAGE_EXCERPT_CHARS = 6000
FETCH_TIMEOUT_S = 10
USER_AGENT = "coffee-roasting dossier (personal home-roasting tool)"


# ------------------------------------------------------------ roast levels

def parse_roast_recommendation(text):
    """'…City to Full City+…' -> {"min": "City", "max": "Full City+"} or None.

    Longest names are matched first so "Full City+" is not read as "City".
    """
    if not text:
        return None
    names = sorted(ROAST_LEVELS, key=len, reverse=True)
    pattern = "|".join(re.escape(n) for n in names)
    found = [m.group(0) for m in re.finditer(pattern, text)]
    if not found:
        return None
    idx = sorted(ROAST_LEVELS.index(n) for n in found)
    return {"min": ROAST_LEVELS[idx[0]], "max": ROAST_LEVELS[idx[-1]]}


def target_level(roast_range, cap=OPERATOR_MAX_LEVEL):
    """The operator's target: the lower-middle of the supplier's range, never
    darker than `cap`."""
    if not roast_range:
        return "City+"
    lo, hi = ROAST_LEVELS.index(roast_range["min"]), ROAST_LEVELS.index(roast_range["max"])
    cap_i = ROAST_LEVELS.index(cap)
    pick = min((lo + hi) // 2, cap_i)          # lower-middle, never past the cap
    pick = max(pick, min(lo, cap_i))            # but not under the supplier's floor
    return ROAST_LEVELS[pick]


# ------------------------------------------------------------ product page

class _TextExtractor(HTMLParser):
    """Visible text of an HTML page, one line per block element."""
    _BLOCK = {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "br", "dt", "dd", "section"}
    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def fetch_product_page(url, timeout=FETCH_TIMEOUT_S):
    """The page HTML, or None on any failure (network is optional here)."""
    if not url:
        return None
    try:
        import requests
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception:
        return None


def parse_product_page(html):
    """Spec fields and an excerpt from a product page's visible text."""
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:
        return {}
    text = parser.text()
    lines = text.splitlines()
    fields = {}
    for key, labels in PAGE_FIELDS.items():
        for i, line in enumerate(lines):
            for label in labels:
                # "Process: Wet Process" on one line, or the label alone with
                # the value on the next line (a spec table cell)
                m = re.match(rf"^{re.escape(label)}\s*[:\-]\s*(.+)$", line, re.IGNORECASE)
                if m:
                    value = m.group(1).strip()
                elif line.strip().lower() == label.lower() and i + 1 < len(lines):
                    value = lines[i + 1].strip()
                else:
                    continue
                if value and key not in fields:
                    fields[key] = value[:300]
            if key in fields:
                break
    # An excerpt for the designer to read what the parser missed: from the
    # first "Cupping Notes"/"Roast" heading if present, else the top
    start = 0
    for anchor in ("Cupping Notes", "Roast Recommendations", "Overview"):
        pos = text.find(anchor)
        if pos > 0:
            start = pos
            break
    fields["page_excerpt"] = text[start:start + PAGE_EXCERPT_CHARS]
    return fields


# ------------------------------------------------------------ dossier

def dossier_path(slug):
    return profile_dir(slug) / "dossier.json"


def build_dossier(bean_name, coffee_data=None, fetch_page=True, fetcher=fetch_product_page):
    """Assemble a dossier from a find-coffee record (may be None) and its
    product page. Every source fails soft; the dossier says what it has."""
    profile = extract_bean_profile(coffee_data) if coffee_data else {}
    name = profile.get("name") or bean_name
    url = (coffee_data or {}).get("url") or ""
    attributes = profile.get("attributes") or ""
    dossier = {
        "slug": slugify(bean_name),
        "name": name,
        "aliases": sorted({bean_name, name} - {name}) if name != bean_name else [],
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source_url": url,
        "cupping_notes": profile.get("cupping_notes") or "",
        "overall_score": profile.get("overall_score"),
        "chart_score": profile.get("chart_score"),
        "flavor_scores": profile.get("flavor_scores") or {},
        "cupping_scores": profile.get("cupping_scores") or {},
        "dominant_flavors": profile.get("dominant_flavors") or [],
        "attributes": attributes,
        "roast_range": parse_roast_recommendation(attributes),
        "target_level": None,
        "process": "", "region": "", "variety": "", "altitude": "", "arrival": "", "grade": "",
        "roast_notes": "",
        "page_excerpt": "",
        "page_fetched": False,
        "research": None,
        "versions": [],
    }
    if fetch_page and url:
        html = fetcher(url)
        if html:
            fields = parse_product_page(html)
            dossier["page_fetched"] = True
            for key, value in fields.items():
                if value and not dossier.get(key):
                    dossier[key] = value
            if not dossier["roast_range"]:
                dossier["roast_range"] = (parse_roast_recommendation(fields.get("roast_notes", ""))
                                          or parse_roast_recommendation(fields.get("page_excerpt", "")))
    if not dossier["roast_range"]:
        dossier["roast_range"] = parse_roast_recommendation(dossier["cupping_notes"])
    dossier["target_level"] = target_level(dossier["roast_range"])
    return dossier


def load_dossier(slug):
    path = dossier_path(slug)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_dossier(dossier):
    """Write the dossier; when one exists, note which fields changed."""
    slug = dossier["slug"]
    previous = load_dossier(slug)
    versions = list((previous or {}).get("versions") or [])
    if previous:
        changed = [k for k in dossier if k not in ("fetched_at", "versions", "research")
                   and previous.get(k) != dossier.get(k)]
        versions.append({"fetched_at": dossier["fetched_at"], "changed": changed})
        if dossier.get("research") is None and previous.get("research"):
            dossier["research"] = previous["research"]
        # aliases accumulate
        dossier["aliases"] = sorted(set(dossier.get("aliases", [])) | set(previous.get("aliases", [])))
    else:
        versions.append({"fetched_at": dossier["fetched_at"], "changed": ["created"]})
    dossier["versions"] = versions
    profile_dir(slug, create=True)
    path = dossier_path(slug)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dossier, indent=2, default=str))
    tmp.replace(path)
    return path


def find_dossier_for_title(title):
    """The dossier whose slug or aliases match an Artisan roast title."""
    if not title:
        return None
    slug = slugify(title)
    direct = load_dossier(slug)
    if direct:
        return direct
    root = beans_dir()
    if not root.exists():
        return None
    for d in root.iterdir():
        doc = load_dossier(d.name)
        if not doc:
            continue
        names = {doc.get("name", "")} | set(doc.get("aliases", []))
        if any(slugify(n) == slug for n in names if n):
            return doc
    return None


def add_alias(dossier, title):
    """Record an Artisan title that names this bean (for the lookup above)."""
    if title and slugify(title) != dossier["slug"] and title not in dossier.get("aliases", []):
        dossier.setdefault("aliases", []).append(title)
    return dossier


# ------------------------------------------------------------ research

_RESEARCH_SYSTEM = """\
You research green coffee for a home roaster with a Hottop KN-8828B-2K+ (a
250 g drum roaster) who brews only with a Hario Switch and dislikes dark
roasts. Search the web for the coffee named below (the supplier's page and
what other roasters report) and write a short plain-text brief with these
headings, in this order:

ORIGIN: process, variety, altitude, harvest/arrival, anything about density
or screen size.
ROASTING: the supplier's roast recommendation and any reported tendencies
(bakes easily, scorches, tips, quiet first crack, needs a long Maillard,
shines at a light or a darker roast). Quote roast levels as the supplier
names them (City, City+, Full City ...).
FLAVOR: what to expect in the cup at a light-medium roast, in plain words.
SOURCES: one URL per line.

Say when something could not be found. Do not invent numbers.
"""


def research_bean(dossier, call=None):
    """Ask the model to research the coffee on the web; stores the brief and
    the URLs under `dossier["research"]`. Returns the status string."""
    from llm_client import research_call
    call = call or research_call
    lines = [f"Coffee: {dossier.get('name')}"]
    if dossier.get("source_url"):
        lines.append(f"Supplier page: {dossier['source_url']}")
    for key in ("process", "region", "variety", "altitude"):
        if dossier.get(key):
            lines.append(f"{key.capitalize()} (from the supplier page): {dossier[key]}")
    if dossier.get("attributes"):
        lines.append(f"Supplier blurb: {dossier['attributes'][:600]}")
    result, status = call(_RESEARCH_SYSTEM, "\n".join(lines))
    if not result:
        return status
    dossier["research"] = {
        "text": result["text"], "urls": result.get("urls", []),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    return "ok"


# ------------------------------------------------------------ rendering

def dossier_block(doc):
    """One text block for prompts from a dossier (or a bean_profile dict)."""
    if not doc:
        return "No bean profile available."
    parts = []
    name = doc.get("name")
    if name:
        parts.append(f"Bean: {name}")
    for key, label in (("process", "Process"), ("region", "Region"), ("variety", "Variety"),
                       ("altitude", "Altitude"), ("arrival", "Arrival"), ("grade", "Grade")):
        if doc.get(key):
            parts.append(f"{label}: {doc[key]}")
    rr = doc.get("roast_range") or parse_roast_recommendation(doc.get("attributes") or "")
    if rr:
        line = f"Supplier roast range: {rr['min']} to {rr['max']}"
        tl = doc.get("target_level") or target_level(rr)
        line += f"; this operator's target: {tl} (never darker than {OPERATOR_MAX_LEVEL})"
        parts.append(line)
    if doc.get("attributes"):
        parts.append(f"Supplier description: {doc['attributes']}")
    if doc.get("roast_notes"):
        parts.append(f"Supplier roast notes: {doc['roast_notes']}")
    notes = doc.get("cupping_notes")
    if notes:
        parts.append(f"Professional cupping notes: {notes}")
    flavors = doc.get("flavor_scores")
    if flavors:
        parts.append("Flavor scores (0-10): " + json.dumps(flavors))
    elif doc.get("dominant_flavors"):
        flav = ", ".join(f"{n} ({s})" for n, s in doc["dominant_flavors"])
        parts.append(f"Dominant flavors: {flav}")
    cupping = doc.get("cupping_scores")
    if cupping:
        parts.append("Cupping scores: " + json.dumps(cupping))
    if doc.get("overall_score"):
        parts.append(f"Supplier score: {doc['overall_score']}")
    research = doc.get("research") or {}
    if research.get("text"):
        parts.append("Web research brief:\n" + research["text"].strip())
    return "\n".join(parts) if parts else "No bean profile available."
