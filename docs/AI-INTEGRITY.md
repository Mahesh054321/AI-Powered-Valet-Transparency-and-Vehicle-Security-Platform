# AI integrity statement

This document exists so that an evaluator never has to guess which parts of the
platform are learned models, which are deterministic algorithms, and which are
simulated inputs. It is deliberately blunt.

## 1. Nothing in this project is a pretrained deep-learning model

There is **no** CNN, no YOLO, no ViT, no transformer, and no downloaded
checkpoint anywhere in this repository. If a future phase introduces one, it
will be named explicitly here, with its dataset, training script, metrics and
limitations.

**One component is a trained model, and it is called out here rather than
buried.** The security classifier (logistic regression or random forest, in
`backend/ai/ml/`) *is* fitted by a training script, and its weights are learned
rather than written by hand. It is not pretrained, not deep, and not downloaded:
it is trained locally on synthetic telemetry by
`utils/scripts/train_security_model.py`, from a dataset whose labels were also
written by hand. That distinction matters and is the subject of §4b.

What the platform does use:

| Module | Technique | Trained? |
| --- | --- | --- |
| Vehicle condition assessment | OpenCV classical computer vision: colour-space differencing, edge/gradient comparison, morphological filtering, contour analysis | No — deterministic |
| Image alignment | ORB/SIFT feature matching + RANSAC homography | No — deterministic |
| Geofence anomaly detection | Haversine distance, point-in-polygon, speed/heading thresholds, dwell-time rules | No — deterministic rules |
| Security classification (advisory) | Logistic regression or random forest over 8 telemetry features, scikit-learn | **Yes** — locally, on synthetic data. Prototype only. See §4b |
| Retrieval-time prediction | Ridge regression over 11 features, scikit-learn, trained **offline** by a script and loaded from a JSON artifact | **Yes** — locally, on synthetic data. Baseline only. See §5 |

## 2. Why the condition assessment is a heuristic, not a classifier

A scratch/dent classifier needs a labelled dataset of vehicle damage under
controlled lighting. We do not have one, and inventing one would mean claiming
performance we cannot substantiate. Claiming "AI detects scratches with 94%
accuracy" without a dataset, a test split and a confusion matrix would be
academic dishonesty.

Instead the pipeline produces a **candidate finding with evidence**, and the
human reviewer decides. That is the honest framing, and it is also what real
valet operations need: the operator must see *why* a region was flagged.

The word "detection" in the UI is always qualified: findings are reported as
*possible* damage with a confidence score and a rendered evidence image.

## 2a. What the condition pipeline actually does, and where it fails

The condition assessment is implemented in `backend/ai/vision/`. It is worth
being precise about both its behaviour and its failure modes, because the
difference between them is the difference between a useful reviewer aid and a
liability.

**The thirteen steps, in the modules that implement them:**

| Steps | Module | Technique |
| --- | --- | --- |
| 1-3 load, validate, resize | `preprocessing.py` | header probe, dimension check, fixed working resolution |
| 4-5 normalise, colour space | `preprocessing.py` | median-based exposure gain, CIELAB with a/b centred |
| 6 align | `alignment.py` | ORB features + Lowe's ratio test + RANSAC homography |
| 7-9 difference, threshold, clean | `compare.py` | LAB lightness + chroma distance + relative edge emergence, Gaussian blur, morphological opening, border exclusion |
| 10 candidate regions | `compare.py` | connected components, area gate, diffuse-region rejection, fragment merging |
| 11 label and score | `damage.py` | shape and colour heuristics, weighted confidence |
| 12 persist | `services/inspections.py` | unverified findings with stored parameters |
| 13 evidence images | `report.py` | three-panel composite plus a per-finding crop |

**Four false-positive defences**, each targeting a specific artefact:

1. *Exposure gain* corrects a global brightness change multiplicatively, using
   the median so genuine damage does not drag the estimate. An additive shift
   was tried first and failed: it fixes midtones and leaves highlights
   mismatched.
2. *Relative edge emergence* measures how much local contrast **increased**, not
   the absolute gradient difference. This is the most consequential line in the
   pipeline. The absolute formulation saturates at every panel seam, window line
   and badge in the frame, so a camera that moved one pixel reported the whole
   car as scratched.
3. *Diffuse-region rejection* drops a region covering a third of the frame with a
   weak mean signal - a cloud passing over, not a dent.
4. *Pre-existing-edge rejection* drops a region sitting on structure that was
   already in the drop-off photo, unless a colour change corroborates it.
   Measured on the demo photographs, genuine damage across smooth paint scores
   single digits here while registration residue scores 50-170. The cost is
   explicit: damage lying directly on an existing edge can be suppressed.

