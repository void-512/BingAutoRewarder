"""Account initialization, dashboard selection, and daily schedule settings."""

from ..config import account_meta_path
from ..storage import read_json, write_json

DEFAULT_ACCOUNT_SCHEDULE = {
    "enabled": False,
    "advancedScheduling": False,
    "runDuration": 3,
    "queriesPerHour": 10,
    "queries_pc": 30,
    "queries_mobile": 20,
    "last_triggered_date": None,
}
DASHBOARD_VARIANTS = ("auto", "legacy", "new")


class AccountMetaManager:
    def __init__(self, account_id):
        self.path = account_meta_path(account_id)

    def get_meta(self):
        meta = read_json(self.path, {})
        if not isinstance(meta, dict):
            raise ValueError(
                f"Invalid account metadata: {self.path}; existing data was left intact"
            )
        return {"first_setup_done": False, **meta}

    def save_meta(self, meta):
        write_json(self.path, meta)

    def is_first_setup_done(self):
        return bool(self.get_meta()["first_setup_done"])

    def mark_up_as_done(self):
        meta = self.get_meta()
        meta["first_setup_done"] = True
        self.save_meta(meta)

    def get_schedule(self):
        schedule = self.get_meta().get("schedule", {})
        if not isinstance(schedule, dict):
            raise ValueError(f"Invalid schedule in {self.path}")
        return {**DEFAULT_ACCOUNT_SCHEDULE, **schedule}

    def set_schedule(self, schedule):
        meta = self.get_meta()
        meta["schedule"] = schedule
        self.save_meta(meta)

    def get_dashboard_variant(self):
        return self.get_meta().get("dashboard_variant", "auto")

    def set_dashboard_variant(self, variant):
        if variant not in DASHBOARD_VARIANTS:
            raise ValueError("Unknown dashboard variant")
        meta = self.get_meta()
        meta["dashboard_variant"] = variant
        self.save_meta(meta)
