"""Development history measured from the true first crack (review item B6),
and the borrowed-envelope note the designer reads."""

import machine_envelope as me
import profile_designer as pd


def test_corrected_dev_uses_the_ear_when_the_mark_was_off():
    # batch #21: marked 49 s before the ear heard first crack
    metrics = {"dev_phase_time": 116.0,
               "fc_audio": {"offset": 49, "mark_suspect": True},
               "fc_check": {"offset": 46, "mark_suspect": True}}
    assert me.corrected_dev(metrics) == (67.0, "ear")


def test_corrected_dev_falls_back_to_the_curve_then_the_mark():
    assert me.corrected_dev({"dev_phase_time": 120.0,
                             "fc_check": {"offset": -40, "mark_suspect": True}}) == (160.0, "curve")
    # offsets inside the tolerance leave the mark standing
    assert me.corrected_dev({"dev_phase_time": 152.0,
                             "fc_audio": {"offset": -10, "mark_suspect": False}}) == (152.0, "mark")
    assert me.corrected_dev({}) == (None, None)


def test_dev_history_rows_carry_both_figures():
    entries = [{"batch_nr": 21, "roast_date": "2026-09-04",
                "metrics": {"dev_phase_time": 116.0, "fc_audio": {"offset": 49, "mark_suspect": True}}}]
    row = me._dev_history(entries)[0]
    assert row["dev_s"] == 116.0 and row["dev_s_corrected"] == 67.0
    assert row["dev_source"] == "ear" and row["mark_suspect"] is True


def test_best_dev_reasons_with_the_corrected_figure():
    env = {"dev_history": [
        {"dev_s": 116, "dev_s_corrected": 67, "cupped": True, "drink_again": "yes", "balance": 0},
        {"dev_s": 134, "dev_s_corrected": 134, "cupped": True, "drink_again": "meh", "balance": 0},
    ]}
    assert pd._best_dev(env) == 67


def test_borrowed_note_says_the_verdicts_belong_to_another_bean():
    text = pd._envelope_block({"borrowed_from": "Rwanda Rusizi Gaseke", "count": 6,
                               "dev_history": [{"batch": 21, "dev_s": 116, "dev_s_corrected": 67}]})
    assert "borrowed from Rwanda Rusizi Gaseke" in text
    assert "Rwanda Rusizi Gaseke's, not this bean's" in text
    assert "Reason with dev_s_corrected" in text
