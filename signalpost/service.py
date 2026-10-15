from threading import RLock
from time import monotonic

from .source import FIELDS, LookupProblem, extract, now, valid_number


class Service:
    def __init__(self, store, source):
        self.store, self.source = store, source
        self.lock = RLock()

    def lookup(self, number, refresh=False):
        number = number.strip() if isinstance(number, str) else number
        if not valid_number(number):
            return {
                "registration_number": number, "status": "invalid",
                "message": "Enter a nine-digit Norwegian organization number with a valid checksum.",
                "facts": {}, "history": [], "stale": False, "cached": False,
            }
        with self.lock:
            cached = self.store.get(number)
            if cached and not refresh:
                return {**cached, "cached": True}
            checked_at = now()
            try:
                payload = self.source.fetch(number)
                facts = extract(number, payload, self.source.name, checked_at)
                result = self.store.record(number, checked_at, "ok", "Exact registration-number match.", facts)
            except LookupProblem as exc:
                result = self.store.record(number, checked_at, exc.status, str(exc))
            return {**result, "cached": False}

    def batch(self, numbers, refresh=False, max_seconds=300):
        if not isinstance(numbers, list) or not 1 <= len(numbers) <= 10_000:
            raise ValueError("Provide a list of 1–10000 registration numbers.")
        if any(not isinstance(n, str) or len(n) > 100 for n in numbers):
            raise ValueError("Each registration number must be a string of at most 100 characters.")
        if not 0 < max_seconds <= 86_400:
            raise ValueError("Batch time budget must be between 0 and 86400 seconds.")
        deadline = monotonic() + max_seconds
        results, seen = [], {}
        for number in numbers:
            number = number.strip()
            if number not in seen:
                if monotonic() >= deadline:
                    seen[number] = {
                        "registration_number": number, "status": "failed",
                        "message": "Batch time budget exhausted; lookup not attempted.",
                        "facts": {}, "history": [], "cached": False, "stale": False,
                    }
                else:
                    seen[number] = self.lookup(number, refresh)
            results.append(seen[number])
        return results

    def describe(self):
        return {"source": self.source.name, "fields": list(FIELDS), "max_batch": 10_000}
