"""Pick the route to subtitle-server at startup: the official site or a relay mirror.

Relay nodes also serve the whole site under their own hostname, for users who
cannot reach Cloudflare directly. At startup the client probes the official
site and every known mirror at the same time with ``GET /client/ping``. Once the
first probe succeeds it waits a short grace period (SERVER_ROUTE_GRACE_SECONDS),
then picks among the probes that succeeded so far, in this order: the official
site, then mirrors by priority (lower number first). The choice holds for the
whole run; it is redone only after a network failure on the chosen route.

The mirror list comes from the server (every successful ping returns it) and is
cached on disk. The list bundled with the hosted build (SUBTITLE_SERVER_MIRRORS)
is used only until the first server-provided list has been saved.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

import requests

logger = logging.getLogger("server_route")

PING_PATH = "/client/ping"


@dataclass(frozen=True)
class Route:
    base: str
    rank: tuple  # sort key: official first, then mirrors by priority
    official: bool

    @property
    def label(self) -> str:
        return "official" if self.official else self.base


def _clean_base(value) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text.lower().startswith("https://"):
        return ""
    return text


def parse_bundled_mirrors(value: str) -> list[dict]:
    """Parse ``https://a|100,https://b|200`` (priority optional) into mirror dicts."""
    mirrors = []
    for index, item in enumerate(str(value or "").replace(";", ",").split(",")):
        item = item.strip()
        if not item:
            continue
        url, _, priority = item.partition("|")
        base = _clean_base(url)
        if not base:
            continue
        try:
            prio = int(priority.strip()) if priority.strip() else (index + 1) * 100
        except ValueError:
            prio = (index + 1) * 100
        mirrors.append({"url": base, "priority": prio})
    return mirrors


def normalize_mirrors(raw) -> list[dict] | None:
    """Validate a server-provided mirror list; None when the shape is wrong."""
    if not isinstance(raw, list):
        return None
    mirrors = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        base = _clean_base(entry.get("url"))
        if not base:
            continue
        try:
            priority = int(entry.get("priority", 0))
        except (TypeError, ValueError):
            priority = 0
        mirrors.append({"url": base, "priority": priority})
    return mirrors


