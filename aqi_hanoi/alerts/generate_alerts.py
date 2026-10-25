from pathlib import Path
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score

from aqi_hanoi.analysis.alert_classify import (
    HORIZONS,
    PROBABILITY_THRESHOLDS,
    TEST_START,
    VALIDATION_START,
    build_features,
    build_samples,
    make_models,
)
from aqi_hanoi.analysis.model_validation import fit_model_matrix, split_temporal_samples
from aqi_hanoi.core.settings import PROJECT_DIR


MAX_DATA_AGE_HOURS = 6
OUTPUT_PATH = PROJECT_DIR / "figures" / "local_alerts.csv"


def _empty_alert(horizon, source_time, age_hours, status, detail):
    return {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "source_time": source_time,
        "target_time": pd.NaT,
        "horizon_hours": horizon,
        "status": status,
        "probability_aqi_above_150": np.nan,
        "probability_threshold": np.nan,
        "model": "",
        "source_age_hours": age_hours,
        "detail": detail,
    }


def generate_alerts(project_dir=PROJECT_DIR, now=None):
    project_dir = Path(project_dir)
    now = (
        pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
        if now is None else pd.Timestamp(now)
    )
    if now.tzinfo is not None:
        now = now.tz_convert("Asia/Bangkok").tz_localize(None)
    data_path = project_dir / "clean" / "fact_model_hourly.csv"
    data = (pd.read_csv(data_path, parse_dates=["time"])
            .set_index("time").sort_index())
    data = data[[
        "aqi", "pm25", "temp", "humidity", "precipitation",
        "wind_speed", "pressure",
    ]]
    data = data[~data.index.duplicated(keep="last")]
    data = data.loc[data.index <= now.floor("h")]
    if data.empty:
        raise ValueError("Không có dữ liệu trước thời điểm hiện tại để tạo cảnh báo.")
    data = data.reindex(pd.date_range(data.index.min(), data.index.max(), freq="h"))

    latest_features = {}
    for horizon in HORIZONS:
        features = build_features(data, horizon).dropna()
        if features.empty:
            raise ValueError(f"Không có đặc trưng đầy đủ cho dự báo {horizon} giờ.")
        latest_features[horizon] = features.iloc[-1]

    source_time = min(row.name for row in latest_features.values())
    age_hours = round((now - source_time).total_seconds() / 3600, 2)
    if age_hours < -24 or age_hours > MAX_DATA_AGE_HOURS:
        return pd.DataFrame([
            _empty_alert(
                horizon,
                source_time,
                age_hours,
                "DATA_STALE",
                f"Dữ liệu đầu vào cũ hơn {MAX_DATA_AGE_HOURS} giờ; "
                "không tạo cảnh báo dự báo.",
            )
            for horizon in HORIZONS
        ])

    rows = []
    for horizon in HORIZONS:
        samples = build_samples(data, horizon)
        train, validation, _ = split_temporal_samples(
            samples, VALIDATION_START, TEST_START, horizon
        )
        y_train = train["y"].astype(int)
        y_validation = validation["y"].astype(int)
        if y_train.nunique() < 2 or y_validation.nunique() < 2:
            raise ValueError(
                f"Không đủ cả hai lớp trong train/validation cho dự báo {horizon} giờ."
            )

        validation_results = {}
        for name, estimator in make_models().items():
            estimator.fit(train.drop(columns="y"), y_train)
            probabilities = estimator.predict_proba(
                validation.drop(columns="y")
            )[:, 1]
            threshold = max(
                PROBABILITY_THRESHOLDS,
                key=lambda candidate: f1_score(
                    y_validation,
                    (probabilities >= candidate).astype(int),
                    zero_division=0,
                ),
            )
            score = f1_score(
                y_validation,
                (probabilities >= threshold).astype(int),
                zero_division=0,
            )
            validation_results[name] = (score, threshold)

        model_name, (validation_f1, threshold) = max(
            validation_results.items(), key=lambda item: item[1][0]
        )
        final_samples = fit_model_matrix(samples, source_time, horizon)
        model = make_models()[model_name]
        model.fit(final_samples.drop(columns="y"), final_samples["y"].astype(int))
        probability = model.predict_proba(
            latest_features[horizon].to_frame().T
        )[0, 1]
        rows.append({
            "generated_at": now.isoformat(timespec="seconds"),
            "source_time": latest_features[horizon].name,
            "target_time": latest_features[horizon].name + pd.Timedelta(hours=horizon),
            "horizon_hours": horizon,
            "status": "ALERT" if probability >= threshold else "NORMAL",
            "probability_aqi_above_150": round(float(probability), 4),
            "probability_threshold": threshold,
            "model": model_name,
            "validation_f1": round(float(validation_f1), 4),
            "source_age_hours": age_hours,
            "detail": (
                "AQI > 150 risk exceeds validation threshold"
                if probability >= threshold
                else "AQI > 150 risk is below validation threshold"
            ),
        })
    return pd.DataFrame(rows)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    alerts = generate_alerts()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    alerts.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    print(alerts.to_string(index=False))
    print(f"\nĐã lưu cảnh báo cục bộ: {OUTPUT_PATH.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError) as error:
        print(f"Lỗi: {error}")
        raise SystemExit(1) from None
