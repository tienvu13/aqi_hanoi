import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from aqi_hanoi.analysis.model_validation import fit_model_matrix, split_temporal_samples
from aqi_hanoi.core.settings import PROJECT_DIR


TARGET = "pm25"
VALIDATION_START = "2025-01-01"
TEST_START = "2026-01-01"
HORIZONS = [1, 24]
FEATURE_COLUMNS = [
    "temp", "humidity", "precipitation", "wind_speed", "pressure",
]


def build_samples(data, horizon):
    features = pd.DataFrame(index=data.index)
    for lag in [0, 1, 2, 3, 6, 12, 24, 48]:
        features[f"lag{lag}"] = data[TARGET].shift(lag)
    features["roll6"] = data[TARGET].rolling(6).mean()
    features["roll24"] = data[TARGET].rolling(24).mean()
    for column in FEATURE_COLUMNS:
        features[column] = data[column]

    target_time = features.index + pd.Timedelta(hours=horizon)
    features["hour_sin"] = np.sin(2 * np.pi * target_time.hour / 24)
    features["hour_cos"] = np.cos(2 * np.pi * target_time.hour / 24)
    features["month"] = target_time.month
    features["dow"] = target_time.dayofweek
    target = data[TARGET].shift(-horizon).rename("y")
    return pd.concat([features, target], axis=1).dropna()


def make_models():
    return {
        "Ridge": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "RandomForest": RandomForestRegressor(
            n_estimators=200, min_samples_leaf=5,
            n_jobs=-1, random_state=42,
        ),
        "GradientBoosting": HistGradientBoostingRegressor(
            max_iter=300, learning_rate=0.05, random_state=42,
        ),
    }


def regression_metrics(actual, predicted):
    return {
        "mae": mean_absolute_error(actual, predicted),
        "rmse": np.sqrt(mean_squared_error(actual, predicted)),
        "r2": r2_score(actual, predicted),
    }


def main():
    figures_dir = PROJECT_DIR / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    data_path = PROJECT_DIR / "clean" / "fact_model_hourly.csv"
    data = (pd.read_csv(data_path, parse_dates=["time"])
            .set_index("time").sort_index())
    data = data[[TARGET, *FEATURE_COLUMNS]]
    data = data[~data.index.duplicated(keep="last")]
    data = data.reindex(pd.date_range(data.index.min(), data.index.max(), freq="h"))

    metric_rows = []
    prediction_rows = []
    for horizon in HORIZONS:
        samples = build_samples(data, horizon)
        train, validation, test = split_temporal_samples(
            samples, VALIDATION_START, TEST_START, horizon
        )
        feature_names = samples.columns.drop("y")
        print(
            f"\nHorizon {horizon}h | train={len(train)} "
            f"| validation={len(validation)} | final test={len(test)}"
        )

        validation_scores = {}
        for name, estimator in make_models().items():
            estimator.fit(train[feature_names], train["y"])
            validation_predictions = estimator.predict(validation[feature_names])
            validation_score = regression_metrics(
                validation["y"], validation_predictions
            )
            validation_scores[name] = validation_score["mae"]
            metric_rows.append({
                "horizon": horizon,
                "split": "validation",
                "model": name,
                **validation_score,
            })
            print(f"  validation {name}: MAE={validation_score['mae']:.3f}")

        best_name = min(validation_scores, key=validation_scores.get)
        fit_samples = fit_model_matrix(samples, TEST_START, horizon)
        final_model = make_models()[best_name]
        final_model.fit(fit_samples[feature_names], fit_samples["y"])
        final_predictions = final_model.predict(test[feature_names])

        baseline = regression_metrics(test["y"], test["lag0"])
        best_metrics = regression_metrics(test["y"], final_predictions)
        metric_rows.extend([
            {
                "horizon": horizon,
                "split": "test",
                "model": "Baseline (giữ nguyên hiện tại)",
                **baseline,
            },
            {
                "horizon": horizon,
                "split": "test",
                "model": best_name,
                "selected_on": "validation",
                **best_metrics,
            },
        ])
        print(f"Chọn trên validation: {best_name}")
        print(
            f"Test chỉ dùng đánh giá cuối | baseline MAE={baseline['mae']:.3f} "
            f"| {best_name} MAE={best_metrics['mae']:.3f}"
        )

        tail_index = test.index[-336:]
        plt.figure(figsize=(11, 4))
        plt.plot(tail_index, test.loc[tail_index, "y"], label="Thực tế", lw=1.2)
        prediction_series = pd.Series(final_predictions, index=test.index)
        plt.plot(
            tail_index, prediction_series.loc[tail_index],
            label=f"Dự báo ({best_name})", lw=1.2,
        )
        plt.title(f"PM2.5: thực tế và dự báo trước {horizon} giờ (14 ngày cuối)")
        plt.ylabel("µg/m³")
        plt.legend()
        plt.savefig(
            figures_dir / f"05_forecast_{horizon}h.png",
            dpi=150, bbox_inches="tight",
        )
        plt.close()

        prediction_rows.append(pd.DataFrame({
            "time": test.index,
            "horizon_hours": horizon,
            "actual": test["y"].to_numpy(),
            "predicted": final_predictions,
            "model": best_name,
        }))

    metrics = pd.DataFrame(metric_rows).round(3)
    print("\n===== VALIDATION / TEST =====")
    print(metrics.to_string(index=False))
    metrics.to_csv(figures_dir / "forecast_results.csv", index=False)
    pd.concat(prediction_rows, ignore_index=True).to_csv(
        figures_dir / "forecast_predictions.csv", index=False
    )
    print("\nĐã lưu forecast_results.csv, forecast_predictions.csv và biểu đồ 05.")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError) as error:
        print(f"Lỗi: {error}")
        raise SystemExit(1) from None