**Known failure modes.** These are not hypothetical; each was observed and is
covered by a test:

- A dent's difference signature is a *ring*, not a blob - it brightens on the
  lit side and darkens on the other. An early version gated on region solidity
  and therefore rejected every genuine dent.
- Damage on a panel seam can be suppressed by defence 4.
- With a viewpoint the region mapping is a coarse 3x3 grid, so the named panel
  can be one cell out. Without a viewpoint it refuses to name a panel at all
  rather than guessing.
- The damage *type* is the weakest claim in the system, which is why it carries
  its own `type_confidence`, capped at 0.78, and is never allowed to inherit the
  region's confidence.

**What would make it better, and is not claimed here:** a labelled dataset of
vehicle damage, part segmentation before region mapping, and calibration of
confidence against measured precision/recall. None of these exist in this
project.

## 3. Every AI result is explainable

Each stored finding carries:

- **the inputs** — paths/hashes of the before and after images, capture
  timestamps, camera viewpoint tag;
- **the parameters** — thresholds, alignment quality score, region map;
- **the output** — bounding box, mapped body zone (bonnet, door-front-left,
  …), difference magnitude, confidence in `[0, 1]`;
- **the evidence** — a side-by-side rendering with the flagged region outlined.

A reviewer can reproduce any finding by re-running the pipeline with the stored
parameters. Confidence is presented as a *ranking* signal, never as a
probability of truth.

## 4. Simulated GPS is labelled, everywhere

Real vehicle telemetry requires an OBD-II dongle, a CAN bus reader or a
tracking device. None is assumed to be present.

**Provenance is stored on every fix, not inferred from a deployment setting.**
Each `gps_events` row carries its own `source`:

| `source`      | Meaning                                                        |
| ------------- | -------------------------------------------------------------- |
| `SIMULATED`   | generated by `ai/synthetic/gps_tracks.py`                       |
| `MOBILE_GPS`  | supplied by an authorized mobile browser after user consent      |
| `REAL_GPS`    | supplied by a registered hardware device with its configured secret |
| `MANUAL`      | keyed in by staff through `POST /api/gps/events` or `/gps/vehicle` |

`HARDWARE`, `VEHICLE_GPS` and `CAR_GPS` are legacy labels retained for older
records. New registered-device fixes are always `REAL_GPS`; a device ID alone
does not show that a tracker is connected.

A per-row column rather than a `.env` flag, because one custody session can
legitimately contain both - a vehicle with a real tracker and a fix an operator
entered by hand - and a deployment-wide setting would flatten that distinction
away. `TELEMETRY_SOURCE` in `backend/.env` still exists and is reported by
`GET /api/v1/readyz`, but it describes the *installation*, never an individual
reading.

The UI reads the per-row value:

- `POST /api/gps/simulate` forces `SIMULATED` and cannot be talked out of it.
- `GET /api/gps/{custody_id}` returns `has_simulated_telemetry`,
  `has_hardware_telemetry`, the list of `sources` present, and a
  `telemetry_note` string written for display.
- The tracking dashboard renders a persistent banner from those flags, labels
  the current reading individually, and shows each fix's source in the log.

The same applies to demo vehicles, demo customers and synthetic retrieval
history: they are seeded by `python utils/scripts/init_db.py --seed` and are
marked as demo data in the database and in the UI.

## 4a. What the geofence engine actually does, and where it fails

`ai/anomaly/geofence.py` classifies each GPS fix as `NORMAL`, `WARNING`,
`SUSPICIOUS` or `BREACH`. It is a rule engine. Nothing in it has learned
anything about a specific car park, and it does not claim to.

**The rules, in full.** The verdict is the most severe of these; they are not
averaged or weighted.

| # | Rule | Raises |
| - | ---- | ------ |
| 1 | Fix is outside the boundary but within `buffer_metres` | `GEOFENCE_BREACH` (LOW), `WARNING` |
| 1 | Outside the buffer, but within `breach_margin_metres` | `GEOFENCE_BREACH` (MEDIUM), `SUSPICIOUS` |
| 1 | Outside the breach margin | `GEOFENCE_BREACH` (HIGH), `BREACH` |
| 2 | Speed above the zone limit, inside the yard | `UNUSUAL_SPEED`, `WARNING`/`SUSPICIOUS` |
| 3a | No fix for longer than `telemetry_gap_seconds` | `TELEMETRY_LOSS`, `WARNING` |
| 3b | Implied speed between two fixes exceeds a physical maximum | `ABNORMAL_MOVEMENT`, `SUSPICIOUS` |
| 3c | Reported heading contradicts the direction of travel | `ABNORMAL_MOVEMENT`, `SUSPICIOUS` |
| 3d | Distance from the boundary increased across several fixes while moving | `ROUTE_DEVIATION` |
| 3e | Moving inside the zone with no task in progress | `ABNORMAL_MOVEMENT`, `SUSPICIOUS` |
| 3f | Outside the zone and travelling in a direction that increases the distance | `ROUTE_DEVIATION`, `BREACH` |

