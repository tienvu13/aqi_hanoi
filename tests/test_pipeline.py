import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, text

from aqi_hanoi.core.aqi_guidance import interpret_aqi
from aqi_hanoi.analysis.aqi_trends import (
    build_daily_aqi_summary,
    build_hourly_coverage_report,
)
from aqi_hanoi.analysis.compare_stations import (
    aggregate_station_hours,
    calculate_stats,
    main as compare_stations_main,
)
from aqi_hanoi.collectors.collect_aq_model import main as collect_aq_model
from aqi_hanoi.collectors.collect_hanoi_portal import parse_pm25_history, parse_sites
from aqi_hanoi.collectors.collect_sensorcommunity import parse_pm25_measurements
from aqi_hanoi.collectors.collect_waqi_feed import has_fresh_aqi
from aqi_hanoi.collectors.collect_weather import main as collect_weather
from aqi_hanoi.pipeline.data_quality import build_quality_report
from aqi_hanoi.dashboard.dashboard_export import build_dashboard_export
from aqi_hanoi.pipeline.forecast_monitoring import record_and_resolve_forecasts
from aqi_hanoi.core.http_utils import get_with_retry
from aqi_hanoi.alerts.generate_alerts import generate_alerts
from aqi_hanoi.database.load_dw import upsert_table
from aqi_hanoi.analysis.model_validation import fit_model_matrix, split_temporal_samples
from aqi_hanoi.alerts.notify_alerts import notify_current_alerts
from aqi_hanoi.core.pipeline_utils import update_hourly_csv
from aqi_hanoi.pipeline.scheduled_pipeline import STEPS, run_pipeline
from aqi_hanoi.core.settings import get_database_url, get_openaq_api_key, get_waqi_token
from aqi_hanoi.analysis.source_comparison import (
    build_model_pm25_comparison,
    summarize_pm25_model_agreement,
)
from aqi_hanoi.core.source_measurements import save_measurement_history
from aqi_hanoi.analysis.station_coverage import (
    build_hourly_comparison,
    build_station_comparison_history,
    build_daily_station_summary,
    build_station_history,
    build_station_map_data,
    build_station_coverage,
    latest_station_collection_batch,
    count_overlap_hours,
    summarize_station_collection,
)
from aqi_hanoi.core.waqi_client import fetch_json
from aqi_hanoi.core.waqi_stations import (
    filter_hanoi_coordinates,
    filter_hanoi_waqi_data,
    select_station_uids,
)
from aqi_hanoi.collectors.collect_openaq import parse_location_latest, select_locations
from aqi_hanoi.collectors.collect_optional_sources import COLLECTORS, collect_optional_sources
from aqi_hanoi.collectors.collect_noaa_weather import parse_metar_reports, save_metar_history


class PipelineUtilsTests(unittest.TestCase):
    def test_update_hourly_csv_preserves_history_and_updates_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "hourly.csv"
            original = pd.DataFrame({
                "time": ["2026-10-01 00:00:00", "2026-10-01 01:00:00"],
                "aqi": [10, 20],
            })
            update_hourly_csv(path, original)

            latest = pd.DataFrame({
                "time": ["2026-10-01 01:00:00", "2026-10-01 02:00:00"],
                "aqi": [25, 30],
            })
            merged = update_hourly_csv(path, latest)

            self.assertEqual(len(merged), 3)
            self.assertEqual(merged["aqi"].tolist(), [10, 25, 30])
            self.assertTrue(merged["time"].is_monotonic_increasing)

    def test_update_hourly_csv_rejects_empty_or_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hourly.csv"
            with self.assertRaises(ValueError):
                update_hourly_csv(path, pd.DataFrame())
            with self.assertRaises(ValueError):
                update_hourly_csv(path, pd.DataFrame({"time": ["invalid"]}))


class DashboardExportTests(unittest.TestCase):
    def test_export_bundle_includes_recent_safe_data_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            clean = project / "clean"
            raw = project / "raw"
            figures = project / "figures"
            clean.mkdir()
            raw.mkdir()
            figures.mkdir()
            pd.DataFrame({
                "time": ["2026-09-01 00:00", "2026-10-25 19:00"],
                "aqi": [40, 80],
                "pm25": [10, 20],
            }).to_csv(clean / "fact_model_hourly.csv", index=False)
            pd.DataFrame({
                "time": ["2026-10-25 19:00"],
                "station_uid": [123],
                "station_name": ["Hanoi test station"],
                "aqi_tram": [85],
                "lat": [21.03],
                "lon": [105.86],
            }).to_csv(clean / "station_feed_hourly.csv", index=False)
            pd.DataFrame({
                "station_uid": [123],
                "station_name": ["Hanoi test station"],
                "collected_at": ["2026-10-25 19:30"],
                "obs_time": ["2026-10-25 19:00"],
                "age_min": [30],
                "aqi": [85],
                "lat": [21.03],
                "lon": [105.86],
                "private_secret": ["secret_token_should_not_be_exported"],
            }).to_csv(raw / "waqi_feed.csv", index=False)
            pd.DataFrame({
                "source": ["raw/waqi_feed.csv"],
                "entity": ["station:123"],
                "status": ["OK"],
                "detail": ["Fresh"],
            }).to_csv(figures / "data_quality_report.csv", index=False)
            bundle = build_dashboard_export(
                project, now=pd.Timestamp("2026-10-25 20:00:00")
            )

        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            filenames = set(archive.namelist())
            self.assertIn("summary.csv", filenames)
            self.assertIn("aqi_hanoi.analysis.station_coverage.csv", filenames)
            self.assertIn("aqi_model_hourly_last_30_days.csv", filenames)
            self.assertIn("cams_daily_aqi_summary_last_30_days.csv", filenames)
            self.assertIn("hourly_coverage_last_30_days.csv", filenames)
            self.assertIn("waqi_daily_station_summary_last_30_days.csv", filenames)
            self.assertNotIn("raw/waqi_feed.csv", filenames)
            self.assertNotIn(
                b"secret_token_should_not_be_exported",
                b"".join(archive.read(name) for name in filenames),
            )
            summary = pd.read_csv(archive.open("summary.csv"))
            model_export = pd.read_csv(
                archive.open("aqi_model_hourly_last_30_days.csv")
            )

        self.assertIn("Fresh WAQI stations", summary["metric"].tolist())
        self.assertEqual(model_export["aqi"].tolist(), [80])

    def test_export_rejects_present_file_with_missing_required_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            clean = Path(directory) / "clean"
            clean.mkdir()
            pd.DataFrame({"time": ["2026-10-25 19:00"]}).to_csv(
                clean / "fact_model_hourly.csv", index=False
            )

            with self.assertRaisesRegex(ValueError, "thiếu cột bắt buộc"):
                build_dashboard_export(
                    directory, now=pd.Timestamp("2026-10-25 20:00:00")
                )


