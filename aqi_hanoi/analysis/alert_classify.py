import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from aqi_hanoi.analysis.model_validation import fit_model_matrix, split_temporal_samples
from aqi_hanoi.core.settings import PROJECT_DIR


THRESH = 150
VALIDATION_START = "2025-01-01"
TEST_START = "2026-01-01"
HORIZONS = [6, 24]
PROBABILITY_THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7]
WEATHER_COLUMNS = [
    "temp", "humidity", "precipitation", "wind_speed", "pressure",
]


def build_features(data, horizon):
    features = pd.DataFrame(index=data.index)
    for lag in [0, 1, 2, 3, 6, 12, 24, 48]:
        features[f"aqi_lag{lag}"] = data["aqi"].shift(lag)
    features["pm25_now"] = data["pm25"]
    features["aqi_roll6"] = data["aqi"].rolling(6).mean()
    features["aqi_roll24"] = data["aqi"].rolling(24).mean()
    for column in WEATHER_COLUMNS:
        features[column] = data[column]

    target_time = features.index + pd.Timedelta(hours=horizon)
    features["hour_sin"] = np.sin(2 * np.pi * target_time.hour / 24)
    features["hour_cos"] = np.cos(2 * np.pi * target_time.hour / 24)
    features["month"] = target_time.month
    features["dow"] = target_time.dayofweek
    return features


def build_samples(data, horizon):
    features = build_features(data, horizon)
    future_aqi = data["aqi"].shift(-horizon)
    target = (future_aqi > THRESH).astype(float).rename("y")
    target[future_aqi.isna()] = np.nan
    return pd.concat([features, target], axis=1).dropna()


def make_models():
    return {
        "Logistic Regression": make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, class_weight="balanced"),
        ),
        "Random Forest": RandomForestClassifier(
            n_estimators=300, min_samples_leaf=5, class_weight="balanced",
            n_jobs=-1, random_state=42,
        ),
        "Gradient Boosting": HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.05, class_weight="balanced",
            random_state=42,
        ),
    }


def classification_metrics(actual, predicted, probabilities):
    return {
        "accuracy": accuracy_score(actual, predicted),
        "precision": precision_score(actual, predicted, zero_division=0),
        "recall": recall_score(actual, predicted, zero_division=0),
        "f1": f1_score(actual, predicted, zero_division=0),
        "auc": (
            roc_auc_score(actual, probabilities)
            if pd.Series(actual).nunique() == 2 else np.nan
        ),
    }