Every threshold is a named field on `GeofenceConfig`, and the whole config is
copied into the verdict's `evidence` alongside the measured distance, the speed
and the list of rules that fired. A reviewer can re-derive the verdict by hand
from the row it is stored on.

**Four decisions that limit false positives, each of which was made after
watching the engine produce the wrong answer:**

- **The zone speed limit applies only inside the yard.** A vehicle on a public
  road is expected to travel at road speed. Applying a yard's 20 km/h limit
  outside the boundary flagged every arrival, retrieval and escape alike - and
  a dashboard that cries wolf on legitimate traffic gets ignored. Outside,
  speed is still recorded as evidence; it simply is not a finding of its own.
- **The drift rule requires actual movement.** A parked receiver wanders a few
  metres, and three consecutive outward wobbles happen by chance. Without
  requiring the vehicle to be moving *and* the drift to exceed the tolerance
  band, every parked vehicle eventually raised a route-deviation alert on its
  own.
- **Movement during arrival is authorised.** `CHECKED_IN` and `IN_TRANSIT`
  count as states where movement is expected, not only retrieval. Treating only
  retrieval as authorised flagged every parking manoeuvre as an anomaly.
- **Alerts are throttled per type.** A vehicle driving away produces a dozen
  consecutive out-of-zone fixes. Raising twelve identical alerts would bury the
  one that matters, so a finding only becomes an alert when no open alert of
  that type exists for the same session within `ALERT_COOLDOWN` (10 minutes).
  Suppressed findings are still stored on their fixes and reported in the
  response as `alerts_suppressed` - the *notification* is throttled, never the
  observation.

**Known failure modes.** Stated plainly, because the alternative is a reviewer
discovering them mid-demo:

- A geofence polygon supplied with coordinates in `[lat, lon]` order is
  accepted whenever both values fall inside the legal ranges, which at most
  latitudes they do. No range check can catch it. The map draws exactly the
  ring that was stored, so the error is visible rather than silent - but a
  boundary is only as trustworthy as the coordinates that defined it.
- A receiver with a skewed clock can produce a fix timestamped in the future.
  Because acknowledgement must come after occurrence, such an alert would be
  unactionable until the clock caught up. `POST /api/gps/events` therefore
  refuses a fix more than five minutes ahead of the server clock, and triage
  endpoints explain the situation rather than surfacing a raw constraint
  violation for rows that predate that guard.
- The engine sees point fixes, not a road network. "Route deviation" means the
  vehicle is moving in a direction that increases its distance from the zone -
  not that it left a prescribed path, which nothing here knows.
- GPS multipath beside a wall or under cover can push a stationary vehicle
  outside the boundary. The buffer absorbs ordinary jitter; it cannot absorb a
  receiver reporting 200 m of error. That is what the `DISMISSED` status is
  for, and why dismissals are recorded separately from resolutions.

## 4b. The security classifier is a prototype, and is fenced in

This is the one learned model in the platform, so it gets the longest warning.

**What it is.** A scikit-learn classifier — logistic regression by default,
random forest as an option — over eight features derived from a GPS fix:
speed, signed distance from the zone boundary, time spent moving, change of
direction, distance travelled since the previous fix, a geofence-breach flag,
and time of day encoded as a sine/cosine pair. Two classes: `NORMAL` and
`SUSPICIOUS`.

**What it was trained on.** Synthetic telemetry generated by
`backend/ai/ml/dataset.py`. The labels are hand-written rules about what the
generator considers suspicious. **The model has never seen a real vehicle, a
real yard, or a real incident.** Its reported accuracy measures how well it
recovers a distinction that file drew, and nothing else.

That is worth stating plainly because the metrics are good — accuracy 0.96, F1
0.96 on a held-out split against a 0.58 baseline — and good-looking numbers
invite over-reading. A high score here would also be produced by a model that
is useless in the field, because the field is not what was measured. An earlier
draft of this dataset scored a perfect 1.00, which was not a success: the two
generated distributions did not overlap, so a single threshold on a single
feature would have scored the same. The generators were rewritten to overlap so
the confusion matrix would mean something.

