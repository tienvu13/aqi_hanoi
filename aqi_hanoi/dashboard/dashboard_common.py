import json

import pandas as pd
import streamlit as st

from aqi_hanoi.core.settings import PROJECT_DIR


def read_csv(relative_path, required_columns=()):
    path = PROJECT_DIR / relative_path
    if not path.is_file():
        st.error(
            f"Chưa có tệp `{relative_path}`. Hãy chạy pipeline thu thập/ETL trước."
        )
        st.stop()
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeError) as error:
        st.error(f"Không đọc được `{relative_path}`: {error}")
        st.stop()
    missing = set(required_columns).difference(frame.columns)
    if missing:
        st.error(
            f"`{relative_path}` thiếu cột bắt buộc: {', '.join(sorted(missing))}."
        )
        st.stop()
    return frame


def read_optional_csv(relative_path, required_columns=(), command=None):
    path = PROJECT_DIR / relative_path
    if not path.is_file():
        message = f"Chưa có kết quả `{relative_path}`."
        if command:
            message += f" Chạy `{command}` để tạo tệp."
        st.info(message)
        return None
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeError) as error:
        st.error(f"Không đọc được `{relative_path}`: {error}")
        st.stop()
    missing = set(required_columns).difference(frame.columns)
    if missing:
        st.error(
            f"`{relative_path}` thiếu cột bắt buộc: "
            f"{', '.join(sorted(missing))}."
        )
        st.stop()
    return frame


def load_model():
    model = read_csv(
        "clean/fact_model_hourly.csv",
        ("time", "aqi", "pm25"),
    )
    model["time"] = pd.to_datetime(model["time"], errors="coerce")
    model["aqi"] = pd.to_numeric(model["aqi"], errors="coerce")
    model["pm25"] = pd.to_numeric(model["pm25"], errors="coerce")
    model = (
        model.dropna(subset=["time", "aqi"])
        .sort_values("time")
        .drop_duplicates("time", keep="last")
        .set_index("time")
    )
    if model.empty:
        st.error("Không có dòng AQI hợp lệ trong dữ liệu mô hình.")
        st.stop()
    return model


def load_station_feed():
    path = PROJECT_DIR / "clean" / "station_feed_hourly.csv"
    if not path.is_file():
        return None
    stations = read_csv(
        "clean/station_feed_hourly.csv",
        ("time", "station_uid", "station_name", "aqi_tram", "lat", "lon"),
    )
    stations["time"] = pd.to_datetime(stations["time"], errors="coerce")
    stations["aqi_tram"] = pd.to_numeric(
        stations["aqi_tram"], errors="coerce"
    )
    return stations.dropna(subset=["time", "aqi_tram"]).sort_values("time")


def render_pipeline_status(now):
    st.subheader("Trạng thái pipeline")
    status_path = PROJECT_DIR / "figures" / "pipeline_status.json"
    if not status_path.is_file():
        st.info(
            "Chưa có trạng thái lần chạy tự động. Chạy `run_waqi.bat` để "
            "khởi tạo trạng thái; log chi tiết nằm trong `raw/log.txt`."
        )
        return
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        st.error(f"Không đọc được trạng thái pipeline: {error}")
        st.stop()

    state = status.get("status", "UNKNOWN")
    started_at = status.get("started_at")
    finished_at = status.get("finished_at")
    last_success_at = status.get("last_success_at")
    metrics = st.columns(4)
    metrics[0].metric("Kết quả lần chạy gần nhất", state)
    metrics[1].metric(
        "Bắt đầu",
        str(started_at).replace("T", " ") if started_at else "—",
    )
    metrics[2].metric(
        "Lần chạy thành công gần nhất",
        str(last_success_at).replace("T", " ") if last_success_at else "Chưa có",
    )
    metrics[3].metric(
        "Thời lượng",
        (
            f"{float(status['duration_seconds']):.1f} giây"
            if status.get("duration_seconds") is not None else "Đang chạy"
        ),
    )

    if state == "FAILED":
        st.error(
            f"Bước lỗi: {status.get('failed_step') or 'không xác định'} "
            f"(mã {status.get('exit_code')}). {status.get('error') or ''}"
        )
    elif state == "RUNNING":
        steps = status.get("steps", [])
        latest_step = steps[-1].get("name", "khởi động") if steps else "khởi động"
        st.info(f"Pipeline đang chạy bước {latest_step}.")
    elif state == "SUCCEEDED":
        st.success(
            f"Pipeline hoàn tất lúc "
            f"{str(finished_at).replace('T', ' ') if finished_at else 'không rõ'}."
        )
    else:
        st.warning(f"Trạng thái pipeline không nhận diện: {state}.")

    if last_success_at:
        last_success_time = pd.Timestamp(last_success_at)
        if last_success_time.tzinfo is not None:
            last_success_time = last_success_time.tz_convert(
                "Asia/Bangkok"
            ).tz_localize(None)
        success_age = (now - last_success_time).total_seconds() / 3600
        if success_age > 2:
            st.warning(
                f"Đã {success_age:.1f} giờ từ lần chạy thành công gần nhất; "
                "hãy kiểm tra Task Scheduler và `raw/log.txt`."
            )
    with st.expander("Chi tiết các bước pipeline"):
        step_rows = pd.DataFrame(status.get("steps", []))
        if step_rows.empty:
            st.info("Chưa có bước chạy nào được ghi nhận.")
        else:
            st.dataframe(
                step_rows,
                width="stretch",
                hide_index=True,
                alt="Trạng thái từng bước thu thập và xử lý pipeline",
            )


def current_hanoi_time():
    return pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
