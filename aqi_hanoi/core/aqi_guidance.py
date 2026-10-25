import pandas as pd


AQI_BANDS = (
    (50, "Tốt"),
    (100, "Trung bình"),
    (150, "Kém"),
    (200, "Xấu"),
    (300, "Rất xấu"),
    (500, "Nguy hại"),
)


def interpret_aqi(aqi):
    """Return the project's AQI band and general outdoor-activity guidance."""
    value = pd.to_numeric(pd.Series([aqi]), errors="coerce").iloc[0]
    if pd.isna(value):
        return None
    if not 0 <= value <= 500:
        raise ValueError("AQI phải nằm trong khoảng từ 0 đến 500.")

    if value <= 50:
        advice = (
            "Có thể sinh hoạt ngoài trời bình thường. Nhóm nhạy cảm vẫn nên "
            "chú ý triệu chứng và làm theo hướng dẫn y tế cá nhân."
        )
    elif value <= 100:
        advice = (
            "Phần lớn mọi người có thể sinh hoạt bình thường; người đặc biệt "
            "nhạy cảm nên cân nhắc giảm hoạt động gắng sức kéo dài ngoài trời."
        )
    elif value <= 150:
        advice = (
            "Trẻ em, người cao tuổi và người có bệnh tim/phổi nên hạn chế "
            "hoạt động gắng sức kéo dài ngoài trời."
        )
    elif value <= 200:
        advice = (
            "Mọi người nên giảm hoạt động gắng sức kéo dài ngoài trời; nhóm "
            "nhạy cảm nên hạn chế ở ngoài trời nhiều nhất có thể."
        )
    elif value <= 300:
        advice = (
            "Nên tránh hoạt động gắng sức ngoài trời; nhóm nhạy cảm nên ở "
            "trong nhà và làm theo khuyến nghị y tế/chính quyền địa phương."
        )
    else:
        advice = (
            "Hạn chế ra ngoài trời; nếu phải ra ngoài, hãy làm theo hướng dẫn "
            "chính thức và tư vấn chuyên môn phù hợp."
        )

    for upper_bound, label in AQI_BANDS:
        if value <= upper_bound:
            return {
                "value": float(value),
                "level": label,
                "advice": advice,
            }
    raise AssertionError("AQI range validation and category bands disagree.")
