"""Bean dossier: roast-range parsing, page parsing, storage, prompt block."""

import json

import bean_dossier as bd

ATTRIBUTES = ("Rwanda Rusizi Gaseke. Raw sugar and milk chocolate, bright flashes of fruit: "
              "plum tea, citrus, red berry, candied ginger. City to Full City+. Good for espresso.")

PAGE = """<html><head><title>Rwanda Rusizi Gaseke</title><style>.x{}</style>
<script>var nav = 1;</script></head><body>
<div class="nav">Home | Green Coffee | Rwanda</div>
<h1>Rwanda Rusizi Gaseke</h1>
<div class="specs"><table>
<tr><td>Process</td><td>Wet Process</td></tr>
<tr><td>Region</td><td>Rusizi District, Western Province</td></tr>
<tr><td>Variety</td><td>Bourbon</td></tr>
<tr><td>Altitude</td><td>1700 - 1900 meters</td></tr>
<tr><td>Arrival Date</td><td>June 2026</td></tr>
</table></div>
<h2>Cupping Notes</h2>
<p>Roast Recommendations: City to Full City+. Chocolate roast tones have a bigger impact at Full City.</p>
<p>Gaseke's sweetness was impressive in both light and darker roasts.</p>
</body></html>"""


def test_parse_roast_recommendation_reads_the_range_and_target():
    assert bd.parse_roast_recommendation(ATTRIBUTES) == {"min": "City", "max": "Full City+"}
    assert bd.parse_roast_recommendation("City+ only") == {"min": "City+", "max": "City+"}
    assert bd.parse_roast_recommendation("Full City to Vienna") == {"min": "Full City", "max": "Vienna"}
    assert bd.parse_roast_recommendation("no levels here") is None
    assert bd.target_level({"min": "City", "max": "Full City+"}) == "City+"
    assert bd.target_level({"min": "Full City", "max": "Vienna"}) == "Full City"     # capped
    assert bd.target_level({"min": "Vienna", "max": "French"}) == "Full City"        # capped
    assert bd.target_level(None) == "City+"


def test_parse_product_page_extracts_specs_and_excerpt():
    fields = bd.parse_product_page(PAGE)
    assert fields["process"] == "Wet Process"
    assert fields["region"].startswith("Rusizi")
    assert fields["variety"] == "Bourbon" and fields["altitude"].startswith("1700")
    assert fields["arrival"] == "June 2026"
    assert fields["roast_notes"].startswith("City to Full City+")
    assert "var nav" not in fields["page_excerpt"] and fields["page_excerpt"].startswith("Cupping Notes")
    assert bd.parse_product_page("") == {"page_excerpt": ""}
    # a bare "Roast" navigation link is not a roast note, and altitude in prose is found
    prose = "<p>Roast</p><p>Starter Kits</p><p>It sits at 1500 meters above sea level and farms reach 1800+ meters.</p>"
    fields = bd.parse_product_page(prose)
    assert "roast_notes" not in fields
    assert fields["altitude"] == "1500, 1800+ m (from the page text)"


