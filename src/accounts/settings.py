"""Shared operational settings, preserving unrecognized existing keys."""

import os

from ..config import GLOBAL_SETTINGS_PATH
from ..storage import read_json, write_json


class GlobalSettingsManager:
    def get_settings(self):
        settings = read_json(GLOBAL_SETTINGS_PATH, {})
        if not isinstance(settings, dict):
            raise ValueError(
                "settings.json must contain an object; existing data was left intact"
            )
        return settings

    def save_settings(self, settings):
        write_json(GLOBAL_SETTINGS_PATH, settings)

    def get_current_account_id(self):
        return self.get_settings().get("current_account_id")

    def set_current_account_id(self, account_id):
        settings = self.get_settings()
        settings["current_account_id"] = account_id
        self.save_settings(settings)

    def get_force_tasks(self):
        settings = self.get_settings()
        return {
            key: bool(settings.get(key, False))
            for key in ("force_daily_tasks", "force_visual_search")
        }

    def get_search_delay(self):
        settings = self.get_settings()
        minimum = float(settings.get("manual_search_delay_min", 4))
        maximum = float(settings.get("manual_search_delay_max", 10))
        if not 0 <= minimum <= maximum <= 1800:
            raise ValueError("Search delays must satisfy 0 <= min <= max <= 1800")
        return minimum, maximum

    def get_llm_config(self):
        settings = self.get_settings()
        defaults = {
            "use_llm_queries": False,
            "llm_provider": "openai",
            "llm_model": "",
            "llm_api_key": "",
            "llm_base_url": "",
        }
        result = {key: settings.get(key, value) for key, value in defaults.items()}
        result["llm_api_key"] = os.environ.get(
            "AUTOREWARDER_LLM_API_KEY", result["llm_api_key"]
        )
        return result
