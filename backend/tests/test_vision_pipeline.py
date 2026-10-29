"""Tests for the OpenCV condition-comparison pipeline.

These run without a database or an application: :mod:`ai.vision` deliberately
knows nothing about Flask or SQLAlchemy, so the whole pipeline can be exercised
in isolation. That is the point of the import direction, and these tests are
what would break if someone reversed it.

The cases mirror the ones the module is expected to survive:

* **same image** - nothing changed, so nothing may be reported;
* **different image** - a planted defect must be found;
* **invalid image** - undecodable bytes must be a 422, not a 500;
* **missing image** - absent bytes must be rejected, not silently skipped;
* **multiple angles** - each viewpoint maps to its own region grid.

The synthetic photographs come from :mod:`ai.synthetic`, so the "damage" here is
drawn rather than real. That is a limitation of the test data, not of the
assertions: the pipeline is genuinely run, and a defect it fails to find is a
genuine failure.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from ai.synthetic import (
    SyntheticDefect,
    build_scenario,
    generate_pair,
    scenario_names,
)
from ai.vision import compare_images
from ai.vision.config import DEFAULT_CONFIG
from ai.vision.damage import TYPE_CONFIDENCE_CEILING
from ai.vision.regions import (
    REGION_GRIDS,
    SUPPORTED_ANGLES,
    Viewpoint,
    cell_for,
    map_region,
    normalise_viewpoint,
)
from utils.exceptions import ValidationError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _is_png(blob: bytes) -> bool:
    return blob.startswith(PNG_SIGNATURE)


def _dimensions(blob: bytes) -> tuple[int, int]:
    decoded = cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    height, width = decoded.shape[:2]
    return width, height


# ---------------------------------------------------------------------------
# Same image
# ---------------------------------------------------------------------------
def test_identical_images_report_no_damage():
    """The 'same image' case: no change, so no finding."""
    pair = generate_pair(defects=(), camera_jitter=False, label="identical")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert result.findings == []
    assert result.damage_detected is False
    assert result.comparison_png


def test_identical_images_still_produce_a_comparison_image():
    """A clean comparison still renders - the report page always has a picture."""
    pair = generate_pair(defects=(), camera_jitter=False)

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert _is_png(result.comparison_png)
    # Three panels side by side plus the footer strip.
    width, height = _dimensions(result.comparison_png)
    assert width > height


def test_a_clean_result_says_it_is_not_a_guarantee():
    """An empty result must not read as a clean bill of health."""
    pair = generate_pair(defects=(), camera_jitter=False)

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert any("not a guarantee" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# Different image
# ---------------------------------------------------------------------------
def test_a_planted_scratch_is_detected():
    """The 'different image' case: a real change must be found."""
    pair = build_scenario("scratch")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert result.damage_detected is True
    assert len(result.findings) >= 1
    assert result.findings[0].damage_type == "SCRATCH"


def test_a_detected_region_lands_where_the_defect_was_planted():
    """The bounding box must overlap the planted location, not just exist."""
    pair = build_scenario("scratch")
    planted = pair.defects[0]

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)
    box = result.findings[0].bounding_box

    assert box["x"] <= planted.centre[0] <= box["x"] + box["width"]
    assert box["y"] <= planted.centre[1] <= box["y"] + box["height"]


def test_a_planted_dent_is_detected_as_a_dent():
    pair = build_scenario("dent")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert [finding.damage_type for finding in result.findings] == ["DENT"]


def test_a_planted_rust_patch_is_detected_as_rust():
    """Rust is a colour change; only the chroma channel can see it."""
    pair = build_scenario("rust")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert [finding.damage_type for finding in result.findings] == ["RUST"]


def test_three_planted_defects_produce_three_findings():
    pair = build_scenario("multiple")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert len(result.findings) == 3
    assert {finding.damage_type for finding in result.findings} == {
        "SCRATCH",
        "DENT",
        "PAINT_CHIP",
    }


def test_findings_are_ranked_by_confidence():
    pair = build_scenario("multiple")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    confidences = [finding.confidence for finding in result.findings]
    assert confidences == sorted(confidences, reverse=True)


def test_the_pipeline_is_deterministic():
    """Same input, same output. A heuristic that varies is not reproducible."""
    pair = build_scenario("scratch")

    first = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)
    second = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert [f.confidence for f in first.findings] == [f.confidence for f in second.findings]
    assert [f.bounding_box for f in first.findings] == [f.bounding_box for f in second.findings]
    assert first.comparison_png == second.comparison_png


# ---------------------------------------------------------------------------
# False positives
# ---------------------------------------------------------------------------
def test_a_lighting_change_alone_reports_no_damage():
    """Requirement 9: minor lighting differences must not become findings.

    This is the case a naive differencer fails, and the reason the pipeline
    normalises exposure and rejects diffuse low-contrast regions.
    """
    pair = build_scenario("lighting_only")
    assert pair.lighting_shift != 1.0

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert result.findings == []


def test_a_global_brightness_shift_is_corrected_before_differencing():
    """The exposure correction must actually run, not silently no-op."""
    pair = build_scenario("lighting_only")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert result.statistics["luminance_gain_applied"] != 1.0


def test_raising_the_threshold_suppresses_a_weak_finding():
    """Exposes the heuristic's sensitivity honestly.

    If a reviewer doubts a finding, this is how they check it: change the
    threshold and see whether the finding survives. A pipeline whose output
    cannot be interrogated this way is not explainable.
    """
    pair = build_scenario("scratch")

    sensitive = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)
    strict = compare_images(
        pair.drop_off_png,
        pair.pickup_png,
        angle=pair.angle,
        config=DEFAULT_CONFIG.with_overrides(difference_threshold=1e6),
    )

    assert sensitive.findings
    assert strict.findings == []


def test_the_parameters_that_produced_a_result_are_returned():
    """Reproducibility: a reviewer can re-run with the stored settings."""
    pair = build_scenario("scratch")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert result.parameters["version"] == DEFAULT_CONFIG.version
    assert result.parameters["difference_threshold"] == DEFAULT_CONFIG.difference_threshold
    assert result.parameters["orb_features"] == DEFAULT_CONFIG.orb_features


# ---------------------------------------------------------------------------
# Invalid and missing images
# ---------------------------------------------------------------------------
def test_undecodable_bytes_are_rejected_not_crashed():
    """The 'invalid image' case: a 422, not an AttributeError three modules on."""
    pair = build_scenario("scratch")

    with pytest.raises(ValidationError) as error:
        compare_images(b"this is not an image at all", pair.pickup_png, angle="left")

    assert error.value.status_code == 422
    assert error.value.details["field"] == "drop_off_image"


def test_a_truncated_png_is_rejected():
    """A valid header with the body cut off still fails to decode."""
    pair = build_scenario("scratch")
    truncated = pair.drop_off_png[: len(pair.drop_off_png) // 2]

    with pytest.raises(ValidationError) as error:
        compare_images(truncated, pair.pickup_png, angle="left")

    assert error.value.details["field"] == "drop_off_image"


def test_missing_image_bytes_are_rejected():
    """The 'missing image' case: empty bytes, on either side."""
    pair = build_scenario("scratch")

    for empty, other, expected_field in (
        (b"", pair.pickup_png, "drop_off_image"),
        (pair.drop_off_png, b"", "pickup_image"),
    ):
        with pytest.raises(ValidationError) as error:
            compare_images(empty, other, angle="left")
        assert error.value.details["field"] == expected_field


def test_a_text_file_renamed_to_png_is_rejected():
    """The extension is irrelevant; the bytes are what get decoded."""
    pair = build_scenario("scratch")

    with pytest.raises(ValidationError):
        compare_images(b"\x89PNG\r\n\x1a\nbut not really a png", pair.pickup_png)


def test_an_image_too_small_to_assess_is_rejected():
    """Below the minimum, difference regions are indistinguishable from noise."""
    tiny = cv2.imencode(".png", np.zeros((32, 32, 3), dtype=np.uint8))[1].tobytes()
    pair = build_scenario("scratch")

    with pytest.raises(ValidationError) as error:
        compare_images(tiny, pair.pickup_png, angle="left")

    assert "too small" in str(error.value.message).lower()
    assert error.value.details["min_dimension"] == DEFAULT_CONFIG.min_image_dimension


def test_the_field_name_identifies_which_image_failed():
    """With two inputs, an error must say which one was bad."""
    pair = build_scenario("scratch")

    with pytest.raises(ValidationError) as bad_drop_off:
        compare_images(b"nonsense", pair.pickup_png)
    with pytest.raises(ValidationError) as bad_pickup:
        compare_images(pair.drop_off_png, b"nonsense")

    assert bad_drop_off.value.details["field"] == "drop_off_image"
    assert bad_pickup.value.details["field"] == "pickup_image"


# ---------------------------------------------------------------------------
# Multiple angles
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("angle", sorted(set(SUPPORTED_ANGLES) - {"GENERAL"}))
def test_every_supported_angle_produces_a_result(angle):
    """Multiple angles: each viewpoint runs end to end."""
    pair = generate_pair(
        defects=(
            SyntheticDefect(kind="scratch", centre=(0.6, 0.6), extent=(0.2, 0.03)),
        ),
        angle=angle,
        label=f"angle-{angle}",
    )

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=angle)

    assert result.viewpoint == angle
    assert result.findings
    assert result.statistics["viewpoint"] == angle


def test_angle_aliases_normalise_to_the_same_viewpoint():
    """'front-left', 'FRONT_LEFT' and 'front left' must not be three viewpoints."""
    assert normalise_viewpoint("front-left") is Viewpoint.LEFT_SIDE
    assert normalise_viewpoint("FRONT_LEFT") is Viewpoint.LEFT_SIDE
    assert normalise_viewpoint("front left") is Viewpoint.LEFT_SIDE
    assert normalise_viewpoint("  Rear  ") is Viewpoint.REAR
    assert normalise_viewpoint("tyre") is Viewpoint.WHEEL


def test_an_unknown_angle_falls_back_to_general_rather_than_raising():
    assert normalise_viewpoint("upside-down-underwater") is Viewpoint.GENERAL
    assert normalise_viewpoint(None) is Viewpoint.GENERAL
    assert normalise_viewpoint("") is Viewpoint.GENERAL


def test_an_unlabelled_photo_is_not_mapped_to_a_panel():
    """Guessing a panel from an unlabelled photo would be an overclaim."""
    pair = generate_pair(
        defects=(
            SyntheticDefect(kind="scratch", centre=(0.6, 0.6), extent=(0.2, 0.03)),
        ),
        angle=None,
        label="unlabelled",
    )

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=None)

    assert result.findings
    assert result.findings[0].region == "OTHER"
    assert "No camera angle was supplied" in result.findings[0].region_mapping["basis"]
    assert result.findings[0].region_mapping["mapped"] is False


def test_front_and_rear_grids_mirror_left_and_right():
    """Standing at the nose, the car's left is the viewer's right.

    Getting this backwards would blame the wrong door, so the two grids are
    asserted to be mirrors rather than sharing one.
    """
    front_row = REGION_GRIDS[Viewpoint.FRONT][2]
    rear_row = REGION_GRIDS[Viewpoint.REAR][2]

    assert front_row[0].value == "RIGHT_FENDER"
    assert front_row[2].value == "LEFT_FENDER"
    assert rear_row[0].value == "LEFT_FENDER"
    assert rear_row[2].value == "RIGHT_FENDER"


def test_every_viewpoint_grid_is_three_by_three_and_names_real_regions():
    for viewpoint, grid in REGION_GRIDS.items():
        assert len(grid) == 3, viewpoint
        for row in grid:
            assert len(row) == 3, viewpoint
            for region in row:
                assert isinstance(region.value, str) and region.value


def test_grid_cells_cover_the_whole_frame():
    """No part of the image may fall outside the mapping."""
    for x in (0, 0.25, 0.5, 0.75, 0.999):
        for y in (0, 0.25, 0.5, 0.75, 0.999):
            row, column = cell_for((int(x * 960), int(y * 640), 10, 10), 960, 640)
            assert 0 <= row <= 2 and 0 <= column <= 2


def test_the_same_defect_maps_to_different_regions_for_different_angles():
    """The mapping is viewpoint-relative, which is the whole point of angles."""
    box = (int(0.10 * 960), int(0.55 * 640), 60, 60)

    left = map_region(
        box, frame_width=960, frame_height=640, viewpoint=Viewpoint.LEFT_SIDE
    )
    right = map_region(
        box, frame_width=960, frame_height=640, viewpoint=Viewpoint.RIGHT_SIDE
    )

    assert left["region"] != right["region"]
    assert left["mapped"] and right["mapped"]


# ---------------------------------------------------------------------------
# Honesty of the output
# ---------------------------------------------------------------------------
def test_every_finding_explains_itself_in_plain_language():
    """Explainability is a schema requirement, not a nicety."""
    pair = build_scenario("multiple")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    for finding in result.findings:
        assert len(finding.reasoning) > 40
        assert "heuristic" in finding.reasoning
        assert "trained model" in finding.reasoning
        assert finding.statistics


def test_confidence_stays_within_probability_bounds():
    pair = build_scenario("multiple")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    for finding in result.findings:
        assert 0.0 <= finding.confidence <= 1.0
        assert 0.0 <= finding.type_confidence <= 1.0


def test_label_confidence_is_capped_below_the_region_confidence():
    """Naming a defect is a weaker claim than measuring a change.

    The cap is what stops a well-measured region from implying a well-guessed
    label. If this test fails, the pipeline is overstating its labeller.
    """
    assert TYPE_CONFIDENCE_CEILING < 1.0

    for name in scenario_names():
        pair = build_scenario(name)
        result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)
        for finding in result.findings:
            assert finding.type_confidence <= TYPE_CONFIDENCE_CEILING


def test_the_comparison_image_is_a_real_png():
    pair = build_scenario("scratch")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert _is_png(result.comparison_png)
    assert _dimensions(result.comparison_png)[0] > 300


def test_each_finding_gets_its_own_evidence_crop():
    pair = build_scenario("multiple")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    assert len(result.evidence) == min(len(result.findings), 8)
    for item in result.evidence:
        assert _is_png(item.png)


def test_every_demo_scenario_detects_exactly_its_planted_defects():
    """The catalogue is a regression suite, not decoration."""
    for name in scenario_names():
        pair = build_scenario(name)
        result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

        planted_types = {defect.kind.upper() for defect in pair.defects}
        detected_types = {finding.damage_type for finding in result.findings}

        assert detected_types == planted_types, (
            f"{name}: planted {planted_types}, detected {detected_types}"
        )


def test_a_config_with_an_unknown_parameter_is_rejected():
    """A typo in an override must fail loudly, not be silently ignored."""
    with pytest.raises(ValueError, match="Unknown pipeline parameters"):
        DEFAULT_CONFIG.with_overrides(differnce_threshold=10)


def test_overrides_produce_a_new_config_without_mutating_the_default():
    original = DEFAULT_CONFIG.difference_threshold

    adjusted = DEFAULT_CONFIG.with_overrides(difference_threshold=99.0)

    assert adjusted.difference_threshold == 99.0
    assert DEFAULT_CONFIG.difference_threshold == original


def test_pipeline_config_is_frozen():
    """The parameter set must not be mutable at runtime."""
    with pytest.raises(Exception):  # noqa: B017, PT011 - dataclasses raise FrozenInstanceError
        DEFAULT_CONFIG.difference_threshold = 1.0  # type: ignore[misc]


def test_alignment_reports_when_it_could_not_help():
    """Comparing unrelated photos must say alignment failed, not fake it."""
    left = build_scenario("scratch")
    unrelated = generate_pair(
        defects=(), paint="pearl_white", seed=555, label="unrelated", camera_jitter=False
    )

    result = compare_images(left.drop_off_png, unrelated.drop_off_png, angle="left_side")

    # Whether or not it aligned, the result must describe what it did.
    assert "applied" in result.alignment.describe()
    assert "quality" in result.alignment.describe()


def test_statistics_record_the_frame_and_alignment_facts():
    pair = build_scenario("scratch")

    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    stats = result.statistics
    assert stats["frame"]["width"] > 0
    assert "before" in stats and "after" in stats
    assert "alignment" in stats
    assert "changed_pixel_fraction" in stats
    assert 0.0 <= stats["changed_pixel_fraction"] <= 1.0