def main():
    figures_dir = PROJECT_DIR / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    data_path = PROJECT_DIR / "clean" / "fact_model_hourly.csv"
    data = (pd.read_csv(data_path, parse_dates=["time"])
            .set_index("time").sort_index())
    data = data[["aqi", "pm25", *WEATHER_COLUMNS]]
    data = data[~data.index.duplicated(keep="last")]
    data = data.reindex(pd.date_range(data.index.min(), data.index.max(), freq="h"))

    metric_rows = []
    threshold_rows = []
    for horizon in HORIZONS:
        samples = build_samples(data, horizon)
        train, validation, test = split_temporal_samples(
            samples, VALIDATION_START, TEST_START, horizon
        )
        feature_names = samples.columns.drop("y")
        y_train = train["y"].astype(int)
        y_validation = validation["y"].astype(int)
        y_test = test["y"].astype(int)
        if y_train.nunique() < 2 or y_validation.nunique() < 2 or y_test.nunique() < 2:
            raise ValueError(
                f"Horizon {horizon}h cần cả hai lớp AQI ở train, validation và test."
            )

        print(f"\n===== Cảnh báo trước {horizon} giờ (AQI > {THRESH}) =====")
        print(
            f"Train: {len(train)} mẫu | validation: {len(validation)} mẫu "
            f"({y_validation.mean():.3f} dương tính) | "
            f"test: {len(test)} mẫu ({y_test.mean():.3f} dương tính)"
        )
        validation_predictions = {}
        for name, estimator in make_models().items():
            estimator.fit(train[feature_names], y_train)
            probabilities = estimator.predict_proba(validation[feature_names])[:, 1]
            best_threshold = max(
                PROBABILITY_THRESHOLDS,
                key=lambda threshold: f1_score(
                    y_validation,
                    (probabilities >= threshold).astype(int),
                    zero_division=0,
                ),
            )
            validation_predictions[name] = {
                "threshold": best_threshold,
                "probabilities": probabilities,
            }
            threshold_predictions = (probabilities >= best_threshold).astype(int)
            validation_score = classification_metrics(
                y_validation, threshold_predictions, probabilities
            )
            metric_rows.append({
                "horizon": horizon,
                "split": "validation",
                "model": name,
                "probability_threshold": best_threshold,
                **validation_score,
            })
            threshold_rows.append({
                "horizon": horizon,
                "model": name,
                "threshold": best_threshold,
                **validation_score,
            })

        best_name = max(
            validation_predictions,
            key=lambda name: f1_score(
                y_validation,
                (
                    validation_predictions[name]["probabilities"]
                    >= validation_predictions[name]["threshold"]
                ).astype(int),
                zero_division=0,
            ),
        )
        best_threshold = validation_predictions[best_name]["threshold"]
        fit_samples = fit_model_matrix(samples, TEST_START, horizon)
        final_model = make_models()[best_name]
        final_model.fit(
            fit_samples[feature_names], fit_samples["y"].astype(int)
        )
        test_probabilities = final_model.predict_proba(test[feature_names])[:, 1]
        test_predictions = (test_probabilities >= best_threshold).astype(int)
        final_metrics = classification_metrics(
            y_test, test_predictions, test_probabilities
        )

        baseline_predictions = (test["aqi_lag0"] > THRESH).astype(int)
        baseline_metrics = classification_metrics(
            y_test, baseline_predictions, baseline_predictions.astype(float)
        )
        metric_rows.extend([
            {
                "horizon": horizon,
                "split": "test",
                "model": "Baseline (giữ nguyên hiện tại)",
                "probability_threshold": np.nan,
                **baseline_metrics,
            },
            {
                "horizon": horizon,
                "split": "test",
                "model": best_name,
                "probability_threshold": best_threshold,
                "selected_on": "validation",
                **final_metrics,
            },
        ])
        print(
            f"Chọn trên validation: {best_name}, "
            f"ngưỡng xác suất={best_threshold:.1f}"
        )
        print(
            f"Test cuối | baseline F1={baseline_metrics['f1']:.3f} "
            f"| mô hình F1={final_metrics['f1']:.3f}, "
            f"recall={final_metrics['recall']:.3f}, "
            f"precision={final_metrics['precision']:.3f}"
        )

        matrix = confusion_matrix(y_test, test_predictions, labels=[0, 1])
        ConfusionMatrixDisplay(
            matrix, display_labels=["Không xấu", "AQI xấu+"]
        ).plot(cmap="Blues", values_format="d")
        plt.title(f"Ma trận nhầm lẫn: {best_name}, trước {horizon} giờ")
        plt.savefig(
            figures_dir / f"08_confusion_{horizon}h.png",
            dpi=150, bbox_inches="tight",
        )
        plt.close()

        false_positive_rate, true_positive_rate, _ = roc_curve(
            y_test, test_probabilities
        )
        plt.figure(figsize=(5.5, 5))
        plt.plot(
            false_positive_rate, true_positive_rate,
            label=f"{best_name} (AUC {final_metrics['auc']:.3f})",
        )
        plt.plot([0, 1], [0, 1], "k--", lw=0.8)
        plt.xlabel("Tỷ lệ báo động giả")
        plt.ylabel("Tỷ lệ phát hiện đúng")
        plt.title(f"Đường ROC trên tập test, cảnh báo trước {horizon} giờ")
        plt.legend(loc="lower right")
        plt.savefig(
            figures_dir / f"09_roc_{horizon}h.png",
            dpi=150, bbox_inches="tight",
        )
        plt.close()

    metrics = pd.DataFrame(metric_rows).round(3)
    print("\n===== KẾT QUẢ VALIDATION / TEST =====")
    print(metrics.to_string(index=False))
    metrics.to_csv(figures_dir / "alert_results.csv", index=False)
    pd.DataFrame(threshold_rows).round(3).to_csv(
        figures_dir / "alert_thresholds.csv", index=False
    )
    print("\nĐã lưu alert_results.csv, alert_thresholds.csv và các hình 08, 09.")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError) as error:
        print(f"Lỗi: {error}")
        raise SystemExit(1) from None
