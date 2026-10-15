import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, number TEXT NOT NULL, checked_at TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL, changes TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_number ON runs(number, id);
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY, number TEXT NOT NULL, field TEXT NOT NULL,
                    value TEXT NOT NULL, evidence TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE UNIQUE INDEX IF NOT EXISTS current_fact
                    ON facts(number, field) WHERE active = 1;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, number, checked_at, status, message, facts=None):
        changes = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if facts is not None:
                previous = {row["field"]: row for row in db.execute(
                    "SELECT * FROM facts WHERE number = ? AND active = 1", (number,)
                )}
                for field in sorted(previous.keys() | facts.keys()):
                    old, new = previous.get(field), facts.get(field)
                    if old is not None and new is not None and old["value"] == encode(new["value"]):
                        db.execute("UPDATE facts SET last_seen = ?, evidence = ? WHERE id = ?",
                                   (checked_at, encode(new["evidence"]), old["id"]))
                        continue
                    if old is not None:
                        db.execute("UPDATE facts SET active = 0 WHERE id = ?", (old["id"],))
                    if new is not None:
                        db.execute(
                            "INSERT INTO facts(number, field, value, evidence, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                            (number, field, encode(new["value"]), encode(new["evidence"]), checked_at, checked_at),
                        )
                    changes.append({
                        "field": field,
                        "kind": "added" if old is None else "removed" if new is None else "updated",
                        "before": json.loads(old["value"]) if old is not None else None,
                        "after": new["value"] if new is not None else None,
                    })
            db.execute("INSERT INTO runs(number, checked_at, status, message, changes) VALUES (?, ?, ?, ?, ?)",
                       (number, checked_at, status, message, encode(changes)))
        return self.get(number)

    def get(self, number):
        with self.connect() as db:
            runs = db.execute("SELECT * FROM runs WHERE number = ? ORDER BY id DESC", (number,)).fetchall()
            if not runs:
                return None
            facts = {}
            for row in db.execute("SELECT * FROM facts WHERE number = ? AND active = 1 ORDER BY field", (number,)):
                facts[row["field"]] = {
                    "value": json.loads(row["value"]), "evidence": json.loads(row["evidence"]),
                    "first_seen": row["first_seen"], "last_seen": row["last_seen"],
                }
            history = [{
                "id": run["id"], "checked_at": run["checked_at"], "status": run["status"],
                "message": run["message"], "changes": json.loads(run["changes"]),
            } for run in runs]
        return {
            "registration_number": number, "status": history[0]["status"],
            "message": history[0]["message"], "checked_at": history[0]["checked_at"],
            "stale": bool(facts) and history[0]["status"] != "ok",
            "facts": facts, "history": history,
        }

    def list(self):
        with self.connect() as db:
            numbers = [row[0] for row in db.execute("SELECT number FROM runs GROUP BY number ORDER BY MAX(id) DESC")]
        return [self.get(number) for number in numbers]