def test_build_dossier_merges_find_coffee_and_page(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAST_BEANS_DIR", str(tmp_path))
    coffee = {"name": "Rwanda Rusizi Gaseke", "url": "https://example.test/gaseke",
              "attributes": ATTRIBUTES, "score": 88.7, "cupping_notes": "Sweet and clean.",
              "floral_score": 2, "honey_score": 5, "sugar_score": 8, "caramel_score": 6,
              "fruit_score": 5, "citrus_score": 4, "berry_score": 4, "cocoa_score": 7,
              "nut_score": 3, "rustic_score": 2, "spice_score": 4, "body_score": 6}
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return PAGE

    doc = bd.build_dossier("Rwanda Rusizi Gaseke", coffee, fetcher=fake_fetch)
    assert fetched == ["https://example.test/gaseke"] and doc["page_fetched"]
    assert doc["slug"] == "rwanda-rusizi-gaseke"
    assert doc["roast_range"] == {"min": "City", "max": "Full City+"} and doc["target_level"] == "City+"
    assert doc["process"] == "Wet Process" and doc["variety"] == "Bourbon"
    assert doc["flavor_scores"]["sugar"] == 8 and doc["overall_score"] == 88.7
    path = bd.save_dossier(doc)
    assert path.name == "dossier.json" and json.loads(path.read_text())["versions"][0]["changed"] == ["created"]
    # a second fetch with a changed field appends a version note and keeps research
    doc["research"] = {"text": "brief", "urls": ["https://example.test"], "at": "now"}
    bd.save_dossier(doc)
    doc2 = bd.build_dossier("Rwanda Rusizi Gaseke", dict(coffee, cupping_notes="Changed."), fetcher=fake_fetch)
    bd.save_dossier(doc2)
    stored = bd.load_dossier("rwanda-rusizi-gaseke")
    assert len(stored["versions"]) == 3 and "cupping_notes" in stored["versions"][-1]["changed"]
    assert stored["research"]["text"] == "brief"
    # alias lookup by an Artisan title that differs
    bd.add_alias(stored, "Rwanda Gaseke lot 2")
    bd.save_dossier(stored)
    assert bd.find_dossier_for_title("Rwanda Gaseke Lot 2")["slug"] == "rwanda-rusizi-gaseke"
    assert bd.find_dossier_for_title("Kenya") is None


def test_build_dossier_without_find_coffee_or_page(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAST_BEANS_DIR", str(tmp_path))
    doc = bd.build_dossier("Mystery Bean", None, fetcher=lambda url: None)
    assert doc["name"] == "Mystery Bean" and not doc["page_fetched"]
    assert doc["roast_range"] is None and doc["target_level"] == "City+"


def test_dossier_block_includes_range_process_and_all_flavors():
    doc = {"name": "Rwanda", "attributes": ATTRIBUTES, "process": "Wet Process",
           "flavor_scores": {"floral": 2, "sugar": 8}, "cupping_notes": "Sweet.",
           "research": {"text": "ORIGIN: Bourbon at 1800 m."}}
    text = bd.dossier_block(doc)
    assert "Supplier roast range: City to Full City+; this operator's target: City+" in text
    assert "Process: Wet Process" in text and '"floral": 2' in text
    assert "Web research brief:" in text and "Bourbon" in text
    # the old bean_profile shape renders too
    old = {"name": "Bolivia", "cupping_notes": "Nutty.", "dominant_flavors": [("nut", 8)],
           "cupping_scores": {"body": 8.5}, "attributes": "City to Full City+."}
    text = bd.dossier_block(old)
    assert "Dominant flavors: nut (8)" in text and "City to Full City+" in text
    assert bd.dossier_block(None) == "No bean profile available."


def test_research_bean_stores_text_and_urls():
    doc = {"name": "Rwanda", "source_url": "https://example.test", "process": "Wet"}
    calls = []

    def fake_call(system, content, max_uses=3):
        calls.append((content, max_uses))
        return {"text": "I'll research this for you.\nLet me search.\nORIGIN: ...\nSOURCES:\nhttps://example.test",
                "urls": ["https://example.test"]}, "ok"

    doc["page_excerpt"] = "Specs\nregion\nRusizi"
    assert bd.research_bean(doc, call=fake_call) == "ok"
    assert doc["research"]["urls"] == ["https://example.test"]
    assert doc["research"]["text"].startswith("ORIGIN:")                     # narration stripped
    content, max_uses = calls[0]
    assert "Supplier page" in content and "do not re-search" in content and "Rusizi" in content
    assert max_uses == bd.RESEARCH_MAX_SEARCHES
    assert bd.research_bean(doc, call=lambda s, c, max_uses=3: (None, "no API credentials")) == "no API credentials"