class ServerRouter:
    def __init__(
        self,
        official: str,
        bundled_mirrors: str = "",
        cache_path: str = "",
        grace_seconds: float = 2.0,
        probe_timeout_seconds: float = 8.0,
        rerace_min_interval_seconds: float = 30.0,
        probe: Callable[[str, float], dict] | None = None,
        clock: Callable[[], float] = time.monotonic,
        simulate_official_down: bool = False,
    ):
        self.official = _clean_base(official) or str(official or "").strip().rstrip("/")
        self.bundled = parse_bundled_mirrors(bundled_mirrors)
        self.cache_path = cache_path
        self.grace_seconds = max(0.0, float(grace_seconds))
        self.probe_timeout_seconds = max(0.5, float(probe_timeout_seconds))
        self.rerace_min_interval_seconds = max(0.0, float(rerace_min_interval_seconds))
        self._probe = probe or self._http_probe
        self.simulate_official_down = bool(simulate_official_down)
        self._clock = clock
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._racing = False
        self._active = self.official
        self._last_race_started = None

    # ---- public API -------------------------------------------------------
    @property
    def active_base(self) -> str:
        with self._lock:
            return self._active

    @property
    def on_mirror(self) -> bool:
        return self.active_base != self.official

    def start(self) -> None:
        """Start the startup race in the background (no-op if one ran or runs)."""
        with self._lock:
            if self._racing or self._last_race_started is not None:
                return
            # Nothing to race against: keep the official site without waiting;
            # the background ping still fetches the server's mirror list.
            if len(self._routes()) == 1:
                self._ready.set()
            self._begin_locked()

    def wait_ready(self, timeout: float | None = None) -> bool:
        """Block until the first race has finished. Starts it when needed."""
        self.start()
        return self._ready.wait(timeout)

    def report_network_failure(self) -> bool:
        """A request on the chosen route failed to connect: race again (rate limited)."""
        with self._lock:
            if self._racing:
                return False
            last = self._last_race_started
            if last is not None and self._clock() - last < self.rerace_min_interval_seconds:
                return False
            print(f"[server-route] network failure on {self._active}; choosing again", flush=True)
            self._begin_locked()
            return True

    def mirrors(self) -> list[dict]:
        cached = self._load_cache()
        return cached if cached is not None else list(self.bundled)

    # ---- internals ----------------------------------------------------------
    def _begin_locked(self) -> None:
        self._racing = True
        self._last_race_started = self._clock()
        threading.Thread(target=self._run_race, name="server-route-race", daemon=True).start()

    def _routes(self) -> list[Route]:
        routes = [Route(self.official, (0, 0, 0), True)]
        seen = {self.official}
        for index, mirror in enumerate(sorted(self.mirrors(), key=lambda m: m["priority"])):
            if mirror["url"] in seen:
                continue
            seen.add(mirror["url"])
            routes.append(Route(mirror["url"], (1, mirror["priority"], index), False))
        return routes

    def _run_race(self) -> None:
        try:
            chosen, mirrors = self.race(self._routes())
            if mirrors is not None:
                self._save_cache(mirrors)
            with self._lock:
                if chosen is not None:
                    self._active = chosen.base
            # print: the app does not configure logging, so INFO would be dropped.
            if chosen is None:
                print(f"[server-route] no route answered; keeping {self.active_base}", flush=True)
            else:
                print(f"[server-route] using {chosen.label}", flush=True)
        except Exception as exc:  # never leave waiters hanging
            logger.warning("Server route race failed: %s", exc)
        finally:
            with self._lock:
                self._racing = False
            self._ready.set()

    def race(self, routes: Iterable[Route]) -> tuple[Route | None, list | None]:
        """Probe every route at once; returns (chosen route, server mirror list)."""
        routes = list(routes)
        cond = threading.Condition()
        results: dict[Route, dict] = {}
        finished = {"count": 0}
        started = self._clock()

        def worker(route: Route) -> None:
            t0 = self._clock()
            try:
                if route.official and self.simulate_official_down:
                    raise ConnectionError("official site disabled by SIMULATE_OFFICIAL_UNREACHABLE")
                data = self._probe(route.base, self.probe_timeout_seconds)
                elapsed = self._clock() - t0
                print(f"[server-route] probe {route.label} ok in {elapsed * 1000:.0f} ms", flush=True)
                with cond:
                    results[route] = data if isinstance(data, dict) else {}
            except Exception as exc:
                elapsed = self._clock() - t0
                print(f"[server-route] probe {route.label} failed after {elapsed * 1000:.0f} ms: {exc}", flush=True)
            finally:
                with cond:
                    finished["count"] += 1
                    cond.notify_all()

        for route in routes:
            threading.Thread(target=worker, args=(route,), name="server-route-probe", daemon=True).start()

        first_success_at = None
        hard_deadline = started + self.probe_timeout_seconds + 1.0
        with cond:
            while True:
                now = self._clock()
                if results and first_success_at is None:
                    first_success_at = now
                if finished["count"] >= len(routes):
                    break
                # The best possible route already answered: no reason to wait.
                if results and min(r.rank for r in results) == min(r.rank for r in routes):
                    break
                if first_success_at is not None and now >= first_success_at + self.grace_seconds:
                    break
                if now >= hard_deadline:
                    break
                deadline = hard_deadline if first_success_at is None else min(
                    hard_deadline, first_success_at + self.grace_seconds
                )
                cond.wait(max(0.01, min(0.1, deadline - now)))
            snapshot = dict(results)

        if not snapshot:
            return None, None
        chosen = min(snapshot, key=lambda r: r.rank)
        # Prefer the official site's view of the mirror list, else the chosen route's.
        mirrors = None
        for route in sorted(snapshot, key=lambda r: r.rank):
            mirrors = normalize_mirrors(snapshot[route].get("mirrors"))
            if mirrors is not None:
                break
        return chosen, mirrors

    def _http_probe(self, base: str, timeout: float) -> dict:
        response = requests.get(
            base + PING_PATH,
            timeout=timeout,
            headers={"Cache-Control": "no-cache"},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise RuntimeError("unexpected ping response")
        return data

    def _load_cache(self) -> list[dict] | None:
        if not self.cache_path:
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("official") != self.official:
            return None
        return normalize_mirrors(data.get("mirrors"))

    def _save_cache(self, mirrors: list[dict]) -> None:
        if not self.cache_path:
            return
        payload = {"official": self.official, "mirrors": mirrors, "saved_at": int(time.time())}
        try:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            logger.warning("Could not save server mirror list: %s", exc)
