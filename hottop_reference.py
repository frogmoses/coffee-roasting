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


HOTTOP_DYNAMICS = """\
Measured on this machine (roasts #8-#26, June-September 2026):
- A single -10% heater step shows in ET only after 60-70 s of dead time and
  stays inside BT rate-of-rise noise (+/-1 F/min) for at least two minutes.
  A 10% step is a trim, not steering. Steering is the charge state and 20-30%
  moves made 1-2 minutes ahead of where they should act.
- Drum heat-soak dominates batch-to-batch variance: later batches on the same
  day reach first crack 15-35 s sooner, and ET at charge does not show it.
  The warm-up routine (empty drum to ~370F, heater off and fan up to cool to
  300F, heater 100%, charge) cut the FC spread on one day from 92 s to 18 s.
- Ambient moves first crack: with the same schedule FC drifted from 9:16 in
  July to about 11:00 at 69F outdoors in September. Time-based moves land
  within +/-3 s; BT at those moves runs 4-16F lower on a cool day.
- Post-FC BT rise at heater 60-70% is 7-9.5 F/min, close to linear, so drop
  BT is FC BT plus that rise times development minutes.
- The bean temperature falls from the 300F charge to a turning point near
  160-170F at about 60 s, then rises; the DRY mark (300F) comes at ~6:00-6:40
  and first crack at 10:30-11:30 in cool weather, 9:00-10:00 in warm.
- The ear (microphone first-crack detector) declares first crack 17-18 s
  after the first cracks; the curve check grades a mark to about +/-25 s.
"""

OPERATOR_PREFS = """\
The operator:
- brews only with a Hario Switch (immersion then drain), never espresso;
- dislikes dark roast and stops long batches near 3:00 after first crack
  himself; development is capped at 180 s and never darker than Full City;
- charges ~200 g at 300F after the warm-up routine, in manual mode;
- wants Maillard length under control as the next lever after development;
- is not a trained cupper: verdicts arrive as the intake axes below
  (balance sour<->roasty, astringency, sweetness, body, preference vs the
  previous batch, drink again), which outrank flavor-wheel words.
"""
