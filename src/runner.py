"""Daily task/search orchestration with persistent per-account statistics."""

from datetime import date
import os
import random
import threading
import time

from .accounts import AccountMetaManager, GlobalSettingsManager
from .auth import AuthenticationRequired, require_session
from .config import JSON_FILE_PATH, history_path, stats_path, status_path
from .dailytasks import DailySet
from .emulator import DriverManager, HumanBehavior
from .search import HistoryManager, SearchEngine, llm, resolve_search_locale
from .search.engine import SearchRestricted, webdriver_error_details
from .stats import StatsManager, scrape_points_balance


class Runner:
    def __init__(self, account, log=print):
        self.log = log
        self.account = account
        aid = account["id"]
        self.global_settings = GlobalSettingsManager()
        self.account_meta = AccountMetaManager(aid)
        self.driver_manager = DriverManager(aid)
        self.history = HistoryManager(history_path(aid), logger=log)
        self.daily_set = DailySet(
            status_path(aid),
            logger=log,
            dashboard_variant=self.account_meta.get_dashboard_variant(),
        )
        self.search_engine = SearchEngine(logger=log, history=self.history)
        self.stats = StatsManager(stats_path(aid), logger=log)
        self._stop_event = threading.Event()
        self._driver = None
        self._daily_set_attempted = False
        self._visual_search_attempted = False
        self._last_scraped_balance = None
        self._session_counts = dict.fromkeys(
            ("pc", "mobile", "cards", "earn", "quests"), 0
        )
        self._search_summary = {
            mode: dict.fromkeys(
                (
                    "requested",
                    "attempted",
                    "successful",
                    "failed",
                    "retries",
                    "restarts",
                ),
                0,
            )
            for mode in ("pc", "mobile")
        }
        self._phase_errors = []
        self._failed_modes = set()

    def run(self, pc, mobile, daily_only=False):
        if self.account.get("deletion_pending"):
            raise ValueError(
                "Account deletion is pending; retry --delete-account before using it"
            )
        if not self.account_meta.is_first_setup_done():
            raise AuthenticationRequired(
                f"Initialize this account: ./run.sh --account {self.account['id']} --init"
            )
        schedule = self.account_meta.get_schedule()
        self._search_summary["pc"]["requested"] = 0 if daily_only else pc
        self._search_summary["mobile"]["requested"] = 0 if daily_only else mobile
        complete = False
        try:
            if daily_only or pc + mobile == 0:
                self._attempt_phase(False, 0)
            elif schedule.get("advancedScheduling") and schedule.get("enabled"):
                self._scheduled(pc, mobile, schedule)
            else:
                if pc:
                    self._attempt_phase(False, pc)
                if mobile:
                    self._attempt_phase(True, mobile)
            if self._stop_event.is_set():
                self._phase_errors.append("Run stopped before completion")
            if self._phase_errors:
                raise RuntimeError("; ".join(self._phase_errors))
            schedule["last_triggered_date"] = date.today().isoformat()
            self.account_meta.set_schedule(schedule)
            complete = True
        except Exception as error:
            if not self._phase_errors or isinstance(error, AuthenticationRequired):
                self._phase_errors.append(webdriver_error_details(error))
            raise
        finally:
            for mode, counts in self._search_summary.items():
                counts["skipped"] = counts["requested"] - counts["attempted"]
                self.log(
                    f"{mode.upper()} final: "
                    + ", ".join(f"{key}={value}" for key, value in counts.items())
                )
            self.log(
                f"Run {'complete' if complete else 'incomplete'}. "
                "Search success means verified results, not guaranteed Rewards credit."
            )
            self.stats.record_session(
                pc_searches=self._session_counts["pc"],
                mobile_searches=self._session_counts["mobile"],
                daily_cards=self._session_counts["cards"],
                earn_cards=self._session_counts["earn"],
                quest_tasks=self._session_counts["quests"],
                balance=self._last_scraped_balance,
                search_summary={
                    "complete": complete,
                    "phases": self._search_summary,
                    "errors": self._phase_errors,
                },
            )

    def _attempt_phase(self, mobile, count):
        if mobile in self._failed_modes or self._stop_event.is_set():
            return
        try:
            self._phase(mobile, count)
        except AuthenticationRequired:
            # Account challenges and service restrictions apply across modes.
            raise
        except Exception as error:
            detail = f"{'Mobile' if mobile else 'PC'} phase: {webdriver_error_details(error)}"
            self.log(f"[ERROR] {detail}")
            self._phase_errors.append(detail)
            self._failed_modes.add(mobile)

    def _scheduled(self, pc, mobile, schedule):
        hours = float(schedule.get("runDuration", 3))
        qph = int(schedule.get("queriesPerHour", 10))
        if not 1 <= hours <= 24 or not 1 <= qph <= 99:
            raise ValueError(
                "Invalid advanced schedule: duration 1..24 hours and queries/hour 1..99 required"
            )
        batch = max(1, min(10, qph // 6))
        batches = [(False, min(batch, pc - index)) for index in range(0, pc, batch)]
        batches += [
            (True, min(batch, mobile - index)) for index in range(0, mobile, batch)
        ]
        interval = hours * 3600 / max(1, len(batches))
        for index, (is_mobile, count) in enumerate(batches):
            if is_mobile in self._failed_modes:
                continue
            self._attempt_phase(is_mobile, count)
            if any(mode not in self._failed_modes for mode, _ in batches[index + 1 :]):
                delay = max(5, interval * random.uniform(0.75, 1.25))
                self.log(f"Waiting {delay:.0f}s until the next search batch")
                time.sleep(delay)

    def _phase(self, mobile, count):
        label = "Mobile" if mobile else "PC"
        self.log(f"{label}: {count} text searches")
        self._driver = self.driver_manager.setup_driver(mobile=mobile)
        errors = []
        try:
            require_session(self._driver)
            if count:
                queries = self._build_queries(count)
                if not queries:
                    raise RuntimeError("No search queries available")
                result = self.search_engine.perform_searches(
                    self._driver,
                    queries,
                    mobile=mobile,
                    stop_event=self._stop_event,
                    search_delay_range=self.global_settings.get_search_delay(),
                    recreate_driver=lambda: self._restart_search_driver(mobile),
                )
                mode = "mobile" if mobile else "pc"
                self._session_counts[mode] += result.successful
                for key in ("attempted", "successful", "failed", "retries", "restarts"):
                    self._search_summary[mode][key] += getattr(result, key)
                if result.restriction:
                    raise SearchRestricted(result.restriction)
                if result.successful < count:
                    errors.append(
                        f"{label} searches incomplete: {result.successful}/{count}"
                    )
            if not mobile:
                force = self.global_settings.get_force_tasks()
                if not self._daily_set_attempted and (
                    self.daily_set.should_perform_daily_set()
                    or force["force_daily_tasks"]
                ):
                    self._daily_set_attempted = True
                    success = self.daily_set.perform_daily_set(
                        self._driver,
                        HumanBehavior(self._driver, mobile=False),
                        stop_event=self._stop_event,
                    )
                    totals = self.daily_set.last_totals
                    self._session_counts["cards"] += totals.get("newly", 0)
                    self._session_counts["earn"] += totals.get("earn", 0)
                    self._session_counts["quests"] += totals.get("quests", 0)
                    if success:
                        self.daily_set.mark_as_completed()
                    else:
                        errors.append("Daily tasks failed")
                needed = not self._visual_search_attempted and (
                    self.daily_set.should_perform_visual_search()
                    or force["force_visual_search"]
                )
                if needed and not self._run_visual_search_if_needed():
                    errors.append("Visual search failed or was not credited")
            balance = scrape_points_balance(self._driver)
            if balance is not None:
                self._last_scraped_balance = balance
            if errors:
                raise RuntimeError("; ".join(errors))
        finally:
            self._close_driver()

    def _close_driver(self):
        if self._driver is not None:
            self._driver.quit()
            self._driver = None

    def _restart_search_driver(self, mobile):
        self._close_driver()
        self._driver = self.driver_manager.setup_driver(mobile=mobile)
        require_session(self._driver)
        return self._driver

    def _build_queries(self, count):
        """
        Build the list of queries for a phase.

        When LLM generation is enabled and an API key is set, ask the chosen
        provider for `count` fresh queries in the user's language. Any failure
        (no key, invalid key, quota, offline, malformed answer) or a shortfall
        is transparently topped up / replaced with a random sample from the
        static assets/queries.json, so the daily search target is still met and
        the run never depends on the network.

        Args:
            count (int): how many queries to produce.

        Returns:
            list: up to `count` query strings (empty only if the static file is
            missing).
        """
        if count <= 0:
            return []

        queries = []
        cfg = self.global_settings.get_llm_config()
        if cfg["use_llm_queries"]:
            if cfg["llm_api_key"]:
                locale = resolve_search_locale(
                    self.global_settings.get_settings(), self.log
                )
                self.log(
                    f"Generating {count} queries via LLM "
                    f"({cfg['llm_provider']}, {locale})…"
                )
                queries = llm.generate_queries(
                    count,
                    locale,
                    provider=cfg["llm_provider"],
                    model=cfg["llm_model"],
                    api_key=cfg["llm_api_key"],
                    base_url=cfg["llm_base_url"],
                    logger=self.log,
                )
                if not queries:
                    self.log(
                        "[WARNING] LLM generation failed — "
                        "falling back to static queries."
                    )
            else:
                self.log(
                    "[WARNING] LLM enabled but no API key set — "
                    "using static queries."
                )

        if len(queries) >= count:
            return queries[:count]

        # Top up from the static file, skipping any query the LLM already
        # produced. Request extra headroom so de-dup can't leave us short.
        static = self.search_engine.load_queries_from_json(
            JSON_FILE_PATH, num_needed=count + len(queries)
        )

        if not queries:
            return static[:count]

        seen = set(queries)
        extra = [q for q in static if q not in seen][: count - len(queries)]
        self.log(
            f"LLM returned {len(queries)}/{count} queries — "
            f"topped up with {len(extra)} static queries."
        )
        return queries + extra

    def _run_visual_search_if_needed(self):
        """
        Controls the visual search process.

        Checks if the task is needed today, generates a unique image,
        performs the search, and updates the status after successful completion.

        A day already marked as done is skipped, unless the "Force visual
        search" setting is on — the saved status can be stale (a search that
        was credited but is worth redoing, or a status file the user doesn't
        trust), and that toggle is how the user says so.

        Returns:
            bool: True if visual search was performed, False otherwise.
        """
        if self.daily_set is None or self.search_engine is None:
            return False

        force = (
            not self._visual_search_attempted
            and self.global_settings.get_force_tasks()["force_visual_search"]
        )
        already_done = not self.daily_set.should_perform_visual_search()

        if already_done and not force:
            self.log("Visual Search already completed today. Skipping.")
            return False

        if already_done:
            self.log(
                "Visual Search already marked as done today, but running anyway "
                "(Force visual search is ON)."
            )
        else:
            self.log(
                "Visual Search not completed today. Starting Visual Search task..."
            )

        self._visual_search_attempted = True

        used_images = self.daily_set.get_used_visual_search_images()
        mission_url = getattr(self.daily_set, "visual_search_url", None)

        # Surface to search from: None lets the engine use its own order; the
        # second pass forces the other one when Rewards ignored the first.
        search_url = None

        for attempt in (1, 2):
            image_id, updated_images = self.search_engine.get_next_image_id(used_images)

            image_path = self.search_engine.prepare_unique_image(image_id)

            if image_path is None:
                return False

            try:
                success = self.search_engine.perform_visual_search(
                    self._driver,
                    image_path,
                    stop_event=self._stop_event,
                    entry_url=mission_url,
                    search_url=search_url,
                )

                # Rewards is the authority, in both directions: a results page
                # is not proof of credit, and Bing rendering the results
                # somewhere we didn't recognise is not proof of failure. So ask
                # the mission.
                stopped = self._stop_event is not None and self._stop_event.is_set()
                credited = None if stopped else self._check_visual_search_credited()

                # The image reached Bing in either of these cases, so don't
                # offer it again on the next run.
                if success or credited is True:
                    self.daily_set.save_used_visual_search_images(updated_images)
                    used_images = updated_images

                if not success:
                    if credited is not True:
                        return False
                    self.log(
                        "Bing's results page never loaded, but Rewards counted "
                        "the search anyway."
                    )
                elif credited is False:
                    # Which surface Rewards credits is Microsoft's call and has
                    # changed before, so one refusal doesn't write the day off:
                    # search again from the other surface, once, with a fresh
                    # image.
                    other = self.search_engine.other_surface_url()

                    if attempt == 1 and other and not stopped:
                        self.log(
                            f"Rewards ignored a search from "
                            f"{self.search_engine.last_search_url}. "
                            f"Trying again from {other}."
                        )
                        search_url = other
                        continue

                    self.log(
                        "[WARNING] Rewards did not count the visual search. "
                        "Not marked as done for today."
                    )
                    return False

                self.daily_set.mark_visual_search_as_completed()
                self.log("Visual search marked as done for today.")

                return True

            except Exception as e:
                self.log(f"[WARNING] Visual search failed: {e}")
                return False

            finally:
                if os.path.exists(image_path):
                    try:
                        os.remove(image_path)
                    except Exception as e:
                        self.log(
                            f"[WARNING] Failed to remove temporary image file: {e}"
                        )

        return False

    def _check_visual_search_credited(self):
        """
        Check the Rewards "visual search streak" mission after a search.

        Returns:
            bool: True when the mission counted the search, False when it
                explicitly still shows no activity today, or None when there
                is nothing to compare against (legacy dashboard, mission not
                offered, or the progress couldn't be read).
        """
        try:
            progress = self.daily_set.read_visual_search_progress(self._driver)
        except Exception as e:
            self.log(f"[WARNING] Could not read the visual search mission: {e}")
            return None

        if progress is None:
            return None

        done, total = progress
        before = getattr(self.daily_set, "visual_search_progress", None)
        was = f" (was {before[0]}/{before[1]})" if before else ""
        self.log(f"Rewards visual search streak: {done}/{total}{was}")

        return done > 0