**The rule that makes it safe to ship.** In `services/security_ml.py`:

> **The rule engine is a floor. The classifier may raise a verdict and may never
> lower it.**

| Rule engine says | Classifier says | Stored verdict |
| --- | --- | --- |
| `BREACH` | `NORMAL` | **`BREACH`** — alert raised regardless |
| `SUSPICIOUS` | `NORMAL` | `SUSPICIOUS` — not downgraded |
| `WARNING` | `SUSPICIOUS` above threshold | `SUSPICIOUS` — escalated |
| `NORMAL` | `SUSPICIOUS` above threshold | `SUSPICIOUS` — escalated |
| anything | below the 0.70 threshold | unchanged |

The asymmetry is deliberate and is the whole justification for shipping a
prototype at all. Escalating on a false positive costs a reviewer a glance.
De-escalating on a false negative costs the vehicle — a breach that raised no
alert, which is precisely the failure this project exists to prevent. Those are
not comparable errors, so only the cheap one is permitted.

**A critical geofence breach always raises an alert even when the classifier is
uncertain, or is confident the vehicle is fine.** This is asserted three ways in
`tests/test_security_ml.py`: on the status, on the survival of the breach
finding that the alert layer iterates, and over an exhaustive table of every
rule status crossed with five confidence levels. All five deliberate mutations
of the combination logic were caught by those tests.

**Where the numbers come from.**

```powershell
cd backend
python utils\scripts\train_security_model.py                    # train + evaluate
python utils\scripts\train_security_model.py --algorithm random_forest
python utils\scripts\evaluate_security_model.py --fresh-seed 999
```

The `--fresh-seed` run scores the saved model against a newly generated dataset
it never saw, so a model that memorised one draw rather than learning the
behaviour shows up as a gap between the two figures.

**Known limitations.**

- Eight features, one of which (`zone_breach`) is itself derived from the rule
  engine's distance measurement. The model is partly re-reading the rules'
  output, so agreement between them is weaker evidence than it looks.
- The classes are imbalanced by construction and the training set is weighted to
  compensate, which raises recall at some cost to precision. Recall is the right
  trade here, but it is a choice, not a property of the data.
- A random-forest artifact is saved with `joblib`, which is pickle. It must only
  ever be loaded from a path the operator controls; the default logistic model
  is saved as plain JSON with no pickle involved.
- Time of day is a feature, which means the model can learn that night-time
  movement is suspicious. On synthetic data that is the intended signal. On real
  data it would encode whatever bias the labelling carried, and it would do so
  invisibly. **This is not hypothetical — it produced a real false positive,
  described immediately below.**

### A false positive this layer actually produced

Worth recording, because it is the concrete version of the warning above.

A stationary GPS receiver still reports a heading, but with no motion to average
over that heading drifts. In the `parked` simulator scenario a car that never
moved produced successive headings of 141°, 172°, 70°, 213°, 54°. The feature
extractor read those as a genuine **change of direction** of up to 159°, and the
classifier — which has learned that direction changes are suspicious — escalated
the parked vehicle to **P(suspicious) = 0.78**, above the escalation threshold.

Two things made it easy to miss:

- **It was time-dependent.** Time of day supplies the last few points needed to
  cross the threshold, so the suite passed when run in the afternoon and failed
  when run at 01:30. A test that passes at 4pm and fails at 2am is worse than no
  test at all, because it reads as a flake.
- **The escalation was working as designed.** The combination rule did exactly
  what it says: the rules found nothing, the classifier was confident, so the
  verdict was raised. The defect was upstream, in a feature that reported an
  observation which never happened.

**The fix was at the feature layer, not the threshold.** Direction change is now
derived only when the vehicle is above the stationary speed threshold, and an
implied bearing is only inferred above a 5 m displacement floor — beneath
receiver accuracy, a bearing is invented rather than measured. Lowering the
escalation threshold would have hidden this case and left the fabricated feature
in place for the next one.

Verified afterwards: the parked scenario produces zero escalations at **every
hour of the day**, and a genuinely moving vehicle at night still escalates at
p = 0.99. `tests/test_security_ml.py::TestStationaryNoiseIsNotBehaviour` sweeps
all 24 hours rather than sampling the hour CI happens to run at, and asserts the
moving case alongside the stationary one so the feature cannot be silently
hard-wired to zero.

**The general lesson:** a feature that can report a value the sensors did not
observe is worse than a missing feature, because the model will act on it
confidently. Anything derived from a receiver needs a floor below which it says
nothing.

