"""Ambient conditions flow from the .alog into metrics, display, and prompts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm_recommender import _curated_metrics, _prior_block
from roast_analysis import select_prior_roasts
from roast_display import display_roast_summary
from roast_metrics import extract_metrics
from roast_parser import extract_roast_data


def _raw(ambient=69.0):
    timex = [float(i) for i in range(0, 900, 2)]
    n = len(timex)
    return {
        "title": "Rwanda", "roastisodate": "2026-09-13", "roasttime": "10:43", "roastbatchnr": 24,
        "timex": timex, "temp1": [330.0] * n, "temp2": [300.0] * n,
        "timeindex": [0, 150, 300, 0, 0, 0, n - 1, 0], "mode": "F",
        "ambientTemp": ambient, "ambient_humidity": 0.0, "ambient_pressure": 0.0,
        "specialevents": [], "specialeventstype": [], "specialeventsvalue": [],
        "computed": {"totaltime": 898, "weightin": 200, "weightout": 0},
    }


def test_ambient_reaches_metrics_display_and_prompt():
    data = extract_roast_data(_raw())
    assert data["ambient_temp"] == 69.0
    metrics = extract_metrics(data)
    assert metrics["ambient_temp"] == 69.0
    text = display_roast_summary({"title": "Rwanda", "metrics": metrics})
    assert "Ambient: 69F" in text
    assert _curated_metrics(metrics)["ambient_temp"] == 69.0

    history = {"24_Rwanda_2026-09-13": {"title": "Rwanda", "roast_date": "2026-09-13", "batch_nr": 24,
                                        "metrics": metrics}}
    prior = select_prior_roasts(history, "25_Rwanda_2026-09-13", "Rwanda", "2026-09-13", 25)
    assert prior[0]["metrics"]["ambient_temp"] == 69.0
    assert "ambient 69F" in _prior_block(prior)


def test_ambient_blank_stays_silent():
    metrics = extract_metrics(extract_roast_data(_raw(ambient=0.0)))
    assert metrics["ambient_temp"] == 0
    assert "Ambient" not in display_roast_summary({"title": "Rwanda", "metrics": metrics})
    assert "ambient_temp" not in _curated_metrics(metrics)