class AqiTrendTests(unittest.TestCase):
    def test_daily_summary_aggregates_unique_hours_and_excludes_future_data(self):
        model = pd.DataFrame({
            "time": [
                "2026-10-24 23:00",
                "2026-10-25 00:00",
                "2026-10-25 00:30",
                "2026-10-25 01:00",
                "2026-10-25 02:00",
                "2026-10-25 20:00",
            ],
            "aqi": [101, 50, 150, 151, 501, 300],
        })

        summary = build_daily_aqi_summary(
            model,
            days=2,
            now=pd.Timestamp("2026-10-25 01:30"),
        ).set_index("date")

        self.assertEqual(len(summary), 2)
        current_day = summary.loc[pd.Timestamp("2026-10-25")]
        self.assertEqual(current_day["hours_available"], 2)
        self.assertEqual(current_day["aqi_mean"], 125.5)
        self.assertEqual(current_day["aqi_max"], 151)
        self.assertEqual(current_day["hours_above_100"], 1)
        self.assertEqual(current_day["hours_above_150"], 1)
        self.assertEqual(current_day["coverage_pct"], round(2 / 24 * 100, 1))

    def test_daily_summary_rejects_invalid_days_and_columns(self):
        with self.assertRaisesRegex(ValueError, "days"):
            build_daily_aqi_summary(
                pd.DataFrame(columns=["time", "aqi"]),
                days=0,
                now=pd.Timestamp("2026-10-25 01:00"),
            )
        with self.assertRaisesRegex(ValueError, "Thiếu cột"):
            build_daily_aqi_summary(
                pd.DataFrame(columns=["aqi"]),
                now=pd.Timestamp("2026-10-25 01:00"),
            )

    def test_hourly_coverage_reports_gaps_and_excludes_incomplete_current_hour(self):
        model = pd.DataFrame({
            "time": [
                "2026-10-25 17:00",
                "2026-10-25 18:00",
                "2026-10-25 20:00",
                "2026-10-25 21:00",
            ],
            "aqi": [40, 50, 70, 80],
        })
        stations = pd.DataFrame({
            "time": ["2026-10-25 17:00", "2026-10-25 19:00"],
            "station_uid": [1, 1],
            "station_name": ["Station A", "Station A"],
            "aqi_tram": [30, 60],
        })
        tracked = pd.DataFrame({
            "station_uid": [1, 2],
            "station_name": ["Station A", "No data station"],
        })

        report = build_hourly_coverage_report(
            model,
            stations=stations,
            days=1,
            now=pd.Timestamp("2026-10-25 21:30"),
            tracked_stations=tracked,
        ).set_index("source")

        cams = report.loc["CAMS (mô hình)"]
        station = report.loc["WAQI · Station A"]
        self.assertEqual(cams["expected_hours"], 24)
        self.assertEqual(cams["observed_hours"], 3)
        self.assertEqual(cams["missing_hours"], 21)
        self.assertEqual(cams["gap_count"], 2)
        self.assertEqual(cams["longest_gap_hours"], 20)
        self.assertEqual(cams["latest_observation"], pd.Timestamp("2026-10-25 20:00"))
        self.assertEqual(station["observed_hours"], 2)
        self.assertEqual(station["gap_count"], 3)
        no_data = report.loc["WAQI · No data station"]
        self.assertEqual(no_data["observed_hours"], 0)
        self.assertEqual(no_data["coverage_pct"], 0)
        self.assertEqual(no_data["missing_hours"], 24)

    def test_hourly_coverage_validates_days_and_required_columns(self):
        with self.assertRaisesRegex(ValueError, "days"):
            build_hourly_coverage_report(
                pd.DataFrame(columns=["time", "aqi"]),
                days=0,
                now=pd.Timestamp("2026-10-25 21:00"),
            )
        with self.assertRaisesRegex(ValueError, "Thiếu cột"):
            build_hourly_coverage_report(
                pd.DataFrame(columns=["time"]),
                days=1,
                now=pd.Timestamp("2026-10-25 21:00"),
            )


class ModelValidationTests(unittest.TestCase):
    def test_temporal_split_excludes_targets_crossing_split_boundaries(self):
        samples = pd.DataFrame(
            {"y": range(24 * 3)},
            index=pd.date_range("2024-12-31", periods=24 * 3, freq="h"),
        )

        train, validation, test = split_temporal_samples(
            samples, "2025-01-01", "2025-01-02", horizon_hours=2
        )

        self.assertLess(train.index.max() + pd.Timedelta(hours=2),
                        pd.Timestamp("2025-01-01"))
        self.assertGreaterEqual(validation.index.min(), pd.Timestamp("2025-01-01"))
        self.assertLess(validation.index.max() + pd.Timedelta(hours=2),
                        pd.Timestamp("2025-01-02"))
        self.assertGreaterEqual(test.index.min(), pd.Timestamp("2025-01-02"))
        self.assertEqual(len(validation), 22)

    def test_final_fit_includes_validation_without_crossing_test_boundary(self):
        samples = pd.DataFrame(
            {"y": range(48)},
            index=pd.date_range("2025-12-30", periods=48, freq="h"),
        )

        fit = fit_model_matrix(samples, "2026-01-01", horizon_hours=2)

        self.assertLess(fit.index.max() + pd.Timedelta(hours=2),
                        pd.Timestamp("2026-01-01"))
        self.assertTrue((fit.index >= pd.Timestamp("2025-01-01")).any())


