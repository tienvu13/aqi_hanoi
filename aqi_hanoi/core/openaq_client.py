import requests

from aqi_hanoi.core.http_utils import get_with_retry


BASE_URL = "https://api.openaq.org/v3"


def fetch_json(endpoint, api_key, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    headers["X-API-Key"] = api_key
    try:
        response = get_with_retry(
            f"{BASE_URL}{endpoint}",
            headers=headers,
            **kwargs,
        )
        return response.json()
    except requests.RequestException as error:
        status = (
            error.response.status_code
            if error.response is not None else "network error"
        )
        if status == 401:
            raise RuntimeError(
                "OpenAQ từ chối API key (401); hãy kiểm tra OPENAQ_API_KEY."
            ) from None
        raise RuntimeError(f"OpenAQ API request failed ({status})") from None
