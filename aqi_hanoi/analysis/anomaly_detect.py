import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.ensemble import IsolationForest, HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score

os.makedirs("figures", exist_ok=True)
pd.set_option("display.width", 160)

df = pd.read_csv("clean/fact_model_hourly.csv", parse_dates=["time"])
df["date"] = df["time"].dt.date

# =====================================================================
# PHẦN 1. Ngày bất thường bằng Isolation Forest (cùng bộ biến với phân cụm)
# =====================================================================
g = df.groupby("date")
daily = pd.DataFrame({
    "pm25_mean": g.pm25.mean(),
    "pm25_max":  g.pm25.max(),
    "temp":      g.temp.mean(),
    "humidity":  g.humidity.mean(),
    "rain_log":  np.log1p(g.precipitation.sum()),
    "wind":      g.wind_speed.mean(),
    "pressure":  g.pressure.mean(),
    "n_hours":   g.size(),
})
daily = daily[daily.n_hours >= 18].drop(columns="n_hours").dropna()
print("Số ngày phân tích:", len(daily))

X = StandardScaler().fit_transform(daily)
iso = IsolationForest(n_estimators=300, contamination=0.03, random_state=42).fit(X)
daily["score"] = -iso.score_samples(X)          # càng cao càng bất thường
daily["bat_thuong"] = iso.predict(X) == -1
daily["loai"] = np.where(daily.pm25_mean > daily.pm25_mean.median(),
                         "PM2.5 cao bất thường", "bất thường khác")
n_anom = int(daily.bat_thuong.sum())
print(f"Số ngày bị đánh dấu bất thường (3%): {n_anom}")
print("\nPhân loại các ngày bất thường:\n", daily[daily.bat_thuong].loai.value_counts().to_string())

cols = ["pm25_mean", "pm25_max", "wind", "rain_log", "humidity", "temp", "score", "loai"]
print("\n===== TOP 10 NGÀY BẤT THƯỜNG NHẤT =====")
print(daily[daily.bat_thuong].sort_values("score", ascending=False)
      .head(10)[cols].round(2).to_string())

months = pd.to_datetime(daily[daily.bat_thuong].index).month
print("\nSố ngày bất thường theo tháng:\n",
      pd.Series(months).value_counts().sort_index().to_string())

# Hình: PM2.5 trung bình theo ngày, đánh dấu ngày bất thường
dd = daily.copy()
dd.index = pd.to_datetime(dd.index)
plt.figure(figsize=(11, 4))
plt.plot(dd.index, dd.pm25_mean, lw=0.8, color="#0F766E", label="PM2.5 trung bình ngày")
a = dd[dd.bat_thuong]
plt.scatter(a.index, a.pm25_mean, color="#DC2626", s=22, zorder=3, label="Ngày bất thường")
plt.ylabel("µg/m³"); plt.legend(); plt.title("Các ngày bất thường (Isolation Forest)")
plt.savefig("figures/14_anomaly_timeline.png", dpi=150, bbox_inches="tight")
plt.close()

plt.figure(figsize=(7, 3.5))
pd.Series(months).value_counts().sort_index().reindex(range(1, 13), fill_value=0).plot(
    kind="bar", color="#2563EB")
plt.xlabel("Tháng"); plt.ylabel("Số ngày bất thường"); plt.xticks(rotation=0)
plt.title("Ngày bất thường theo tháng")
plt.savefig("figures/15_anomaly_by_month.png", dpi=150, bbox_inches="tight")
plt.close()

# =====================================================================
# PHẦN 2. Giờ "cao bất thường so với thời tiết" (phần dư của mô hình chỉ dùng thời tiết)
# =====================================================================
h = df.copy()
h["hour_sin"] = np.sin(2 * np.pi * h.time.dt.hour / 24)
h["hour_cos"] = np.cos(2 * np.pi * h.time.dt.hour / 24)
feats = ["temp", "humidity", "precipitation", "wind_speed", "pressure",
         "hour_sin", "hour_cos"]
h = h.dropna(subset=feats + ["pm25"]).reset_index(drop=True)

# Dự đoán chéo theo khối tháng: mô hình không thấy tháng mà nó đang dự đoán
groups = h.time.dt.strftime("%Y-%m")
oof = np.zeros(len(h))
for tr, te in GroupKFold(n_splits=5).split(h[feats], h.pm25, groups):
    m = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, random_state=42)
    m.fit(h.loc[tr, feats], h.loc[tr, "pm25"])
    oof[te] = m.predict(h.loc[te, feats])
h["expected"] = oof
h["resid"] = h.pm25 - h.expected
r2 = r2_score(h.pm25, h.expected)
print(f"\nR² khi chỉ dùng thời tiết và giờ để đoán PM2.5 (dự đoán chéo): {r2:.3f}")

z = (h.resid - h.resid.mean()) / h.resid.std()
h["cao_bat_thuong"] = z > 3
print(f"Số giờ PM2.5 cao hơn mức mong đợi theo thời tiết quá 3 độ lệch chuẩn: "
      f"{int(h.cao_bat_thuong.sum())} / {len(h)}")

hh = h.groupby(h.time.dt.date).agg(
    gio_bat_thuong=("cao_bat_thuong", "sum"),
    pm25_tb=("pm25", "mean"),
    pm25_mong_doi=("expected", "mean"))
top = hh[hh.gio_bat_thuong >= 6].sort_values("gio_bat_thuong", ascending=False)
print(f"\nSố ngày có từ 6 giờ cao bất thường trở lên: {len(top)}")
print("\n===== TOP 10 NGÀY CÓ NHIỀU GIỜ CAO BẤT THƯỜNG SO VỚI THỜI TIẾT =====")
print(top.head(10).round(1).to_string())

both = set(top.index) & set(daily[daily.bat_thuong].index)
print(f"\nNgày được cả hai phương pháp đánh dấu: {len(both)}")

daily["gio_cao_bat_thuong"] = hh.gio_bat_thuong.reindex(daily.index).fillna(0).astype(int)
daily.to_csv("clean/daily_anomalies.csv")
print("\nĐã lưu clean/daily_anomalies.csv và figures/14, 15")