import requests

from aqi_hanoi.core.http_utils import get_with_retry


def fetch_json(url, **kwargs):
    try:
        response = get_with_retry(url, **kwargs)
        return response.json()
    except requests.RequestException as error:
        status = error.response.status_code if error.response is not None else "network error"
        raise RuntimeError(f"WAQI API request failed ({status})") from None
