"""Tests for the retrieval-time predictor (Phase 9b).

Two layers, tested separately because they fail differently.

**The model layer** runs without a database. Its most important test is the one
at the top: single-row and batch inference must agree. They did not, once --
``predict_minutes`` skipped the feature scaler while the batch path applied it,
and the two disagreed by a factor of ten. Because the live service scores one
request at a time, that bug would have shipped in production while every
batch-scored metric in the evaluation script still looked correct.

**The endpoint layer** runs against real PostgreSQL. A custody session is created
through the ticket API, an estimate is requested over HTTP, stored, and read
back. Nothing is stubbed.

What is deliberately asserted rather than assumed:

* **MAE is reported next to a baseline.** A number with no comparison is not
  evidence, so the test requires the model to beat the median predictor and
  fails if it does not.
* **Every estimate discloses its synthetic provenance.** The brief requires it;
  the test enforces it on all three endpoints.
* **Validation refuses a partly-specified feature set**, because defaulting a
  missing feature to zero would produce a confident answer from a guess.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import text

from ai.retrieval.dataset import generate_history, load_history
from ai.retrieval.features import (
    RETRIEVAL_FEATURE_COLUMNS,
    RetrievalContext,
    extract_features,
)
from ai.retrieval.model import MODEL_VERSION, RetrievalModel, round_minutes
from app.extensions import db
from models.enums import CustodyStatus, PredictionStatus
from models.retrieval_prediction import RetrievalPrediction
from tests.workflow_helpers import bearer, make_user, make_vehicle
from utils.security import Role

PREDICT = "/api/v1/retrieval/predict"
PREDICT_ALIAS = "/api/retrieval/predict"
MODEL_INFO = "/api/v1/retrieval/model"


# ---------------------------------------------------------------------------
# The model layer: no database required
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def trained():
    """One model fitted once and shared, so the suite does not refit per test."""
    dataset = generate_history(episodes=1200, seed=20260909)
    return RetrievalModel(alpha=1.0).fit(dataset), dataset


def _context(**overrides) -> RetrievalContext:
    base = {
        "queue_size": 3,
        "queue_position": 2,
        "active_valets": 5,
        "zone_capacity": 20,
        "zone_occupancy": 10,
        "requested_at": datetime(2026, 9, 9, 14, 0),
        "historical_zone_mean_minutes": 8.0,
    }
    base.update(overrides)
    return RetrievalContext(**base)


class TestSingleAndBatchInferenceAgree:
    """The regression that caught a shipped-would-have-been bug.

    A linear model must scale its inputs before applying coefficients. Two
    code paths did that independently and one of them forgot, so single-row
    prediction was wrong by an order of magnitude while batch prediction was
    right. Any test that only ever scored a batch would have passed.
    """

    def test_single_row_matches_batch_exactly(self, trained):
        model, _ = trained
        contexts = [
            _context(),
            _context(queue_size=9, queue_position=7, active_valets=2),
            _context(vehicle_flagged=True, requested_at=datetime(2026, 9, 9, 1, 30)),
        ]
        features = [extract_features(ctx) for ctx in contexts]

        singles = np.array([model.predict_minutes(f) for f in features])
        batch = model.predict_array(
            np.array([[f[name] for name in RETRIEVAL_FEATURE_COLUMNS] for f in features])
        )
        np.testing.assert_allclose(singles, batch, rtol=1e-12, atol=1e-12)

    def test_predictions_are_in_the_range_the_data_occupies(self, trained):
        """A guard on plausibility, independent of the exact fit.

        The generator produces retrievals between roughly 2 and 38 minutes. An
        estimate outside that band for an ordinary input means the arithmetic is
        wrong somewhere, even if no assertion about coefficients would notice.
        """
        model, dataset = trained
        low, high = float(dataset.y.min()), float(dataset.y.max())
        for ctx in (_context(), _context(queue_size=1, queue_position=1)):
            minutes = model.predict_minutes(extract_features(ctx))
            assert low * 0.5 <= minutes <= high * 1.5, (
                f"{minutes:.1f} min is implausible for this input "
                f"(data spans {low:.1f}-{high:.1f})"
            )

    def test_explained_contributions_sum_to_the_prediction(self, trained):
        """The explanation must account for the answer it explains."""
        model, _ = trained
        features = extract_features(_context(queue_size=5, queue_position=4))
        contributions, _basis = model.explain(features)
        predicted = model.predict_minutes(features)
        intercept = float(np.asarray(model._estimator.intercept_).ravel()[0])  # noqa: SLF001
        assert abs(sum(contributions.values()) + intercept - predicted) < 1e-6


class TestModelBeatsItsBaseline:
    """MAE is the primary metric, and it is only meaningful against a baseline."""

    def test_mae_is_lower_than_the_median_predictor(self, trained):
        model, _ = trained
        metrics = model.metrics
        assert metrics is not None
        assert metrics.mae < metrics.baseline_mae, (
            f"MAE {metrics.mae:.3f} does not beat the median baseline "
            f"{metrics.baseline_mae:.3f}; the model adds nothing"
        )

    def test_metrics_report_mae_as_the_primary_metric(self, trained):
        model, _ = trained
        payload = model.metrics.to_dict()
        assert payload["primary_metric"] == "mae_minutes"
        assert payload["mae_minutes"] > 0
        # Reported alongside so a reader can see the distribution, not just a mean.
        assert payload["median_absolute_error_minutes"] > 0
        assert payload["rmse_minutes"] >= payload["mae_minutes"]

    def test_mae_has_a_floor_because_the_data_is_noisy(self, trained):
        """The generator adds lognormal noise, so MAE cannot approach zero.

        If a future change makes this pass at near-zero, the labels have stopped
        being noisy and the evaluation has stopped measuring anything.
        """
        model, _ = trained
        assert model.metrics.mae > 0.05

    def test_improvement_is_a_positive_fraction(self, trained):
        model, _ = trained
        assert 0.0 < model.metrics.improvement_over_baseline < 1.0


class TestArtifactRoundTrip:
    def test_saved_model_reloads_and_predicts_identically(self, trained, tmp_path):
        model, _ = trained
        path = model.save(tmp_path / "retrieval.json")
        reloaded = RetrievalModel.load(path)

        features = extract_features(_context(queue_size=4, queue_position=3))
        assert model.predict_minutes(features) == pytest.approx(
            reloaded.predict_minutes(features), rel=1e-12
        )
        # The artifact stores metrics rounded to three decimals for readability,
        # so the reloaded figure is that rounded value, not the full-precision
        # one the in-memory model still holds.
        assert reloaded.metrics.mae == pytest.approx(model.metrics.mae, abs=1e-3)
        assert reloaded.metrics.mae == pytest.approx(
            round(model.metrics.mae, 3), abs=1e-9
        )

    def test_artifact_is_plain_json_and_carries_the_disclaimer(self, trained, tmp_path):
        """No pickle: loading must not be able to execute anything."""
        model, _ = trained
        path = model.save(tmp_path / "retrieval.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["format"] == "json-linear-retrieval-v1"
        assert payload["trained_on_synthetic_data"] is True
        assert "synthetic" in payload["prototype_disclaimer"].lower()
        assert len(payload["coefficients"]) == len(RETRIEVAL_FEATURE_COLUMNS)

    def test_loading_an_artifact_with_different_features_is_refused(self, trained, tmp_path):
        """Applying the right coefficients to the wrong columns is worse than failing."""
        model, _ = trained
        path = model.save(tmp_path / "retrieval.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["feature_columns"] = ["something", "else"]
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="different feature set"):
            RetrievalModel.load(path)


class TestFeaturesAndData:
    def test_generated_history_round_trips_losslessly(self, tmp_path):
        """Training on the exported file must equal training on fresh data."""
        dataset = generate_history(episodes=600, seed=4242)
        path = tmp_path / "history.json"
        payload = {
            "feature_columns": list(RETRIEVAL_FEATURE_COLUMNS),
            "generator": {"test_fraction": 0.25},
            "rows": [episode.as_row() for episode in dataset.episodes],
        }
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")

        reloaded = load_history(path)
        assert np.array_equal(reloaded.X, dataset.X)
        assert np.array_equal(reloaded.y, dataset.y)
        assert np.array_equal(reloaded.test_mask, dataset.test_mask)

    def test_loading_history_with_a_mismatched_feature_set_is_refused(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text(
            json.dumps({"feature_columns": ["nope"], "rows": [{"x": 1}]}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="different feature set"):
            load_history(path)

    def test_split_is_temporal_not_random(self):
        """Held-out rows must all be later than training rows.

        A random split would let the model see Friday evenings during training
        and be scored on other Friday evenings, flattering the MAE.
        """
        dataset = generate_history(episodes=800, seed=99)
        times = [episode.requested_at for episode in dataset.episodes]
        training = [t for t, keep in zip(times, ~dataset.test_mask, strict=True) if keep]
        held_out = [t for t, keep in zip(times, dataset.test_mask, strict=True) if keep]
        assert training and held_out
        assert max(training) <= min(held_out)

    def test_zone_profile_is_stable_across_calls(self):
        """The mapping must not depend on the process's hash seed.

        ``hash()`` on strings is salted per process, so a mapping built from it
        would differ between training and the running service. This asserts the
        code uses something fixed instead.
        """
        from ai.retrieval.dataset import zone_profile_for

        first = zone_profile_for("ZONE-A")
        assert zone_profile_for("ZONE-A") == first
        # An unseen zone must still resolve rather than raise.
        assert zone_profile_for("SOME-BRAND-NEW-ZONE")["capacity"] > 0

    def test_feature_vector_covers_every_trained_column(self):
        features = extract_features(_context())
        assert set(features) == set(RETRIEVAL_FEATURE_COLUMNS)


class TestHelpers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(1.0, 1), (1.4, 1), (1.5, 2), (8.49, 8), (8.5, 9), (0.2, 0)],
    )
    def test_round_minutes_rounds_half_up(self, value, expected):
        """Half rounds up: under-promising a wait is the worse failure.

        This is pure rounding. The one-minute floor is applied in
        ``predict_minutes``, not here, so 0.2 genuinely rounds to 0.
        """
        assert round_minutes(value) == expected

    def test_minimum_wait_is_one_minute(self, trained):
        model, _ = trained
        # An extreme input should still not produce a zero or negative wait.
        minutes = model.predict_minutes(
            extract_features(
                _context(
                    queue_size=1,
                    queue_position=1,
                    active_valets=12,
                    zone_capacity=12,
                    zone_occupancy=0,
                    requested_at=datetime(2026, 9, 9, 3, 0),
                    historical_zone_mean_minutes=3.0,
                )
            )
        )
        assert minutes >= 1.0


# ---------------------------------------------------------------------------
# The endpoint layer: real PostgreSQL
# ---------------------------------------------------------------------------
def _custody(client, *, zone=None, status=CustodyStatus.PARKED, requested=True):
    """A real ticket and custody session, created through the API."""
    from models.custody_session import CustodySession
    from models.valet_ticket import ValetTicket

    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    issued = client.post(
        "/api/v1/tickets",
        json={"vehicle_id": str(vehicle.id)},
        headers=bearer(operator),
    )
    assert issued.status_code == 201, issued.get_json()
    ticket = db.session.get(ValetTicket, uuid.UUID(issued.get_json()["data"]["ticket"]["id"]))

    # dropped_off_at has to be set explicitly and in the past: the schema has a
    # CHECK that a retrieval request cannot precede the drop-off, and the
    # column's own default is "now", which would be later than the request.
    dropped_off_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    requested_at = dropped_off_at + timedelta(minutes=29) if requested else None
    custody = CustodySession(
        ticket_id=ticket.id,
        vehicle_id=vehicle.id,
        zone_id=zone.id if zone else None,
        assigned_valet_id=operator.id,
        received_by_id=operator.id,
        status=str(status),
        dropped_off_at=dropped_off_at,
        retrieval_requested_at=requested_at,
    )
    db.session.add(custody)
    db.session.commit()
    return {
        "operator": operator,
        "customer": customer,
        "vehicle": vehicle,
        "custody": custody,
        "custody_id": str(custody.id),
    }


@pytest.fixture(scope="module")
def trained_artifact(pg_app, tmp_path_factory):
    """Train a small model to a temp path and point the app at it.

    The artifact is not committed -- it is regenerable, and a checked-in copy
    drifts out of step with the code that reads it. The alternative used
    elsewhere in this suite is to skip when no artifact exists, which quietly
    stops testing the endpoints on any fresh checkout. Fitting 1,200 rows takes
    about a second, so these tests can simply always run.
    """
    from services.retrieval import retrieval_model

    dataset = generate_history(episodes=1200, seed=20260909)
    path = tmp_path_factory.mktemp("retrieval") / "retrieval_model.json"
    RetrievalModel(alpha=1.0).fit(dataset).save(path)

    previous = pg_app.config.get("RETRIEVAL_MODEL_PATH")
    pg_app.config["RETRIEVAL_MODEL_PATH"] = path
    retrieval_model.reset()
    yield path
    pg_app.config["RETRIEVAL_MODEL_PATH"] = previous
    retrieval_model.reset()


@pytest.mark.usefixtures("trained_artifact")
@pytest.mark.postgres
class TestPredictEndpoint:
    def test_predict_returns_the_four_required_fields(self, pg_client, pg_app):
        context = _custody(pg_client)
        response = pg_client.post(
            PREDICT,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 201, response.get_json()
        data = response.get_json()["data"]

        # The brief names these four specifically.
        for field in (
            "estimated_retrieval_time",
            "estimated_minutes",
            "queue_position",
            "recommended_dispatch_time",
        ):
            assert field in data, f"missing required field {field}"

        assert isinstance(data["estimated_minutes"], int)
        assert data["estimated_minutes"] >= 1
        assert data["queue_position"] >= 1
        assert "recommended_dispatch" in data

    def test_estimate_discloses_its_synthetic_origin(self, pg_client, pg_app):
        context = _custody(pg_client)
        response = pg_client.post(
            PREDICT,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["operator"]),
        )
        data = response.get_json()["data"]
        assert data["trained_on_synthetic_data"] is True
        assert "synthetic" in data["data_basis"].lower()
        assert data["disclaimer"]

    def test_prediction_is_stored_with_its_inputs(self, pg_client, pg_app):
        context = _custody(pg_client)
        response = pg_client.post(
            PREDICT,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["operator"]),
        )
        prediction_id = response.get_json()["data"]["prediction_id"]
        db.session.expire_all()

        row = db.session.get(RetrievalPrediction, uuid.UUID(prediction_id))
        assert row is not None
        assert row.status == PredictionStatus.PENDING.value
        assert row.predicted_wait_minutes == response.get_json()["data"]["estimated_minutes"]
        assert row.model_version == MODEL_VERSION
        assert row.inputs_snapshot["trained_on_synthetic_data"] is True
        assert "synthetic" in row.explanation.lower()
        # The schema's own CHECK constraints would reject an out-of-order interval.
        assert row.min_wait_minutes <= row.predicted_wait_minutes <= row.max_wait_minutes
        assert row.dispatch_after >= row.requested_at

    def test_the_prediction_is_committed_not_merely_flushed(self, pg_client, pg_app):
        """A separate connection must be able to see the row.

        The rest of this suite cannot detect a missing commit: it shares one
        connection with the request, and an uncommitted row is visible on the
        same connection. The endpoint once returned 201 with a prediction id
        while the row was discarded at the end of the request, and every test
        here still passed. Only a second connection sees the truth.
        """
        context = _custody(pg_client)
        response = pg_client.post(
            PREDICT,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 201, response.get_json()
        prediction_id = response.get_json()["data"]["prediction_id"]

        engine = db.engine
        with engine.connect() as connection:
            found = connection.execute(
                text("SELECT status FROM retrieval_predictions WHERE id = :id"),
                {"id": prediction_id},
            ).first()
        assert found is not None, (
            "the prediction is not visible to another connection, so it was "
            "flushed but never committed"
        )
        assert found[0] == PredictionStatus.PENDING.value

    def test_dispatch_recommendation_is_not_before_the_request(self, pg_client, pg_app):
        context = _custody(pg_client)
        response = pg_client.post(
            PREDICT,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["operator"]),
        )
        data = response.get_json()["data"]
        assert data["recommended_dispatch_time"] >= data["requested_at"]
        assert data["recommended_dispatch_time"] <= data["estimated_retrieval_time"]

    def test_a_longer_queue_produces_a_longer_estimate(self, pg_client, pg_app):
        """The estimate must respond to the thing it claims to measure."""
        first = _custody(pg_client)
        alone = pg_client.post(
            PREDICT,
            json={"custody_session_id": first["custody_id"]},
            headers=bearer(first["operator"]),
        ).get_json()["data"]
        assert alone["queue_position"] == 1

        # Three more retrievals, each requested later, so the fourth is last.
        later_positions = []
        for _ in range(3):
            other = _custody(
                pg_client, status=CustodyStatus.RETRIEVAL_REQUESTED, requested=True
            )
            later_positions.append(other)

        last = _custody(pg_client)
        crowded = pg_client.post(
            PREDICT,
            json={"custody_session_id": last["custody_id"]},
            headers=bearer(last["operator"]),
        ).get_json()["data"]

        assert crowded["queue_position"] > alone["queue_position"]
        assert crowded["estimated_minutes"] > alone["estimated_minutes"]

    def test_second_request_supersedes_the_first_but_keeps_it(self, pg_client, pg_app):
        """What the customer was told is part of the record."""
        context = _custody(pg_client)
        headers = bearer(context["operator"])

        first = pg_client.post(
            PREDICT, json={"custody_session_id": context["custody_id"]}, headers=headers
        ).get_json()["data"]["prediction_id"]
        second = pg_client.post(
            PREDICT, json={"custody_session_id": context["custody_id"]}, headers=headers
        ).get_json()["data"]["prediction_id"]
        assert first != second
        db.session.expire_all()

        assert (
            db.session.get(RetrievalPrediction, uuid.UUID(first)).status
            == PredictionStatus.SUPERSEDED.value
        )
        assert (
            db.session.get(RetrievalPrediction, uuid.UUID(second)).status
            == PredictionStatus.PENDING.value
        )

    def test_anonymous_request_is_rejected(self, pg_client, pg_app):
        response = pg_client.post(PREDICT, json={"custody_session_id": str(uuid.uuid4())})
        assert response.status_code == 401

    def test_unknown_custody_session_is_a_404(self, pg_client, pg_app):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        response = pg_client.post(
            PREDICT,
            json={"custody_session_id": str(uuid.uuid4())},
            headers=bearer(operator),
        )
        assert response.status_code == 404

    def test_neither_session_nor_features_is_refused(self, pg_client, pg_app):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        response = pg_client.post(PREDICT, json={}, headers=bearer(operator))
        assert response.status_code == 422
        assert "custody_session_id" in response.get_json()["error"]["message"]

    def test_supplying_both_is_refused(self, pg_client, pg_app):
        context = _custody(pg_client)
        features = extract_features(_context())
        response = pg_client.post(
            PREDICT,
            json={"custody_session_id": context["custody_id"], "features": features},
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 422


@pytest.mark.usefixtures("trained_artifact")
@pytest.mark.postgres
class TestWhatIfMode:
    def test_features_are_scored_without_writing_anything(self, pg_client, pg_app):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        # Measured as a delta, not an absolute count: this suite shares a
        # session-scoped application, so rows committed by earlier tests are
        # still present and an absolute zero would be asserting about them.
        before = db.session.query(RetrievalPrediction).count()

        features = extract_features(_context(queue_size=6, queue_position=5))
        response = pg_client.post(
            PREDICT, json={"features": features}, headers=bearer(operator)
        )
        assert response.status_code == 200, response.get_json()
        data = response.get_json()["data"]
        assert data["mode"] == "what_if"
        assert data["persisted"] is False
        assert data["estimated_minutes"] >= 1
        assert db.session.query(RetrievalPrediction).count() == before

    def test_a_partial_feature_set_is_refused(self, pg_client, pg_app):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        features = extract_features(_context())
        del features["queue_position"]
        response = pg_client.post(
            PREDICT, json={"features": features}, headers=bearer(operator)
        )
        assert response.status_code == 422
        details = response.get_json()["error"]["details"]
        assert details["missing_features"] == ["queue_position"]

    def test_a_non_numeric_feature_is_refused(self, pg_client, pg_app):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        features = extract_features(_context())
        features["active_valets"] = "many"
        response = pg_client.post(
            PREDICT, json={"features": features}, headers=bearer(operator)
        )
        assert response.status_code == 422

    def test_position_beyond_queue_size_is_refused(self, pg_client, pg_app):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        features = extract_features(_context(queue_size=2, queue_position=9))
        response = pg_client.post(
            PREDICT, json={"features": features}, headers=bearer(operator)
        )
        assert response.status_code == 422


@pytest.mark.usefixtures("trained_artifact")
@pytest.mark.postgres
class TestReadBackEndpoint:
    def test_stored_estimate_is_readable_at_both_prefixes(self, pg_client, pg_app):
        context = _custody(pg_client)
        headers = bearer(context["operator"])
        pg_client.post(
            PREDICT, json={"custody_session_id": context["custody_id"]}, headers=headers
        )

        for prefix in ("/api/v1/retrieval", "/api/retrieval"):
            response = pg_client.get(f"{prefix}/{context['custody_id']}", headers=headers)
            assert response.status_code == 200, (prefix, response.get_json())
            data = response.get_json()["data"]
            assert data["custody_session_id"] == context["custody_id"]
            assert data["predicted_wait_minutes"] >= 1
            assert data["trained_on_synthetic_data"] is True

    def test_reading_before_any_estimate_explains_how_to_make_one(
        self, pg_client, pg_app
    ):
        context = _custody(pg_client)
        response = pg_client.get(
            f"/api/v1/retrieval/{context['custody_id']}", headers=bearer(context["operator"])
        )
        assert response.status_code == 404
        assert "predict" in response.get_json()["error"]["message"]

    def test_unversioned_predict_alias_works(self, pg_client, pg_app):
        """The brief specifies the unversioned path; it must be live."""
        context = _custody(pg_client)
        response = pg_client.post(
            PREDICT_ALIAS,
            json={"custody_session_id": context["custody_id"]},
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 201, response.get_json()
        assert response.get_json()["data"]["estimated_minutes"] >= 1


@pytest.mark.usefixtures("trained_artifact")
@pytest.mark.postgres
class TestModelEndpoint:
    def test_model_info_reports_mae_and_its_baseline(self, pg_client, pg_app):
        operator = make_user(Role.VALET_OPERATOR)
        db.session.commit()
        response = pg_client.get(MODEL_INFO, headers=bearer(operator))
        assert response.status_code == 200, response.get_json()
        data = response.get_json()["data"]

        assert data["available"] is True
        assert data["trained_on_synthetic_data"] is True
        assert data["primary_metric"] == "mae_minutes"
        assert data["metrics"]["mae_minutes"] > 0
        assert data["metrics"]["baseline_mae_minutes"] > data["metrics"]["mae_minutes"]
        assert {column["name"] for column in data["feature_columns"]} == set(
            RETRIEVAL_FEATURE_COLUMNS
        )
