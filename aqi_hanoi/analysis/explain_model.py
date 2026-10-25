import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingRegressor, HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance, PartialDependenceDisplay

os.makedirs("figures", exist_ok=True)

H = 24                  # giải thích mô hình dự báo trước 24 giờ
THRESH = 150            # AQI > 150 là xấu trở lên
CUTOFF = "2026-01-01"

NAMES = {
    "aqi_lag0": "AQI hiện tại", "aqi_lag1": "AQI trước 1h", "aqi_lag2": "AQI trước 2h",
    "aqi_lag3": "AQI trước 3h", "aqi_lag6": "AQI trước 6h", "aqi_lag12": "AQI trước 12h",
    "aqi_lag24": "AQI trước 24h", "aqi_lag48": "AQI trước 48h",
    "pm25_now": "PM2.5 hiện tại", "aqi_roll6": "AQI TB 6h", "aqi_roll24": "AQI TB 24h",
    "temp": "Nhiệt độ", "humidity": "Độ ẩm", "precipitation": "Lượng mưa",
    "wind_speed": "Tốc độ gió", "pressure": "Áp suất",
    "hour_sin": "Giờ (sin)", "hour_cos": "Giờ (cos)", "month": "Tháng", "dow": "Thứ",
}

# ---- 1. Dữ liệu và đặc trưng ----
df = (pd.read_csv("clean/fact_model_hourly.csv", parse_dates=["time"])
        .set_index("time").sort_index())
df = df[["aqi", "pm25", "temp", "humidity", "precipitation", "wind_speed", "pressure"]]
df = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq="h"))

X = pd.DataFrame(index=df.index)
for L in [0, 1, 2, 3, 6, 12, 24, 48]:
    X[f"aqi_lag{L}"] = df["aqi"].shift(L)
X["pm25_now"] = df["pm25"]
X["aqi_roll6"] = df["aqi"].rolling(6).mean()
X["aqi_roll24"] = df["aqi"].rolling(24).mean()
for c in ["temp", "humidity", "precipitation", "wind_speed", "pressure"]:
    X[c] = df[c]
tgt = X.index + pd.Timedelta(hours=H)
X["hour_sin"] = np.sin(2 * np.pi * tgt.hour / 24)
X["hour_cos"] = np.cos(2 * np.pi * tgt.hour / 24)
X["month"], X["dow"] = tgt.month, tgt.dayofweek

y_reg = df["pm25"].shift(-H).rename("y_reg")
future_aqi = df["aqi"].shift(-H)
y_cls = (future_aqi > THRESH).astype(float).rename("y_cls")
y_cls[future_aqi.isna()] = np.nan

d = pd.concat([X, y_reg, y_cls], axis=1).dropna()
train = d[d.index < pd.Timestamp(CUTOFF) - pd.Timedelta(hours=H)]
test = d[d.index >= CUTOFF]
feats = list(X.columns)
Xtr, Xte = train[feats], test[feats]
print(f"Train: {len(train)} | Test: {len(test)}")


def barh(series, title, xlabel, path, color="#0F766E"):
    s = series.sort_values()
    s.index = [NAMES.get(i, i) for i in s.index]
    plt.figure(figsize=(7, 4.5))
    plt.barh(s.index, s.values, color=color)
    plt.title(title); plt.xlabel(xlabel)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()


# ---- 2. Hồi quy PM2.5 trước 24 giờ: permutation importance ----
reg = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, random_state=42)
reg.fit(Xtr, train["y_reg"])
r = permutation_importance(reg, Xte, test["y_reg"], scoring="neg_mean_absolute_error",
                           n_repeats=5, random_state=42, n_jobs=-1)
imp_reg = pd.Series(r.importances_mean, index=feats).sort_values(ascending=False)
print("\n== Dự báo PM2.5 trước 24h: MAE tăng thêm (µg/m3) khi xáo trộn từng đặc trưng ==")
print(imp_reg.head(10).round(3).to_string())
barh(imp_reg.head(10), "Dự báo PM2.5 trước 24 giờ: đặc trưng quan trọng",
     "MAE tăng thêm khi xáo trộn (µg/m³)", "figures/10_perm_importance_reg24.png")

# ---- 3. Phân loại AQI xấu trước 24 giờ: hệ số hồi quy logistic ----
logit = make_pipeline(StandardScaler(),
                      LogisticRegression(max_iter=1000, class_weight="balanced"))
logit.fit(Xtr, train["y_cls"].astype(int))
coef = pd.Series(logit[-1].coef_[0], index=feats)
print("\n== Hệ số logistic (đã chuẩn hoá): dương = tăng khả năng AQI xấu ==")
print(coef.sort_values(ascending=False).round(3).to_string())
top = coef.reindex(coef.abs().sort_values(ascending=False).head(10).index)
barh(top, "Cảnh báo AQI xấu trước 24 giờ: hệ số logistic",
     "Hệ số chuẩn hoá (dương = tăng khả năng AQI xấu)",
     "figures/11_logit_coef_24h.png", color="#2563EB")

# ---- 4. Phân loại: permutation importance và partial dependence (Gradient Boosting) ----
clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                     class_weight="balanced", random_state=42)
clf.fit(Xtr, train["y_cls"].astype(int))
r2 = permutation_importance(clf, Xte, test["y_cls"].astype(int), scoring="roc_auc",
                            n_repeats=5, random_state=42, n_jobs=-1)
imp_cls = pd.Series(r2.importances_mean, index=feats).sort_values(ascending=False)
print("\n== Cảnh báo AQI xấu trước 24h: AUC giảm khi xáo trộn từng đặc trưng ==")
print(imp_cls.head(10).round(4).to_string())
barh(imp_cls.head(10), "Cảnh báo AQI xấu trước 24 giờ: đặc trưng quan trọng",
     "AUC giảm khi xáo trộn", "figures/12_perm_importance_cls24.png", color="#DC2626")

show = ["wind_speed", "humidity", "temp", "pressure", "precipitation", "aqi_lag0"]
fig, axes = plt.subplots(2, 3, figsize=(12, 6))
PartialDependenceDisplay.from_estimator(clf, Xte.iloc[::4], show, kind="average",
                                        grid_resolution=30, ax=axes)
for ax, f in zip(axes.ravel(), show):
    ax.set_xlabel(NAMES.get(f, f))
    ax.set_ylabel("Xác suất AQI xấu+")
plt.suptitle("Ảnh hưởng của từng yếu tố đến xác suất AQI xấu sau 24 giờ")
plt.tight_layout()
plt.savefig("figures/13_pdp_cls24.png", dpi=150, bbox_inches="tight")
plt.close()

pd.DataFrame({"perm_mae_reg24": imp_reg, "perm_auc_cls24": imp_cls,
              "logit_coef": coef}).round(4).to_csv("figures/explain_results.csv")
print("\nĐã lưu figures/10 đến 13 và figures/explain_results.csv")