import os
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.decomposition import PCA

os.makedirs("figures", exist_ok=True)

# ---- 1. Gộp theo ngày ----
df = pd.read_csv("clean/fact_model_hourly.csv", parse_dates=["time"])
df["date"] = df["time"].dt.date
g = df.groupby("date")
daily = pd.DataFrame({
    "pm25_mean": g.pm25.mean(),
    "pm25_max":  g.pm25.max(),
    "temp":      g.temp.mean(),
    "humidity":  g.humidity.mean(),
    "rain_log":  np.log1p(g.precipitation.sum()),   # mưa lệch phải -> lấy log
    "wind":      g.wind_speed.mean(),
    "pressure":  g.pressure.mean(),
    "n_hours":   g.size(),
})
daily = daily[daily.n_hours >= 18].drop(columns="n_hours").dropna()   # bỏ ngày thiếu quá nhiều giờ
print("Số ngày dùng để phân cụm:", len(daily))

X = StandardScaler().fit_transform(daily)

# ---- 2. Chọn số cụm K ----
ks, inertia, sil = range(2, 8), [], []
for k in ks:
    km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(X)
    inertia.append(km.inertia_)
    sil.append(silhouette_score(X, km.labels_))
print("\nSilhouette theo K:", {k: round(s, 3) for k, s in zip(ks, sil)})

fig, ax = plt.subplots(1, 2, figsize=(10, 3.5))
ax[0].plot(list(ks), inertia, marker="o"); ax[0].set_title("Elbow (inertia)")
ax[1].plot(list(ks), sil, marker="o");     ax[1].set_title("Silhouette")
for a in ax: a.set_xlabel("K")
plt.savefig("figures/06_chon_k.png", dpi=150, bbox_inches="tight"); plt.close()

K = 4    # có thể đổi sau khi xem silhouette và hình elbow
km = KMeans(n_clusters=K, n_init=10, random_state=42).fit(X)
daily["cum"] = km.labels_

# ---- 3. Mô tả từng cụm ----
prof = daily.groupby("cum").mean().round(2)
prof["so_ngay"] = daily.groupby("cum").size()
print("\n===== ĐẶC ĐIỂM TRUNG BÌNH TỪNG CỤM =====\n", prof.to_string())

daily["month"] = pd.to_datetime(daily.index).month
print("\n===== TỶ LỆ NGÀY THEO THÁNG TRONG MỖI CỤM (%) =====")
print((pd.crosstab(daily.cum, daily.month, normalize="index") * 100).round(0).to_string())

# ---- 4. Hình minh hoạ (PCA 2 chiều) ----
xy = PCA(n_components=2, random_state=42).fit_transform(X)
plt.figure(figsize=(7, 5))
sc = plt.scatter(xy[:, 0], xy[:, 1], c=daily["cum"], cmap="tab10", s=14)
plt.legend(*sc.legend_elements(), title="Cụm")
plt.title(f"Phân cụm {len(daily)} ngày (K={K}), chiếu PCA")
plt.savefig("figures/07_cum_pca.png", dpi=150, bbox_inches="tight"); plt.close()

daily.drop(columns="month").to_csv("clean/daily_clusters.csv")
print("\nĐã lưu clean/daily_clusters.csv")
