from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from aqi_hanoi.core.settings import PROJECT_DIR


MIN_HOURS = 24
BINS = [-1, 100, 150, 1000]
LABELS = ["tốt-TB", "kém", "xấu+"]


def load_inputs(project_dir=PROJECT_DIR):
    raw_model = project_dir / "raw" / "aq_model_hanoi.csv"
    clean_model = project_dir / "clean" / "fact_model_hourly.csv"
    if raw_model.exists():
        model = pd.read_csv(raw_model, parse_dates=["time"])
        model = model[["time", "us_aqi"]].rename(columns={"us_aqi": "aqi_cams"})
        print(f"Nguồn CAMS: {raw_model.relative_to(project_dir)}")
    else:
        model = pd.read_csv(clean_model, parse_dates=["time"])
        model = model[["time", "aqi"]].rename(columns={"aqi": "aqi_cams"})
        print(f"Nguồn CAMS: {clean_model.relative_to(project_dir)}")

    model["aqi_cams"] = pd.to_numeric(model["aqi_cams"], errors="coerce")
    model = (model.dropna(subset=["time", "aqi_cams"])
             .drop_duplicates("time", keep="last")
             .set_index("time")
             .sort_index())
    if model.empty:
        raise ValueError("Không có dữ liệu CAMS hợp lệ.")

    feed = project_dir / "clean" / "station_feed_hourly.csv"
    fact = project_dir / "clean" / "fact_station_hourly.csv"
    if feed.exists():
        stations = pd.read_csv(feed, parse_dates=["time"])
        stations = stations[["time", "station_uid", "station_name", "aqi_tram"]]
        print(f"Nguồn dữ liệu trạm: {feed.relative_to(project_dir)} (giờ đo thật)")
    else:
        stations = pd.read_csv(fact, parse_dates=["time"])
        stations = stations[["time", "station_uid", "station_name", "aqi"]]
        stations = stations.rename(columns={"aqi": "aqi_tram"})
        print(f"Nguồn dữ liệu trạm: {fact.relative_to(project_dir)} (giờ thu thập)")

    return model, aggregate_station_hours(stations)


def aggregate_station_hours(stations):
    required = {"time", "station_uid", "station_name", "aqi_tram"}
    missing = required.difference(stations.columns)
    if missing:
        raise ValueError(f"Thiếu cột dữ liệu trạm: {', '.join(sorted(missing))}")

    data = stations.copy()
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    data["aqi_tram"] = pd.to_numeric(data["aqi_tram"], errors="coerce")
    data = data.dropna(subset=["time", "station_uid", "aqi_tram"])
    return (data.groupby(["station_uid", "time"], as_index=False)
            .agg(station_name=("station_name", "last"),
                 aqi_tram=("aqi_tram", "mean"))
            .sort_values(["station_uid", "time"]))


def calculate_stats(data):
    difference = data.aqi_cams - data.aqi_tram
    n_hours = data.time.nunique()
    return pd.Series({
        "so_gio": n_hours,
        "tuong_quan": (
            data.aqi_cams.corr(data.aqi_tram)
            if n_hours >= MIN_HOURS else np.nan
        ),
        "lech_TB(CAMS-tram)": difference.mean(),
        "MAE": difference.abs().mean(),
        "RMSE": np.sqrt((difference ** 2).mean()),
        "aqi_tram_TB": data.aqi_tram.mean(),
        "aqi_cams_TB": data.aqi_cams.mean(),
    })


