import pandas as pd

from aqi_hanoi.core.waqi_stations import filter_hanoi_waqi_data

MAX_AGE_MIN = 180      # bỏ các lượt đọc mà số liệu của trạm đã cũ quá 3 giờ

raw = pd.read_csv("raw/waqi_feed.csv", parse_dates=["collected_at", "obs_time"])
print(f"Tổng số lượt thu thập: {len(raw)} | số trạm: {raw.station_uid.nunique()}")
raw = filter_hanoi_waqi_data(raw, "lat", "lon", "station_name")
print(
    f"Lượt thu thập thuộc khu vực Hà Nội: {len(raw)} | "
    f"số trạm: {raw.station_uid.nunique()}"
)

# ---- 1. Thống kê tỷ lệ lượt đọc dùng được của từng trạm ----
raw["co_aqi"] = raw.aqi.notna() & raw.aqi.between(0, 500)
raw["moi"] = raw.age_min.between(-10, MAX_AGE_MIN)   # loại cả giờ đo nằm trong tương lai
n_future = int((raw.age_min < -10).sum())
if n_future:
    print(f"Cảnh báo: {n_future} lượt có giờ đo nằm trong tương lai, đã loại.")
raw["dung_duoc"] = raw.co_aqi & raw.moi & raw.obs_time.notna()
names = raw.groupby("station_uid").station_name.last().str.slice(0, 38)
cov = raw.groupby("station_uid").agg(
    so_luot=("aqi", "size"),
    co_aqi=("co_aqi", "sum"),
    so_moi=("moi", "sum"),
    dung_duoc=("dung_duoc", "sum"))
cov["ty_le_dung_duoc"] = (cov.dung_duoc / cov.so_luot).round(2)
cov.insert(0, "station_name", names)
print("\n===== TỶ LỆ LƯỢT ĐỌC DÙNG ĐƯỢC THEO TRẠM =====")
print(cov.to_string())

# ---- 2. Giữ lượt đọc hợp lệ, gom theo giờ đo thật ----
ok = raw[raw.dung_duoc].copy()
ok["time"] = ok.obs_time.dt.floor("h")
hourly = (ok.groupby(["station_uid", "time"], as_index=False)
            .agg(station_name=("station_name", "last"),
                 lat=("lat", "last"), lon=("lon", "last"),
                 aqi_tram=("aqi", "mean"),
                 pm25_aqi=("pm25_aqi", "mean"),
                 pm10_aqi=("pm10_aqi", "mean")))
hourly = hourly[["time", "station_uid", "station_name", "lat", "lon",
                 "aqi_tram", "pm25_aqi", "pm10_aqi"]].sort_values(["station_uid", "time"])
hourly.to_csv("clean/station_feed_hourly.csv", index=False, encoding="utf-8")

print(f"\nSố dòng (trạm, giờ) sau khi gom: {len(hourly)}")
print("Số giờ có số liệu của từng trạm:")
print(hourly.groupby("station_uid").time.nunique().to_string())
if len(hourly):
    print("Khoảng thời gian:", hourly.time.min(), "->", hourly.time.max())
print("\nĐã lưu clean/station_feed_hourly.csv")