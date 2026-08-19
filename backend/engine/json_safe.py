"""Recursively coerce numpy/pandas scalars into plain JSON-serializable
Python types.

Extracted out of backend/services/jobs.py so it can be shared with
backend/engine/api_prompt.py (header-row confirmation payloads come
straight from a pandas DataFrame via engine/ingestion.py's header_preview()
and hit exactly the same numpy.int64 / numpy.bool_ / pandas.Timestamp /
NaN problem that run outcomes did -- see jobs.py's original docstring for
why this matters for a JSON column written through SQLAlchemy).
"""

from __future__ import annotations

import math
from typing import Any


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float):  # covers numpy.float64 too (it IS a float)
        return None if math.isnan(value) else value
    if hasattr(value, "item"):  # numpy scalar (int64, bool_, float32, ...)
        item = value.item()
        return json_safe(item) if isinstance(item, float) else item
    if hasattr(value, "isoformat"):  # datetime/date/Timestamp
        return value.isoformat()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)  # last-resort fallback so a stray type never raises
