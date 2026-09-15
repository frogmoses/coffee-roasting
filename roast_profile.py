"""Versioned roast profiles, one directory per bean.

A profile is a designed schedule plus everything needed to judge it later:
what it was based on, what it predicted, which roasts ran on it, how they
executed, and how they cupped. Versions are never edited in place once
exported; a revision is a new version. The operator marks the version that
tasted right as the bean's reference, and later revisions delta against it.

    beans/<slug>/
      dossier.json            the bean research (bean_dossier.py)
      profile-v1.json ...     one file per version
      reference.json          {"version": N, "roast_id": ..., "set_at": ...}
      alarms/profile-vN.alrm  what the roaster loads (alarm_export.py)
      sheet-vN.html           the printable session sheet (session_sheet.py)

The directory is committed: like `artisan/Artisan.conf` it is the reviewed
truth of what the roaster was told to do. `ROAST_BEANS_DIR` overrides the
location (tests point it at a temp dir).
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent

PROFILE_STATUSES = ("draft", "exported", "roasted", "cupped", "reference", "superseded")


def beans_dir():
    """Root of the per-bean directories (env override for tests)."""
    return Path(os.environ.get("ROAST_BEANS_DIR") or PROJECT_DIR / "beans")


def slugify(name):
    """'Rwanda Rusizi Gaseke' -> 'rwanda-rusizi-gaseke'."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "bean"


def profile_dir(slug, create=False):
    d = beans_dir() / slug
    if create:
        (d / "alarms").mkdir(parents=True, exist_ok=True)
    return d


def profile_path(slug, version):
    return profile_dir(slug) / f"profile-v{int(version)}.json"


def alarm_path(slug, version, suffix=""):
    return profile_dir(slug) / "alarms" / f"profile-v{int(version)}{suffix}.alrm"


def sheet_path(slug, version):
    return profile_dir(slug) / f"sheet-v{int(version)}.html"


def list_versions(slug):
    """Sorted version numbers present for a bean."""
    d = profile_dir(slug)
    if not d.exists():
        return []
    out = []
    for p in d.glob("profile-v*.json"):
        m = re.fullmatch(r"profile-v(\d+)\.json", p.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def _write_json(path, obj):
    """Atomic JSON write (temp file + rename), like the history file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)


def load_profile(slug, version=None):
    """A version's profile dict (latest when `version` is None), or None."""
    versions = list_versions(slug)
    if not versions:
        return None
    v = int(version) if version is not None else versions[-1]
    path = profile_path(slug, v)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def new_profile(slug, bean, schedule, based_on, designer=None, predicted=None,
                flavor_hypothesis="", contrast_lever=None, changes=None,
                validator=None, envelope_snapshot=None, watch_for=None):
    """A draft profile dict for `save_profile` (version assigned on save)."""
    return {
        "slug": slug, "bean": bean, "version": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "designer": designer or {"model": None, "status": "deterministic", "repair_rounds": 0},
        "based_on": based_on,
        "schedule": schedule,
        "predicted": predicted or schedule.get("predicted") or {},
        "flavor_hypothesis": flavor_hypothesis,
        "contrast_lever": contrast_lever,
        "changes_from_baseline": changes or [],
        "validator": validator or {"errors": [], "warnings": [], "clamped": []},
        "envelope_snapshot": envelope_snapshot,
        "watch_for": watch_for or [],
        "exported": None,
        "roasts": [],
        "status": "draft",
    }


def save_profile(profile):
    """Write a profile; a new one gets the next version number. Returns the path."""
    slug = profile["slug"]
    if profile.get("version") is None:
        versions = list_versions(slug)
        profile["version"] = (versions[-1] + 1) if versions else 1
    path = profile_path(slug, profile["version"])
    _write_json(path, profile)
    return path


def mark_exported(profile, mode, ear_latency_s, manual_drop, files):
    """Record an export on the profile (in memory; call save_profile)."""
    profile["exported"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode, "ear_latency_s": ear_latency_s, "manual_drop": manual_drop,
        "files": [str(f) for f in files],
    }
    if profile.get("status") == "draft":
        profile["status"] = "exported"


def set_reference(slug, version, roast_id):
    """Mark a version as the bean's reference (the roast that tasted right)."""
    ref = {"version": int(version), "roast_id": roast_id,
           "set_at": datetime.now().isoformat(timespec="seconds")}
    _write_json(profile_dir(slug) / "reference.json", ref)
    prof = load_profile(slug, version)
    if prof:
        prof["status"] = "reference"
        save_profile(prof)
    return ref


def get_reference(slug):
    path = profile_dir(slug) / "reference.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def attach_roast(profile, entry, execution=None):
    """Link a history entry to the profile (in memory), replacing an earlier
    link for the same roast id."""
    m = entry.get("metrics", {})
    link = {
        "roast_id": entry.get("roast_id"), "batch_nr": entry.get("batch_nr", 0),
        "roast_date": entry.get("roast_date", ""),
        "dev_s": m.get("dev_phase_time") or None,
        "fc_time": m.get("fc_time") or None, "fc_bt": m.get("fc_bt") or None,
        "drop_bt": m.get("drop_bt") or None,
        "execution": (execution or {}).get("summary") if execution else None,
        "cupping_intake": entry.get("cupping_intake"),
        "cupping_notes": entry.get("cupping_notes") or "",
    }
    roasts = [r for r in profile.get("roasts", []) if r.get("roast_id") != link["roast_id"]]
    roasts.append(link)
    roasts.sort(key=lambda r: (r.get("roast_date", ""), r.get("batch_nr", 0)))
    profile["roasts"] = roasts
    if profile.get("status") in ("draft", "exported"):
        profile["status"] = "roasted"
    return link


def attach_verdict(profile, roast_id, intake=None, notes=""):
    """Record how a linked roast cupped (in memory). Returns the link or None."""
    for r in profile.get("roasts", []):
        if r.get("roast_id") == roast_id:
            if intake:
                r["cupping_intake"] = intake
            if notes:
                r["cupping_notes"] = notes
            if profile.get("status") in ("draft", "exported", "roasted"):
                profile["status"] = "cupped"
            return r
    return None


def match_profile_to_roast(slug, roast_date, roast_time=""):
    """The latest version exported before the roast started, or None.

    `roast_date` is ISO (YYYY-MM-DD); `roast_time` HH:MM[:SS]. A version
    exported later the same day is still "before" when the time is unknown.
    """
    stamp = f"{roast_date}T{(roast_time or '23:59')[:5]}"
    best = None
    for v in list_versions(slug):
        prof = load_profile(slug, v)
        exported = (prof or {}).get("exported") or {}
        at = exported.get("at", "")
        if at and at[:16] <= stamp:
            best = prof
    return best


def find_bean_title(history, name):
    """Resolve a CLI bean name against history titles.

    Exact (case-insensitive) match first, then a unique substring match,
    then the most recently roasted of several matches. Returns the title or
    None.
    """
    key = (name or "").strip().lower()
    if not key:
        return None
    titles = {}
    for e in history.values():
        t = (e.get("title") or "").strip()
        if t:
            titles[t] = max(titles.get(t, ""), e.get("roast_date", ""))
    for t in titles:
        if t.lower() == key:
            return t
    hits = [t for t in titles if key in t.lower() or slugify(t).startswith(slugify(name))]
    if not hits:
        return None
    hits.sort(key=lambda t: titles[t])
    return hits[-1]
