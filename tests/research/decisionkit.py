"""Seed decisions into the decision store (the way `migrate` step 2 does)."""

from __future__ import annotations

from expnote.research.decision_store import DecisionStore


def decision_record(number: int, **overrides) -> dict:
    """A confirmed, non-legacy decision row of the demo project."""
    status = overrides.get("status", "confirmed")
    record = {
        "number": number,
        "id": f"O2O-DEC-{number:04d}",
        "title": f"决定 {number}",
        "date": "2026-10-22",
        "status": status,
        "layer": "研究",
        "decided": "做 A。",
        "reason": "因为 B。",
        "options": "- C",
        "lands": "",
        "plans": ["PLAN-900A"],
        "targets": [],
        "supersedes": [],
        "superseded_by": None,
        "confirmed_by": "user" if status == "confirmed" else None,
        "confirmed_on": "2026-10-22" if status == "confirmed" else None,
        "authorization": None,
        "legacy": False,
        "status_raw": status,
        "review": None,
        "review_by": None,
        "source": "cli",
        "created_at": "2026-10-22T00:00:00Z",
        "updated_at": "2026-10-22T00:00:00Z",
        "version": 1,
    }
    record.update(overrides)
    return record


def seed(*numbers: int, project_id: str = "off2on", **overrides) -> None:
    """Insert one decision per number (same overrides for all)."""
    with DecisionStore.open(project_id) as store, store.transaction():
        for number in numbers:
            store.insert_imported(
                decision_record(number, **overrides), actor="test"
            )


def seed_records(*records: dict, project_id: str = "off2on") -> None:
    with DecisionStore.open(project_id) as store, store.transaction():
        for record in records:
            store.insert_imported(record, actor="test")


def reseed(number: int, *, project_id: str = "off2on", **overrides) -> None:
    """Replace a stored decision (tests build states the store never lets happen)."""
    with DecisionStore.open(project_id) as store, store.transaction():
        store.conn.execute("DELETE FROM decisions WHERE number=?", (number,))
    seed_records(decision_record(number, **overrides), project_id=project_id)
