import os
import time
import pandas as pd

from aqi_hanoi.core.settings import PROJECT_DIR, get_waqi_token
from aqi_hanoi.core.waqi_client import fetch_json
from aqi_hanoi.core.waqi_stations import (
    MAX_STATION_CANDIDATES,
    HANOI_WAQI_BOUNDS,
    TARGET_STATIONS,
    filter_hanoi_waqi_data,
    select_station_uids,
)

OUT = PROJECT_DIR / "raw" / "waqi_feed.csv"
HANOI = pd.Timedelta(hours=7)                 # Hà Nội là UTC+7


def num(x):
    """Chuyển về số; dấu '-' hoặc rỗng thành None."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def iaqi_v(d, key):
    return num((d.get("iaqi") or {}).get(key, {}).get("v"))


def obs_candidates(t):
    """Hai cách suy ra giờ đo (theo giờ Hà Nội): từ chuỗi giờ địa phương 's' và từ epoch 'v'."""
    out = {}
    s, tz, v = t.get("s"), t.get("tz"), t.get("v")
    if s:
        try:
            local = pd.to_datetime(s)                 # giờ địa phương của trạm
            off = HANOI
            if tz:                                    # ví dụ "+07:00"
                z = str(tz)
                sign = -1 if z.startswith("-") else 1
                hh, mm = z.lstrip("+-").split(":")
                off = sign * pd.Timedelta(hours=int(hh), minutes=int(mm))
            out["s"] = local - off + HANOI            # đổi về giờ Hà Nội
        except Exception:
            pass
    if v is not None:
        try:
            out["v"] = (pd.to_datetime(v, unit="s", utc=True)
                          .tz_convert("Asia/Bangkok").tz_localize(None))
        except Exception:
            pass
    return out


def parse_feed(js, collected_at):
    """Đọc một phản hồi feed của WAQI thành một dòng dữ liệu (hoặc None nếu lỗi)."""
    if not isinstance(js, dict) or js.get("status") != "ok":
        return None
    d = js.get("data")
    if not isinstance(d, dict):
        return None
    t = d.get("time") or {}
    cand = obs_candidates(t)
    obs, src = None, None
    for key in ("s", "v"):                            # ưu tiên chuỗi giờ địa phương
        if key in cand and cand[key] <= collected_at + pd.Timedelta(minutes=10):
            obs, src = cand[key], key                 # bỏ giờ nằm trong tương lai
            break
    city = d.get("city") or {}
    geo = city.get("geo") or [None, None]
    age = None
    if obs is not None:
        age = round((collected_at - obs).total_seconds() / 60, 1)
    return {
        "collected_at": collected_at.strftime("%Y-%m-%d %H:%M:%S"),
        "obs_time": obs.strftime("%Y-%m-%d %H:%M:%S") if obs is not None else None,
        "obs_source": src,
        "age_min": age,
        "time_s": t.get("s"), "time_tz": t.get("tz"), "time_v": t.get("v"),
        "station_uid": d.get("idx"),
        "station_name": city.get("name"),
        "lat": geo[0], "lon": geo[1],
        "aqi": num(d.get("aqi")),
        "pm25_aqi": iaqi_v(d, "pm25"),     # chỉ số phụ AQI của PM2.5, KHÔNG phải µg/m3
        "pm10_aqi": iaqi_v(d, "pm10"),
        "temp": iaqi_v(d, "t"), "humidity": iaqi_v(d, "h"),
        "pressure": iaqi_v(d, "p"), "wind": iaqi_v(d, "w"),
        "dominant": d.get("dominentpol"),
    }


def has_fresh_aqi(reading):
    try:
        aqi = float(reading["aqi"])
        age_min = float(reading["age_min"])
        obs_time = pd.to_datetime(reading["obs_time"], errors="coerce")
    except (KeyError, TypeError, ValueError):
        return False
    return (
        0 <= aqi <= 500
        and -10 <= age_min <= 180
        and pd.notna(obs_time)
    )


def main():
    token = get_waqi_token()
    now = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
    map_result = fetch_json(
        "https://api.waqi.info/map/bounds/",
        params={"latlng": HANOI_WAQI_BOUNDS, "token": token},
        timeout=30,
    )
    if map_result.get("status") != "ok":
        if map_result.get("data") == "Invalid key":
            raise RuntimeError("WAQI token không hợp lệ; hãy thay token mới.")
        raise RuntimeError(
            f"WAQI không trả về danh sách trạm hợp lệ: {map_result.get('data')}"
        )
    uids = select_station_uids(
        map_result.get("data", []),
        max_stations=MAX_STATION_CANDIDATES,
    )
    if not uids:
        raise RuntimeError(
            "WAQI không trả về trạm nào có AQI số hợp lệ trong vùng quanh Hà Nội."
        )

    print(
        f"Đang kiểm tra tối đa {len(uids)} trạm WAQI trong khu vực Hà Nội "
        f"để tìm {TARGET_STATIONS} trạm có AQI hợp lệ."
    )
    rows = []
    for uid in uids:
        try:
            js = fetch_json(f"https://api.waqi.info/feed/@{uid}/",
                            params={"token": token}, timeout=30)
        except RuntimeError as error:
            print(f"Loi tram {uid}: {error}")
            continue
        if (
            isinstance(js, dict)
            and js.get("status") != "ok"
            and js.get("data") == "Invalid key"
        ):
            raise RuntimeError("WAQI token không hợp lệ; hãy thay token mới.")
        r = parse_feed(js, now)
        if r and has_fresh_aqi(r):
            rows.append(r)
            print(f"Trạm {uid}: AQI mới {r['aqi']} ({r['age_min']} phút).")
            if len(rows) >= TARGET_STATIONS:
                break
        elif r:
            print(
                f"Trạm {uid}: bỏ qua AQI không hợp lệ hoặc đã cũ "
                f"({r['age_min']} phút)."
            )
        else:
            print(f"Tram {uid}: khong co du lieu hop le")
        if len(rows) < TARGET_STATIONS:
            time.sleep(1.1)          # tránh vượt giới hạn số yêu cầu

    if not rows:
        raise RuntimeError(
            "Không thu thập được AQI hợp lệ và mới từ các trạm WAQI quanh Hà Nội."
        )
    df = filter_hanoi_waqi_data(
        pd.DataFrame(rows), "lat", "lon", "station_name"
    )
    if df.empty:
        raise RuntimeError(
            "Không thu thập được AQI hợp lệ và mới từ các trạm WAQI quanh Hà Nội."
        )
    if len(df) < TARGET_STATIONS:
        print(
            f"Cảnh báo: chỉ tìm được {len(df)}/{TARGET_STATIONS} trạm "
            "có AQI hợp lệ và mới; nguồn WAQI hiện không cung cấp đủ."
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # Nếu tệp cũ có các cột khác (bản trước), đổi tên để không bị lệch cột
    if OUT.exists():
        with OUT.open(encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        if header != list(df.columns):
            old = OUT.with_name(f"waqi_feed_old_{now:%Y%m%d%H%M}.csv")
            os.replace(OUT, old)
            print("Tep cu co cot khac, da doi ten thanh", old)

    df.to_csv(OUT, mode="a", header=not OUT.exists(), index=False,
              encoding="utf-8")
    n_ok = int(df.aqi.notna().sum())
    print(f"Da ghi {len(df)} tram luc {now:%Y-%m-%d %H:%M} ({n_ok} tram co AQI)")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"Lỗi: {error}")
        raise SystemExit(1) from None