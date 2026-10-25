import time

import requests


RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


def get_with_retry(url, *, attempts=3, backoff_seconds=1, **kwargs):
    if attempts < 1:
        raise ValueError("attempts phải ít nhất là 1.")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds không được âm.")

    for attempt in range(attempts):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.HTTPError as error:
            status = (
                error.response.status_code
                if error.response is not None else None
            )
            if status not in RETRYABLE_STATUSES or attempt == attempts - 1:
                raise
        except requests.RequestException:
            if attempt == attempts - 1:
                raise
        time.sleep(backoff_seconds * (2 ** attempt))

    raise RuntimeError("Yêu cầu HTTP kết thúc ngoài dự kiến.")
