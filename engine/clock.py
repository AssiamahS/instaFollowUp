"""Real wall-clock time. This Mac's clock drifts by more than a day (sntp 2026-09-15: 25h behind),
so anything compared against Instagram's timestamps goes through here."""
from __future__ import annotations

import email.utils
import time
import urllib.request

_offset = None


def now():
    global _offset
    if _offset is None:
        try:
            r = urllib.request.urlopen(urllib.request.Request("https://www.google.com", method="HEAD"), timeout=5)
            real = email.utils.parsedate_to_datetime(r.headers["Date"]).timestamp()
            _offset = real - time.time()
        except Exception:
            _offset = 0.0
    return time.time() + _offset


def offset():
    now()
    return _offset
