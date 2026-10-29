"""AI and computer-vision services.

Nothing in this package is a pretrained deep-learning model, and nothing here
claims to be. See ``docs/AI-INTEGRITY.md`` for the full statement.

Planned modules, in delivery order:

    ai/vision/preprocessing.py   Phase 5 - validation, EXIF orientation, resize
    ai/vision/alignment.py       Phase 5 - ORB/SIFT feature match + homography
    ai/vision/compare.py         Phase 5 - LAB colour and gradient differencing
    ai/vision/damage.py          Phase 6 - contour analysis -> candidate findings
    ai/anomaly/geofence.py       Phase 7 - haversine, point-in-polygon, thresholds
    ai/prediction/retrieval.py   Phase 9 - fit on the project's own history

Every function that produces a finding must return its inputs, the parameters
used, a confidence in [0, 1] treated as a ranking signal, and a path to a
rendered evidence image, so a reviewer can reproduce the result.
"""
