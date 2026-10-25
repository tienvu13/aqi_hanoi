import json
import sys
from pathlib import Path

import pandas as pd

from aqi_hanoi.core.settings import PROJECT_DIR


ALERTS_PATH = PROJECT_DIR / "figures" / "local_alerts.csv"
STATE_PATH = PROJECT_DIR / "figures" / "alert_notification_state.json"
COOLDOWN = pd.Timedelta(hours=6)
REQUIRED_COLUMNS = {
    "status",
    "horizon_hours",
    "probability_aqi_above_150",
    "probability_threshold",
    "source_age_hours",
    "target_time",
}


def send_windows_notification(title, message):
    if sys.platform != "win32":
        raise RuntimeError("Thông báo desktop chỉ được hỗ trợ trên Windows.")
    try:
        from winotify import Notification
    except ImportError as error:
        raise RuntimeError(
            "Thiếu winotify; hãy cài dependencies bằng "
            "`python -m pip install -r requirements.txt`."
        ) from error

    Notification(
        app_id="AQI Hanoi",
        title=title,
        msg=message,
        duration="short",
    ).show()


def _current_time(now):
    current = (
        pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None)
        if now is None else pd.Timestamp(now)
    )
    if current.tzinfo is not None:
        current = current.tz_convert("Asia/Bangkok").tz_localize(None)
    return current


def _load_state(path):
    if not path.exists():
        return {"signature": None, "last_notified_at": None}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Không đọc được trạng thái thông báo {path}: {error}") from error
    if not isinstance(state, dict) or not {
        "signature", "last_notified_at"
    }.issubset(state):
        raise ValueError(f"Tệp trạng thái thông báo không hợp lệ: {path}")
    return state


def _save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _notification_text(alerts):
    lines = []
    for row in alerts.sort_values("horizon_hours").itertuples():
        target_time = pd.to_datetime(row.target_time, errors="coerce")
        target_label = (
            target_time.strftime("%d/%m %H:%M")
            if pd.notna(target_time) else "chưa xác định"
        )
        probability = float(row.probability_aqi_above_150) * 100
        lines.append(
            f"{int(row.horizon_hours)} giờ: nguy cơ {probability:.0f}% "
            f"(mốc {target_label})"
        )
    return "\n".join(lines)


def notify_current_alerts(
    alerts,
    state_path=STATE_PATH,
    now=None,
    notifier=send_windows_notification,
):
    missing = REQUIRED_COLUMNS.difference(alerts.columns)
    if missing:
        raise ValueError(
            f"Thiếu cột cảnh báo: {', '.join(sorted(missing))}"
        )
    current = _current_time(now)
    state_path = Path(state_path)
    state = _load_state(state_path)

    statuses = alerts["status"].astype(str)
    if statuses.eq("DATA_STALE").all():
        return "stale"

    data_age = pd.to_numeric(alerts["source_age_hours"], errors="coerce")
    eligible = alerts.loc[
        statuses.eq("ALERT")
        & data_age.between(-24, 6)
    ]
    if eligible.empty:
        if statuses.eq("NORMAL").all() and state["signature"] is not None:
            state["signature"] = None
            _save_state(state_path, state)
        return "no_alert"

    horizons = sorted(pd.to_numeric(eligible["horizon_hours"]).astype(int).unique())
    signature = ",".join(map(str, horizons))
    last_notified = state["last_notified_at"]
    cooldown_active = False
    if state["signature"] == signature and last_notified:
        last_time = pd.Timestamp(last_notified)
        if last_time.tzinfo is not None:
            last_time = last_time.tz_convert("Asia/Bangkok").tz_localize(None)
        cooldown_active = current - last_time < COOLDOWN
    if cooldown_active:
        return "suppressed"

    notifier(
        "Cảnh báo chất lượng không khí Hà Nội",
        _notification_text(eligible),
    )
    state["signature"] = signature
    state["last_notified_at"] = current.isoformat(timespec="seconds")
    _save_state(state_path, state)
    return "sent"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if not ALERTS_PATH.is_file():
        raise FileNotFoundError(
            f"Chưa có {ALERTS_PATH.relative_to(PROJECT_DIR)}; "
            "hãy chạy generate_alerts.py trước."
        )
    alerts = pd.read_csv(ALERTS_PATH)
    result = notify_current_alerts(alerts)
    print(f"Kết quả thông báo Windows: {result}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"Lỗi: {error}")
        raise SystemExit(1) from None
