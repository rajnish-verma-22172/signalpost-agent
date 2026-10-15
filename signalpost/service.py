from threading import RLock

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

    def batch(self, numbers, refresh=False):
        if not isinstance(numbers, list) or not 1 <= len(numbers) <= 100:
            raise ValueError("Provide a list of 1–100 registration numbers.")
        if any(not isinstance(n, str) or len(n) > 100 for n in numbers):
            raise ValueError("Each registration number must be a string of at most 100 characters.")
        return [self.lookup(number, refresh) for number in dict.fromkeys(n.strip() for n in numbers)]

    def describe(self):
        return {"source": self.source.name, "fields": list(FIELDS), "max_batch": 100}
