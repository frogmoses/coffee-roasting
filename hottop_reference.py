"""Hottop KN-8828B-2K+ control reference, fed to the LLM recommender.

The point of moving to an LLM recommender is advice tied to the operator's
*actual controls*. This reference describes the levers the user has on this
specific machine so the model frames its suggestions as concrete dial moves
(heater %, fan %, when to cut) rather than abstract roasting theory.

Keep this factual and machine-specific. It is prepended to the analysis
prompt as background, not as instructions.
"""

HOTTOP_CONTROLS = """\
Machine: Hottop KN-8828B-2K+, run in MANUAL mode under Artisan.

Controls the operator can change mid-roast (each logged as an event):
- HEATER: 0-100% in 10% steps. The dominant energy lever. Set high at charge,
  eased down through the roast. This is what "apply more/less heat" means in
  practice.
- FAN (airflow): 0-100% in 10% steps. Higher fan increases convective heat
  transfer and chaff clearing early, but too much too soon stalls the rise.
  Raised through development to even out the roast and manage smoke.
- DRUM: rotation speed. Rarely changed during a roast; affects bean agitation
  and evenness.
- DAMPER: usually fixed on this setup.

Regime: hot charge (~300F panel/ET), drop timed from first crack (FC), not by
a target drop temperature.

Machine reference points (BT = bean temp probe):
- The BT display flags FC as imminent around 356F.
- Hottop manual guidance: cut heat / raise fan around 340-345F, before FC, to
  ease the rate of rise into the crack.
- SAFETY: the machine ejects the beans at 408F BT unless ENTER is held — keep
  clear of it. (The Hottop manual's 395F figure is wrong; 408F is the real
  ejection point on this machine.) Deliberate Full City+ / espresso drops can
  approach this; flag the safety margin but do not treat a planned dark drop as
  an error.

How to phrase advice for this operator:
- Name the dial and the moment: "ease the heater from 100% to ~80% by 250F BT",
  "raise fan to 40% just after FC", "make one heat cut around 340F and hold it".
- The actionable development lever is seconds from FC to DROP. Drop temp and
  weight loss are OUTCOMES of that, not dials to aim at.
- Avoid vague advice like "charge hotter" when the control timeline shows the
  heater was already maxed — reach for the airflow and timing levers instead.
"""
