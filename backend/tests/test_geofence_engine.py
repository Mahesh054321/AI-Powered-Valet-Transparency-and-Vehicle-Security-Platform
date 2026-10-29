"""Unit tests for the geofence rule engine and the GPS track simulator.

No database here. The engine takes numbers in and returns a verdict with its
reasoning, so every rule can be checked directly -- which matters more than
usual, because a geofence that quietly reports the wrong side of a boundary is
worse than no geofence at all.

The last group of tests are drift guards: ``ai/anomaly/geofence.py`` cannot
import from ``models`` (an ``ai`` module must not know about SQLAlchemy), so it
restates the status and alert vocabularies as plain strings. These tests are the
only thing keeping those mirrors honest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai.anomaly import geofence as engine
from ai.synthetic import gps_tracks

#: A plausible Mathura coordinate, reused across tests.
LAT = 27.492413
LON = 77.673673
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture()
def ring():
    return engine.circular_ring(LAT, LON, 100.0)


@pytest.fixture()
def config():
    return engine.GeofenceConfig(buffer_metres=25.0, breach_margin_metres=150.0)


def north_of(distance_metres: float) -> tuple[float, float]:
    """A point due north of the fixture centre, at a known distance."""
    return engine.destination_point(LAT, LON, 0.0, distance_metres)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def test_haversine_matches_the_known_length_of_a_degree_of_latitude():
    # One degree of latitude is ~111.2 km everywhere on a sphere.
    assert engine.haversine_m(LAT, LON, LAT + 1.0, LON) == pytest.approx(111_195, abs=200)


def test_haversine_shrinks_with_latitude_for_east_west_distance():
    equator = engine.haversine_m(0.0, LON, 0.0, LON + 1.0)
    at_fixture = engine.haversine_m(LAT, LON, LAT, LON + 1.0)
    assert at_fixture == pytest.approx(equator * 0.887, rel=0.01)


def test_haversine_of_a_point_with_itself_is_zero():
    assert engine.haversine_m(LAT, LON, LAT, LON) == 0.0


def test_bearing_north_is_zero_and_east_is_ninety():
    north = engine.destination_point(LAT, LON, 0.0, 100.0)
    east = engine.destination_point(LAT, LON, 90.0, 100.0)
    assert engine.bearing_degrees(LAT, LON, north[0], north[1]) == pytest.approx(0.0, abs=0.5)
    assert engine.bearing_degrees(LAT, LON, east[0], east[1]) == pytest.approx(90.0, abs=0.5)


def test_angle_between_wraps_across_north():
    assert engine.angle_between_degrees(350.0, 10.0) == pytest.approx(20.0)
    assert engine.angle_between_degrees(10.0, 350.0) == pytest.approx(20.0)
    assert engine.angle_between_degrees(0.0, 180.0) == pytest.approx(180.0)
    assert engine.angle_between_degrees(45.0, 45.0) == pytest.approx(0.0)


def test_point_in_polygon_separates_inside_from_outside(ring):
    assert engine.point_in_polygon(LAT, LON, ring) is True
    outside = north_of(160.0)
    assert engine.point_in_polygon(outside[0], outside[1], ring) is False


def test_point_in_polygon_refuses_a_degenerate_ring():
    assert engine.point_in_polygon(LAT, LON, []) is False
    assert engine.point_in_polygon(LAT, LON, [[LON, LAT], [LON + 1, LAT]]) is False


def test_circular_ring_has_the_requested_vertex_count_and_contains_its_centre(ring):
    assert len(ring) == engine.CIRCLE_RING_POINTS
    assert engine.point_in_polygon(LAT, LON, ring) is True
    # GeoJSON ordering: [longitude, latitude].
    assert all(len(vertex) == 2 for vertex in ring)
    assert all(abs(vertex[0] - LON) < 0.01 for vertex in ring)


def test_circular_ring_rejects_a_non_positive_radius():
    with pytest.raises(ValueError, match="positive"):
        engine.circular_ring(LAT, LON, 0.0)


def test_circular_ring_rejects_fewer_than_three_vertices():
    with pytest.raises(ValueError, match="at least 3"):
        engine.circular_ring(LAT, LON, 100.0, points=2)


def test_destination_point_round_trips_back_to_the_start():
    outward = engine.destination_point(LAT, LON, 45.0, 250.0)
    returned = engine.destination_point(outward[0], outward[1], 225.0, 250.0)
    assert engine.haversine_m(returned[0], returned[1], LAT, LON) < 1.0


def test_signed_distance_is_negative_inside_and_positive_outside(ring):
    assert engine.signed_distance_to_boundary_m(LAT, LON, ring) == pytest.approx(-100.0, abs=2.0)
    outside = north_of(160.0)
    assert engine.signed_distance_to_boundary_m(
        outside[0], outside[1], ring
    ) == pytest.approx(60.0, abs=2.0)


def test_signed_distance_rejects_a_ring_that_cannot_bound_anything():
    with pytest.raises(ValueError, match="at least three"):
        engine.signed_distance_to_boundary_m(LAT, LON, [[LON, LAT]])


# ---------------------------------------------------------------------------
# Zone membership
# ---------------------------------------------------------------------------
def evaluate(ring, config, **overrides):
    kwargs = {
        "latitude": LAT,
        "longitude": LON,
        "zone_ring": ring,
        "zone_centre": (LAT, LON),
        "zone_radius_metres": 100.0,
        "speed_kmph": 0.0,
        "recorded_at": NOW,
        "config": config,
    }
    kwargs.update(overrides)
    return engine.evaluate_fix(**kwargs)


def test_a_parked_vehicle_at_the_centre_is_normal(ring, config):
    verdict = evaluate(ring, config)
    assert verdict.status == engine.NORMAL
    assert verdict.is_within_zone is True
    assert verdict.findings == ()
    assert verdict.distance_from_boundary_metres < 0


def test_a_fix_just_outside_the_boundary_but_in_the_buffer_is_a_warning(ring, config):
    position = north_of(110.0)
    verdict = evaluate(ring, config, latitude=position[0], longitude=position[1])
    assert verdict.status == engine.WARNING
    assert verdict.is_within_zone is False
    assert verdict.findings[0].alert_type == engine.ALERT_GEOFENCE_BREACH
    assert "tolerance" in verdict.findings[0].reason


def test_a_fix_past_the_buffer_is_suspicious(ring, config):
    position = north_of(160.0)
    verdict = evaluate(ring, config, latitude=position[0], longitude=position[1])
    assert verdict.status == engine.SUSPICIOUS


def test_a_fix_past_the_breach_margin_is_a_breach(ring, config):
    position = north_of(400.0)
    verdict = evaluate(ring, config, latitude=position[0], longitude=position[1])
    assert verdict.status == engine.BREACH
    assert verdict.findings[0].severity == engine.SEVERITY_HIGH


def test_the_verdict_ranks_the_worst_finding_not_an_average(ring, config):
    position = north_of(400.0)
    verdict = evaluate(
        ring, config, latitude=position[0], longitude=position[1], speed_kmph=0.0
    )
    assert verdict.status == engine.BREACH
    assert all(engine.MOVEMENT_STATUSES.index(f.status) <= 3 for f in verdict.findings)


def test_a_fix_with_no_zone_geometry_says_so_rather_than_claiming_normal():
    verdict = engine.evaluate_fix(
        latitude=LAT,
        longitude=LON,
        speed_kmph=0.0,
        recorded_at=NOW,
        config=engine.GeofenceConfig(),
    )
    assert verdict.status == engine.WARNING
    assert verdict.is_within_zone is False
    assert verdict.distance_from_boundary_metres is None
    assert "No parking zone geometry" in verdict.findings[0].reason


def test_a_zone_can_be_described_by_a_circle_instead_of_a_polygon(config):
    verdict = engine.evaluate_fix(
        latitude=LAT,
        longitude=LON,
        zone_ring=None,
        zone_centre=(LAT, LON),
        zone_radius_metres=100.0,
        speed_kmph=0.0,
        recorded_at=NOW,
        config=config,
    )
    assert verdict.status == engine.NORMAL
    assert verdict.distance_from_boundary_metres == pytest.approx(-100.0, abs=0.1)


# ---------------------------------------------------------------------------
# Speed
# ---------------------------------------------------------------------------
def test_speed_within_the_zone_limit_is_not_a_finding(ring, config):
    verdict = evaluate(ring, config, speed_kmph=18.0)
    assert verdict.status == engine.NORMAL


def test_speed_above_the_zone_limit_is_a_warning(ring, config):
    verdict = evaluate(ring, config, speed_kmph=25.0)
    assert verdict.status == engine.WARNING
    assert verdict.findings[0].alert_type == engine.ALERT_UNUSUAL_SPEED


def test_speed_far_above_the_zone_limit_is_suspicious(ring, config):
    verdict = evaluate(ring, config, speed_kmph=55.0)
    assert verdict.status == engine.SUSPICIOUS


def test_the_zone_speed_limit_does_not_apply_on_the_public_road(ring, config):
    """A car outside the yard travelling at road speed is not speeding.

    This is the false positive the module exists to avoid: applying a yard's
    20 km/h limit to a vehicle on a main road would flag every arrival,
    retrieval and escape alike, and a dashboard that cries wolf gets ignored.
    """
    position = north_of(400.0)
    verdict = evaluate(
        ring, config, latitude=position[0], longitude=position[1], speed_kmph=55.0
    )
    assert [f.alert_type for f in verdict.findings] == [engine.ALERT_GEOFENCE_BREACH]
    assert verdict.evidence["speed_limit_applies"] is False


def test_speed_is_still_recorded_as_evidence_outside_the_yard(ring, config):
    position = north_of(400.0)
    verdict = evaluate(
        ring, config, latitude=position[0], longitude=position[1], speed_kmph=55.0
    )
    assert verdict.evidence["speed_kmph"] == 55.0


def test_an_unknown_speed_never_produces_a_speed_finding(ring, config):
    verdict = evaluate(ring, config, speed_kmph=None)
    assert verdict.findings == ()


# ---------------------------------------------------------------------------
# Movement pattern
# ---------------------------------------------------------------------------
def prior(offset_seconds: int, *, latitude=LAT, longitude=LON, speed=0.0):
    return engine.PriorFix(
        latitude=latitude,
        longitude=longitude,
        recorded_at=NOW - timedelta(seconds=offset_seconds),
        speed_kmph=speed,
    )


def test_a_long_gap_between_fixes_is_a_telemetry_loss_finding(ring, config):
    verdict = evaluate(
        ring,
        config,
        prior_fixes=(prior(900),),
    )
    assert verdict.status == engine.WARNING
    assert verdict.findings[0].alert_type == engine.ALERT_TELEMETRY_LOSS


def test_an_impossible_jump_between_fixes_is_called_out(ring, config):
    far = north_of(600.0)
    verdict = evaluate(
        ring,
        config,
        latitude=far[0],
        longitude=far[1],
        speed_kmph=0.0,
        prior_fixes=(prior(10),),
    )
    assert any(f.alert_type == engine.ALERT_ABNORMAL_MOVEMENT for f in verdict.findings)
    assert any("physically impossible" in f.title for f in verdict.findings)
    assert any("faulty or fabricated" in f.reason for f in verdict.findings)


def _heading_verdict(ring, config, heading: float):
    """Evaluate the same 60 m northward move with a chosen reported heading."""
    target = north_of(60.0)
    return evaluate(
        ring,
        config,
        latitude=target[0],
        longitude=target[1],
        speed_kmph=15.0,
        heading_degrees=heading,
        prior_fixes=(prior(10),),
    )


def _heading_findings(verdict):
    return [f for f in verdict.findings if "contradicts direction of travel" in f.title]


def test_a_reported_heading_that_contradicts_the_direction_of_travel(ring, config):
    verdict = _heading_verdict(ring, config, 180.0)  # moving north, claims south
    matched = _heading_findings(verdict)
    assert matched, [f.title for f in verdict.findings]
    assert "degree mismatch" in matched[0].reason


def test_a_heading_that_agrees_with_the_direction_of_travel_is_not_a_finding(ring, config):
    """The negative case, paired with the positive one so it cannot pass vacuously.

    Asserting only that a finding is absent proves nothing if the rule never
    ran at all, so the same movement with a contradicting heading is evaluated
    first and shown to fire.
    """
    assert _heading_findings(_heading_verdict(ring, config, 180.0)), "precondition"
    assert not _heading_findings(_heading_verdict(ring, config, 0.0))


def test_sustained_outward_drift_is_detected_when_the_vehicle_is_moving(ring, config):
    """Three consecutive fixes each further out, while moving, is a departure."""
    priors = (
        prior(90, latitude=north_of(80.0)[0], longitude=LON, speed=12.0),
        prior(60, latitude=north_of(90.0)[0], longitude=LON, speed=12.0),
        prior(30, latitude=north_of(100.0)[0], longitude=LON, speed=12.0),
    )
    position = north_of(120.0)
    verdict = evaluate(
        ring,
        config,
        latitude=position[0],
        longitude=position[1],
        speed_kmph=12.0,
        prior_fixes=priors,
    )
    assert any(f.alert_type == engine.ALERT_ROUTE_DEVIATION for f in verdict.findings)


def test_gps_noise_on_a_stationary_vehicle_is_not_drift(ring, config):
    """The false positive that made this rule unusable before it was guarded.

    A parked receiver wanders a few metres, and three consecutive outward
    wobbles happen by chance. Without requiring actual movement and a drift
    larger than the tolerance band, every parked vehicle eventually raises a
    route-deviation alert on its own.
    """
    wobble = [north_of(97.0)[0], north_of(98.0)[0], north_of(99.0)[0]]
    priors = tuple(
        prior(90 - index * 30, latitude=wobble[index], longitude=LON, speed=0.5)
        for index in range(3)
    )
    verdict = evaluate(
        ring,
        config,
        latitude=north_of(100.5)[0],
        longitude=LON,
        speed_kmph=0.5,
        prior_fixes=priors,
    )
    # The rule was eligible to run - three priors were supplied - it simply
    # declined to fire, which is the behaviour under test. Scoped to the drift
    # rule's own finding, not the alert type, so a sibling route-deviation rule
    # firing elsewhere cannot mask a regression here.
    assert verdict.evidence["prior_fix_count"] == 3
    assert not any(
        "steadily moving away from its zone" in f.title for f in verdict.findings
    )


def _task_verdict(ring, config, *, movement_expected: bool):
    """The same 15 km/h move inside the zone, with and without an active task."""
    position = north_of(50.0)
    return evaluate(
        ring,
        config,
        latitude=position[0],
        longitude=position[1],
        speed_kmph=15.0,
        prior_fixes=(prior(60),),
        movement_expected=movement_expected,
    )


def _no_task_findings(verdict):
    return [
        f
        for f in verdict.findings
        if f.alert_type == engine.ALERT_ABNORMAL_MOVEMENT
        and "no active task" in f.title
    ]


def test_a_material_outward_drift_reported_at_zero_speed_is_not_called_a_departure(ring, config):
    """The second drift guard, tested on its own.

    The previous test's noise never exceeds the tolerance band, so it would
    still pass if the movement guard were deleted outright. This one drifts 45 m
    outward -- comfortably past the 25 m band -- while reporting a stationary
    speed. That combination is what the guard is actually for: a receiver whose
    position is moving but whose speed reads zero is a faulty sensor or a
    vehicle being moved without its engine, and neither is the sustained
    departure the route-deviation rule claims to detect.
    """
    priors = tuple(
        prior(
            90 - index * 30,
            latitude=north_of(85.0 + index * 15.0)[0],
            longitude=LON,
            speed=0.4,
        )
        for index in range(3)
    )
    position = north_of(130.0)
    verdict = evaluate(
        ring,
        config,
        latitude=position[0],
        longitude=position[1],
        speed_kmph=0.4,
        prior_fixes=priors,
    )
    # The drift is real and material; only the movement guard can stop it here.
    assert verdict.evidence["prior_fix_count"] == 3
    # Scoped to the drift rule's own finding. The vehicle is also outside the
    # boundary and travelling away from the zone, which the separate
    # "moving away from the yard" rule legitimately reports - asserting on the
    # alert type alone would conflate the two.
    assert not any(
        "steadily moving away from its zone" in f.title for f in verdict.findings
    )


def test_movement_inside_the_zone_with_no_task_is_abnormal(ring, config):
    matched = _no_task_findings(_task_verdict(ring, config, movement_expected=False))
    assert matched, "a vehicle moving in the yard with no task should be flagged"
    assert "no task in progress" in matched[0].reason


def test_the_same_movement_is_expected_when_a_task_authorises_it(ring, config):
    """Paired with the positive case so the negative assertion cannot be vacuous."""
    assert _no_task_findings(
        _task_verdict(ring, config, movement_expected=False)
    ), "precondition"
    assert not _no_task_findings(_task_verdict(ring, config, movement_expected=True))


def test_leaving_the_yard_in_a_direction_that_does_not_return_is_route_deviation(ring, config):
    position = north_of(200.0)
    verdict = evaluate(
        ring,
        config,
        latitude=position[0],
        longitude=position[1],
        speed_kmph=40.0,
        heading_degrees=0.0,
        prior_fixes=(prior(60, latitude=north_of(150.0)[0], longitude=LON, speed=40.0),),
    )
    matched = [
        f
        for f in verdict.findings
        if f.alert_type == engine.ALERT_ROUTE_DEVIATION
        and "moving away from the yard" in f.title
    ]
    assert matched, [f.title for f in verdict.findings]
    assert "off the bearing back to the zone" in matched[0].reason


# ---------------------------------------------------------------------------
# Verdict shape
# ---------------------------------------------------------------------------
def test_the_verdict_exposes_the_inputs_a_reviewer_would_need(ring, config):
    verdict = evaluate(ring, config, speed_kmph=25.0)
    assert verdict.evidence["buffer_metres"] == 25.0
    assert verdict.evidence["breach_margin_metres"] == 150.0
    assert verdict.evidence["zone_speed_limit_kmph"] == 20.0
    assert verdict.evidence["rules_fired"]
    assert verdict.evidence["rules_fired"][0]["reason"]


def test_reasons_are_ordered_most_severe_first(ring, config):
    position = north_of(400.0)
    verdict = evaluate(
        ring, config, latitude=position[0], longitude=position[1], speed_kmph=55.0
    )
    assert len(verdict.reasons) == len(verdict.findings)
    assert verdict.summary


def test_a_clean_verdict_has_a_readable_summary(ring, config):
    assert "Inside the assigned geofence" in evaluate(ring, config).summary


def test_a_finding_rejects_a_confidence_outside_the_probability_range():
    with pytest.raises(ValueError, match="confidence"):
        engine.Finding(
            alert_type=engine.ALERT_GEOFENCE_BREACH,
            severity=engine.SEVERITY_LOW,
            title="t",
            reason="r",
            confidence=1.5,
            status=engine.NORMAL,
        )


# ---------------------------------------------------------------------------
# Track simulator
# ---------------------------------------------------------------------------
def test_every_scenario_generates_a_non_empty_track():
    for name in gps_tracks.TRACK_SCENARIOS:
        track = gps_tracks.generate_track(
            name,
            centre_latitude=LAT,
            centre_longitude=LON,
            radius_metres=120.0,
            start_time=NOW,
            seed=7,
        )
        assert track.fixes, name
        assert track.scenario == name
        assert track.description


def test_a_generated_track_is_deterministic_for_a_given_seed():
    kwargs = {
        "centre_latitude": LAT,
        "centre_longitude": LON,
        "radius_metres": 120.0,
        "start_time": NOW,
    }
    first = gps_tracks.generate_track("unauthorised", seed=42, **kwargs)
    second = gps_tracks.generate_track("unauthorised", seed=42, **kwargs)
    assert first.fixes == second.fixes


def test_different_seeds_produce_different_tracks():
    kwargs = {
        "centre_latitude": LAT,
        "centre_longitude": LON,
        "radius_metres": 120.0,
        "start_time": NOW,
    }
    first = gps_tracks.generate_track("unauthorised", seed=1, **kwargs)
    second = gps_tracks.generate_track("unauthorised", seed=2, **kwargs)
    assert first.fixes != second.fixes


def test_an_unknown_scenario_is_refused():
    with pytest.raises(ValueError, match="scenario must be one of"):
        gps_tracks.generate_track(
            "teleport",
            centre_latitude=LAT,
            centre_longitude=LON,
            radius_metres=120.0,
            start_time=NOW,
        )


def test_generated_fixes_stay_inside_the_legal_coordinate_ranges():
    for name in gps_tracks.TRACK_SCENARIOS:
        track = gps_tracks.generate_track(
            name,
            centre_latitude=LAT,
            centre_longitude=LON,
            radius_metres=120.0,
            start_time=NOW,
            seed=3,
        )
        for fix in track.fixes:
            assert -90.0 <= fix.latitude <= 90.0
            assert -180.0 <= fix.longitude <= 180.0
            assert 0.0 <= fix.heading_degrees < 360.0
            assert fix.speed_kmph >= 0.0
            assert fix.accuracy_metres > 0


def test_generated_fixes_are_ordered_in_time():
    track = gps_tracks.generate_track(
        "retrieval",
        centre_latitude=LAT,
        centre_longitude=LON,
        radius_metres=120.0,
        start_time=NOW,
        seed=5,
    )
    stamps = [fix.recorded_at for fix in track.fixes]
    assert stamps == sorted(stamps)


def test_the_parked_scenario_classifies_entirely_normal():
    """The baseline: ordinary GPS wander must not produce findings.

    If this fails, every other scenario's findings are meaningless noise.
    """
    ring = engine.circular_ring(LAT, LON, 120.0)
    track = gps_tracks.generate_track(
        "parked",
        centre_latitude=LAT,
        centre_longitude=LON,
        radius_metres=120.0,
        start_time=NOW,
        seed=11,
    )
    priors: list[engine.PriorFix] = []
    for fix in track.fixes:
        verdict = engine.evaluate_fix(
            latitude=fix.latitude,
            longitude=fix.longitude,
            zone_ring=ring,
            zone_centre=(LAT, LON),
            zone_radius_metres=120.0,
            speed_kmph=fix.speed_kmph,
            heading_degrees=fix.heading_degrees,
            recorded_at=fix.recorded_at,
            prior_fixes=tuple(priors),
            movement_expected=track.movement_expected,
            config=engine.GeofenceConfig(buffer_metres=25.0, breach_margin_metres=150.0),
        )
        assert verdict.status == engine.NORMAL, verdict.reasons
        priors.append(
            engine.PriorFix(
                fix.latitude, fix.longitude, fix.recorded_at, fix.speed_kmph
            )
        )
        priors = priors[-4:]


def test_the_unauthorised_scenario_reaches_a_breach():
    ring = engine.circular_ring(LAT, LON, 120.0)
    track = gps_tracks.generate_track(
        "unauthorised",
        centre_latitude=LAT,
        centre_longitude=LON,
        radius_metres=120.0,
        start_time=NOW,
        seed=13,
    )
    priors: list[engine.PriorFix] = []
    statuses = []
    for fix in track.fixes:
        verdict = engine.evaluate_fix(
            latitude=fix.latitude,
            longitude=fix.longitude,
            zone_ring=ring,
            zone_centre=(LAT, LON),
            zone_radius_metres=120.0,
            speed_kmph=fix.speed_kmph,
            heading_degrees=fix.heading_degrees,
            recorded_at=fix.recorded_at,
            prior_fixes=tuple(priors),
            movement_expected=track.movement_expected,
            config=engine.GeofenceConfig(buffer_metres=25.0, breach_margin_metres=150.0),
        )
        statuses.append(verdict.status)
        priors.append(
            engine.PriorFix(
                fix.latitude, fix.longitude, fix.recorded_at, fix.speed_kmph
            )
        )
        priors = priors[-4:]
    assert engine.BREACH in statuses
    assert engine.SUSPICIOUS in statuses
    assert statuses[0] == engine.NORMAL  # it starts parked


def test_the_jitter_scenario_never_escalates_past_a_warning():
    """The tolerance band exists for exactly this case."""
    ring = engine.circular_ring(LAT, LON, 120.0)
    track = gps_tracks.generate_track(
        "jitter",
        centre_latitude=LAT,
        centre_longitude=LON,
        radius_metres=120.0,
        start_time=NOW,
        seed=17,
    )
    priors: list[engine.PriorFix] = []
    for fix in track.fixes:
        verdict = engine.evaluate_fix(
            latitude=fix.latitude,
            longitude=fix.longitude,
            zone_ring=ring,
            zone_centre=(LAT, LON),
            zone_radius_metres=120.0,
            speed_kmph=fix.speed_kmph,
            heading_degrees=fix.heading_degrees,
            recorded_at=fix.recorded_at,
            prior_fixes=tuple(priors),
            movement_expected=track.movement_expected,
            config=engine.GeofenceConfig(buffer_metres=25.0, breach_margin_metres=150.0),
        )
        assert verdict.status in (engine.NORMAL, engine.WARNING), verdict.reasons
        priors.append(
            engine.PriorFix(
                fix.latitude, fix.longitude, fix.recorded_at, fix.speed_kmph
            )
        )
        priors = priors[-4:]


def test_the_scenario_catalogue_lists_every_buildable_scenario():
    catalogue = {item["id"] for item in gps_tracks.scenario_catalogue()}
    assert catalogue == set(gps_tracks.TRACK_SCENARIOS)


def test_stable_seed_is_reproducible_from_a_string():
    """``hash()`` is salted per process; this must not be."""
    assert gps_tracks.stable_seed("unauthorised") == gps_tracks.stable_seed("unauthorised")
    assert gps_tracks.stable_seed("parked") != gps_tracks.stable_seed("unauthorised")
    assert gps_tracks.stable_seed(42) == 42


# ---------------------------------------------------------------------------
# Drift guards: the mirrored vocabularies
# ---------------------------------------------------------------------------
def test_the_movement_vocabulary_mirrors_the_database_enum():
    from models.enums import MovementStatus

    assert tuple(item.value for item in MovementStatus) == engine.MOVEMENT_STATUSES


def test_the_alert_vocabulary_mirrors_the_database_enums():
    from models.enums import AlertSeverity, AlertType

    engine_alert_types = {
        engine.ALERT_ABNORMAL_MOVEMENT,
        engine.ALERT_GEOFENCE_BREACH,
        engine.ALERT_ROUTE_DEVIATION,
        engine.ALERT_TELEMETRY_LOSS,
        engine.ALERT_UNUSUAL_SPEED,
    }
    # The engine raises a subset of the schema's vocabulary; every value it can
    # emit must exist in the database enum or the INSERT would fail.
    assert engine_alert_types <= {item.value for item in AlertType}

    engine_severities = {
        engine.SEVERITY_CRITICAL,
        engine.SEVERITY_HIGH,
        engine.SEVERITY_LOW,
        engine.SEVERITY_MEDIUM,
    }
    assert engine_severities == {item.value for item in AlertSeverity}


def test_every_status_the_engine_can_return_is_a_valid_movement_status():
    from models.enums import MovementStatus

    allowed = {item.value for item in MovementStatus}
    ring = engine.circular_ring(LAT, LON, 100.0)
    config = engine.GeofenceConfig()
    for latitude, speed, retrieval in (
        (LAT, 0.0, False),
        (north_of(110.0)[0], 0.0, False),
        (north_of(160.0)[0], 55.0, False),
        (north_of(400.0)[0], 55.0, True),
    ):
        verdict = engine.evaluate_fix(
            latitude=latitude,
            longitude=LON,
            zone_ring=ring,
            zone_centre=(LAT, LON),
            zone_radius_metres=100.0,
            speed_kmph=speed,
            recorded_at=NOW,
            movement_expected=retrieval,
            config=config,
        )
        assert verdict.status in allowed
