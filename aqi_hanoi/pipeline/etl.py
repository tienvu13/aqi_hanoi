import pandas as pd, numpy as np
from aqi_hanoi.core.waqi_stations import filter_hanoi_waqi_data

# ---------- 1. EXTRACT ----------
weather = pd.read_csv("raw/weather_hanoi.csv", parse_dates=["time"])
aq      = pd.read_csv("raw/aq_model_hanoi.csv", parse_dates=["time"])
waqi    = pd.read_csv("raw/waqi_stations.csv", parse_dates=["collected_at"])
waqi = filter_hanoi_waqi_data(waqi, "lat", "lon", "station_name")

# ---------- 2. TRANSFORM: làm sạch thời tiết + AQI mô hình ----------
def basic_clean(df):
    return (df.drop_duplicates("time")
              .sort_values("time")
              .set_index("time"))

weather, aq = basic_clean(weather), basic_clean(aq)

# Giá trị bất hợp lý -> NaN
weather.loc[~weather.relative_humidity_2m.between(0, 100), "relative_humidity_2m"] = np.nan
weather.loc[weather.precipitation < 0, "precipitation"] = np.nan
weather.loc[weather.wind_speed_10m < 0, "wind_speed_10m"] = np.nan
for c in ["pm10", "pm2_5", "us_aqi"]:
    aq.loc[aq[c] < 0, c] = np.nan

# Nội suy các khoảng thiếu ngắn (tối đa 3 giờ liên tiếp), khoảng dài hơn để NaN
weather = weather.interpolate(limit=3)
aq      = aq.interpolate(limit=3)

# ---------- 3. TRANSFORM: tích hợp (join theo giờ) ----------
df = weather.join(aq, how="inner").reset_index()
df = df.rename(columns={"pm2_5": "pm25", "temperature_2m": "temp",
                        "relative_humidity_2m": "humidity",
                        "wind_speed_10m": "wind_speed",
                        "wind_direction_10m": "wind_dir",
                        "surface_pressure": "pressure",
                        "us_aqi": "aqi"})
df = df.dropna(subset=["pm25", "pm10", "aqi"])   # bỏ giờ không có AQI

# ---------- 4. TRANSFORM: tạo thuộc tính thời gian + mức AQI ----------
def add_features(d):
    d["date"]  = d["time"].dt.date
    d["hour"]  = d["time"].dt.hour
    d["month"] = d["time"].dt.month
    d["dow"]   = d["time"].dt.dayofweek          # 0 = Thứ Hai
    d["season"] = d["month"].map(lambda m:
        "Xuân" if m in (2, 3, 4) else
        "Hè"   if m in (5, 6, 7) else
        "Thu"  if m in (8, 9, 10) else "Đông")
    return d

bins   = [-1, 50, 100, 150, 200, 300, 1000]
labels = ["Tốt", "Trung bình", "Kém", "Xấu", "Rất xấu", "Nguy hại"]
df = add_features(df)
df["aqi_level"] = pd.cut(df["aqi"], bins=bins, labels=labels)

df.to_csv("clean/fact_model_hourly.csv", index=False)
print("Model hourly:", df.shape, "|", df.time.min(), "->", df.time.max())

# ---------- 5. TRANSFORM: dữ liệu trạm WAQI ----------
waqi["aqi"] = pd.to_numeric(waqi["aqi"], errors="coerce")   # "-" -> NaN
waqi = waqi.dropna(subset=["aqi"])
waqi = waqi[waqi.aqi.between(0, 500)]
waqi["time"] = waqi["collected_at"].dt.floor("h")           # gom về đầu giờ

waqi = (waqi.groupby(["station_uid", "station_name", "lat", "lon", "time"],
                     as_index=False)
             .agg(aqi=("aqi", "mean")))

wx = df[["time", "temp", "humidity", "precipitation",
         "wind_speed", "wind_dir", "pressure"]]
waqi = waqi.merge(wx, on="time", how="left")                # gắn thời tiết
waqi = add_features(waqi)
waqi["aqi_level"] = pd.cut(waqi["aqi"], bins=bins, labels=labels)

waqi.to_csv("clean/fact_station_hourly.csv", index=False)
print("Station hourly:", waqi.shape, "| số trạm:", waqi.station_uid.nunique())

# ---------- 6. KIỂM TRA CHÉO: mô hình vs trạm thật ----------
city = waqi.groupby("time").aqi.mean().rename("aqi_station")
chk = df.set_index("time")[["aqi"]].join(city, how="inner")
if len(chk) > 10:
    print("Số giờ trùng:", len(chk),
          "| tương quan:", round(chk.corr().iloc[0, 1], 3))