class ConfigurationTests(unittest.TestCase):
    def test_waqi_token_environment_variable_takes_precedence(self):
        with patch.dict(os.environ, {"WAQI_TOKEN": "environment-token"}):
            self.assertEqual(get_waqi_token(Path("missing-token-file")), "environment-token")

    def test_waqi_token_local_file_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "waqi_token.txt"
            token_file.write_text("local-token\n", encoding="utf-8")
            with patch.dict(os.environ, {"WAQI_TOKEN": ""}):
                self.assertEqual(get_waqi_token(token_file), "local-token")

    def test_missing_waqi_token_is_reported(self):
        with patch.dict(os.environ, {"WAQI_TOKEN": ""}):
            with self.assertRaisesRegex(RuntimeError, "Thiếu WAQI_TOKEN"):
                get_waqi_token(Path("missing-token-file"))

    def test_missing_database_url_is_reported(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            with self.assertRaisesRegex(RuntimeError, "Thiếu DATABASE_URL"):
                get_database_url()

    def test_openaq_api_key_is_read_from_environment(self):
        with patch.dict(os.environ, {"OPENAQ_API_KEY": "private-api-key"}):
            self.assertEqual(get_openaq_api_key(), "private-api-key")

    def test_missing_openaq_api_key_is_reported(self):
        with patch.dict(os.environ, {"OPENAQ_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "Thiếu OPENAQ_API_KEY"):
                get_openaq_api_key()

    def test_waqi_request_errors_do_not_expose_query_tokens(self):
        import requests

        response = requests.Response()
        response.status_code = 403
        request_error = requests.HTTPError(
            "Request failed: https://example.invalid/?token=private-token",
            response=response,
        )
        with patch("aqi_hanoi.core.waqi_client.requests.get", side_effect=request_error):
            with self.assertRaisesRegex(RuntimeError, "WAQI API request failed") as error:
                fetch_json("https://example.invalid/", params={"token": "private-token"})

        self.assertNotIn("private-token", str(error.exception))


class HttpRetryTests(unittest.TestCase):
    def test_temporary_server_error_is_retried_then_succeeds(self):
        class MockResponse:
            def __init__(self, status):
                self.status_code = status

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests
                    response = requests.Response()
                    response.status_code = self.status_code
                    raise requests.HTTPError(response=response)

        responses = [MockResponse(503), MockResponse(200)]
        with patch("aqi_hanoi.core.http_utils.requests.get", side_effect=responses) as get, patch(
            "aqi_hanoi.core.http_utils.time.sleep"
        ) as sleep:
            result = get_with_retry(
                "https://example.invalid", timeout=1, backoff_seconds=0.2
            )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(0.2)

    def test_non_retryable_http_error_fails_without_retry(self):
        import requests

        response = requests.Response()
        response.status_code = 401
        error = requests.HTTPError(response=response)
        with patch("aqi_hanoi.core.http_utils.requests.get", side_effect=error) as get, patch(
            "aqi_hanoi.core.http_utils.time.sleep"
        ) as sleep:
            with self.assertRaises(requests.HTTPError):
                get_with_retry("https://example.invalid", attempts=4)

        get.assert_called_once()
        sleep.assert_not_called()

    def test_retry_attempts_are_bounded(self):
        import requests

        with patch(
            "aqi_hanoi.core.http_utils.requests.get",
            side_effect=requests.Timeout("temporary"),
        ) as get, patch("aqi_hanoi.core.http_utils.time.sleep"):
            with self.assertRaises(requests.Timeout):
                get_with_retry(
                    "https://example.invalid", attempts=3, backoff_seconds=0
                )

        self.assertEqual(get.call_count, 3)


class ScheduledPipelineTests(unittest.TestCase):
    def test_successful_run_records_each_step_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "pipeline_status.json"
            executed = []
            with redirect_stdout(io.StringIO()):
                result = run_pipeline(
                    project_dir=directory,
                    status_path=status_path,
                    run_step=lambda step: executed.append(step) or 0,
                )
            status = pd.read_json(status_path, typ="series").to_dict()

        self.assertEqual(result, 0)
        self.assertEqual(executed, STEPS)
        self.assertEqual(status["status"], "SUCCEEDED")
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(len(status["steps"]), len(STEPS))
        self.assertTrue(all(row["status"] == "SUCCEEDED" for row in status["steps"]))

    def test_failed_step_is_recorded_and_stops_later_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "pipeline_status.json"
            executed = []

            def run_step(step):
                executed.append(step)
                return 23 if step == STEPS[1] else 0

            with redirect_stdout(io.StringIO()):
                result = run_pipeline(
                    project_dir=directory,
                    status_path=status_path,
                    run_step=run_step,
                )
            status = pd.read_json(status_path, typ="series").to_dict()

        self.assertEqual(result, 23)
        self.assertEqual(executed, STEPS[:2])
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(status["failed_step"], STEPS[1])
        self.assertEqual(status["exit_code"], 23)
        self.assertEqual(status["steps"][-1]["status"], "FAILED")

    def test_failure_preserves_timestamp_of_last_successful_run(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "pipeline_status.json"
            with redirect_stdout(io.StringIO()):
                run_pipeline(
                    project_dir=directory,
                    status_path=status_path,
                    run_step=lambda step: 0,
                )
            previous = pd.read_json(status_path, typ="series").to_dict()

            with redirect_stdout(io.StringIO()):
                result = run_pipeline(
                    project_dir=directory,
                    status_path=status_path,
                    run_step=lambda step: 1,
                )
            failed = pd.read_json(status_path, typ="series").to_dict()

        self.assertEqual(result, 1)
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(
            failed["last_success_at"], previous["finished_at"]
        )


class StationComparisonTests(unittest.TestCase):
    def test_duplicate_station_hours_are_averaged(self):
        data = pd.DataFrame({
            "time": ["2026-10-01 00:00:00", "2026-10-01 00:00:00"],
            "station_uid": [1, 1],
            "station_name": ["Station", "Station"],
            "aqi_tram": [20, 40],
        })

        aggregated = aggregate_station_hours(data)

        self.assertEqual(len(aggregated), 1)
        self.assertEqual(aggregated.iloc[0].aqi_tram, 30)

    def test_correlation_requires_24_distinct_hours(self):
        times = pd.date_range("2026-10-01", periods=23, freq="h")
        data = pd.DataFrame({
            "time": times,
            "aqi_tram": range(23),
            "aqi_cams": range(23),
        })

        self.assertTrue(pd.isna(calculate_stats(data)["tuong_quan"]))

        data.loc[len(data)] = [pd.Timestamp("2026-10-01 23:00:00"), 23, 23]
        self.assertAlmostEqual(calculate_stats(data)["tuong_quan"], 1)


class WarehouseTests(unittest.TestCase):
    def test_upsert_is_safe_to_rerun_and_updates_existing_rows(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE sample (key INTEGER PRIMARY KEY, value TEXT)"
                ))
                upsert_table(
                    connection, pd.DataFrame({"key": [1], "value": ["first"]}),
                    "sample", ["key"], ["value"],
                )
                upsert_table(
                    connection, pd.DataFrame({"key": [1], "value": ["updated"]}),
                    "sample", ["key"], ["value"],
                )
                rows = pd.read_sql("SELECT key, value FROM sample", connection)

            self.assertEqual(rows.to_dict("records"), [{"key": 1, "value": "updated"}])
        finally:
            engine.dispose()


class DataQualityTests(unittest.TestCase):
    def test_report_flags_stale_station_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            raw = project / "raw"
            raw.mkdir()
            pd.DataFrame({
                "collected_at": ["2026-10-25 17:00:00"],
                "obs_time": ["2026-10-25 10:00:00"],
                "age_min": [420],
                "station_uid": [123],
                "station_name": ["Hanoi test station"],
                "aqi": [40],
                "lat": [21.03],
                "lon": [105.86],
            }).to_csv(raw / "waqi_feed.csv", index=False)

            report = build_quality_report(project, now=pd.Timestamp("2026-10-25 18:00:00"))
            station = report.loc[report["entity"] == "station:123"].iloc[0]
            self.assertEqual(station["status"], "WARN")

    def test_report_accepts_fresh_station_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            raw = project / "raw"
            raw.mkdir()
            pd.DataFrame({
                "collected_at": ["2026-10-25 18:00:00"],
                "obs_time": ["2026-10-25 17:30:00"],
                "age_min": [30],
                "station_uid": [123],
                "station_name": ["Hanoi test station"],
                "aqi": [40],
                "lat": [21.03],
                "lon": [105.86],
            }).to_csv(raw / "waqi_feed.csv", index=False)

            report = build_quality_report(project, now=pd.Timestamp("2026-10-25 18:00:00"))
            station = report.loc[report["entity"] == "station:123"].iloc[0]
            self.assertEqual(station["status"], "OK")

    def test_report_excludes_non_hanoi_waqi_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw"
            raw.mkdir()
            pd.DataFrame({
                "collected_at": ["2026-10-25 18:00:00"] * 2,
                "obs_time": ["2026-10-25 17:30:00"] * 2,
                "age_min": [30, 30],
                "station_uid": [123, 5506],
                "station_name": [
                    "Hanoi test station",
                    "Viet Tri, Vietnam (Tp Việt Trì)",
                ],
                "aqi": [40, 50],
                "lat": [21.03, 21.33847],
                "lon": [105.86, 105.3673],
            }).to_csv(raw / "waqi_feed.csv", index=False)

            report = build_quality_report(
                directory, now=pd.Timestamp("2026-10-25 18:00:00")
            )

        station_rows = report.loc[
            report["source"] == "raw/waqi_feed.csv"
        ]
        self.assertEqual(
            station_rows.loc[
                station_rows["entity"] == "all", "stations"
            ].iloc[0],
            1,
        )
        self.assertNotIn("station:5506", station_rows["entity"].tolist())

    def test_report_tracks_fresh_optional_multisource_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw"
            raw.mkdir()
            pd.DataFrame({
                "source": ["Sensor.Community"],
                "location_id": [99385],
                "parameter": ["pm25"],
                "value": [37.0],
                "unit": ["µg/m³"],
                "observed_at": ["2026-10-25 17:30:00"],
            }).to_csv(raw / "sensorcommunity_pm25.csv", index=False)

            report = build_quality_report(
                directory, now=pd.Timestamp("2026-10-25 18:00:00")
            )
            source = report.loc[
                report["source"] == "raw/sensorcommunity_pm25.csv"
            ].iloc[0]

        self.assertEqual(source["status"], "OK")
        self.assertEqual(source["stations"], 1)

    def test_report_tracks_fresh_noaa_weather_station_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw"
            raw.mkdir()
            pd.DataFrame({
                "station_id": ["VVNB"],
                "observed_at": ["2026-10-25 17:30:00"],
                "temperature_c": [28],
            }).to_csv(raw / "noaa_vvnb_metar.csv", index=False)

            report = build_quality_report(
                directory, now=pd.Timestamp("2026-10-25 18:00:00")
            )
            source = report.loc[
                report["source"] == "raw/noaa_vvnb_metar.csv"
            ].iloc[0]

        self.assertEqual(source["status"], "OK")
        self.assertEqual(source["stations"], 1)


class StationCoverageTests(unittest.TestCase):
    def test_station_summary_distinguishes_fresh_stale_and_missing_aqi(self):
        feed = pd.DataFrame({
            "station_uid": [1, 2, 3],
            "station_name": ["Fresh", "Stale", "Missing"],
            "collected_at": [
                "2026-10-25 19:30:00",
                "2026-10-25 19:30:00",
                "2026-10-25 19:30:00",
            ],
            "obs_time": [
                "2026-10-25 18:30:00",
                "2026-10-25 14:00:00",
                None,
            ],
            "age_min": [30, 60, None],
            "aqi": [45, 80, None],
        })

        summary = build_station_coverage(
            feed, now=pd.Timestamp("2026-10-25 19:30:00")
        ).set_index("station_uid")

        self.assertEqual(summary.loc["1", "status"], "FRESH")
        self.assertEqual(summary.loc["2", "status"], "STALE")
        self.assertEqual(summary.loc["3", "status"], "NO_USABLE_AQI")
        self.assertEqual(summary.loc["1", "latest_aqi"], 45)
        self.assertEqual(summary.loc["2", "latest_aqi"], 80)
        self.assertTrue(pd.isna(summary.loc["3", "latest_aqi"]))

    def test_overlap_counts_distinct_common_hours(self):
        model_times = pd.to_datetime([
            "2026-10-25 15:00",
            "2026-10-25 16:00",
            "2026-10-25 17:00",
        ])
        station_times = pd.to_datetime([
            "2026-10-25 15:15",
            "2026-10-25 15:45",
            "2026-10-25 17:00",
            "2026-10-25 18:00",
        ])

        self.assertEqual(count_overlap_hours(model_times, station_times), 2)


    def test_hourly_comparison_averages_available_stations_per_common_hour(self):
        model = pd.DataFrame({
            "time": ["2026-10-25 15:00", "2026-10-25 16:00"],
            "aqi": [40, 50],
        })
        stations = pd.DataFrame({
            "time": [
                "2026-10-25 15:00",
                "2026-10-25 15:30",
                "2026-10-25 16:00",
                "2026-10-25 17:00",
            ],
            "station_uid": [1, 2, 1, 1],
            "aqi_tram": [30, 50, 60, 70],
        })

        comparison = build_hourly_comparison(model, stations)

        self.assertEqual(len(comparison), 2)
        self.assertEqual(comparison.loc[0, "aqi_waqi_mean"], 40)
        self.assertEqual(comparison.loc[0, "station_count"], 2)
        self.assertEqual(comparison.loc[1, "aqi_waqi_mean"], 60)
        self.assertEqual(comparison.loc[1, "station_count"], 1)

    def test_station_map_uses_latest_valid_coordinates_and_status(self):
        coverage = pd.DataFrame({
            "station_uid": ["101", "202"],
            "station_name": ["Fresh station", "Station without coordinates"],
            "latest_observation": ["2026-10-25 19:00", None],
            "latest_aqi": [45, None],
            "status": ["FRESH", "NO_USABLE_AQI"],
        })
        feed = pd.DataFrame({
            "station_uid": [101, 101, 202, 303, 404],
            "collected_at": [
                "2026-10-25 18:00",
                "2026-10-25 19:00",
                "2026-10-25 19:00",
                "2026-10-25 19:00",
                "2026-10-25 19:00",
            ],
            "lat": [21.0, 21.1, None, 21.2, 95.0],
            "lon": [105.0, 105.1, 105.2, 105.3, 105.4],
        })

        mapped = build_station_map_data(coverage, feed)

        self.assertEqual(mapped["station_uid"].tolist(), ["101"])
        self.assertEqual(mapped.loc[0, "lat"], 21.1)
        self.assertEqual(mapped.loc[0, "lon"], 105.1)
        self.assertEqual(mapped.loc[0, "map_color"], "#2E8B57")

    def test_station_history_filters_station_window_and_invalid_values(self):
        stations = pd.DataFrame({
            "time": [
                "2026-10-20 19:00",
                "2026-10-25 18:00",
                "2026-10-25 18:30",
                "2026-10-25 19:00",
                "2026-10-25 20:00",
            ],
            "station_uid": [1, 1, 1, 2, 1],
            "station_name": ["A", "A", "A", "B", "A"],
            "aqi_tram": [20, 40, 60, 100, 501],
            "pm25_aqi": [10, 30, 50, 90, 600],
            "pm10_aqi": [5, 20, 40, 80, 700],
        })

        history = build_station_history(
            stations,
            station_uid="1",
            days=1,
            now=pd.Timestamp("2026-10-25 20:00"),
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history.loc[0, "aqi_tram"], 50)
        self.assertEqual(history.loc[0, "pm25_aqi"], 40)
        self.assertEqual(history.loc[0, "pm10_aqi"], 30)

    def test_station_history_rejects_invalid_window(self):
        with self.assertRaisesRegex(ValueError, "days"):
            build_station_history(
                pd.DataFrame(columns=["time", "station_uid", "aqi_tram"]),
                station_uid="1",
                days=0,
                now=pd.Timestamp("2026-10-25 20:00"),
            )

    def test_collection_summary_classifies_attempts_and_limits_window(self):
        feed = pd.DataFrame({
            "station_uid": [1, 1, 1, 2, 3],
            "station_name": ["A", "A", "A", "B", "C"],
            "collected_at": [
                "2026-10-25 19:00",
                "2026-10-25 19:30",
                "2026-10-24 18:00",
                "2026-10-25 19:30",
                "2026-10-24 18:00",
            ],
            "obs_time": [
                "2026-10-25 18:30",
                "2026-10-25 14:00",
                "2026-10-24 17:00",
                "2026-10-25 19:00",
                "2026-10-24 17:00",
            ],
            "age_min": [30, 330, 60, 30, 60],
            "aqi": [45, 80, 60, None, 50],
        })

        summary = summarize_station_collection(
            feed,
            now=pd.Timestamp("2026-10-25 20:00"),
        ).set_index("station_uid")

        self.assertEqual(summary.loc["1", "attempts"], 2)
        self.assertEqual(summary.loc["1", "usable_count"], 1)
        self.assertEqual(summary.loc["1", "usable_pct"], 50.0)
        self.assertEqual(summary.loc["1", "latest_result"], "STALE_OBSERVATION")
        self.assertEqual(summary.loc["2", "latest_result"], "NO_AQI")
        self.assertEqual(summary.loc["3", "attempts"], 0)
        self.assertEqual(summary.loc["3", "latest_result"], "NO_RECENT_COLLECTION")

    def test_collection_summary_validates_window_and_required_columns(self):
        with self.assertRaisesRegex(ValueError, "window_hours"):
            summarize_station_collection(
                pd.DataFrame(columns=[
                    "station_uid", "station_name", "collected_at",
                    "obs_time", "age_min", "aqi",
                ]),
                now=pd.Timestamp("2026-10-25 20:00"),
                window_hours=0,
            )
        with self.assertRaisesRegex(ValueError, "Thiếu cột"):
            summarize_station_collection(
                pd.DataFrame(columns=["station_uid"]),
                now=pd.Timestamp("2026-10-25 20:00"),
            )

    def test_station_comparison_preserves_missing_hours_and_averages_duplicates(self):
        stations = pd.DataFrame({
            "time": [
                "2026-10-25 18:00",
                "2026-10-25 18:30",
                "2026-10-25 19:00",
                "2026-10-25 19:00",
                "2026-10-25 20:00",
            ],
            "station_uid": [1, 1, 1, 2, 2],
            "station_name": ["A", "A", "A", "B", "B"],
            "aqi_tram": [40, 60, 80, 100, 501],
        })

        comparison = build_station_comparison_history(
            stations,
            ["1", "2"],
            days=1,
            now=pd.Timestamp("2026-10-25 20:00"),
        )

        self.assertEqual(len(comparison), 2)
        self.assertEqual(comparison.iloc[0, 0], 50)
        self.assertTrue(pd.isna(comparison.iloc[0, 1]))
        self.assertEqual(comparison.iloc[1, 0], 80)
        self.assertEqual(comparison.iloc[1, 1], 100)

    def test_station_comparison_rejects_invalid_window(self):
        with self.assertRaisesRegex(ValueError, "days"):
            build_station_comparison_history(
                pd.DataFrame(columns=["time", "station_uid", "aqi_tram"]),
                ["1", "2"],
                days=0,
                now=pd.Timestamp("2026-10-25 20:00"),
            )

    def test_daily_station_summary_retains_zero_coverage_and_counts_hours(self):
        stations = pd.DataFrame({
            "time": [
                "2026-10-25 18:00",
                "2026-10-25 18:30",
                "2026-10-25 19:00",
                "2026-10-25 20:00",
            ],
            "station_uid": [1, 1, 1, 1],
            "station_name": ["A", "A", "A", "A"],
            "aqi_tram": [40, 60, 151, 180],
        })
        tracked = pd.DataFrame({
            "station_uid": [1, 2],
            "station_name": ["A", "No data"],
        })

        summary = build_daily_station_summary(
            stations,
            days=1,
            now=pd.Timestamp("2026-10-25 20:30"),
            tracked_stations=tracked,
        )

        current_day = summary.loc[
            summary["date"] == pd.Timestamp("2026-10-25")
        ].set_index("station_uid")
        self.assertEqual(len(current_day), 2)
        self.assertEqual(current_day.loc["1", "hours_available"], 2)
        self.assertEqual(current_day.loc["1", "aqi_mean"], 100.5)
        self.assertEqual(current_day.loc["1", "aqi_max"], 151)
        self.assertEqual(current_day.loc["1", "hours_above_150"], 1)
        self.assertEqual(current_day.loc["1", "expected_hours"], 20)
        self.assertEqual(current_day.loc["2", "hours_available"], 0)
        self.assertTrue(pd.isna(current_day.loc["2", "aqi_mean"]))

    def test_daily_station_summary_validates_required_columns_and_window(self):
        empty = pd.DataFrame(columns=["time", "station_uid", "aqi_tram"])
        with self.assertRaisesRegex(ValueError, "days"):
            build_daily_station_summary(
                empty, days=0, now=pd.Timestamp("2026-10-25 20:00")
            )
        with self.assertRaisesRegex(ValueError, "Thiếu cột"):
            build_daily_station_summary(
                pd.DataFrame(columns=["time"]),
                days=1,
                now=pd.Timestamp("2026-10-25 20:00"),
            )


class WaqiStationSelectionTests(unittest.TestCase):
    def test_station_selection_prefers_nearest_stations_with_valid_aqi(self):
        stations = [
            {
                "uid": 10, "lat": 21.03, "lon": 105.86, "aqi": "45",
                "station": {"name": "Hanoi monitor"},
            },
            {
                "uid": 11, "lat": 21.20, "lon": 105.95, "aqi": 52,
                "station": {"name": "Hanoi north"},
            },
            {
                "uid": 12, "lat": 21.02, "lon": 105.85, "aqi": "120",
                "station": {"name": "Hà Nội center"},
            },
            {
                "uid": 13, "lat": 22.5, "lon": 106.0, "aqi": 40,
                "station": {"name": "Hanoi"},
            },
            {
                "uid": 14, "lat": 21.04, "lon": 105.86, "aqi": "-",
                "station": {"name": "Hanoi"},
            },
        ]

        selected = select_station_uids(
            stations, max_stations=3
        )

        self.assertEqual(selected, [10, 12, 11])

    def test_station_selection_validates_limits(self):
        with self.assertRaises(ValueError):
            select_station_uids([], max_stations=0)

    def test_station_selection_excludes_stations_outside_hanoi_bounds(self):
        stations = [
            {
                "uid": 1, "lat": 21.03, "lon": 105.86, "aqi": 40,
                "station": {"name": "Hanoi"},
            },
            {
                "uid": 2, "lat": 21.03, "lon": 106.10, "aqi": 45,
                "station": {"name": "Hanoi"},
            },
            {
                "uid": 3, "lat": 22.0, "lon": 105.86, "aqi": 50,
                "station": {"name": "Hanoi"},
            },
        ]

        self.assertEqual(select_station_uids(stations), [1])

    def test_waqi_name_filter_excludes_viet_tri_inside_coordinate_box(self):
        data = pd.DataFrame({
            "station_name": [
                "Hanoi, Vietnam (Hà Nội)",
                "Viet Tri, Vietnam (Tp Việt Trì)",
            ],
            "lat": [21.0491, 21.33847],
            "lon": [105.8831, 105.3673],
        })

        filtered = filter_hanoi_waqi_data(
            data, "lat", "lon", "station_name"
        )

        self.assertEqual(filtered["station_name"].tolist(), [
            "Hanoi, Vietnam (Hà Nội)",
        ])

    def test_coordinate_filter_drops_missing_and_out_of_area_rows(self):
        data = pd.DataFrame({
            "lat": ["21.03", "21.03", None],
            "lon": ["105.86", "106.10", "105.86"],
            "value": [1, 2, 3],
        })

        filtered = filter_hanoi_coordinates(data, "lat", "lon")

        self.assertEqual(filtered["value"].tolist(), [1])

    def test_station_selection_caps_results_at_twenty(self):
        stations = [
            {
                "uid": uid,
                "lat": 21.0285 + uid / 1000,
                "lon": 105.8542,
                "aqi": 55,
                "station": {"name": "Hanoi monitor"},
            }
            for uid in range(30)
        ]

        selected = select_station_uids(stations, max_stations=20)

        self.assertEqual(len(selected), 20)
        self.assertEqual(selected, list(range(20)))

    def test_latest_batch_excludes_stations_without_current_usable_aqi(self):
        feed = pd.DataFrame({
            "collected_at": [
                "2026-10-25 19:00:00",
                "2026-10-25 20:00:00",
                "2026-10-25 20:00:00",
                "2026-10-25 20:00:00",
            ],
            "station_uid": [1, 1, 2, 3],
            "aqi": [40, 45, None, 60],
            "age_min": [30, 45, None, 240],
            "obs_time": [
                "2026-10-25 18:30:00",
                "2026-10-25 19:15:00",
                None,
                "2026-10-25 16:00:00",
            ],
        })

        active = latest_station_collection_batch(feed)

        self.assertEqual(active["station_uid"].tolist(), [1])
        self.assertEqual(active["aqi"].tolist(), [45])

    def test_fresh_aqi_requires_valid_value_recent_age_and_observation_time(self):
        base = {"aqi": 80, "age_min": 45, "obs_time": "2026-10-25 20:00"}
        self.assertTrue(has_fresh_aqi(base))
        self.assertFalse(has_fresh_aqi({**base, "aqi": None}))
        self.assertFalse(has_fresh_aqi({**base, "age_min": 181}))
        self.assertFalse(has_fresh_aqi({**base, "obs_time": None}))


class OpenAqCollectionTests(unittest.TestCase):
    def test_location_selection_keeps_nearby_station_with_pm_sensors(self):
        locations = [
            {
                "id": 2,
                "coordinates": {"latitude": 21.03, "longitude": 105.86},
                "sensors": [{"parameter": {"name": "pm25"}}],
            },
            {
                "id": 1,
                "coordinates": {"latitude": 21.01, "longitude": 105.84},
                "sensors": [{"parameter": {"name": "pm10"}}],
            },
            {
                "id": 3,
                "coordinates": {"latitude": 21.02, "longitude": 105.85},
                "sensors": [{"parameter": {"name": "no2"}}],
            },
            {
                "id": 4,
                "coordinates": {"latitude": 24.0, "longitude": 105.0},
                "sensors": [{"parameter": {"name": "pm25"}}],
            },
            {
                "id": 5,
                "coordinates": {"latitude": 21.02, "longitude": 105.85},
                "isMobile": True,
                "sensors": [{"parameter": {"name": "pm25"}}],
            },
            {
                "id": 6,
                "locality": "Bac Ninh",
                "coordinates": {"latitude": 21.02, "longitude": 105.85},
                "sensors": [{"parameter": {"name": "pm25"}}],
            },
            {
                "id": 7,
                "locality": "Hanoi",
                "coordinates": {"latitude": 21.02, "longitude": 106.1},
                "sensors": [{"parameter": {"name": "pm25"}}],
            },
        ]

        selected = select_locations(locations)

        self.assertEqual([location["id"] for location in selected], [2, 1])

    def test_latest_measurement_parser_keeps_fresh_pm_values_separate(self):
        location = {
            "id": 10,
            "name": "Hanoi Monitor",
            "provider": {"name": "Public provider"},
            "licenses": [{"name": "CC BY 4.0"}],
            "coordinates": {"latitude": 21.02, "longitude": 105.85},
            "sensors": [
                {
                    "id": 101,
                    "parameter": {"name": "pm25", "units": "µg/m³"},
                },
                {
                    "id": 102,
                    "parameter": {"name": "pm10", "units": "µg/m³"},
                },
                {
                    "id": 103,
                    "parameter": {"name": "no2", "units": "µg/m³"},
                },
            ],
        }
        payload = {
            "results": [
                {
                    "sensorsId": 101,
                    "value": 35.5,
                    "datetime": {"utc": "2026-10-25T13:00:00Z"},
                },
                {
                    "sensorsId": 102,
                    "value": 44,
                    "datetime": {"utc": "2026-10-25T10:00:00Z"},
                },
                {
                    "sensorsId": 103,
                    "value": 12,
                    "datetime": {"utc": "2026-10-25T13:00:00Z"},
                },
            ],
        }

        rows = parse_location_latest(
            location,
            payload,
            pd.Timestamp("2026-10-25 21:00:00"),
        )

        measurements = {row["parameter"]: row for row in rows}
        self.assertEqual(set(measurements), {"pm25", "pm10"})
        self.assertEqual(measurements["pm25"]["value"], 35.5)
        self.assertEqual(measurements["pm25"]["unit"], "µg/m³")
        self.assertEqual(measurements["pm25"]["age_hours"], 1)
        self.assertEqual(measurements["pm10"]["age_hours"], 4)
        self.assertEqual(measurements["pm25"]["license_names"], "CC BY 4.0")


class SourceComparisonTests(unittest.TestCase):
    def test_comparison_joins_same_hour_pm25_in_matching_units_only(self):
        model = pd.DataFrame({
            "time": [
                "2026-10-25 10:00",
                "2026-10-25 11:00",
                "2026-10-25 12:00",
            ],
            "pm25": [20, 30, 40],
        })
        measurements = pd.DataFrame({
            "location_id": [1, 1, 1, 1, 1],
            "location_name": ["Hanoi station"] * 5,
            "parameter": ["pm25", "pm25", "pm10", "pm25", "pm25"],
            "value": [10, 20, 40, 50, 60],
            "unit": ["µg/m³", "ug/m3", "µg/m³", "ppm", "µg/m³"],
            "observed_at": [
                "2026-10-25 10:15",
                "2026-10-25 10:45",
                "2026-10-25 11:00",
                "2026-10-25 11:00",
                "2026-10-25 13:00",
            ],
        })

        comparison = build_model_pm25_comparison(model, measurements)
        summary = summarize_pm25_model_agreement(comparison)

        self.assertEqual(len(comparison), 1)
        self.assertEqual(
            comparison.iloc[0]["time"], pd.Timestamp("2026-10-25 10:00")
        )
        self.assertEqual(comparison.iloc[0]["openaq_pm25"], 15)
        self.assertEqual(comparison.iloc[0]["cams_minus_openaq"], 5)
        self.assertEqual(summary.iloc[0]["overlap_hours"], 1)

    def test_comparison_rejects_missing_required_columns(self):
        with self.assertRaisesRegex(ValueError, "Thiếu cột"):
            build_model_pm25_comparison(
                pd.DataFrame(columns=["time"]),
                pd.DataFrame(),
            )


class CamsStationComparisonTests(unittest.TestCase):
    def test_insufficient_hour_overlap_skips_stats_without_failing_pipeline(self):
        model = pd.DataFrame(
            {"aqi_cams": [40, 50, 60]},
            index=pd.Index(
                pd.to_datetime([
                    "2026-10-25 16:00",
                    "2026-10-25 17:00",
                    "2026-10-25 18:00",
                ]),
                name="time",
            ),
        )
        stations = pd.DataFrame({
            "time": pd.to_datetime([
                "2026-10-25 16:00",
                "2026-10-25 17:00",
                "2026-10-25 18:00",
            ]),
            "station_uid": [1, 1, 1],
            "station_name": ["Hanoi station"] * 3,
            "aqi_tram": [45, 55, 65],
        })

        with tempfile.TemporaryDirectory() as directory, patch(
            "aqi_hanoi.analysis.compare_stations.load_inputs",
            return_value=(model, stations),
        ), redirect_stdout(io.StringIO()):
            result = compare_stations_main(Path(directory))

        self.assertFalse(result)
        self.assertFalse(
            (Path(directory) / "figures" / "aqi_hanoi.analysis.compare_stations.csv").exists()
        )


class AdditionalSourceCollectionTests(unittest.TestCase):
    def test_optional_collectors_continue_after_failure_and_record_status(self):
        with tempfile.TemporaryDirectory() as directory:
            executed = []

            def run_collector(script):
                executed.append(script)
                return 1 if script == COLLECTORS[0][0] else 0

            with patch.dict(os.environ, {"OPENAQ_API_KEY": ""}):
                status = collect_optional_sources(
                    directory, run_collector=run_collector
                )

            saved = json.loads(
                (
                    Path(directory)
                    / "figures"
                    / "source_collection_status.json"
                ).read_text(encoding="utf-8")
            )
            log_path = Path(directory) / "raw" / "source_collection.log"
            log_contents = log_path.read_text(encoding="utf-8")

        self.assertEqual(executed, [item[0] for item in COLLECTORS[:3]])
        self.assertEqual(
            [item["status"] for item in status["sources"]],
            ["FAILED", "SUCCEEDED", "SUCCEEDED", "SKIPPED"],
        )
        self.assertEqual(saved["sources"][0]["status"], "FAILED")
        self.assertIn("Hanoi monitoring portal", log_contents)

    def test_hanoi_portal_parser_filters_inactive_and_out_of_area_sites(self):
        sites = parse_sites([
            {
                "id": 14,
                "name": "Chi cục môi trường",
                "status": 1,
                "latitude": 21.0152,
                "longtitude": 105.7999,
            },
            {
                "id": 15,
                "name": "Inactive",
                "status": 0,
                "latitude": 21.05,
                "longtitude": 105.74,
            },
            {
                "id": 16,
                "name": "Outside Hanoi bounds",
                "status": 1,
                "latitude": 21.05,
                "longtitude": 106.2,
            },
        ])

        self.assertEqual([site["id"] for site in sites], [14])

    def test_hanoi_portal_parser_keeps_valid_pm25_hourly_records(self):
        site = {
            "id": 14,
            "name": "Chi cục môi trường",
            "latitude": 21.0152,
            "longitude": 105.7999,
        }
        rows = parse_pm25_history(
            site,
            {"PM2.5": [
                {"time": "2026-10-25 20:00", "value": "25.4"},
                {"time": "invalid", "value": "20"},
                {"time": "2026-10-25 21:00", "value": "-1"},
            ]},
            pd.Timestamp("2026-10-25 22:00"),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parameter"], "pm25")
        self.assertEqual(rows[0]["value"], 25.4)
        self.assertEqual(rows[0]["unit"], "µg/m³")

    def test_sensorcommunity_parser_converts_utc_and_filters_indoor_sensors(self):
        collected_at = pd.Timestamp("2026-10-25 22:00")
        payload = [
            {
                "sensor": {
                    "id": 99385,
                    "sensor_type": {"name": "SDS011"},
                },
                "timestamp": "2026-10-25 15:22:59",
                "location": {
                    "latitude": "21.07",
                    "longitude": "105.824",
                    "country": "VN",
                    "indoor": 0,
                },
                "sensordatavalues": [
                    {"value_type": "P2", "value": "37.0"},
                    {"value_type": "P1", "value": "44.9"},
                ],
            },
            {
                "sensor": {
                    "id": 22607,
                    "sensor_type": {"name": "DHT22"},
                },
                "timestamp": "2026-10-25 15:22:59",
                "location": {
                    "latitude": "20.968",
                    "longitude": "105.834",
                    "country": "VN",
                    "indoor": 1,
                },
                "sensordatavalues": [
                    {"value_type": "P2", "value": "12.0"},
                ],
            },
        ]

        rows = parse_pm25_measurements(payload, collected_at)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 37)
        self.assertEqual(
            rows[0]["observed_at"], pd.Timestamp("2026-10-25 22:22:59")
        )

    def test_source_history_deduplicates_recollected_station_hours(self):
        row = {
            "collected_at": "2026-10-25 22:00",
            "source": "Sensor.Community",
            "location_id": 123,
            "location_name": "Sensor 123",
            "provider": "Sensor.Community",
            "latitude": 21.07,
            "longitude": 105.824,
            "parameter": "pm25",
            "value": 37,
            "unit": "µg/m³",
            "observed_at": "2026-10-25 22:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.csv"

            first = save_measurement_history(path, [row])
            updated = save_measurement_history(
                path, [{**row, "collected_at": "2026-10-25 22:05", "value": 38}]
            )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated.iloc[0]["value"], 38)


class AqiGuidanceTests(unittest.TestCase):
    def test_aqi_band_boundaries_match_project_categories(self):
        expectations = {
            0: "Tốt",
            50: "Tốt",
            51: "Trung bình",
            100: "Trung bình",
            101: "Kém",
            150: "Kém",
            151: "Xấu",
            200: "Xấu",
            201: "Rất xấu",
            300: "Rất xấu",
            301: "Nguy hại",
            500: "Nguy hại",
        }

        for aqi, expected_level in expectations.items():
            with self.subTest(aqi=aqi):
                result = interpret_aqi(aqi)
                self.assertEqual(result["level"], expected_level)
                self.assertTrue(result["advice"])

    def test_aqi_guidance_handles_missing_and_rejects_out_of_range_values(self):
        self.assertIsNone(interpret_aqi(None))
        with self.assertRaises(ValueError):
            interpret_aqi(-1)
        with self.assertRaises(ValueError):
            interpret_aqi(501)


class NotificationTests(unittest.TestCase):
    def test_alert_sends_once_and_respects_six_hour_cooldown(self):
        alerts = pd.DataFrame({
            "status": ["ALERT"],
            "horizon_hours": [6],
            "probability_aqi_above_150": [0.82],
            "probability_threshold": [0.6],
            "source_age_hours": [0.5],
            "target_time": ["2026-10-26 02:00:00"],
        })
        notifications = []
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "notification_state.json"
            notifier = lambda title, message: notifications.append((title, message))

            first = notify_current_alerts(
                alerts,
                state_path,
                now=pd.Timestamp("2026-10-25 20:00:00"),
                notifier=notifier,
            )
            second = notify_current_alerts(
                alerts,
                state_path,
                now=pd.Timestamp("2026-10-25 21:00:00"),
                notifier=notifier,
            )

        self.assertEqual(first, "sent")
        self.assertEqual(second, "suppressed")
        self.assertEqual(len(notifications), 1)
        self.assertIn("82%", notifications[0][1])

    def test_notifications_require_alert_with_fresh_source_data(self):
        stale_or_normal = pd.DataFrame({
            "status": ["DATA_STALE", "NORMAL"],
            "horizon_hours": [6, 24],
            "probability_aqi_above_150": [None, 0.2],
            "probability_threshold": [None, 0.6],
            "source_age_hours": [120, 0.5],
            "target_time": [None, "2026-10-26 20:00:00"],
        })
        notifications = []
        with tempfile.TemporaryDirectory() as directory:
            result = notify_current_alerts(
                stale_or_normal,
                Path(directory) / "notification_state.json",
                now=pd.Timestamp("2026-10-25 20:00:00"),
                notifier=lambda title, message: notifications.append(message),
            )

        self.assertEqual(result, "no_alert")
        self.assertEqual(notifications, [])


class ForecastMonitoringTests(unittest.TestCase):
    def _create_monitoring_inputs(self, project):
        figures = project / "figures"
        clean = project / "clean"
        figures.mkdir()
        clean.mkdir()
        pd.DataFrame({
            "generated_at": ["2026-10-25T12:00:00"],
            "source_time": ["2026-10-25 12:00:00"],
            "target_time": ["2026-10-25 18:00:00"],
            "horizon_hours": [6],
            "status": ["ALERT"],
            "probability_aqi_above_150": [0.8],
            "probability_threshold": [0.6],
            "model": ["Random Forest"],
            "source_age_hours": [0.5],
        }).to_csv(figures / "local_alerts.csv", index=False)
        pd.DataFrame({
            "time": ["2026-10-25 12:00:00", "2026-10-25 18:00:00"],
            "aqi": [120, 140],
        }).to_csv(clean / "fact_model_hourly.csv", index=False)
        pd.DataFrame({
            "time": ["2026-10-25 18:00:00"],
            "station_uid": [123],
            "aqi_tram": [165],
        }).to_csv(clean / "station_feed_hourly.csv", index=False)

    def test_forecast_history_is_pending_then_resolved_by_target_time(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self._create_monitoring_inputs(project)

            pending = record_and_resolve_forecasts(
                project, now=pd.Timestamp("2026-10-25 17:59:00")
            )
            first_history = pd.read_csv(pending["path"])
            resolved = record_and_resolve_forecasts(
                project, now=pd.Timestamp("2026-10-25 18:05:00")
            )
            history = pd.read_csv(resolved["path"])

        self.assertEqual(pending["recorded"], 1)
        self.assertEqual(first_history.loc[0, "verification_status"], "PENDING")
        self.assertEqual(resolved["recorded"], 0)
        self.assertEqual(resolved["verified_model"], 1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history.loc[0, "verification_status"], "VERIFIED_MODEL")
        self.assertEqual(history.loc[0, "model_source_aqi"], 140)
        self.assertEqual(history.loc[0, "model_prediction_correct"], 0)
        self.assertEqual(history.loc[0, "station_mean_aqi"], 165)
        self.assertEqual(history.loc[0, "station_count"], 1)
        self.assertEqual(history.loc[0, "station_prediction_correct"], 1)

    def test_repeated_forecast_issue_does_not_overwrite_original_prediction(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self._create_monitoring_inputs(project)
            first = record_and_resolve_forecasts(
                project, now=pd.Timestamp("2026-10-25 17:00:00")
            )
            alerts_path = project / "figures" / "local_alerts.csv"
            alerts = pd.read_csv(alerts_path)
            alerts.loc[0, "generated_at"] = "2026-10-25T17:30:00"
            alerts.loc[0, "probability_aqi_above_150"] = 0.2
            alerts.loc[0, "status"] = "NORMAL"
            alerts.loc[0, "model"] = "Changed model"
            alerts.to_csv(alerts_path, index=False)

            repeated = record_and_resolve_forecasts(
                project, now=pd.Timestamp("2026-10-25 17:30:00")
            )
            history = pd.read_csv(repeated["path"])

        self.assertEqual(first["recorded"], 1)
        self.assertEqual(repeated["recorded"], 0)
        self.assertEqual(len(history), 1)
        self.assertEqual(history.loc[0, "generated_at"], "2026-10-25T12:00:00")
        self.assertEqual(history.loc[0, "probability_aqi_above_150"], 0.8)
        self.assertEqual(history.loc[0, "status"], "ALERT")
        self.assertEqual(history.loc[0, "model"], "Random Forest")


class LocalAlertTests(unittest.TestCase):
    def test_stale_features_block_local_forecasts(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            clean = project / "clean"
            clean.mkdir()
            times = pd.date_range("2026-10-20", periods=120, freq="h")
            model_data = pd.DataFrame({
                "time": times,
                "aqi": 40,
                "pm25": 10,
                "temp": 25,
                "humidity": 70,
                "precipitation": 0,
                "wind_speed": 2,
                "pressure": 1010,
            })
            model_data.loc[len(model_data)] = [
                pd.Timestamp("2026-10-25 23:00:00"), 40, 10, 25,
                70, 0, 2, 1010,
            ]
            model_data.to_csv(clean / "fact_model_hourly.csv", index=False)

            alerts = generate_alerts(
                project, now=pd.Timestamp("2026-10-25 18:00:00")
            )

            self.assertEqual(alerts["status"].tolist(), ["DATA_STALE", "DATA_STALE"])
            self.assertTrue(alerts["probability_aqi_above_150"].isna().all())
            self.assertTrue((alerts["source_age_hours"] > 6).all())
            self.assertTrue(
                (pd.to_datetime(alerts["source_time"])
                 <= pd.Timestamp("2026-10-25 18:00:00")).all()
            )


class WeatherCollectionTests(unittest.TestCase):
    def test_noaa_metar_parser_keeps_station_observations_and_units(self):
        rows = parse_metar_reports(
            [
                {
                    "icaoId": "VVNB",
                    "obsTime": 1792942200,
                    "temp": 28,
                    "dewp": 25,
                    "wdir": 130,
                    "wspd": 7,
                    "visib": "6+",
                    "altim": 1014,
                    "lat": 21.218,
                    "lon": 105.805,
                    "rawOb": "METAR VVNB 251530Z 13007KT CAVOK 28/25 Q1014",
                    "name": "Hanoi/Noi Bai Intl, VN",
                },
                {
                    "icaoId": "OTHER",
                    "obsTime": 1792942200,
                    "lat": 21.0,
                    "lon": 105.0,
                },
            ],
            pd.Timestamp("2026-10-25 22:40:00"),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["station_id"], "VVNB")
        self.assertEqual(rows[0]["temperature_c"], 28)
        self.assertEqual(rows[0]["wind_speed_kt"], 7)
        self.assertEqual(
            rows[0]["observed_at"], pd.Timestamp("2026-10-25 22:30:00")
        )

    def test_noaa_metar_history_deduplicates_same_station_report(self):
        base = {
            "collected_at": "2026-10-25 22:40",
            "source": "NOAA Aviation Weather",
            "station_id": "VVNB",
            "station_name": "Noi Bai",
            "latitude": 21.218,
            "longitude": 105.805,
            "observed_at": "2026-10-25 22:30",
            "temperature_c": 28,
            "dewpoint_c": 25,
            "wind_direction_deg": 130,
            "wind_speed_kt": 7,
            "visibility_sm": "6+",
            "pressure_hpa": 1014,
            "metar": "METAR VVNB",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metar.csv"
            save_metar_history([base], path)
            updated = save_metar_history(
                [{**base, "temperature_c": 29}], path
            )

        self.assertEqual(len(updated), 1)
        self.assertEqual(updated.iloc[0]["temperature_c"], 29)

    def test_recent_forecast_weather_is_merged_with_archive(self):
        class MockResponse:
            def __init__(self, hourly):
                self.hourly = hourly

            def raise_for_status(self):
                return None

            def json(self):
                return {"hourly": self.hourly}

        archive = {
            "time": ["2026-10-20 00:00"],
            "temperature_2m": [20],
        }
        forecast = {
            "time": ["2026-10-25 18:00"],
            "temperature_2m": [25],
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch("aqi_hanoi.collectors.collect_weather.PROJECT_DIR", Path(directory)), patch(
                "aqi_hanoi.core.http_utils.requests.get",
                side_effect=[MockResponse(archive), MockResponse(forecast)],
            ) as get:
                with redirect_stdout(io.StringIO()):
                    collect_weather()

            merged = pd.read_csv(
                Path(directory) / "raw" / "weather_hanoi.csv",
                parse_dates=["time"],
            )

        self.assertEqual(get.call_count, 2)
        self.assertIn("/v1/archive", get.call_args_list[0].args[0])
        self.assertIn("/v1/forecast", get.call_args_list[1].args[0])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged.iloc[-1]["temperature_2m"], 25)


    def test_weather_archive_refresh_starts_near_existing_latest_data(self):
        class MockResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"hourly": {
                    "time": ["2026-10-25 18:00"],
                    "temperature_2m": [25],
                }}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw" / "weather_hanoi.csv"
            output.parent.mkdir()
            pd.DataFrame({
                "time": ["2026-10-20 23:00"],
                "temperature_2m": [20],
            }).to_csv(output, index=False)
            with patch("aqi_hanoi.collectors.collect_weather.PROJECT_DIR", Path(directory)), patch(
                "aqi_hanoi.collectors.collect_weather.pd.Timestamp.now",
                return_value=pd.Timestamp("2026-10-25 18:00:00", tz="Asia/Bangkok"),
            ), patch(
                "aqi_hanoi.core.http_utils.requests.get",
                return_value=MockResponse(),
            ) as get:
                with redirect_stdout(io.StringIO()):
                    collect_weather()

            archive_params = get.call_args_list[0].kwargs["params"]
            merged = pd.read_csv(output, parse_dates=["time"])

        self.assertEqual(archive_params["start_date"], "2026-10-18")
        self.assertEqual(len(merged), 2)


class AirQualityCollectionTests(unittest.TestCase):
    def test_air_quality_archive_refresh_starts_near_existing_latest_data(self):
        class MockResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"hourly": {
                    "time": ["2026-10-25 18:00"],
                    "us_aqi": [42],
                    "pm2_5": [12],
                    "pm10": [16],
                }}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw" / "aq_model_hanoi.csv"
            output.parent.mkdir()
            pd.DataFrame({
                "time": ["2026-10-24 18:00"],
                "us_aqi": [40],
                "pm2_5": [10],
                "pm10": [14],
            }).to_csv(output, index=False)
            with patch("aqi_hanoi.collectors.collect_aq_model.PROJECT_DIR", Path(directory)), patch(
                "aqi_hanoi.collectors.collect_aq_model.pd.Timestamp.now",
                return_value=pd.Timestamp("2026-10-25 18:00:00", tz="Asia/Bangkok"),
            ), patch(
                "aqi_hanoi.core.http_utils.requests.get",
                return_value=MockResponse(),
            ) as get:
                with redirect_stdout(io.StringIO()):
                    collect_aq_model()

            params = get.call_args.kwargs["params"]
            merged = pd.read_csv(output, parse_dates=["time"])

        self.assertEqual(params["start_date"], "2026-10-22")
        self.assertEqual(len(merged), 2)


if __name__ == "__main__":
    unittest.main()