def main(project_dir=PROJECT_DIR):
    project_dir = Path(project_dir)
    figures_dir = project_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 170)

    model, stations = load_inputs(project_dir)
    print("CAMS có dữ liệu đến:", model.index.max())
    print("== DỮ LIỆU TRẠM WAQI ==")
    if stations.empty:
        raise ValueError("Không có dữ liệu AQI trạm hợp lệ.")
    print(f"{len(stations)} dòng | {stations.station_uid.nunique()} trạm | "
          f"{stations.time.min()} -> {stations.time.max()}")

    per = (stations.groupby(["station_uid", "station_name"])
           .agg(so_gio=("time", "nunique"), tu=("time", "min"),
                den=("time", "max"), aqi_tb=("aqi_tram", "mean"))
           .reset_index())
    per["aqi_tb"] = per["aqi_tb"].round(1)
    per["station_name"] = per["station_name"].str.slice(0, 38)
    print(per.to_string(index=False))

    matches = stations.merge(
        model.reset_index(), on="time", how="inner", validate="many_to_one"
    )
    print(f"\nSố cặp (trạm, giờ) trùng với CAMS: {len(matches)} | "
          f"số giờ khác nhau: {matches.time.nunique()}")
    if matches.time.nunique() < MIN_HOURS:
        print(
            f"Chưa đủ {MIN_HOURS} giờ trùng; tạm bỏ qua đánh giá thống kê. "
            "Tiếp tục thu thập, pipeline không bị xem là lỗi."
        )
        return False

    station_rows = []
    for uid, data in matches.groupby("station_uid", sort=True):
        station_rows.append({
            "station_uid": uid,
            "station_name": data.station_name.iloc[-1],
            **calculate_stats(data).to_dict(),
        })
    by = pd.DataFrame(station_rows).round(3)
    by["station_name"] = by["station_name"].str.slice(0, 38)
    print("\n===== SO SÁNH THEO TỪNG TRẠM =====")
    print(by.to_string(index=False))

    city = (matches.groupby("time")
            .agg(aqi_tram=("aqi_tram", "mean"),
                 aqi_cams=("aqi_cams", "first"),
                 so_tram=("station_uid", "nunique"))
            .reset_index())
    print("\n===== TRUNG BÌNH CÁC TRẠM THEO GIỜ SO VỚI CAMS =====")
    print(calculate_stats(city).round(3).to_string())
    print("Số trạm đóng góp mỗi giờ:",
          f"min={city.so_tram.min()}, trung vị={city.so_tram.median():.0f}, "
          f"max={city.so_tram.max()}")

    city_indexed = city.set_index("time")
    comparison_hours = pd.date_range(
        city_indexed.index.min() - pd.Timedelta(hours=6),
        city_indexed.index.max() + pd.Timedelta(hours=6),
        freq="h",
    )
    full = model.reindex(comparison_hours)
    print("\nTương quan giữa AQI trạm lúc t và AQI CAMS lúc t - k "
          "(k = -1 nghĩa là so với CAMS của giờ sau; k = +1 là CAMS của giờ trước):")
    for offset in range(-3, 4):
        shifted = full["aqi_cams"].shift(offset).reindex(city_indexed.index)
        valid = shifted.notna()
        correlation = (
            city_indexed.aqi_tram[valid].corr(shifted[valid])
            if valid.sum() >= MIN_HOURS else np.nan
        )
        print(f"  k = {offset:+d}: {correlation:.3f}")

    level_station = pd.cut(matches.aqi_tram, BINS, labels=LABELS)
    level_model = pd.cut(matches.aqi_cams, BINS, labels=LABELS)
    print("\nBảng chéo mức AQI (hàng = trạm, cột = CAMS):")
    print(pd.crosstab(level_station, level_model).to_string())
    print(f"Tỷ lệ cùng mức AQI: {(level_station == level_model).mean():.3f}")

    plt.figure(figsize=(5.5, 5))
    for (uid, name), data in matches.groupby(["station_uid", "station_name"]):
        plt.scatter(data.aqi_tram, data.aqi_cams, s=14, alpha=0.6,
                    label=f"{uid}: {str(name)[:28]}")
    limit = max(1, matches.aqi_tram.max(), matches.aqi_cams.max()) * 1.05
    plt.plot([0, limit], [0, limit], "k--", lw=0.8)
    plt.xlabel("AQI trạm WAQI")
    plt.ylabel("AQI mô hình CAMS")
    plt.title("CAMS so với trạm đo (mỗi điểm là một giờ)")
    plt.legend(fontsize=7)
    plt.savefig(figures_dir / "16_cams_vs_waqi.png", dpi=150, bbox_inches="tight")
    plt.close()

    plt.figure(figsize=(10, 3.8))
    plt.plot(city.time, city.aqi_tram, marker="o", ms=3, lw=1,
             label="Trung bình các trạm WAQI")
    plt.plot(city.time, city.aqi_cams, marker="o", ms=3, lw=1, label="CAMS")
    plt.ylabel("AQI")
    plt.legend()
    plt.xticks(rotation=30)
    plt.title("AQI theo giờ: trạm đo và mô hình CAMS")
    plt.savefig(figures_dir / "17_cams_vs_waqi_time.png", dpi=150, bbox_inches="tight")
    plt.close()

    by.to_csv(figures_dir / "aqi_hanoi.analysis.compare_stations.csv", index=False, encoding="utf-8")
    print("\nĐã lưu figures/16, 17 và figures/compare_stations.csv")
    return True


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"Lỗi: {error}")
        raise SystemExit(1) from None
