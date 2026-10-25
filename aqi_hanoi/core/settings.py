import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
TOKEN_FILE = PROJECT_DIR / "waqi_token.txt"


def get_waqi_token(token_file=TOKEN_FILE):
    token = os.getenv("WAQI_TOKEN", "").strip()
    if not token and token_file.is_file():
        token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(
            "Thiếu WAQI_TOKEN. Hãy đặt biến môi trường WAQI_TOKEN "
            "hoặc tạo waqi_token.txt ở thư mục dự án."
        )
    return token


def get_openaq_api_key():
    api_key = os.getenv("OPENAQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Thiếu OPENAQ_API_KEY. Hãy đặt biến môi trường OPENAQ_API_KEY "
            "để dùng collector OpenAQ."
        )
    return api_key


def get_database_url():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "Thiếu DATABASE_URL. Hãy đặt biến môi trường DATABASE_URL "
            "trước khi chạy script dùng cơ sở dữ liệu."
        )
    return database_url
