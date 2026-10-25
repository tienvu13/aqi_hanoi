import os
import pandas as pd, matplotlib.pyplot as plt, seaborn as sns
from aqi_hanoi.database.db import engine

os.makedirs("figures", exist_ok=True)
sns.set_theme(style="whitegrid")
ORDER = ["Xuân", "Hè", "Thu", "Đông"]

q = """
SELECT t.full_datetime, t.year, t.month, t.hour, t.dow_name, t.season,
       t.is_weekend, t.is_rush_hour,
       f.aqi, f.pm25, f.pm10, f.temp, f.humidity, f.precipitation,
       f.wind_speed, f.wind_dir, f.pressure, l.level_name
FROM fact_air_quality f
JOIN dim_time t   ON f.time_key = t.time_key
JOIN dim_source s ON f.source_key = s.source_key
LEFT JOIN dim_aqi_level l ON f.aqi_level_key = l.aqi_level_key
WHERE s.source_name = 'Open-Meteo CAMS'
"""
df = pd.read_sql(q, engine)
print("Số dòng:", len(df))

# ---- Q1: AQI biến động theo giờ, mùa, tháng ----
prof = df.groupby(["hour", "season"]).pm25.mean().unstack()[ORDER]
prof.plot(figsize=(9, 4), marker="o")
plt.title("PM2.5 trung bình theo giờ và mùa"); plt.ylabel("µg/m³")
plt.savefig("figures/01_pm25_gio_mua.png", dpi=150, bbox_inches="tight"); plt.close()

mon = df.pivot_table(index="month", columns="year", values="pm25", aggfunc="mean")
mon.plot(kind="bar", figsize=(9, 4)); plt.title("PM2.5 trung bình theo tháng")
plt.ylabel("µg/m³")
plt.savefig("figures/02_pm25_thang.png", dpi=150, bbox_inches="tight"); plt.close()

lvl = pd.crosstab(df.season, df.level_name, normalize="index").loc[ORDER] * 100
lvl.plot(kind="bar", stacked=True, figsize=(9, 4))
plt.title("Tỷ lệ các mức AQI theo mùa (%)"); plt.xticks(rotation=0)
plt.savefig("figures/03_muc_aqi_mua.png", dpi=150, bbox_inches="tight"); plt.close()

print("\n== PM2.5 TB theo mùa ==\n", df.groupby("season").pm25.mean().loc[ORDER].round(1))
print("\n== PM2.5 TB: giờ cao điểm vs không ==\n", df.groupby("is_rush_hour").pm25.mean().round(1))
print("\n== PM2.5 TB: cuối tuần vs ngày thường ==\n", df.groupby("is_weekend").pm25.mean().round(1))
print("\n== Top 5 giờ ô nhiễm nhất ==\n", df.groupby("hour").pm25.mean().nlargest(5).round(1))

# ---- Q2: Ảnh hưởng của thời tiết ----
cols = ["pm25", "pm10", "aqi", "temp", "humidity", "precipitation", "wind_speed", "pressure"]
corr = df[cols].corr()
plt.figure(figsize=(7, 6)); sns.heatmap(corr, annot=True, fmt=".2f", cmap="RdBu_r", center=0)
plt.title("Tương quan thời tiết và ô nhiễm")
plt.savefig("figures/04_tuong_quan.png", dpi=150, bbox_inches="tight"); plt.close()
print("\n== Tương quan với PM2.5 ==\n", corr["pm25"].drop("pm25").round(2))

df["nhom_gio"] = pd.cut(df.wind_speed, [0, 5, 10, 15, 100], labels=["<5", "5-10", "10-15", ">15"])
df["nhom_am"] = pd.cut(df.humidity, [0, 60, 75, 90, 100], labels=["<60", "60-75", "75-90", ">90"])
df["mua_mua"] = df.precipitation > 0.1
print("\n== PM2.5 theo tốc độ gió (km/h) ==\n", df.groupby("nhom_gio", observed=True).pm25.mean().round(1))
print("\n== PM2.5 theo độ ẩm (%) ==\n", df.groupby("nhom_am", observed=True).pm25.mean().round(1))
print("\n== PM2.5 khi có mưa vs không mưa ==\n", df.groupby("mua_mua").pm25.mean().round(1))