**If no artifact is trained, the platform runs on rules alone.** That is a
supported state, not a degraded one: `ML_MODEL_PATH` defaults to a path that
does not exist in a fresh checkout, the service logs why, and the tracking
dashboard says so instead of showing an error.

## 5. The retrieval predictor is a baseline fitted to a generator

An earlier draft of this document described the retrieval estimate as a linear
model "fitted at runtime on the project's own historical records", reporting
in-sample fit quality and a confidence interval. **That was never what shipped,
and this section replaces it.** The claims are corrected here rather than
quietly deleted, because the difference is the interesting part.

What actually exists:

- **Ridge regression over 11 features**, trained *offline* by
  `utils/scripts/train_retrieval_model.py` and saved as plain JSON to
  `storage/models/retrieval_model.json`. No pickle, so loading the artifact
  cannot execute anything and the coefficients can be read and diffed by hand.
- **A temporal split, not a random one.** The last 25% of the generated history
  by timestamp is held out. A random split would let the model train on Friday
  evenings and be scored on other Friday evenings, which flatters the result.
- **MAE as the primary metric, always reported beside a baseline.** A number
  with no comparison is not evidence: predicting the training median also scores
  an MAE. The evaluator prints the median predictor, the mean predictor and a
  hand-written "3.2 minutes per place in the queue" rule alongside, and exits 1
  if the model fails to beat the best of them.
- **A stated range rather than a bare point estimate**, plus per-feature
  contributions in minutes so the figure can be argued with.

What it reports is **not** confidence. The card shows a `confidence` number that
measures how typical the input is relative to the training distribution — a
scaled distance from the training mean, not a probability that the estimate is
right. The model has no calibrated uncertainty, and presenting a probability it
does not have would be a fabrication.

**The central limitation.** The generator in `ai/retrieval/dataset.py` defines
the relationship the model recovers, so the MAE measures how well the model
inverts a function written for this project. It is evidence that the pipeline
works end to end. It is not evidence about any real parking facility. Every
response carries `trained_on_synthetic_data: true` and a `data_basis` string
naming the generator.

**One honesty point about the dispatch recommendation.** The model does not
output a dispatch time. The service derives one by asking the same model a
counterfactual — what the wait would be if this were the only request — and
treating the difference as the part of the wait caused by waiting one's turn. It
is a decomposition of one linear prediction, not a second opinion, and it
inherits every limitation of the prediction it is decomposed from.

## 6. Claims this project does **not** make

- That damage detection matches human expert judgement.
- That any finding is confirmed. Every finding is stored `UNVERIFIED` and
  labelled *Potential Damage* until a manager confirms or rejects it.
- That geofence detection has a measured false-positive rate in the field.
- That retrieval predictions are accurate for real parking facilities.
- That any model generalises beyond the data it was fitted on.
- **That the security classifier can identify a real theft.** It was trained on
  synthetic telemetry and has never seen one. Its accuracy describes how well it
  recovers a hand-written distinction in generated data.
- **That the classifier's verdict is a measurement.** `movement_status` is a
  measurement against a known boundary. `ml_classification` is an estimate, and
  the two are stored in different columns so neither can be mistaken for the
  other.
- That the system is production-ready or security-audited.

## 7. How an evaluator can verify all of the above

```powershell
# No pretrained weights are present in the repository:
Get-ChildItem -Recurse -Include *.pt,*.pth,*.onnx,*.h5,*.pb,*.tflite,*.safetensors

# Every telemetry payload declares its provenance:
curl http://127.0.0.1:5000/api/v1/readyz

# The detection thresholds are plain configuration, not learned weights:
Select-String -Path backend\.env -Pattern "GEOFENCE|SUSPICIOUS|TELEMETRY"

# The classifier discloses its own synthetic origin, with metrics:
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:5000/api/v1/ml/model

# Train it from scratch and watch the metrics come out of synthetic data:
cd backend; python utils\scripts\train_security_model.py
python utils\scripts\evaluate_security_model.py --fresh-seed 999

# The retrieval predictor: generate, train, evaluate, and watch it be scored
# against the baselines it has to beat:
python utils/scripts/generate_retrieval_history.py
python utils/scripts/train_retrieval_model.py
python utils/scripts/evaluate_retrieval_model.py --fresh-seed 7

# The retrieval endpoints, including the synthetic-data disclosure on each:
grep -n "trained_on_synthetic_data" routes/retrieval.py services/retrieval.py

# The escalation-only rule, including the exhaustive "can never lower it" table:
python -m pytest tests/test_security_ml.py -v
```
