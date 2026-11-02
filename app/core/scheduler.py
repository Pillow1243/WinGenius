"""Cron-style scheduler for tools, workflows and macros.

* `parse_cron` / `next_run` — a dependency-free 5-field cron engine
  (`minute hour day-of-month month day-of-week`) with `*`, `a,b`, `a-b`,
  `*/n` and `a-b/n` ranges, Sunday = 0 (7 is accepted as Sunday too).
* `Schedule` — one persisted job: what to run, when, and what happened.
* `ScheduleStore` — JSON file in the app data folder.
* `Scheduler` — a light Qt timer that fires `triggered(schedule)` once per
  matching minute; the shell turns that into a normal background job.

Missing a run while the app was closed is handled by `Schedule.catch_up`:
on the first tick the scheduler notices the last run is older than the
previous window and fires immediately.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from PySide6.QtCore import QObject, QTimer, Signal

from .paths import data_dir

# --------------------------------------------------------------------- parsing
LIMITS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
FIELD_NAMES_FA = ("دقیقه", "ساعت", "روز ماه", "ماه", "روز هفته")
FIELD_NAMES_EN = ("minute", "hour", "day", "month", "weekday")
WEEKDAYS_FA = ("یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه")
WEEKDAYS_EN = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


class CronError(ValueError):
    """Raised for an expression the scheduler cannot understand."""


def _parse_field(expr: str, low: int, high: int, label: str) -> set:
    values: set = set()
    for part in str(expr).split(","):
        part = part.strip()
        if not part:
            raise CronError(f"{label}: empty element")
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            try:
                step = int(raw_step)
            except ValueError as exc:
                raise CronError(f"{label}: bad step '{raw_step}'") from exc
            if step <= 0:
                raise CronError(f"{label}: step must be positive")
        if part in ("*", "?"):
            start, end = low, high
        elif "-" in part:
            left, right = part.split("-", 1)
            try:
                start, end = int(left), int(right)
            except ValueError as exc:
                raise CronError(f"{label}: bad range '{part}'") from exc
            if label == "weekday":                      # allow mon..sun aliases below
                pass
        else:
            try:
                start = end = int(part)
            except ValueError as exc:
                raise CronError(f"{label}: bad value '{part}'") from exc
        if label == "weekday":
            start = 0 if start == 7 else start
            end = 0 if end == 7 else end
        if start < low or end > high or start > end:
            raise CronError(f"{label}: {part} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> Dict[str, set]:
    """Parse a 5-field cron expression into sets of integers."""
    parts = str(expr).strip().split()
    if len(parts) != 5:
        raise CronError("need exactly 5 fields: minute hour day month weekday")
    parsed = {}
    for index, (part, (low, high)) in enumerate(zip(parts, LIMITS)):
        label = FIELD_NAMES_EN[index]
        parsed[label] = _parse_field(part, low, high, label)
    return parsed


def matches(expr: str, when: datetime) -> bool:
    """True when `when` (minute precision) satisfies the expression."""
    try:
        fields = parse_cron(expr)
    except CronError:
        return False
    return (when.minute in fields["minute"]
            and when.hour in fields["hour"]
            and when.day in fields["day"]
            and when.month in fields["month"]
            and (when.weekday() + 1) % 7 in fields["weekday"])


def next_run(expr: str, after: Optional[datetime] = None,
             max_days: int = 3660) -> Optional[datetime]:
    """First matching minute strictly after `after` (defaults to now).

    Instead of walking every minute (which makes a "29 February" schedule crawl
    through half a million steps) the search skips whole hours and days whenever
    the current minute or hour cannot match.
    """
    try:
        spec = parse_cron(expr)
    except CronError:
        return None
    moment = (after or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = moment + timedelta(days=max_days)
    minutes = sorted(spec["minute"])
    while moment < end:
        if matches(expr, moment):
            return moment
        if moment.minute not in spec["minute"]:
            later = [value for value in minutes if value > moment.minute]
            moment += timedelta(minutes=(later[0] - moment.minute) if later else (60 - moment.minute))
        elif moment.hour not in spec["hour"]:
            moment = moment.replace(minute=0) + timedelta(hours=1)
        else:
            # minute and hour are fine, so the day itself does not match
            moment = moment.replace(hour=0, minute=0) + timedelta(days=1)
    return None


def upcoming(expr: str, count: int = 5, after: Optional[datetime] = None) -> List[datetime]:
    results: List[datetime] = []
    moment = after or datetime.now()
    for _ in range(max(1, count)):
        nxt = next_run(expr, moment)
        if nxt is None:
            break
        results.append(nxt)
        moment = nxt
    return results


def describe_cron(expr: str, lang: str = "fa") -> str:
    """A friendly sentence for a cron expression."""
    parts = str(expr).strip().split()
    fa = lang != "en"
    if len(parts) != 5:
        return "فرمت نامعتبر (۵ فیلد لازم است)" if fa else "invalid (5 fields needed)"
    try:
        parse_cron(expr)
    except CronError as exc:
        return ("عبارت نامعتبر: " if fa else "invalid: ") + str(exc)
    minute, hour, dom, month, dow = parts
    if expr.strip() == "* * * * *":
        return "هر دقیقه" if fa else "every minute"
    if minute.startswith("*/") and hour == "*" and dom == "*" and dow == "*":
        step = minute.split("/")[-1]
        return (f"هر {step} دقیقه" if fa else f"every {step} minutes")
    if minute == "0" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "هر ساعت" if fa else "hourly"
    daily = dom == "*" and month == "*" and dow == "*"
    bits: List[str] = []
    if minute.isdigit() and hour.isdigit():
        clock = f"{int(hour):02d}:{int(minute):02d}"
        bits.append(("هر روز " if fa else "daily at ") + clock if daily else clock)
    else:
        bits.append((f"دقیقه {minute} ساعت {hour}" if fa else f"at minute {minute} of hour {hour}"))
    if dow != "*":
        try:
            days = sorted(parse_cron(expr)["weekday"])
            names = WEEKDAYS_FA if fa else WEEKDAYS_EN
            joiner = "، " if fa else ", "
            bits.append(" · " + joiner.join(names[d] for d in days))
        except CronError:
            bits.append(f" · {dow}")
    elif dom != "*":
        bits.append((f" · روز {dom} ماه" if fa else f" · day {dom}"))
    if month != "*":
        bits.append((f" · ماه {month}" if fa else f" · month {month}"))
    return "".join(bits)


PRESETS = (
    ("*/15 * * * *", "هر ۱۵ دقیقه", "every 15 minutes"),
    ("0 * * * *", "هر ساعت", "hourly"),
    ("0 9 * * *", "هر روز ۹ صبح", "daily at 09:00"),
    ("0 9 * * 6", "شنبه‌ها ۹ صبح", "Saturdays at 09:00"),
    ("30 18 * * 0-4", "یکشنبه تا پنجشنبه ۱۸:۳۰", "Sun–Thu at 18:30"),
    ("0 8 1 * *", "اول هر ماه ۸ صبح", "1st of the month, 08:00"),
)


# -------------------------------------------------------------------- schedule
@dataclass
class Schedule:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = ""
    cron: str = "0 9 * * *"
    kind: str = "workflow"          # workflow | tool | macro
    target_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    catch_up: bool = True
    created: float = field(default_factory=time.time)
    last_run: float = 0.0
    last_status: str = ""
    runs: int = 0

    # ------------------------------------------------------------------ helpers
    def title(self, lang: str = "fa") -> str:
        if self.name:
            return self.name
        return self.target_id or ("زمان‌بند" if lang != "en" else "schedule")

    def when(self, lang: str = "fa") -> str:
        return describe_cron(self.cron, lang)

    def next_at(self) -> Optional[datetime]:
        return next_run(self.cron)

    def due(self, now: Optional[datetime] = None) -> bool:
        """True when this schedule should fire for the current minute."""
        if not self.enabled:
            return False
        now = (now or datetime.now()).replace(second=0, microsecond=0)
        if not matches(self.cron, now):
            return False
        stamp = now.timestamp()
        if self.last_run and self.last_run >= stamp:
            return False
        return True

    def catch_up_due(self, now: Optional[datetime] = None) -> bool:
        """Fire late when a window passed while the app was closed."""
        if not self.enabled or not self.catch_up or not self.last_run:
            return False
        now = now or datetime.now()
        if matches(self.cron, now.replace(second=0, microsecond=0)):
            return False
        previous = next_run(self.cron, datetime.fromtimestamp(self.last_run))
        if previous is None:
            return False
        return previous <= now and datetime.fromtimestamp(self.last_run) < previous

    def summary(self, lang: str = "fa") -> str:
        nxt = self.next_at()
        stamp = nxt.strftime("%Y-%m-%d %H:%M") if nxt else "—"
        if lang == "en":
            return f"{self.kind}: {self.target_id} · next {stamp}"
        return f"{self.kind}: {self.target_id} · بعدی {stamp}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "cron": self.cron, "kind": self.kind,
            "target_id": self.target_id, "payload": self.payload, "enabled": self.enabled,
            "catch_up": self.catch_up, "created": self.created, "last_run": self.last_run,
            "last_status": self.last_status, "runs": self.runs,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Schedule":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:10]),
            name=str(data.get("name") or ""),
            cron=str(data.get("cron") or "0 9 * * *"),
            kind=str(data.get("kind") or "workflow"),
            target_id=str(data.get("target_id") or ""),
            payload=dict(data.get("payload") or {}),
            enabled=bool(data.get("enabled", True)),
            catch_up=bool(data.get("catch_up", True)),
            created=float(data.get("created") or time.time()),
            last_run=float(data.get("last_run") or 0),
            last_status=str(data.get("last_status") or ""),
            runs=int(data.get("runs") or 0),
        )


def schedules_path() -> Path:
    return data_dir() / "schedules.json"


class ScheduleStore:
    """Tiny JSON-backed store; safe to call from any thread."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else schedules_path()
        self._items: Optional[List[Schedule]] = None

    def _load(self) -> List[Schedule]:
        if self._items is not None:
            return self._items
        self._items = []
        try:
            if self._path.is_file():
                raw = json.loads(self._path.read_text(encoding="utf-8") or "[]")
                self._items = [Schedule.from_dict(item) for item in raw]
        except (OSError, json.JSONDecodeError):
            self._items = []
        return self._items

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps([item.as_dict() for item in self._load()],
                                             ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def all(self) -> List[Schedule]:
        return list(self._load())

    def get(self, schedule_id: str) -> Optional[Schedule]:
        for item in self._load():
            if item.id == schedule_id:
                return item
        return None

    def new(self) -> Schedule:
        item = Schedule()
        self._load().insert(0, item)
        self._save()
        return item

    def save(self, item: Schedule) -> Schedule:
        items = self._load()
        for index, existing in enumerate(items):
            if existing.id == item.id:
                items[index] = item
                break
        else:
            items.insert(0, item)
        self._save()
        return item

    def delete(self, schedule_id: str) -> None:
        self._items = [item for item in self._load() if item.id != schedule_id]
        self._save()

    def mark_run(self, schedule_id: str, status: str) -> None:
        item = self.get(schedule_id)
        if item is None:
            return
        item.last_run = time.time()
        item.last_status = status
        item.runs += 1
        self.save(item)

    def clear(self) -> None:
        self._items = []
        self._save()


store = ScheduleStore()


# ------------------------------------------------------------------- scheduler
class Scheduler(QObject):
    """Fires `triggered(schedule)` once per matching minute."""

    triggered = Signal(object)
    ticked = Signal(float)

    def __init__(self, store_ref: Optional[ScheduleStore] = None, interval_ms: int = 20_000,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.store = store_ref or store
        self.interval_ms = max(5_000, int(interval_ms))
        self._timer = QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self.tick)
        self.running = False

    # ----------------------------------------------------------------- control
    def start(self) -> None:
        if not self.running:
            self._timer.start()
            self.running = True
        self.tick()

    def stop(self) -> None:
        self._timer.stop()
        self.running = False

    # -------------------------------------------------------------------- tick
    def tick(self, now: Optional[datetime] = None) -> List[Schedule]:
        now = now or datetime.now()
        fired: List[Schedule] = []
        for item in self.store.all():
            if item.due(now) or item.catch_up_due(now):
                fired.append(item)
                self.triggered.emit(item)
        self.ticked.emit(time.time())
        return fired

    def next_runs(self, limit: int = 5) -> List[tuple]:
        """(when, schedule) pairs for the agenda view."""
        pairs = []
        for item in self.store.all():
            if not item.enabled:
                continue
            nxt = item.next_at()
            if nxt is not None:
                pairs.append((nxt, item))
        pairs.sort(key=lambda pair: pair[0])
        return pairs[:limit]
