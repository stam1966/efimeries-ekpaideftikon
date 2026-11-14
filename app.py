# -*- coding: utf-8 -*-
"""
Εφημερίες Εκπαιδευτικών
Αρχική ιδέα / επιμέλεια: Σταμάτης Κοκολόγος
Υλοποίηση: με αξιοποίηση εργαλείων τεχνητής νοημοσύνης

Τρέχει με Python 3.10+ και μπορεί να πακεταριστεί σε Windows .exe με PyInstaller.
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys
import webbrowser
import subprocess
import html
import json
import calendar
import itertools
import functools
import io
import shutil
import re
import zipfile
import tempfile
import socket
import time
import struct
import logging
import logging.handlers
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

APP_TITLE = "Εφημερίες Εκπαιδευτικών"
APP_VERSION = "0.70.2"
UPDATE_REPO = "stam1966/efimeries-ekpaideftikon"  # αποθετήριο GitHub από όπου ελέγχονται οι νέες εκδόσεις

# Όρια εργασίας του εβδομαδιαίου σχεδιασμού (προστατεύουν από πολύ αργούς υπολογισμούς σε μεγάλα σχολεία).
# Όταν υπάρχει ήδη έγκυρη λύση, η αναζήτηση σταματά στο όριο· σε μικρά/μεσαία σχολεία δεν επηρεάζονται τα αποτελέσματα.
COMBO_LIMIT = 40_000
NODE_BUDGET = 25_000  # συνολικά βήματα αναζήτησης ανά εβδομάδα, αφού βρεθεί έγκυρη λύση


class GenerationCancelled(Exception):
    """Ο χρήστης ακύρωσε την παραγωγή από το παράθυρο προόδου."""
APP_ARCH = f"x{struct.calcsize('P') * 8}"
DAYS_GR = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή"]
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri"]


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_PATH = app_dir() / "storage_config.json"
STORAGE_ROOT = app_dir()
DATA_DIR = STORAGE_ROOT / "data"
EXPORT_DIR = STORAGE_ROOT / "exports"
BACKUP_DIR = STORAGE_ROOT / "backups"
LEGACY_DB_PATH = DATA_DIR / "efimeries.db"
YEARS_DIR = DATA_DIR / "years"
ACTIVE_YEAR_FILE = DATA_DIR / "active_school_year.txt"
LOCK_FILE = STORAGE_ROOT / ".efimeries.lock"
MIN_SCHOOL_YEAR_START = 2026




def resource_path(name: str) -> Path:
    """Path to a bundled resource, with fallback next to the executable."""
    bundled = Path(getattr(sys, "_MEIPASS", app_dir())) / name
    if bundled.exists():
        return bundled
    return app_dir() / name


# ---- Καταγραφή σφαλμάτων --------------------------------------------------------
LOG = logging.getLogger("efimeries")
LOG_PATH: Optional[Path] = None


def setup_logging() -> None:
    """Αρχείο καταγραφής logs/efimeries.log δίπλα στην εφαρμογή (εναλλακτικά στον προσωρινό φάκελο)."""
    global LOG_PATH
    if LOG.handlers:
        return
    LOG.setLevel(logging.INFO)
    for base in (app_dir(), Path(tempfile.gettempdir())):
        try:
            logdir = base / "logs"
            logdir.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(logdir / "efimeries.log", maxBytes=500_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            LOG.addHandler(handler)
            LOG_PATH = logdir / "efimeries.log"
            LOG.info("Εκκίνηση v%s %s", APP_VERSION, APP_ARCH)
            return
        except Exception:
            continue


# ---- Κλείδωμα κοινόχρηστου φακέλου ------------------------------------------------
LOCK_HEARTBEAT_MS = 30_000      # ανανέωση ένδειξης «είμαι ζωντανή» κάθε 30"
LOCK_STALE_SECONDS = 180        # χωρίς ανανέωση για 3' => πιθανώς παλιό κλείδωμα


def pid_alive(pid) -> bool:
    """Ασφαλής έλεγχος αν υπάρχει διεργασία με αυτό το PID.

    ΠΡΟΣΟΧΗ: στα Windows το os.kill(pid, 0) ΔΕΝ είναι έλεγχος· τερματίζει τη διεργασία
    (TerminateProcess). Γι' αυτό χρησιμοποιείται το Windows API μόνο για ανάγνωση κατάστασης.
    Αν υπάρχει αμφιβολία επιστρέφεται True (πιο ασφαλές: το κλείδωμα θεωρείται ενεργό).
    """
    try:
        if not isinstance(pid, int) or pid <= 0:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            ERROR_ACCESS_DENIED = 5
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            k32.GetExitCodeProcess.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return ctypes.get_last_error() == ERROR_ACCESS_DENIED
            try:
                code = wintypes.DWORD()
                if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                k32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    except Exception:
        return True


# ---- Έλεγχος backup πριν την επαναφορά ---------------------------------------------
REQUIRED_BACKUP_TABLES = {"settings", "teachers", "areas", "duty_intervals", "assignments"}


def validate_backup_db(data: bytes) -> Optional[str]:
    """Επιστρέφει None αν η βάση του backup είναι υγιής, αλλιώς μήνυμα για τον χρήστη."""
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "check.db"
        probe.write_bytes(data)
        conn = None
        try:
            conn = sqlite3.connect(str(probe))
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                return "Η βάση μέσα στο backup είναι κατεστραμμένη (αποτυχία ελέγχου ακεραιότητας)."
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            missing = REQUIRED_BACKUP_TABLES - tables
            if missing:
                return "Το backup δεν περιέχει τους απαραίτητους πίνακες: " + ", ".join(sorted(missing))
            return None
        except sqlite3.DatabaseError as exc:
            return f"Το αρχείο της βάσης στο backup δεν είναι έγκυρο: {exc}"
        finally:
            if conn is not None:
                conn.close()


def version_tuple(text: str) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(text).split("."))
    except Exception:
        return (0,)


def set_storage_root(root: Path) -> None:
    global STORAGE_ROOT, DATA_DIR, EXPORT_DIR, BACKUP_DIR, LEGACY_DB_PATH, YEARS_DIR, ACTIVE_YEAR_FILE, LOCK_FILE
    STORAGE_ROOT = Path(root).expanduser().resolve()
    DATA_DIR = STORAGE_ROOT / "data"
    EXPORT_DIR = STORAGE_ROOT / "exports"
    BACKUP_DIR = STORAGE_ROOT / "backups"
    LEGACY_DB_PATH = DATA_DIR / "efimeries.db"
    YEARS_DIR = DATA_DIR / "years"
    ACTIVE_YEAR_FILE = DATA_DIR / "active_school_year.txt"
    LOCK_FILE = STORAGE_ROOT / ".efimeries.lock"


def installed_data_root() -> Path:
    """Φάκελος δεδομένων της εγκατεστημένης εφαρμογής: Έγγραφα\\Εφημερίες Εκπαιδευτικών."""
    docs = None
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
                docs = Path(buf.value)
        except Exception:
            docs = None
    return (docs or (Path.home() / "Documents")) / "Εφημερίες Εκπαιδευτικών"


# ---- Σουίτα «Σχολικές Εφαρμογές» (Ωρολόγιο Σχολείου + Εφημερίες) ----------------------
# Ρυθμίσεις του υπολογιστή: %APPDATA%\SchoolApps\suite.json {"root": φάκελος σουίτας, "role": ...} (τις γράφει
# το Setup της σουίτας ή το Ωρολόγιο → Ρυθμίσεις εφαρμογής → Κοινή χρήση). Με φάκελο σουίτας τα δεδομένα των
# Εφημεριών μένουν στο <φάκελος>\Εφημερίες και το Ωρολόγιο γράφει στο <φάκελος>\Κοινά το πρόγραμμα και τις απουσίες.
SUITE_EF_DIR, SUITE_COMMON = "Εφημερίες", "Κοινά"


def suite_config_path() -> Path:
    if os.environ.get("SCHOOLAPPS_CONFIG"):
        return Path(os.environ["SCHOOLAPPS_CONFIG"])
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "SchoolApps" / "suite.json"


def suite_machine() -> dict:
    try:
        d = json.loads(suite_config_path().read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def suite_root() -> Optional[Path]:
    r = str(suite_machine().get("root") or "").strip()
    return Path(r) if r else None


def suite_common_dir() -> Path:
    r = suite_root()
    return r / SUITE_COMMON if r else suite_config_path().parent / SUITE_COMMON


def _read_common(name: str) -> Optional[dict]:
    try:
        d = json.loads((suite_common_dir() / name).read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def orologio_timetable() -> Optional[dict]:
    """Το στιγμιότυπο του «Ωρολογίου Σχολείου» στη μορφή της εισαγωγής ωρολογίου:
    {'source', 'periods': {αριθμός: (από, έως)}, 'teachers': {όνομα: {ημέρα: [ώρες]}}, 'meta': {...}}."""
    d = _read_common("orologio.json")
    if not d or d.get("format") != "orologio-snapshot":
        return None
    periods = {int(p["index"]) + 1: (_hhmm(p.get("start")), _hhmm(p.get("end"))) for p in d.get("periods", []) if "index" in p}
    teachers: Dict[str, Dict[int, List[int]]] = {}
    for t in d.get("teachers", []):
        if not t.get("active", True) or not str(t.get("name", "")).strip():
            continue
        days = {wd: [k + 1 for k in ks] for wd, ks in enumerate(t.get("periods", [])[:5]) if ks}
        if days:
            teachers[str(t["name"]).strip()] = days
    return {"source": "Ωρολόγιο Σχολείου", "periods": periods, "teachers": teachers,
            "meta": {"updated": str(d.get("updated", "")), "school": str(d.get("school", "")), "year": str(d.get("year", "")), "count": len(teachers)}}


def orologio_launcher() -> Optional[List[str]]:
    """Εντολή εκκίνησης του «Ωρολογίου Σχολείου» αν είναι εγκατεστημένο σε αυτόν τον υπολογιστή."""
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Programs" / "OrologioDimotikou"
    exe = base / "runtime" / "pythonw.exe"
    if exe.exists() and (base / "src" / "server.py").exists():
        return [str(exe), "-X", "utf8", str(base / "src" / "server.py")]
    return None


def orologio_absences() -> List[dict]:
    """Απουσίες από τις Αναπληρώσεις του Ωρολογίου, ενωμένες σε διαστήματα συνεχόμενων εργάσιμων ημερών
    ανά εκπαιδευτικό: [{'name', 'from', 'to', 'full': όλη η ημέρα, 'periods', 'reason', 'key'}]."""
    d = _read_common("apousies.json")
    if not d or d.get("format") != "orologio-absences":
        return []
    by: Dict[Tuple[str, bool, str], List[date]] = {}
    info: Dict[Tuple[str, bool, str], dict] = {}
    for a in d.get("absences", []):
        try:
            day = date.fromisoformat(str(a.get("date")))
        except ValueError:
            continue
        name = str(a.get("name", "")).strip()
        if not name:
            continue
        full = a.get("periods") is None
        k = (name, full, str(a.get("reason", "") or ""))
        by.setdefault(k, []).append(day)
        info.setdefault(k, {"periods": {}})["periods"][day.isoformat()] = a.get("periods")
    out = []
    for (name, full, reason), days in by.items():
        days = sorted(set(days))
        start = prev = days[0]
        for dd in days[1:] + [None]:
            nxt = prev + timedelta(days=1)
            while nxt.weekday() > 4:
                nxt += timedelta(days=1)
            if dd is not None and dd == nxt:
                prev = dd
                continue
            per = None if full else sorted({p + 1 for x in (start + timedelta(days=i) for i in range((prev - start).days + 1))
                                            for p in (info[(name, full, reason)]["periods"].get(x.isoformat()) or [])})
            out.append({"name": name, "from": start.isoformat(), "to": prev.isoformat(), "full": full, "periods": per, "reason": reason,
                        "key": f"{name}|{start.isoformat()}|{prev.isoformat()}|{int(full)}"})
            if dd is not None:
                start = prev = dd
    out.sort(key=lambda x: (x["from"], x["name"]))
    return out


def _legacy_storage_root() -> Path:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            value = str(cfg.get("storage_root", "")).strip()
            if value:
                return Path(value)
        except Exception:
            pass
    # Εγκατεστημένη έκδοση (Setup.exe): ο φάκελος της εφαρμογής έχει το αρχείο installed.flag.
    # Τα δεδομένα μένουν στα Έγγραφα, κοινά για x64 και x86, και δεν χάνονται σε ενημέρωση ή απεγκατάσταση.
    try:
        if (app_dir() / "installed.flag").exists():
            return installed_data_root()
    except Exception:
        pass
    # Αν οι x86/x64 βρίσκονται σε αδελφούς φακέλους και υπάρχει Shared,
    # τον αναγνωρίζουμε αυτόματα στην πρώτη εκκίνηση.
    here = app_dir()
    for parent in [here.parent, here.parent.parent, here.parent.parent.parent]:
        candidate = parent / "Shared"
        try:
            if candidate.exists() and ((candidate / "data").exists() or (candidate / "backups").exists()):
                return candidate
        except Exception:
            pass
    return app_dir()


def load_storage_root() -> Path:
    """Φάκελος δεδομένων: της σουίτας αν έχει οριστεί (με μεταφορά των υπαρχόντων δεδομένων την πρώτη φορά),
    αλλιώς όπως πριν."""
    legacy = _legacy_storage_root()
    r = suite_root()
    if not r:
        return legacy
    target = r / SUITE_EF_DIR
    try:
        if not (target / "data").exists() and (legacy / "data").exists() and legacy.resolve() != target.resolve():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(legacy / "data", target / "data")
            LOG.info("Σουίτα: μεταφορά δεδομένων από %s στο %s", legacy, target)
    except Exception:
        LOG.exception("Σουίτα: αποτυχία μεταφοράς δεδομένων")
    return target


def save_storage_root(root: Path) -> None:
    CONFIG_PATH.write_text(json.dumps({"storage_root": str(Path(root).resolve())}, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_dirs() -> None:
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    YEARS_DIR.mkdir(parents=True, exist_ok=True)


def normalize_school_year(value: str) -> str:
    value = (value or "").strip().replace("–", "-").replace("—", "-")
    m = re.fullmatch(r"(\d{4})-(\d{4})", value)
    if not m:
        raise ValueError("Το σχολικό έτος πρέπει να είναι της μορφής 2026-2027.")
    y1, y2 = int(m.group(1)), int(m.group(2))
    if y2 != y1 + 1:
        raise ValueError("Το δεύτερο έτος πρέπει να είναι το αμέσως επόμενο.")
    if y1 < MIN_SCHOOL_YEAR_START:
        raise ValueError("Η εφαρμογή κρατά σχολικά έτη από το 2026-2027 και μετά.")
    return f"{y1}-{y2}"


def year_db_path(school_year: str) -> Path:
    sy = normalize_school_year(school_year)
    return YEARS_DIR / f"efimeries_{sy}.db"


def discover_school_years() -> List[str]:
    years: List[str] = []
    if YEARS_DIR.exists():
        for path in YEARS_DIR.glob("efimeries_*.db"):
            raw = path.stem.replace("efimeries_", "", 1)
            try:
                years.append(normalize_school_year(raw))
            except Exception:
                continue
    return sorted(set(years))


def read_legacy_school_year(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT value FROM settings WHERE key='school_year'").fetchone()
        conn.close()
        if row and row[0]:
            return normalize_school_year(str(row[0]))
    except Exception:
        return None
    return None


def prepare_year_storage() -> str:
    """Προετοιμάζει απομονωμένη βάση ανά σχολικό έτος.

    Αν υπάρχει βάση παλαιότερης έκδοσης και δηλώνει 2026-2027 ή νεότερο,
    αντιγράφεται μία φορά στον νέο φάκελο years. Παλαιότερα έτη δεν εμφανίζονται.
    """
    ensure_dirs()
    years = discover_school_years()
    if not years and LEGACY_DB_PATH.exists():
        legacy_year = read_legacy_school_year(LEGACY_DB_PATH)
        if legacy_year:
            target = year_db_path(legacy_year)
            if not target.exists():
                shutil.copy2(LEGACY_DB_PATH, target)
            years = discover_school_years()

    active = None
    if ACTIVE_YEAR_FILE.exists():
        try:
            active = normalize_school_year(ACTIVE_YEAR_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            active = None
    if active not in years:
        active = years[-1] if years else "2026-2027"
    ACTIVE_YEAR_FILE.write_text(active, encoding="utf-8")
    return active


def parse_date(s: str) -> date:
    s = (s or "").strip().replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError("Η ημερομηνία πρέπει να είναι της μορφής ΗΗ/ΜΜ/ΕΕΕΕ, π.χ. 11/09/2026")


def fmt_date(d: date | str) -> str:
    if isinstance(d, str):
        d = parse_date(d)
    return d.strftime("%d/%m/%Y")


def ui_date(value) -> str:
    """Ημερομηνία για εμφάνιση στον χρήστη: ΗΗ/ΜΜ/ΕΕΕΕ. Αν δεν διαβάζεται, επιστρέφεται όπως είναι."""
    try:
        return fmt_date(value)
    except Exception:
        return str(value or "")


def minutes_between(start: str, end: str) -> int:
    t1 = datetime.strptime(start, "%H:%M")
    t2 = datetime.strptime(end, "%H:%M")
    return int((t2 - t1).total_seconds() // 60)


@dataclass
class Teacher:
    id: int
    name: str
    participates: int
    active: int
    mon: int
    tue: int
    wed: int
    thu: int
    fri: int
    primary_day: int = -1
    alternate_day: int = -1
    extra_cycle: int = 1
    notes: str = ""

    def available_on(self, weekday: int) -> bool:
        if weekday < 0 or weekday > 4:
            return False
        return bool([self.mon, self.tue, self.wed, self.thu, self.fri][weekday])


class DB:
    def __init__(self, path: Path, school_year: str = "2026-2027"):
        self.path = path
        self.school_year = normalize_school_year(school_year)
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.init()

    def init(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS teachers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                participates INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                mon INTEGER NOT NULL DEFAULT 1,
                tue INTEGER NOT NULL DEFAULT 1,
                wed INTEGER NOT NULL DEFAULT 1,
                thu INTEGER NOT NULL DEFAULT 1,
                fri INTEGER NOT NULL DEFAULT 1,
                primary_day INTEGER NOT NULL DEFAULT -1,
                alternate_day INTEGER NOT NULL DEFAULT -1,
                extra_cycle INTEGER NOT NULL DEFAULT 1,
                notes TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS areas(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS exceptions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                d TEXT NOT NULL UNIQUE,
                end_d TEXT DEFAULT '',
                label TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'Εξαίρεση',
                config TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS duty_intervals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS teacher_interval_availability(
                teacher_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL,
                interval_id INTEGER NOT NULL,
                available INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(teacher_id, weekday, interval_id),
                FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE,
                FOREIGN KEY(interval_id) REFERENCES duty_intervals(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS assignments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                d TEXT NOT NULL,
                weekday TEXT NOT NULL,
                interval_name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                area_name TEXT NOT NULL,
                teacher_id INTEGER NOT NULL,
                minutes INTEGER NOT NULL,
                mode TEXT NOT NULL,
                locked INTEGER NOT NULL DEFAULT 0,
                entry_type TEXT NOT NULL DEFAULT 'auto',
                FOREIGN KEY(teacher_id) REFERENCES teachers(id)
            );
            CREATE TABLE IF NOT EXISTS duty_changes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL,
                changed_at TEXT NOT NULL,
                change_type TEXT NOT NULL,
                old_teacher_id INTEGER,
                new_teacher_id INTEGER,
                old_teacher_name TEXT DEFAULT '',
                new_teacher_name TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                FOREIGN KEY(assignment_id) REFERENCES assignments(id)
            );
            CREATE TABLE IF NOT EXISTS absences(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                d_from TEXT NOT NULL,
                d_to TEXT NOT NULL,
                reason TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS shifts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                has_reserve INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS shift_interval_map(
                shift_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL DEFAULT -1,
                interval_id INTEGER NOT NULL,
                PRIMARY KEY(shift_id, weekday, interval_id),
                FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE,
                FOREIGN KEY(interval_id) REFERENCES duty_intervals(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS shift_area_map(
                shift_id INTEGER NOT NULL,
                area_id INTEGER NOT NULL,
                PRIMARY KEY(shift_id, area_id),
                FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE,
                FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS teacher_shift_availability(
                teacher_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL,
                shift_id INTEGER NOT NULL,
                available INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(teacher_id, weekday, shift_id),
                FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE,
                FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS teacher_alternate_days(
                teacher_id INTEGER NOT NULL,
                priority INTEGER NOT NULL,
                weekday INTEGER NOT NULL,
                PRIMARY KEY(teacher_id, priority),
                UNIQUE(teacher_id, weekday),
                FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS teacher_shift_area_permissions(
                teacher_id INTEGER NOT NULL,
                area_id INTEGER NOT NULL,
                PRIMARY KEY(teacher_id, area_id),
                FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE,
                FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS shift_reserves(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                d TEXT NOT NULL,
                shift_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL,
                activated INTEGER NOT NULL DEFAULT 0,
                replacement_assignment_id INTEGER,
                UNIQUE(d, shift_id),
                FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE,
                FOREIGN KEY(teacher_id) REFERENCES teachers(id),
                FOREIGN KEY(replacement_assignment_id) REFERENCES assignments(id)
            );
            """
        )
        # Ασφαλής μικρή μετανάστευση για παλαιότερες βάσεις που δεν έχουν τη στήλη locked.
        cols = [r[1] for r in cur.execute("PRAGMA table_info(assignments)").fetchall()]
        if "locked" not in cols:
            cur.execute("ALTER TABLE assignments ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
        if "entry_type" not in cols:
            cur.execute("ALTER TABLE assignments ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'auto'")
        if "area_id" not in cols:
            cur.execute("ALTER TABLE assignments ADD COLUMN area_id INTEGER")
        # Migration: συνδέουμε παλιές αναθέσεις με σταθερό ID τομέα με βάση την παλιά ονομασία.
        cur.execute("""
            UPDATE assignments
               SET area_id=(SELECT ar.id FROM areas ar WHERE ar.name=assignments.area_name LIMIT 1)
             WHERE area_id IS NULL
        """)
        ex_cols = [r[1] for r in cur.execute("PRAGMA table_info(exceptions)").fetchall()]
        if "config" not in ex_cols:
            cur.execute("ALTER TABLE exceptions ADD COLUMN config TEXT DEFAULT ''")
        if "end_d" not in ex_cols:
            cur.execute("ALTER TABLE exceptions ADD COLUMN end_d TEXT DEFAULT ''")
        # Παλαιές εγγραφές ήταν πάντα μίας ημέρας. Το end_d συμπληρώνεται με την ίδια ημερομηνία.
        cur.execute("UPDATE exceptions SET end_d=d WHERE COALESCE(end_d,'')='' ")
        teacher_cols = [r[1] for r in cur.execute("PRAGMA table_info(teachers)").fetchall()]
        if "primary_day" not in teacher_cols:
            cur.execute("ALTER TABLE teachers ADD COLUMN primary_day INTEGER NOT NULL DEFAULT -1")
        if "alternate_day" not in teacher_cols:
            cur.execute("ALTER TABLE teachers ADD COLUMN alternate_day INTEGER NOT NULL DEFAULT -1")
        if "extra_cycle" not in teacher_cols:
            cur.execute("ALTER TABLE teachers ADD COLUMN extra_cycle INTEGER NOT NULL DEFAULT 1")
        cur.execute("""
            INSERT OR IGNORE INTO teacher_alternate_days(teacher_id,priority,weekday)
            SELECT id,1,alternate_day FROM teachers WHERE alternate_day BETWEEN 0 AND 4
        """)
        self.conn.commit()
        self.seed_defaults()

    def seed_defaults(self) -> None:
        start_year = int(self.school_year.split("-")[0])
        defaults = {
            "school_year": self.school_year,
            "start_date": f"{start_year}-09-11",
            "end_date": f"{start_year + 1}-06-15",
            "mode": "daily",
            "school_name": "22ο Δημοτικό Σχολείο Λαμίας – Ροδίτσας",
            "school_phone": "",
            "school_email": "",
            "print_size": "normal",
        }
        for k, v in defaults.items():
            self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        # Η βάση είναι απομονωμένη ανά σχολικό έτος, άρα το όνομα του έτους
        # πρέπει πάντα να συμφωνεί με το αρχείο που είναι ανοιχτό.
        self.conn.execute("INSERT INTO settings(key,value) VALUES('school_year',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.school_year,))
        area_count = self.conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0]
        if area_count == 0:
            self.conn.executemany(
                "INSERT INTO areas(name,description,active,sort_order) VALUES(?,?,1,?)",
                [
                    ("Ανατολικά (Α)", "Γήπεδο χάντμπολ, χώρος πρωινής προσευχής, χώρος γκαζόν, διάδρομος εισόδου.", 1),
                    ("Νότια (Ν)", "Γήπεδα μπάσκετ, βόλεϊ, υπόστεγο, βρύσες.", 2),
                ],
            )
        interval_count = self.conn.execute("SELECT COUNT(*) FROM duty_intervals").fetchone()[0]
        if interval_count == 0:
            self.conn.executemany(
                "INSERT INTO duty_intervals(name,start_time,end_time,active,sort_order) VALUES(?,?,?,?,?)",
                [
                    ("Προσέλευση", "08:00", "08:15", 1, 1),
                    ("1ο διάλειμμα", "09:40", "10:00", 1, 2),
                    ("2ο διάλειμμα", "11:30", "11:45", 1, 3),
                    ("3ο διάλειμμα", "12:25", "12:35", 1, 4),
                    ("Αποχώρηση", "13:15", "13:20", 1, 5),
                ],
            )
        shift_count = self.conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
        if shift_count == 0:
            self.conn.executemany(
                "INSERT INTO shifts(name,active,sort_order,has_reserve) VALUES(?,?,?,?)",
                [("1η βάρδια",1,1,1),("2η βάρδια",1,2,1)],
            )
        self.conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

    def teachers(self) -> List[Teacher]:
        rows = self.conn.execute("SELECT * FROM teachers ORDER BY name").fetchall()
        return [Teacher(**dict(r)) for r in rows]

    def active_teachers(self) -> List[Teacher]:
        return [t for t in self.teachers() if t.active and t.participates]

    def areas(self, active_only: bool = False) -> List[sqlite3.Row]:
        q = "SELECT * FROM areas"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY sort_order, name"
        return self.conn.execute(q).fetchall()

    def intervals(self, active_only: bool = False) -> List[sqlite3.Row]:
        q = "SELECT * FROM duty_intervals"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY sort_order, start_time"
        return self.conn.execute(q).fetchall()

    def shifts(self, active_only: bool = False) -> List[sqlite3.Row]:
        q = "SELECT * FROM shifts"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY sort_order,name"
        return self.conn.execute(q).fetchall()

    def shift_intervals(self, shift_id: int, weekday: int) -> List[sqlite3.Row]:
        # Αν υπάρχουν ειδικές ρυθμίσεις για τη συγκεκριμένη ημέρα, υπερισχύουν των γενικών (-1).
        specific = self.conn.execute(
            "SELECT COUNT(*) FROM shift_interval_map WHERE shift_id=? AND weekday=?", (shift_id, weekday)
        ).fetchone()[0]
        wd = weekday if specific else -1
        return self.conn.execute(
            """SELECT di.* FROM duty_intervals di
               JOIN shift_interval_map m ON m.interval_id=di.id
               WHERE m.shift_id=? AND m.weekday=? AND di.active=1
               ORDER BY di.sort_order,di.start_time""", (shift_id, wd)
        ).fetchall()

    def shift_areas(self, shift_id: int) -> List[sqlite3.Row]:
        rows = self.conn.execute(
            """SELECT a.* FROM areas a JOIN shift_area_map m ON m.area_id=a.id
               WHERE m.shift_id=? AND a.active=1 ORDER BY a.sort_order,a.name""", (shift_id,)
        ).fetchall()
        return rows if rows else self.areas(True)

    def teacher_shift_available(self, teacher: Teacher, weekday: int, shift_id: int) -> bool:
        if not teacher.available_on(weekday):
            return False
        row = self.conn.execute(
            "SELECT available FROM teacher_shift_availability WHERE teacher_id=? AND weekday=? AND shift_id=?",
            (teacher.id, weekday, shift_id),
        ).fetchone()
        return True if row is None else bool(row["available"])

    def get_teacher_shift_availability(self, teacher_id: int) -> Dict[Tuple[int,int], int]:
        rows = self.conn.execute(
            "SELECT weekday,shift_id,available FROM teacher_shift_availability WHERE teacher_id=?", (teacher_id,)
        ).fetchall()
        return {(int(r["weekday"]),int(r["shift_id"])):int(r["available"]) for r in rows}

    def set_teacher_shift_availability(self, teacher_id: int, values: Dict[Tuple[int,int], int]) -> None:
        self.conn.execute("DELETE FROM teacher_shift_availability WHERE teacher_id=?", (teacher_id,))
        self.conn.executemany(
            "INSERT INTO teacher_shift_availability(teacher_id,weekday,shift_id,available) VALUES(?,?,?,?)",
            [(teacher_id,wd,sid,int(av)) for (wd,sid),av in values.items()]
        )
        self.conn.commit()

    def teacher_alternate_days(self, teacher_id: int, legacy_alternate: int = -1) -> List[int]:
        rows = self.conn.execute(
            "SELECT weekday FROM teacher_alternate_days WHERE teacher_id=? ORDER BY priority", (teacher_id,)
        ).fetchall()
        vals = [int(r[0]) for r in rows if 0 <= int(r[0]) <= 4]
        if not vals and 0 <= int(legacy_alternate) <= 4:
            vals = [int(legacy_alternate)]
        return vals

    def set_teacher_alternate_days(self, teacher_id: int, weekdays: List[int]) -> None:
        clean=[]
        for wd in weekdays:
            try: wd=int(wd)
            except Exception: continue
            if 0 <= wd <= 4 and wd not in clean:
                clean.append(wd)
        self.conn.execute("DELETE FROM teacher_alternate_days WHERE teacher_id=?", (teacher_id,))
        self.conn.executemany(
            "INSERT INTO teacher_alternate_days(teacher_id,priority,weekday) VALUES(?,?,?)",
            [(teacher_id,i+1,wd) for i,wd in enumerate(clean)]
        )
        first = clean[0] if clean else -1
        self.conn.execute("UPDATE teachers SET alternate_day=? WHERE id=?", (first, teacher_id))
        self.conn.commit()

    def teacher_shift_allowed_areas(self, teacher_id: int) -> set:
        rows = self.conn.execute(
            "SELECT area_id FROM teacher_shift_area_permissions WHERE teacher_id=?", (teacher_id,)
        ).fetchall()
        return {int(r[0]) for r in rows}

    def teacher_allowed_for_shift_area(self, teacher_id: int, area_id: int) -> bool:
        allowed = self.teacher_shift_allowed_areas(teacher_id)
        return (not allowed) or (int(area_id) in allowed)

    def set_teacher_shift_allowed_areas(self, teacher_id: int, area_ids: List[int], all_area_ids: List[int]) -> None:
        selected={int(x) for x in area_ids}
        all_ids={int(x) for x in all_area_ids}
        self.conn.execute("DELETE FROM teacher_shift_area_permissions WHERE teacher_id=?", (teacher_id,))
        if selected and selected != all_ids:
            self.conn.executemany(
                "INSERT INTO teacher_shift_area_permissions(teacher_id,area_id) VALUES(?,?)",
                [(teacher_id,a) for a in sorted(selected)]
            )
        self.conn.commit()

    def teacher_interval_available(self, teacher: Teacher, weekday: int, interval_id: int) -> bool:
        """Ελέγχει διαθεσιμότητα εκπαιδευτικού για συγκεκριμένη ημέρα + χρονική ζώνη.
        Αν δεν υπάρχει ειδική ρύθμιση, ισχύει η γενική διαθεσιμότητα της ημέρας.
        """
        if not teacher.available_on(weekday):
            return False
        row = self.conn.execute(
            "SELECT available FROM teacher_interval_availability WHERE teacher_id=? AND weekday=? AND interval_id=?",
            (teacher.id, weekday, interval_id),
        ).fetchone()
        if row is None:
            return True
        return bool(row["available"])

    def set_teacher_interval_availability(self, teacher_id: int, values: Dict[Tuple[int, int], int]) -> None:
        self.conn.execute("DELETE FROM teacher_interval_availability WHERE teacher_id=?", (teacher_id,))
        self.conn.executemany(
            "INSERT INTO teacher_interval_availability(teacher_id,weekday,interval_id,available) VALUES(?,?,?,?)",
            [(teacher_id, wd, iid, int(av)) for (wd, iid), av in values.items()],
        )
        self.conn.commit()

    def get_teacher_interval_availability(self, teacher_id: int) -> Dict[Tuple[int, int], int]:
        rows = self.conn.execute(
            "SELECT weekday,interval_id,available FROM teacher_interval_availability WHERE teacher_id=?",
            (teacher_id,),
        ).fetchall()
        return {(int(r["weekday"]), int(r["interval_id"])): int(r["available"]) for r in rows}

    def teacher_interval_availability_summary(self, teacher_id: int) -> str:
        """Σύντομη ένδειξη για το αν έχει οριστεί ειδική διαθεσιμότητα ανά διάλειμμα."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN available=0 THEN 1 ELSE 0 END) AS unavailable FROM teacher_interval_availability WHERE teacher_id=?",
            (teacher_id,),
        ).fetchone()
        total = int(row["total"] or 0) if row else 0
        unavailable = int(row["unavailable"] or 0) if row else 0
        if total == 0:
            return "Γενική ημέρας"
        if unavailable == 0:
            return "Όλα διαθέσιμα"
        return f"{unavailable} μη διαθέσιμα"

    def exception_dates(self) -> Dict[str, sqlite3.Row]:
        """Επιστρέφει χάρτη ημερομηνία -> εγγραφή εξαίρεσης.

        Οι εγγραφές μπορούν να είναι μίας ημέρας ή διαστήματος. Για συμβατότητα
        με τον υπόλοιπο αλγόριθμο το διάστημα αναπτύσσεται σε επιμέρους ημερομηνίες.
        Σε τυχόν επικάλυψη υπερισχύει η νεότερη εγγραφή (μεγαλύτερο id).
        """
        result: Dict[str, sqlite3.Row] = {}
        rows = self.conn.execute("SELECT * FROM exceptions ORDER BY id").fetchall()
        for r in rows:
            try:
                start = parse_date(r["d"])
                end_raw = r["end_d"] if "end_d" in r.keys() else ""
                end = parse_date(end_raw) if end_raw else start
            except Exception:
                continue
            if end < start:
                start, end = end, start
            cur = start
            while cur <= end:
                result[cur.isoformat()] = r
                cur += timedelta(days=1)
        return result

    def exception_config(self, ex_row) -> dict:
        if not ex_row:
            return {}
        try:
            return json.loads(ex_row["config"] or "{}")
        except Exception:
            return {}





# ---------------------------------------------------------------- μηνύματα και διάλογοι
# Η εφαρμογή δεν έχει πια κλασική διεπαφή (Tkinter): όλα γίνονται από τη διεπαφή στον browser (webui.py).
# Τα μηνύματα της λογικής, όταν εκτελούνται για λογαριασμό της διεπαφής, συγκεντρώνονται και επιστρέφονται
# σε αυτήν (collector)· πριν ανοίξει η διεπαφή (π.χ. κλείδωμα δεδομένων στην εκκίνηση) εμφανίζονται
# ως απλά παράθυρα μηνυμάτων των Windows.
class _Dialogs:
    def __init__(self):
        self.collector: Optional[List[str]] = None

    @staticmethod
    def _native(title: str, message: str, flags: int) -> int:
        LOG.info("%s: %s", title, message)
        if sys.platform.startswith("win"):
            try:
                import ctypes
                return int(ctypes.windll.user32.MessageBoxW(0, str(message), str(title or APP_TITLE), flags | 0x40000))
            except Exception:
                pass
        return 0

    def _note(self, title, message, flags):
        if self.collector is not None:
            self.collector.append(f"{title}: {message}".strip(": "))
            return None
        self._native(title, message, flags)
        return None

    def showinfo(self, title="", message="", **_k):
        return self._note(title, message, 0x40)

    def showwarning(self, title="", message="", **_k):
        return self._note(title, message, 0x30)

    def showerror(self, title="", message="", **_k):
        return self._note(title, message, 0x10)

    def askyesno(self, title="", message="", **_k) -> bool:
        if self.collector is not None:
            return True
        return self._native(title, message, 0x4 | 0x20) == 6

    def askokcancel(self, title="", message="", **_k) -> bool:
        if self.collector is not None:
            return True
        return self._native(title, message, 0x1 | 0x20) == 1

    def askyesnocancel(self, title="", message="", **_k) -> Optional[bool]:
        if self.collector is not None:
            return True
        r = self._native(title, message, 0x3 | 0x20)
        return True if r == 6 else (False if r == 7 else None)

    def askquestion(self, title="", message="", **_k) -> str:
        return "yes" if self.askyesno(title, message) else "no"


messagebox = _Dialogs()


def pick_folder(title: str = "Επιλογή φακέλου", initialdir: str = "") -> str:
    """Παράθυρο επιλογής φακέλου των Windows (το μόνο σημείο που χρησιμοποιεί ακόμη το tkinter)."""
    try:
        import tkinter as _tk
        from tkinter import filedialog as _fd
        root = _tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        try:
            return _fd.askdirectory(parent=root, title=title, initialdir=initialdir or None) or ""
        finally:
            root.destroy()
    except Exception:
        LOG.exception("Αποτυχία παραθύρου επιλογής φακέλου")
        return ""


class _Var:
    """Απλή τιμή με get()/set() (στη θέση των μεταβλητών της παλιάς κλασικής διεπαφής)."""
    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v

    def set(self, value) -> None:
        self._v = value


class _TextBuf:
    """Κείμενο αποτελεσμάτων παραγωγής (στη θέση του παλιού πλαισίου κειμένου)."""
    def __init__(self):
        self._t = ""

    def delete(self, *_a) -> None:
        self._t = ""

    def insert(self, _index, text, *_tags) -> None:
        self._t += str(text)

    def get(self, *_a) -> str:
        return self._t

    def __getattr__(self, _name):          # see(), tag_configure(), update_idletasks() κ.λπ.: τίποτα
        return lambda *a, **k: None

MONTHS_GR_ALL = ["Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος", "Ιούλιος", "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος"]


class EfimeriesApp:
    """Η λογική της εφαρμογής (βάση, παραγωγή, απουσίες, αντίγραφα). Η διεπαφή είναι η webui.WebUI στον browser.

    Δεν υπάρχει πια παράθυρο Tkinter: το κύριο νήμα τρέχει έναν απλό βρόχο (run) με χρονοδιακόπτες (after),
    όπου εκτελούνται με τη σειρά όλες οι κλήσεις της διεπαφής.
    """

    def __init__(self):
        import heapq
        self._heapq = heapq
        self._timers: list = []
        self._cancelled: set = set()
        self._timer_seq = 0
        self._running = False
        self.ok = False
        # ό,τι διάβαζε η λογική από τα πεδία της παλιάς διεπαφής
        self.mode_var = _Var("daily")
        self.regenerate_scope_var = _Var("continue")
        self.regenerate_from_var = _Var("")
        self.regenerate_to_var = _Var("")
        self.generate_text = _TextBuf()
        self.storage_path_var = _Var("")
        self.storage_db_var = _Var("")
        self.messages: List[str] = []
        set_storage_root(load_storage_root())
        ensure_dirs()
        if not self._acquire_storage_lock():
            return
        self.active_school_year = prepare_year_storage()
        self.db = DB(year_db_path(self.active_school_year), self.active_school_year)
        try:
            self.mode_var.set(self.db.get_setting("mode", "daily"))
        except Exception:
            pass
        self.ok = True

    # ---- βρόχος του κύριου νήματος ---------------------------------------------------
    def after(self, ms: int, fn, *args):
        self._timer_seq += 1
        self._heapq.heappush(self._timers, (time.monotonic() + max(0, ms) / 1000.0, self._timer_seq, fn, args))
        return self._timer_seq

    def after_cancel(self, timer_id) -> None:
        if timer_id:
            self._cancelled.add(timer_id)

    def run(self) -> None:
        self._running = True
        while self._running:
            now = time.monotonic()
            while self._timers and self._timers[0][0] <= now and self._running:
                _due, tid, fn, args = self._heapq.heappop(self._timers)
                if tid in self._cancelled:
                    self._cancelled.discard(tid)
                    continue
                try:
                    fn(*args)
                except Exception:
                    LOG.exception("Σφάλμα σε εργασία του κύριου νήματος")
            delay = (self._timers[0][0] - time.monotonic()) if self._timers else 0.05
            time.sleep(min(0.05, max(0.005, delay)))

    def destroy(self) -> None:
        self._running = False

    # ---- ό,τι ενημέρωνε την παλιά διεπαφή: τώρα μόνο καταγραφή --------------------------
    def notify(self, text: str, kind: str = "ok", ms: int = 7000) -> None:
        LOG.info("%s", text)
        self.messages = (self.messages + [str(text)])[-20:]

    def set_status(self, text: str) -> None:
        LOG.info("%s", text)

    def refresh_all(self) -> None:
        """Η διεπαφή ξαναδιαβάζει μόνη της τα δεδομένα· εδώ μένει μόνο ο συγχρονισμός του σχολικού έτους."""
        try:
            self.sync_school_year_fields()
        except Exception:
            LOG.exception("Αποτυχία συγχρονισμού σχολικού έτους")

    def refresh_home(self) -> None:
        pass

    def refresh_schedule(self) -> None:
        pass

    def refresh_school_year_selector(self) -> None:
        pass

    def _undo_ui(self) -> None:
        pass

    def _update_window_title(self) -> None:
        pass

    def _save_window_geometry(self) -> None:
        pass

    def _gen_progress_begin(self) -> None:
        pass

    def _gen_progress_step(self, i: int, n: int, text: str) -> None:
        pass

    def _gen_progress_end(self) -> None:
        pass

    # ---- νέα διεπαφή ------------------------------------------------------------------
    def open_web_ui(self, hidden: bool = True) -> None:
        """Ξεκινά τον τοπικό server της διεπαφής και την ανοίγει στον Edge (ή στον προεπιλεγμένο browser)."""
        ui = self.__dict__.get("_webui")
        if ui is None:
            import webui
            ui = webui.WebUI(self, hidden=True)
            self._webui = ui
            url = ui.start()
            self._webui_url = url
            try:   # για δεύτερη εκκίνηση (π.χ. από το Ωρολόγιο): ανοίγει το ίδιο παράθυρο αντί για μήνυμα κλειδώματος
                p = suite_config_path().parent / "efimeries.url"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({"url": url, "pid": os.getpid(), "host": socket.gethostname()}), encoding="utf-8")
            except Exception:
                pass
        ui.open_browser(self._webui_url)

    # ---- εισαγωγή CSV (από τη διεπαφή: τα bytes του αρχείου) ------------------------------
    @staticmethod
    def _decode_csv(raw: bytes) -> Tuple[str, str]:
        for enc in ("utf-8-sig", "utf-8", "cp1253"):
            try:
                return raw.decode(enc), enc
            except UnicodeDecodeError:
                continue
        raise ValueError("Το αρχείο δεν αναγνωρίστηκε ως UTF-8 ή ελληνικό Windows CSV.")

    # ---- Κλείδωμα κοινόχρηστου φακέλου --------------------------------------------
    def _lock_payload(self) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        return {"host": socket.gethostname(), "pid": os.getpid(), "started": getattr(self, "_lock_started", now),
                "heartbeat": now, "app_version": APP_VERSION, "arch": APP_ARCH}

    @staticmethod
    def _read_lock_info() -> dict:
        try:
            data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _remove_lock_file() -> None:
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            LOG.exception("Δεν αφαιρέθηκε το αρχείο κλειδώματος")

    def _acquire_storage_lock(self) -> bool:
        """Αποκλειστικό κλείδωμα του φακέλου δεδομένων (μία ενεργή συνεδρία κάθε φορά).

        Το αρχείο δημιουργείται ατομικά και ανανεώνεται κάθε 30''. Ένα κλείδωμα θεωρείται παλιό
        μόνο αν (α) η διεργασία δεν υπάρχει πια στον ίδιο υπολογιστή, ή (β) δεν ανανεώθηκε για
        πάνω από 3'· στη δεύτερη περίπτωση ο χρήστης ερωτάται πριν αφαιρεθεί.
        """
        try:
            ensure_dirs()
            host = socket.gethostname()
            pid = os.getpid()
            for _attempt in range(3):
                try:
                    payload = self._lock_payload()
                    with open(LOCK_FILE, "x", encoding="utf-8") as fh:
                        json.dump(payload, fh, ensure_ascii=False, indent=2)
                    self._lock_started = payload["started"]
                    self._owns_storage_lock = True
                    self._lock_lost_warned = False
                    self._start_lock_heartbeat()
                    return True
                except FileExistsError:
                    pass
                info = self._read_lock_info()
                lock_host = str(info.get("host", "άγνωστος"))
                lock_pid = info.get("pid")
                started = str(info.get("started", "άγνωστη ώρα"))
                age = None
                if "heartbeat" in info:
                    try:
                        age = max(0.0, (datetime.now() - datetime.fromisoformat(str(info["heartbeat"]))).total_seconds())
                    except Exception:
                        age = None
                same_host = lock_host == host
                if same_host and lock_pid == pid:
                    self._owns_storage_lock = True
                    self._start_lock_heartbeat()
                    return True
                if same_host and isinstance(lock_pid, int) and not pid_alive(lock_pid):
                    LOG.warning("Αφαίρεση παλιού κλειδώματος (η διεργασία %s δεν υπάρχει πια)", lock_pid)
                    self._remove_lock_file()
                    continue
                if age is not None and age > LOCK_STALE_SECONDS:
                    mins = int(age // 60)
                    question = (
                        "Ο φάκελος δεδομένων φαίνεται κλειδωμένος, αλλά το κλείδωμα δεν έχει ανανεωθεί εδώ και "
                        f"{mins} λεπτά.\n\nΥπολογιστής: {lock_host}\nΈναρξη: {started}\n\n"
                        "Πιθανότατα η εφαρμογή έκλεισε απότομα ή ο υπολογιστής σβήστηκε. "
                        "Αν είσαι σίγουρος/η ότι η εφαρμογή ΔΕΝ είναι ανοιχτή αλλού, μπορείς να αφαιρέσεις το κλείδωμα.\n\n"
                        "Να αφαιρεθεί το κλείδωμα και να συνεχίσεις;"
                    )
                    if messagebox.askyesno("Παλιό κλείδωμα δεδομένων", question):
                        LOG.warning("Ο χρήστης αφαίρεσε παλιό κλείδωμα του %s (%s λεπτά χωρίς ανανέωση)", lock_host, mins)
                        self._remove_lock_file()
                        continue
                    return False
                if same_host:   # ήδη ανοιχτή σε αυτόν τον υπολογιστή: άνοιγμα του υπάρχοντος παραθύρου
                    try:
                        info2 = json.loads((suite_config_path().parent / "efimeries.url").read_text(encoding="utf-8"))
                        if info2.get("pid") == lock_pid and info2.get("url"):
                            import webui as _w
                            edge = _w.find_edge()
                            if edge:
                                subprocess.Popen([edge, "--app=" + info2["url"], "--start-maximized"], close_fds=True)
                            else:
                                webbrowser.open(info2["url"])
                            return False
                    except Exception:
                        pass
                msg = (
                    "Ο κοινόχρηστος φάκελος δεδομένων χρησιμοποιείται ήδη.\n\n"
                    f"Υπολογιστής: {lock_host}\nΔιεργασία: {lock_pid if lock_pid is not None else '?'}\nΈναρξη: {started}\n\n"
                    "Για ασφάλεια δεν επιτρέπεται ταυτόχρονη επεξεργασία της ίδιας βάσης από δύο υπολογιστές ή δύο παράθυρα. "
                    "Κλείσε πρώτα την εφαρμογή στον άλλο υπολογιστή.\n\n"
                    f"Αν είσαι σίγουρος/η ότι δεν τρέχει πουθενά, μπορείς να διαγράψεις χειροκίνητα το αρχείο:\n{LOCK_FILE}"
                )
                messagebox.showerror("Κοινόχρηστα δεδομένα σε χρήση", msg)
                return False
            return False
        except Exception as e:
            LOG.exception("Αποτυχία κλειδώματος φακέλου δεδομένων")
            messagebox.showerror("Κλείδωμα δεδομένων", f"Δεν ήταν δυνατό να κλειδωθεί ο φάκελος δεδομένων:\n{STORAGE_ROOT}\n\n{e}")
            return False

    def _start_lock_heartbeat(self) -> None:
        self._stop_lock_heartbeat()
        try:
            self._lock_after_id = self.after(LOCK_HEARTBEAT_MS, self._lock_heartbeat)
        except Exception:
            self._lock_after_id = None

    def _stop_lock_heartbeat(self) -> None:
        aid = getattr(self, "_lock_after_id", None)
        if aid:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._lock_after_id = None

    def _lock_heartbeat(self) -> None:
        self._lock_after_id = None
        if not getattr(self, "_owns_storage_lock", False):
            return
        try:
            info = self._read_lock_info()
            mine = info.get("host") == socket.gethostname() and info.get("pid") == os.getpid()
            if mine or not LOCK_FILE.exists():
                tmp = LOCK_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._lock_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, LOCK_FILE)
            elif not getattr(self, "_lock_lost_warned", False):
                self._lock_lost_warned = True
                LOG.error("Το κλείδωμα πάρθηκε από άλλη συνεδρία: %s", info)
                messagebox.showwarning(
                    "Κλείδωμα δεδομένων",
                    "Το κλείδωμα του φακέλου δεδομένων το έχει πάρει άλλη συνεδρία "
                    f"({info.get('host', '?')}). Για να μη χαλάσουν τα δεδομένα, κλείσε αυτή την εφαρμογή."
                )
        except Exception:
            LOG.exception("Αποτυχία ανανέωσης κλειδώματος")
        finally:
            try:
                self._lock_after_id = self.after(LOCK_HEARTBEAT_MS, self._lock_heartbeat)
            except Exception:
                pass

    def _release_storage_lock(self) -> None:
        self._stop_lock_heartbeat()
        if not getattr(self, "_owns_storage_lock", False):
            return
        try:
            if LOCK_FILE.exists():
                info = self._read_lock_info()
                if info.get("host") == socket.gethostname() and info.get("pid") == os.getpid():
                    LOCK_FILE.unlink()
        except Exception:
            LOG.exception("Αποτυχία αποδέσμευσης κλειδώματος")
        self._owns_storage_lock = False

    # ---- Αυτόματα αντίγραφα ασφαλείας ----------------------------------------------
    AUTO_BACKUP_KEEP = 40

    def _write_backup_zip(self, path, sy: str, conn: Optional[sqlite3.Connection] = None) -> None:
        """Γράφει αρχείο .efbackup (zip με στιγμιότυπο της βάσης + manifest.json)."""
        source = conn or self.db.conn
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td) / f"efimeries_{sy}.db"
            dst = sqlite3.connect(snap)
            try:
                source.backup(dst)
            finally:
                dst.close()
            manifest = {"format": "efimeries-portable-backup", "format_version": 1, "app_version": APP_VERSION,
                        "school_year": sy, "created_at": datetime.now().isoformat(timespec="seconds")}
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(snap, arcname=snap.name)
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    def auto_backup(self, reason: str, conn: Optional[sqlite3.Connection] = None, sy: Optional[str] = None) -> Optional[Path]:
        """Αυτόματο backup στο backups/auto πριν από ενέργεια που σβήνει ή αντικαθιστά δεδομένα.

        Ποτέ δεν διακόπτει την ενέργεια: αν αποτύχει, καταγράφεται στο log και η ενέργεια συνεχίζεται.
        Δεν δημιουργείται backup για εντελώς άδεια βάση.
        """
        try:
            source = conn or self.db.conn
            sy = sy or self.db.get_setting("school_year", self.active_school_year)
            has_data = source.execute("SELECT (SELECT COUNT(*) FROM teachers) + (SELECT COUNT(*) FROM assignments)").fetchone()[0]
            if not has_data:
                return None
            folder = BACKUP_DIR / "auto"
            folder.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_-]", "", reason) or "auto"
            target = folder / f"Efimeries_Auto_{sy}_{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.efbackup"
            self._write_backup_zip(target, sy, source)
            LOG.info("Αυτόματο backup (%s): %s", reason, target)
            files = sorted(folder.glob("Efimeries_Auto_*.efbackup"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[self.AUTO_BACKUP_KEEP:]:
                try:
                    old.unlink()
                except Exception:
                    pass
            return target
        except Exception:
            LOG.exception("Αποτυχία αυτόματου backup (%s)", reason)
            return None

    def _auto_backup_if_due(self, hours: int = 20) -> None:
        folder = BACKUP_DIR / "auto"
        newest = 0.0
        if folder.exists():
            newest = max((p.stat().st_mtime for p in folder.glob("Efimeries_Auto_*.efbackup")), default=0.0)
        if time.time() - newest > hours * 3600:
            self.auto_backup("exit")

    def _on_close(self) -> None:
        self._save_window_geometry()
        try:
            self._auto_backup_if_due()
        except Exception:
            LOG.exception("Αποτυχία ημερήσιου αυτόματου backup")
        try:
            self.db.close()
        except Exception:
            pass
        self._release_storage_lock()
        self.destroy()

    def change_storage_root(self, selected: Optional[str] = None, copy_data: Optional[bool] = None) -> None:
        global STORAGE_ROOT
        if not selected:
            selected = pick_folder("Επιλογή φακέλου κοινόχρηστων δεδομένων", str(STORAGE_ROOT))
        if not selected:
            return
        new_root = Path(selected).resolve()
        # Ευκολία: αν επιλεγεί ο γονικός φάκελος "Εφημερίες" και υπάρχει υποφάκελος Shared
        # με δεδομένα, χρησιμοποιούμε αυτόν για να μην καταλήξουν x86/x64 σε διαφορετικές βάσεις.
        shared_candidate = new_root / "Shared"
        if shared_candidate.exists() and (shared_candidate / "data").exists() and not (new_root / "data").exists():
            new_root = shared_candidate.resolve()
        if new_root == STORAGE_ROOT:
            messagebox.showinfo("Φάκελος δεδομένων", "Ο επιλεγμένος φάκελος χρησιμοποιείται ήδη.")
            return
        old_root = STORAGE_ROOT
        if copy_data is None:
            copy_data = messagebox.askyesno(
                "Μεταφορά δεδομένων",
                "Θέλεις να αντιγραφούν τα τρέχοντα data, backups και exports στον νέο φάκελο;\n\n"
                "Επίλεξε Ναι όταν ρυθμίζεις για πρώτη φορά τον κοινόχρηστο φάκελο. "
                "Επίλεξε Όχι αν ο κοινόχρηστος φάκελος έχει ήδη τα δεδομένα που θέλεις να χρησιμοποιήσεις."
            )
        try:
            # Κλείνουμε την τρέχουσα βάση πριν αλλάξουμε τοποθεσία.
            try: self.db.close()
            except Exception: pass
            self._release_storage_lock()
            new_root.mkdir(parents=True, exist_ok=True)
            if copy_data:
                for name in ("data", "backups", "exports"):
                    src = old_root / name
                    dst = new_root / name
                    if src.exists():
                        shutil.copytree(src, dst, dirs_exist_ok=True)
            set_storage_root(new_root)
            ensure_dirs()
            if not self._acquire_storage_lock():
                # Αποθηκεύουμε την επιλογή για την επόμενη εκκίνηση. Αν η άλλη αρχιτεκτονική
                # είναι ανοιχτή, το lock είναι αναμενόμενο και δεν πρέπει να "χάνεται" η ρύθμιση.
                save_storage_root(new_root)
                set_storage_root(old_root)
                ensure_dirs()
                self._acquire_storage_lock()
                self.active_school_year = prepare_year_storage()
                self.db = DB(year_db_path(self.active_school_year), self.active_school_year)
                messagebox.showinfo(
                    "Κοινόχρηστος φάκελος",
                    f"Η διαδρομή αποθηκεύτηκε:\n{new_root}\n\n"
                    "Ο κοινόχρηστος φάκελος χρησιμοποιείται αυτή τη στιγμή από άλλη συνεδρία. "
                    "Κλείσε την άλλη εφαρμογή και άνοιξε ξανά αυτήν· τότε θα φορτωθούν τα κοινά δεδομένα."
                )
                return
            save_storage_root(new_root)
            self.active_school_year = prepare_year_storage()
            self.db = DB(year_db_path(self.active_school_year), self.active_school_year)
            self.sync_school_year_fields()
            self.refresh_all()
            try:
                self.storage_path_var.set(str(STORAGE_ROOT))
                if hasattr(self, "storage_db_var"):
                    self.storage_db_var.set(str(year_db_path(self.active_school_year)))
            except Exception:
                pass
            self.set_status(f"Κοινόχρηστα δεδομένα: {STORAGE_ROOT}")
            messagebox.showinfo("Φάκελος δεδομένων", f"Η εφαρμογή χρησιμοποιεί πλέον:\n{STORAGE_ROOT}\n\nΗ ίδια διαδρομή πρέπει να δηλωθεί και στον άλλο υπολογιστή.")
        except Exception as e:
            try:
                set_storage_root(old_root); ensure_dirs(); self._acquire_storage_lock()
                self.active_school_year = prepare_year_storage(); self.db = DB(year_db_path(self.active_school_year), self.active_school_year)
            except Exception:
                pass
            messagebox.showerror("Αλλαγή φακέλου δεδομένων", str(e))

    def day_name(self, idx: int) -> str:
        return DAYS_GR[idx] if isinstance(idx, int) and 0 <= idx < len(DAYS_GR) else "—"

    # ================= Βήμα 3: αναίρεση, εναλλαγή, απουσία, αρχική, ημερολόγιο =================
    UNDO_LIMIT = 15

    UNDO_TABLES = ("assignments", "duty_changes", "shift_reserves", "absences")

    def _undo_snapshot(self) -> dict:
        out = {}
        for tbl in self.UNDO_TABLES:
            try:
                cur = self.db.conn.execute(f"SELECT * FROM {tbl} ORDER BY 1")
            except sqlite3.Error:
                out[tbl] = ([], [])
                continue
            out[tbl] = ([d[0] for d in cur.description], [tuple(r) for r in cur.fetchall()])
        return out

    def _undo_push(self, label: str, snap: dict) -> None:
        stack = self.__dict__.setdefault("_undo_stack", [])
        stack.append((label, str(self.db.path), snap))
        del stack[:-self.UNDO_LIMIT]
        self._undo_ui()

    def _undo_restore(self, snap: dict) -> None:
        c = self.db.conn
        try:
            for tbl in ("absences", "duty_changes", "shift_reserves", "assignments"):
                try:
                    c.execute(f"DELETE FROM {tbl}")
                except sqlite3.Error:
                    pass
            for tbl in ("assignments", "duty_changes", "shift_reserves", "absences"):
                cols, rows = snap.get(tbl, ([], []))
                if rows:
                    c.executemany(f"INSERT INTO {tbl}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", rows)
            c.commit()
            self._absent_cache = None
        except Exception:
            c.rollback()
            raise

    def undo_last(self) -> None:
        stack = self.__dict__.setdefault("_undo_stack", [])
        if not stack:
            self.notify("Δεν υπάρχει αλλαγή για αναίρεση.", kind="info")
            return
        label, path, snap = stack[-1]
        if path != str(self.db.path):
            stack.clear()
            self._undo_ui()
            self.notify("Η αναίρεση δεν είναι διαθέσιμη μετά την αλλαγή σχολικού έτους.", kind="info")
            return
        try:
            self.auto_backup("undo")
            self._undo_restore(snap)
        except Exception as e:
            LOG.exception("Αποτυχία αναίρεσης")
            messagebox.showerror("Αναίρεση", f"Η αναίρεση δεν ολοκληρώθηκε:\n{e}")
            return
        stack.pop()
        self._undo_ui()
        self.refresh_all()
        self.notify(f"Αναιρέθηκε: {label}.")

    @staticmethod
    def _slot_key(r) -> tuple:
        return (r["d"], r["interval_name"], r["start_time"], r["end_time"])

    # ---- απουσία για διάστημα ----
    def _teacher_ok_for(self, t, r) -> bool:
        """Αν ο/η εκπαιδευτικός είναι δηλωμένα διαθέσιμος/η για την εφημερία (ίδιος έλεγχος με την αντικατάσταση)."""
        weekday = parse_date(r["d"]).weekday()
        mode = r["mode"]
        if mode == "breaks":
            iv = self.db.conn.execute("SELECT id FROM duty_intervals WHERE name=? LIMIT 1", (r["interval_name"],)).fetchone()
            return self.db.teacher_interval_available(t, weekday, iv["id"]) if iv else t.available_on(weekday)
        if mode == "shifts":
            sh = self.db.conn.execute("SELECT id FROM shifts WHERE name=? LIMIT 1", (r["interval_name"],)).fetchone()
            return self.db.teacher_shift_available(t, weekday, sh["id"]) if sh else t.available_on(weekday)
        return t.available_on(weekday)

    def _plan_absence(self, teacher_id: int, d1: date, d2: date):
        """Προτείνει αντικαταστάτες για όλες τις μη κλειδωμένες εφημερίες του/της εκπαιδευτικού στο διάστημα.
        Επιλέγεται όποιος/α είναι διαθέσιμος/η και έχει το μικρότερο συνολικό φορτίο· δεν επιτρέπεται ταυτόχρονη διπλή εφημερία."""
        c = self.db.conn
        teachers = {t.id: t for t in self.db.active_teachers()}
        rows = c.execute("SELECT * FROM assignments WHERE teacher_id=? AND d BETWEEN ? AND ? AND locked=0 ORDER BY d, start_time, id",
                         (teacher_id, d1.isoformat(), d2.isoformat())).fetchall()
        locked_n = c.execute("SELECT COUNT(*) FROM assignments WHERE teacher_id=? AND d BETWEEN ? AND ? AND locked=1",
                             (teacher_id, d1.isoformat(), d2.isoformat())).fetchone()[0]
        sums = {r[0]: int(r[1]) for r in c.execute("SELECT teacher_id, COALESCE(SUM(minutes),0) FROM assignments GROUP BY teacher_id")}
        for tid in teachers:
            sums.setdefault(tid, 0)
        busy: Dict[tuple, set] = {}
        for r in c.execute("SELECT teacher_id,d,interval_name,start_time,end_time FROM assignments WHERE d BETWEEN ? AND ?",
                           (d1.isoformat(), d2.isoformat())).fetchall():
            busy.setdefault(self._slot_key(r), set()).add(r["teacher_id"])
        names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM teachers")}
        area_of = {r["id"]: r["area_name"] for r in c.execute("SELECT id,area_name FROM assignments WHERE teacher_id=?", (teacher_id,))}
        plan = []
        for r in rows:
            key = self._slot_key(r)
            cands = [t for tid, t in teachers.items() if tid != teacher_id and tid not in busy.get(key, set()) and not self._is_absent(tid, r["d"])]
            ok = [t for t in cands if self._teacher_ok_for(t, r)]
            pool = ok or cands
            item = {"id": r["id"], "d": r["d"], "area": area_of.get(r["id"], r["area_name"]), "start": r["start_time"], "mode": r["mode"],
                    "minutes": r["minutes"], "new_id": None, "new_name": "", "note": ""}
            if not pool:
                item["note"] = "δεν υπάρχει διαθέσιμος/η"
            else:
                pick = min(pool, key=lambda t: (sums.get(t.id, 0), t.name))
                item.update(new_id=pick.id, new_name=names.get(pick.id, pick.name),
                            note="" if ok else "εκτός δηλωμένης διαθεσιμότητας (εθελοντικά)")
                sums[pick.id] = sums.get(pick.id, 0) + int(r["minutes"])
                sums[teacher_id] = sums.get(teacher_id, 0) - int(r["minutes"])
                busy.setdefault(key, set()).add(pick.id)
            plan.append(item)
        return plan, locked_n

    def _apply_absence_plan(self, teacher_id: int, plan: list, reason: str, lock: bool) -> int:
        c = self.db.conn
        old = c.execute("SELECT name FROM teachers WHERE id=?", (teacher_id,)).fetchone()[0]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        n = 0
        try:
            for it in plan:
                if not it["new_id"]:
                    continue
                new_name = c.execute("SELECT name FROM teachers WHERE id=?", (it["new_id"],)).fetchone()[0]
                c.execute("UPDATE assignments SET teacher_id=?, locked=? WHERE id=?", (it["new_id"], 1 if lock else 0, it["id"]))
                c.execute(
                    "INSERT INTO duty_changes(assignment_id,changed_at,change_type,old_teacher_id,new_teacher_id,old_teacher_name,new_teacher_name,reason) VALUES(?,?,?,?,?,?,?,?)",
                    (it["id"], now, "Αντικατάσταση λόγω απουσίας (διάστημα)", teacher_id, it["new_id"], old, new_name,
                     (reason + " | " if reason else "") + (it["note"] or "Αυτόματη πρόταση με βάση διαθεσιμότητα και ισοκατανομή.")))
                n += 1
            c.commit()
        except Exception:
            c.rollback()
            raise
        return n

    # ================= v0.41: καταχωρημένες απουσίες (ολόκληρες ημέρες) =================
    def _absent_map(self) -> dict:
        cache = self.__dict__.get("_absent_cache")
        if cache is None or cache[0] != id(self.db):
            m: Dict[int, list] = {}
            try:
                for r in self.db.conn.execute("SELECT teacher_id,d_from,d_to FROM absences"):
                    m.setdefault(r[0], []).append((r[1], r[2]))
            except sqlite3.Error:
                pass
            cache = (id(self.db), m)
            self._absent_cache = cache
        return cache[1]

    def _is_absent(self, teacher_id: int, d) -> bool:
        spans = self._absent_map().get(teacher_id)
        if not spans:
            return False
        ds = d.isoformat() if hasattr(d, "isoformat") else str(d)
        return any(a <= ds <= b for a, b in spans)

    def _absence_rows(self, only_current: bool = False) -> list:
        sql = "SELECT ab.id, ab.teacher_id, t.name AS teacher, ab.d_from, ab.d_to, ab.reason FROM absences ab JOIN teachers t ON t.id=ab.teacher_id"
        args: tuple = ()
        if only_current:
            sql += " WHERE ab.d_to>=?"
            args = (date.today().isoformat(),)
        return self.db.conn.execute(sql + " ORDER BY ab.d_from, t.name", args).fetchall()

    def _add_absence(self, teacher_id: int, d1: date, d2: date, reason: str = "") -> int:
        if d2 < d1:
            d1, d2 = d2, d1
        cur = self.db.conn.execute("INSERT INTO absences(teacher_id,d_from,d_to,reason,created_at) VALUES(?,?,?,?,?)",
                                   (teacher_id, d1.isoformat(), d2.isoformat(), reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self.db.conn.commit()
        self._absent_cache = None
        return cur.lastrowid

    def _delete_absence(self, absence_id: int) -> None:
        self.db.conn.execute("DELETE FROM absences WHERE id=?", (absence_id,))
        self.db.conn.commit()
        self._absent_cache = None

    # ---- έλεγχος αποτελέσματος παραγωγής ----
    def _gen_metrics(self) -> dict:
        c = self.db.conn
        sums = {r[0]: int(r[1]) for r in c.execute("SELECT teacher_id, COALESCE(SUM(minutes),0) FROM assignments GROUP BY teacher_id")}
        vals = [sums.get(t.id, 0) for t in self.db.active_teachers()] or [0]
        keys = {(r[0], r[1], r[2], r[3], r[4]) for r in c.execute("SELECT d,interval_name,start_time,area_name,teacher_id FROM assignments")}
        return {"n": c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], "spread": max(vals) - min(vals), "keys": keys}

    # ------------------------------------------------------------------
    #  Έλεγχος νέας έκδοσης (GitHub Releases, δημόσιο αποθετήριο, χωρίς κωδικούς)
    # ------------------------------------------------------------------
    @staticmethod
    def _version_tuple(v: str) -> Tuple[int, ...]:
        nums = re.findall(r"\d+", (v or "").split("-")[0])
        return tuple(int(n) for n in nums) or (0,)

    def _update_state_path(self) -> Path:
        return app_dir() / "update_state.json"

    def _update_state(self) -> dict:
        try:
            return json.loads(self._update_state_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_update_state(self, **kw) -> None:
        st = self._update_state()
        st.update(kw)
        try:
            self._update_state_path().write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            LOG.exception("Αποθήκευση ρυθμίσεων ελέγχου έκδοσης")

    def _fetch_latest_release(self) -> dict:
        """Ζητά από το GitHub την τελευταία δημοσιευμένη έκδοση. Στέλνεται μόνο αίτημα ανάγνωσης, χωρίς δεδομένα σχολείου."""
        import urllib.request
        req = urllib.request.Request(
            f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"EfimeriesPortable/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {"tag": str(data.get("tag_name", "")), "name": str(data.get("name") or ""),
                "assets": [{"name": str(a.get("name", "")), "url": str(a.get("browser_download_url", "")), "size": int(a.get("size") or 0)} for a in data.get("assets", [])],
                "body": str(data.get("body") or ""), "url": str(data.get("html_url") or f"https://github.com/{UPDATE_REPO}/releases/latest")}

    def _setup_asset(self, rel: dict) -> Optional[dict]:
        """Το Setup.exe της δικής μας αρχιτεκτονικής, μόνο για εγκατεστημένη εφαρμογή και μόνο από το github.com."""
        try:
            if not (app_dir() / "installed.flag").exists() or not getattr(sys, "frozen", False):
                return None
        except Exception:
            return None
        arch = "x64" if APP_ARCH == "x64" else "x86"
        for a in rel.get("assets", []):
            n, u = a.get("name", ""), a.get("url", "")
            if re.fullmatch(rf"Efimeries_Setup_v[\d.]+_{arch}\.exe", n) and u.startswith("https://github.com/"):
                return a
        return None

    def _download_file(self, url: str, dest: Path, progress=None) -> None:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": f"EfimeriesPortable/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)

    def _save_shift_maps(self,sid:int,data:dict):
        self.db.conn.execute("DELETE FROM shift_interval_map WHERE shift_id=?",(sid,)); self.db.conn.execute("DELETE FROM shift_area_map WHERE shift_id=?",(sid,))
        self.db.conn.executemany("INSERT INTO shift_interval_map(shift_id,weekday,interval_id) VALUES(?, -1, ?)",[(sid,i) for i in data["general"]])
        for wd,ids in data["overrides"].items():
            self.db.conn.executemany("INSERT INTO shift_interval_map(shift_id,weekday,interval_id) VALUES(?,?,?)",[(sid,wd,i) for i in ids])
        self.db.conn.executemany("INSERT INTO shift_area_map(shift_id,area_id) VALUES(?,?)",[(sid,a) for a in data["areas"]])

    # ======================================================================================
    #  Καρτέλα «Πρόγραμμα» (v0.40): λίστα ή εβδομάδα, φίλτρα και πάνελ ενεργειών
    # ======================================================================================
    ALL_AREAS = "Όλοι οι τομείς"

    STATUS_FILTERS = ["Όλες", "Προγραμματισμένες", "Κλειδωμένες / πραγματοποιημένες", "Αντικαταστάσεις"]

    SCHEDULE_SQL = """
        SELECT a.*, COALESCE(ar.name, a.area_name) AS display_area, t.name AS teacher,
               EXISTS(SELECT 1 FROM duty_changes c WHERE c.assignment_id=a.id AND c.change_type LIKE 'Αντικατάσταση%') AS replaced,
               (SELECT c.old_teacher_name FROM duty_changes c WHERE c.assignment_id=a.id AND c.change_type LIKE 'Αντικατάσταση%'
                ORDER BY c.id DESC LIMIT 1) AS old_teacher
        FROM assignments a JOIN teachers t ON t.id=a.teacher_id LEFT JOIN areas ar ON ar.id=a.area_id
    """

    COL_LOCKED, COL_REP, COL_TODAY, COL_GROUP = "#F1F3F5", "#FFF8E6", "#F5F9FE", "#EEF2F6"

    def available_school_years(self) -> List[str]:
        years = discover_school_years()
        current = self.db.get_setting("school_year", getattr(self, "active_school_year", "2026-2027"))
        try:
            current = normalize_school_year(current)
            if current not in years and year_db_path(current).exists():
                years.append(current)
        except Exception:
            pass
        return sorted(set(years)) or ["2026-2027"]

    def school_year_options(self) -> List[str]:
        """Επιλογές για δημιουργία νέου έτους, μόνο από 2026-2027 και μετά."""
        existing = self.available_school_years()
        try:
            max_existing = max(int(y.split("-")[0]) for y in existing)
        except Exception:
            max_existing = MIN_SCHOOL_YEAR_START
        last = max(max_existing + 6, 2032)
        return [f"{y}-{y+1}" for y in range(MIN_SCHOOL_YEAR_START, last + 1)]

    def dates_from_school_year(self, school_year: str) -> Tuple[str, str]:
        sy = normalize_school_year(school_year)
        start_year = int(sy.split("-")[0])
        return f"{start_year}-09-11", f"{start_year + 1}-06-15"

    def sync_school_year_fields(self) -> None:
        sy = self.db.get_setting("school_year", self.active_school_year)
        start = self.db.get_setting("start_date", self.dates_from_school_year(sy)[0])
        end = self.db.get_setting("end_date", self.dates_from_school_year(sy)[1])
        self.active_school_year = sy
        self.refresh_school_year_selector()
        try:
            self.mode_var.set(self.db.get_setting("mode", "daily"))
        except Exception:
            pass

    def switch_school_year(self, school_year: str, show_message: bool = False) -> None:
        sy = normalize_school_year(school_year)
        path = year_db_path(sy)
        if not path.exists():
            raise FileNotFoundError(f"Δεν βρέθηκε αρχείο δεδομένων για το {sy}.")
        self.db.close()
        self.db = DB(path, sy)
        self.active_school_year = sy
        ACTIVE_YEAR_FILE.write_text(sy, encoding="utf-8")
        self.sync_school_year_fields()
        self.refresh_all()
        self.set_status(f"Ενεργό σχολικό έτος: {sy}")
        if show_message:
            messagebox.showinfo("Σχολικό έτος", f"Άνοιξε το σχολικό έτος {sy}.")

    def create_school_year(self, sy_text: str, start_text: str, end_text: str, opts: dict) -> str:
        """Δημιουργεί νέο σχολικό έτος (χωρίς πρόγραμμα) με προαιρετική αντιγραφή ρυθμίσεων από το τρέχον. Επιστρέφει το έτος."""
        newdb = None
        try:
            sy = normalize_school_year(sy_text)
            start = parse_date(start_text).isoformat()
            end = parse_date(end_text).isoformat()
            if parse_date(start) > parse_date(end):
                raise ValueError("Η έναρξη δεν μπορεί να είναι μετά τη λήξη.")
            target = year_db_path(sy)
            if target.exists():
                raise ValueError(f"Το σχολικό έτος {sy} υπάρχει ήδη.")

            source = self.db
            newdb = DB(target, sy)
            newdb.set_setting("start_date", start)
            newdb.set_setting("end_date", end)
            if opts["mode"]:
                newdb.set_setting("mode", source.get_setting("mode", "daily"))
            if opts["school"]:
                for key in ("school_name", "school_phone", "school_email", "print_size"):
                    newdb.set_setting(key, source.get_setting(key, "" if key != "print_size" else "normal"))

            if opts["areas"]:
                newdb.conn.execute("DELETE FROM areas")
                rows = source.conn.execute("SELECT name,description,active,sort_order FROM areas ORDER BY id").fetchall()
                newdb.conn.executemany("INSERT INTO areas(name,description,active,sort_order) VALUES(?,?,?,?)", [tuple(r) for r in rows])

            if opts["intervals"]:
                newdb.conn.execute("DELETE FROM duty_intervals")
                rows = source.conn.execute("SELECT name,start_time,end_time,active,sort_order FROM duty_intervals ORDER BY id").fetchall()
                newdb.conn.executemany("INSERT INTO duty_intervals(name,start_time,end_time,active,sort_order) VALUES(?,?,?,?,?)", [tuple(r) for r in rows])

            if opts["shifts"] and opts["intervals"] and opts["areas"]:
                newdb.conn.execute("DELETE FROM shift_interval_map")
                newdb.conn.execute("DELETE FROM shift_area_map")
                newdb.conn.execute("DELETE FROM shifts")
                source_shifts=source.conn.execute("SELECT * FROM shifts ORDER BY id").fetchall()
                old_to_new_shift={}
                old_area_name={int(r["id"]):r["name"] for r in source.conn.execute("SELECT id,name FROM areas").fetchall()}
                new_area_by_name={r["name"]:int(r["id"]) for r in newdb.conn.execute("SELECT id,name FROM areas").fetchall()}
                old_interval_name={int(r["id"]):r["name"] for r in source.conn.execute("SELECT id,name FROM duty_intervals").fetchall()}
                new_interval_by_name={r["name"]:int(r["id"]) for r in newdb.conn.execute("SELECT id,name FROM duty_intervals").fetchall()}
                for sh in source_shifts:
                    cur=newdb.conn.execute("INSERT INTO shifts(name,active,sort_order,has_reserve) VALUES(?,?,?,?)",(sh["name"],sh["active"],sh["sort_order"],sh["has_reserve"]))
                    old_to_new_shift[int(sh["id"])]=int(cur.lastrowid)
                for r in source.conn.execute("SELECT shift_id,weekday,interval_id FROM shift_interval_map").fetchall():
                    ns=old_to_new_shift.get(int(r["shift_id"])); ni=new_interval_by_name.get(old_interval_name.get(int(r["interval_id"]),""))
                    if ns and ni:newdb.conn.execute("INSERT OR IGNORE INTO shift_interval_map(shift_id,weekday,interval_id) VALUES(?,?,?)",(ns,r["weekday"],ni))
                for r in source.conn.execute("SELECT shift_id,area_id FROM shift_area_map").fetchall():
                    ns=old_to_new_shift.get(int(r["shift_id"])); na=new_area_by_name.get(old_area_name.get(int(r["area_id"]),""))
                    if ns and na:newdb.conn.execute("INSERT OR IGNORE INTO shift_area_map(shift_id,area_id) VALUES(?,?)",(ns,na))

            teacher_map = {}
            if opts["teachers"]:
                newdb.conn.execute("DELETE FROM teachers")
                rows = source.conn.execute("SELECT * FROM teachers ORDER BY id").fetchall()
                for r in rows:
                    if opts["availability"]:
                        mon,tue,wed,thu,fri = r["mon"],r["tue"],r["wed"],r["thu"],r["fri"]
                        primary, alternate = r["primary_day"], r["alternate_day"]
                    else:
                        mon=tue=wed=thu=fri=1
                        primary=alternate=-1
                    cur = newdb.conn.execute(
                        "INSERT INTO teachers(name,participates,active,mon,tue,wed,thu,fri,primary_day,alternate_day,extra_cycle,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["name"], r["participates"], r["active"], mon,tue,wed,thu,fri, primary,alternate, r["extra_cycle"], r["notes"])
                    )
                    teacher_map[int(r["id"])] = int(cur.lastrowid)
                    if opts["availability"]:
                        alts=source.teacher_alternate_days(int(r["id"]), int(r["alternate_day"]))
                        newdb.set_teacher_alternate_days(int(cur.lastrowid), alts)

            if opts["teachers"] and opts["availability"]:
                interval_name_by_id = {int(r["id"]): r["name"] for r in source.conn.execute("SELECT id,name FROM duty_intervals").fetchall()}
                new_interval_by_name = {r["name"]: int(r["id"]) for r in newdb.conn.execute("SELECT id,name FROM duty_intervals").fetchall()}
                av_rows = source.conn.execute("SELECT teacher_id,weekday,interval_id,available FROM teacher_interval_availability").fetchall()
                for r in av_rows:
                    nt = teacher_map.get(int(r["teacher_id"]))
                    name = interval_name_by_id.get(int(r["interval_id"]))
                    ni = new_interval_by_name.get(name) if name else None
                    if nt and ni:
                        newdb.conn.execute("INSERT OR REPLACE INTO teacher_interval_availability(teacher_id,weekday,interval_id,available) VALUES(?,?,?,?)", (nt, r["weekday"], ni, r["available"]))
                if opts["shifts"]:
                    source_shift_name={int(r["id"]):r["name"] for r in source.conn.execute("SELECT id,name FROM shifts").fetchall()}
                    new_shift_by_name={r["name"]:int(r["id"]) for r in newdb.conn.execute("SELECT id,name FROM shifts").fetchall()}
                    for r in source.conn.execute("SELECT teacher_id,weekday,shift_id,available FROM teacher_shift_availability").fetchall():
                        nt=teacher_map.get(int(r["teacher_id"])); ns=new_shift_by_name.get(source_shift_name.get(int(r["shift_id"]),""))
                        if nt and ns:newdb.conn.execute("INSERT OR REPLACE INTO teacher_shift_availability(teacher_id,weekday,shift_id,available) VALUES(?,?,?,?)",(nt,r["weekday"],ns,r["available"]))
                    # Περιορισμοί τομέων βαρδιών ανά εκπαιδευτικό, με αντιστοίχιση ονομασίας τομέα.
                    for r in source.conn.execute("SELECT teacher_id,area_id FROM teacher_shift_area_permissions").fetchall():
                        nt=teacher_map.get(int(r["teacher_id"])); na=new_area_by_name.get(old_area_name.get(int(r["area_id"]),""))
                        if nt and na:newdb.conn.execute("INSERT OR IGNORE INTO teacher_shift_area_permissions(teacher_id,area_id) VALUES(?,?)",(nt,na))

            newdb.conn.commit()
            newdb.close()
            return sy
        except Exception:
            try:
                if newdb is not None:
                    newdb.close()
            except Exception:
                pass
            raise

    def yesno(self, v: int) -> str:
        return "Ναι" if v else "Όχι"

    def exception_range_overlap(self, start_d: date, end_d: date, exclude_id: Optional[int] = None):
        """Βρίσκει τυχόν υπάρχουσα ειδική εγγραφή που επικαλύπτει το νέο διάστημα."""
        rows = self.db.conn.execute("SELECT * FROM exceptions ORDER BY d").fetchall()
        for r in rows:
            if exclude_id is not None and int(r["id"]) == int(exclude_id):
                continue
            try:
                rs = parse_date(r["d"])
                re_raw = r["end_d"] if "end_d" in r.keys() and r["end_d"] else r["d"]
                re = parse_date(re_raw)
            except Exception:
                continue
            if re < rs:
                rs, re = re, rs
            if max(start_d, rs) <= min(end_d, re):
                return r
        return None

    def school_days(self) -> List[date]:
        start = parse_date(self.db.get_setting("start_date"))
        end = parse_date(self.db.get_setting("end_date"))
        exceptions = self.db.exception_dates()
        days = []
        d = start
        while d <= end:
            if d.weekday() < 5:
                ex = exceptions.get(d.isoformat())
                # «Χωρίς εφημερία» υπερισχύει απόλυτα, ακόμη κι αν υπήρχε παλιό config.
                if self.is_no_duty_exception(ex):
                    d += timedelta(days=1)
                    continue
                # Οι ημέρες με ειδικό πρόγραμμα ζωνών ή «Μόνο προσέλευση-αποχώρηση» παραμένουν στο πρόγραμμα.
                if ex is None or self.is_arrival_departure_only(ex) or self.has_special_zone_config(ex):
                    days.append(d)
            d += timedelta(days=1)
        return days

    def is_no_duty_exception(self, ex_row) -> bool:
        if not ex_row:
            return False
        return (ex_row["type"] or "").strip().lower() == "χωρίς εφημερία"

    def is_arrival_departure_only(self, ex_row) -> bool:
        if not ex_row:
            return False
        text = f"{ex_row['type']} {ex_row['label']}".lower()
        return "μόνο προσέλευση" in text or "προσέλευση-αποχώρηση" in text or "προσέλευση αποχώρηση" in text

    def has_special_zone_config(self, ex_row) -> bool:
        return bool(self.db.exception_config(ex_row))

    def intervals_for_day(self, d: date, intervals: List[sqlite3.Row]) -> Tuple[List[sqlite3.Row], bool]:
        ex = self.db.exception_dates().get(d.isoformat())
        if self.is_no_duty_exception(ex):
            return [], True
        cfg = self.db.exception_config(ex)
        if cfg:
            selected = [r for r in intervals if r["name"] in cfg and cfg.get(r["name"], {}).get("active")]
            return selected, True
        if not self.is_arrival_departure_only(ex):
            return list(intervals), False
        selected = [r for r in intervals if "προσέλευ" in r["name"].lower() or "αποχώρ" in r["name"].lower()]
        if not selected and intervals:
            selected = [intervals[0]]
            if len(intervals) > 1:
                selected.append(intervals[-1])
        return selected, True

    def area_limit_for_interval(self, d: date, interval_name: str, default_count: int) -> int:
        ex = self.db.exception_dates().get(d.isoformat())
        if self.is_no_duty_exception(ex):
            return 0
        cfg = self.db.exception_config(ex)
        if interval_name in cfg:
            try:
                return max(0, min(default_count, int(cfg[interval_name].get("count", default_count))))
            except Exception:
                return default_count
        if self.is_arrival_departure_only(ex) and ("αποχώ" in interval_name.lower() or "αποχω" in interval_name.lower()):
            return min(default_count, 1)
        return default_count

    def choose_teacher(
        self,
        teachers: List[Teacher],
        weekday: int,
        total_minutes: Dict[int, int],
        week_minutes: Dict[Tuple[int, str], int],
        used_today: set,
        week_key: str,
        area_name: str = "",
        preferred_area_by_teacher: Optional[Dict[int, str]] = None,
        interval_id: Optional[int] = None,
        assignment_minutes: int = 0,
        fairness_focus: bool = False,
        allow_reuse_if_needed: bool = True,
        on_date: Optional[date] = None,
    ) -> Optional[Teacher]:
        """
        Επιλέγει εκπαιδευτικό με βασική προτεραιότητα την ισοκατανομή.
        Αν δοθεί area_name, μπορεί να ληφθεί υπόψη ως δευτερεύον κριτήριο.
        Στην ανά-διάλειμμα εφημερία η προτεραιότητα είναι η μείωση των αποκλίσεων στα λεπτά.
        """
        def is_available(t: Teacher) -> bool:
            if on_date is not None and self._is_absent(t.id, on_date):
                return False
            if interval_id is None:
                return t.available_on(weekday)
            return self.db.teacher_interval_available(t, weekday, interval_id)

        candidates = [t for t in teachers if is_available(t) and t.id not in used_today]
        if not candidates and allow_reuse_if_needed:
            candidates = [t for t in teachers if is_available(t)]
        if not candidates:
            return None

        preferred_area_by_teacher = preferred_area_by_teacher or {}

        def area_penalty(t: Teacher) -> int:
            if not area_name:
                return 0
            preferred = preferred_area_by_teacher.get(t.id, "")
            return 0 if preferred == area_name else 1

        # Ετήσια δικαιοσύνη: για κάθε υποψήφιο/α δοκιμάζουμε νοητά την ανάθεση
        # και προτιμούμε εκείνον/η που δίνει τη μικρότερη διαφορά ελάχιστου-μέγιστου
        # στο συνολικό ετήσιο φορτίο. Αυτό εφαρμόζεται και στην ημερήσια εφημερία
        # και στην ανά διάλειμμα, γιατί οι προστριβές συνήθως προκύπτουν στα τελικά σύνολα.
        def annual_spread_if(t: Teacher) -> int:
            vals = []
            for x in teachers:
                val = int(total_minutes.get(x.id, 0))
                if x.id == t.id:
                    val += int(assignment_minutes or 0)
                vals.append(val)
            return (max(vals) - min(vals)) if vals else 0

        # v0.52: η σειρά των κριτηρίων ακολουθεί τις προτεραιότητες του χρήστη (0 = αγνόηση).
        # Με τις προεπιλογές (έτος 5 > εβδομάδα 3) η σειρά είναι ίδια με των προηγούμενων εκδόσεων.
        crit = []
        w_year, w_week = self.rule_weight("w_fair_year"), self.rule_weight("w_fair_week")
        if w_year > 0:
            crit.append((w_year, 0, lambda t: (annual_spread_if(t), total_minutes.get(t.id, 0) + assignment_minutes)))
        if w_week > 0:
            crit.append((w_week, 1, lambda t: (week_minutes.get((t.id, week_key), 0) + assignment_minutes,)))
        crit.sort(key=lambda c: (-c[0], c[1]))
        def _key(t):
            k = ()
            for _w, _o, fn in crit:
                k += fn(t)
            if not fairness_focus:
                k += (area_penalty(t),)
            return k + (t.name,)
        candidates.sort(key=_key)
        return candidates[0]

    def build_area_rotation_state(self, teachers: List[Teacher], areas: List[sqlite3.Row], regenerate_from: date, mode: str):
        """
        Δημιουργεί κατάσταση εναλλαγής τομέων με βάση όσα έχουν ήδη γίνει/κλειδωθεί.
        - Ημερήσια εφημερία: κάθε νέα ανάθεση του ίδιου εκπαιδευτικού πάει στον επόμενο τομέα.
        - Ανά διάλειμμα: δεν επιβάλλεται σταθερός τομέας εβδομάδας, ώστε να μειώνονται οι αποκλίσεις στα λεπτά.
        """
        area_names = [a["name"] for a in areas]
        area_index = {name: i for i, name in enumerate(area_names)}
        teacher_ids = {t.id for t in teachers}
        next_daily_area = {t.id: 0 for t in teachers}
        week_area: Dict[Tuple[int, str], str] = {}
        week_count = {t.id: 0 for t in teachers}

        previous = self.db.conn.execute(
            """
            SELECT teacher_id,d,area_name,mode
            FROM assignments
            WHERE teacher_id IS NOT NULL AND (d < ? OR locked=1)
            ORDER BY d,start_time,id
            """,
            (regenerate_from.isoformat(),),
        ).fetchall()

        seen_weeks = {t.id: [] for t in teachers}
        for r in previous:
            tid = r["teacher_id"]
            if tid not in teacher_ids or r["area_name"] not in area_index:
                continue
            if mode == "daily":
                next_daily_area[tid] = (area_index[r["area_name"]] + 1) % max(len(area_names), 1)
            else:
                pd = parse_date(r["d"])
                wk = f"{pd.isocalendar().year}-{pd.isocalendar().week:02d}"
                if (tid, wk) not in week_area:
                    week_area[(tid, wk)] = r["area_name"]
                    if wk not in seen_weeks[tid]:
                        seen_weeks[tid].append(wk)

        for tid, weeks in seen_weeks.items():
            week_count[tid] = len(weeks)

        return area_names, next_daily_area, week_area, week_count

    def preferred_daily_area(self, teacher_id: int, area_names: List[str], next_daily_area: Dict[int, int]) -> str:
        if not area_names:
            return ""
        return area_names[next_daily_area.get(teacher_id, 0) % len(area_names)]

    def mark_daily_area_used(self, teacher_id: int, area_names: List[str], next_daily_area: Dict[int, int]) -> None:
        if area_names:
            next_daily_area[teacher_id] = (next_daily_area.get(teacher_id, 0) + 1) % len(area_names)

    def balance_break_rows(
        self,
        rows: List[Tuple[str, str, str, str, str, str, int, int, str]],
        teachers: List[Teacher],
        intervals: List[sqlite3.Row],
        total_minutes: Dict[int, int],
    ) -> Tuple[List[Tuple[str, str, str, str, str, str, int, int, str]], int, int]:
        """
        Ισχυρότερο δεύτερο πέρασμα ισοκατανομής για την παραγωγή «ανά διάλειμμα».
        Δεν κοιτά μόνο τον απολύτως μικρότερο εκπαιδευτικό, αλλά δοκιμάζει μετακινήσεις
        από τους/τις περισσότερο επιβαρυμένους/ες προς κάθε διαθέσιμο/η λιγότερο
        επιβαρυμένο/η εκπαιδευτικό και κρατά μόνο ό,τι μειώνει τη συνολική ετήσια απόκλιση.
        """
        if not rows or not teachers:
            return rows, 0, 0
        interval_id_by_name = {r["name"]: r["id"] for r in intervals}
        rows = list(rows)

        def slot_key(row):
            return (row[0], row[2], row[3], row[4])  # ημερομηνία, ζώνη, ώρα αρχής/λήξης

        def rebuild_used_slots():
            used: Dict[Tuple[str, str, str, str], set] = {}
            for row in rows:
                used.setdefault(slot_key(row), set()).add(row[6])
            return used

        def spread(totals: Optional[Dict[int, int]] = None) -> int:
            totals = totals or total_minutes
            vals = [int(totals.get(t.id, 0)) for t in teachers]
            return (max(vals) - min(vals)) if vals else 0

        changes = 0
        old_spread = spread()
        max_passes = max(500, len(rows) * 8)
        for _ in range(max_passes):
            current_spread = spread()
            if current_spread <= 20:
                break
            used_slots = rebuild_used_slots()
            best = None
            best_spread = current_spread
            teacher_order_high = sorted(teachers, key=lambda t: total_minutes.get(t.id, 0), reverse=True)
            teacher_order_low = sorted(teachers, key=lambda t: total_minutes.get(t.id, 0))
            for high in teacher_order_high:
                high_rows = [i for i, r in enumerate(rows) if r[6] == high.id]
                high_rows.sort(key=lambda i: rows[i][7], reverse=True)
                for idx in high_rows:
                    row = rows[idx]
                    d_iso, _weekday_name, interval_name, _st, _en, _area, _teacher_id, mins, _mode = row
                    d_obj = parse_date(d_iso)
                    weekday = d_obj.weekday()
                    interval_id = interval_id_by_name.get(interval_name)
                    if interval_id is None:
                        continue
                    sk = slot_key(row)
                    for low in teacher_order_low:
                        if low.id == high.id:
                            continue
                        if total_minutes.get(low.id, 0) >= total_minutes.get(high.id, 0):
                            continue
                        if self._is_absent(low.id, d_obj) or not self.db.teacher_interval_available(low, weekday, interval_id):
                            continue
                        if low.id in used_slots.get(sk, set()):
                            continue
                        new_totals = dict(total_minutes)
                        new_totals[high.id] = new_totals.get(high.id, 0) - mins
                        new_totals[low.id] = new_totals.get(low.id, 0) + mins
                        new_spread = spread(new_totals)
                        if new_spread < best_spread:
                            # Δευτερεύον κριτήριο: να μη γυρίζει άδικα σε ήδη φορτωμένο/η.
                            best = (idx, row, high, low, new_totals)
                            best_spread = new_spread
                            if new_spread <= 20:
                                break
                    if best_spread <= 20:
                        break
                if best_spread <= 20:
                    break
            if not best:
                break
            idx, row, _high, low, new_totals = best
            rows[idx] = (row[0], row[1], row[2], row[3], row[4], row[5], low.id, row[7], row[8])
            total_minutes.clear()
            total_minutes.update(new_totals)
            changes += 1
        return rows, changes, spread() - old_spread

    def daily_fairness_state(self, teachers: List[Teacher]) -> Tuple[Dict[int, int], Dict[int, int]]:
        """Επιστρέφει πραγματικές ημέρες εφημερίας και πλήθος πρόσθετων εβδομαδιαίων εφημεριών.
        Οι αντικαταστάσεις μετρούν στον εκπαιδευτικό που τελικά βρίσκεται στην ανάθεση.
        """
        teacher_ids = {t.id for t in teachers}
        annual_days = {t.id: 0 for t in teachers}
        extra_counts = {t.id: 0 for t in teachers}
        rows = self.db.conn.execute(
            "SELECT teacher_id,d FROM assignments WHERE mode='daily' ORDER BY d,id"
        ).fetchall()
        per_teacher_days: Dict[int, set] = {t.id: set() for t in teachers}
        per_week: Dict[Tuple[int, str], set] = {}
        for r in rows:
            tid = r["teacher_id"]
            if tid not in teacher_ids:
                continue
            per_teacher_days[tid].add(r["d"])
            d_obj = parse_date(r["d"])
            wk = f"{d_obj.isocalendar().year}-{d_obj.isocalendar().week:02d}"
            per_week.setdefault((tid, wk), set()).add(r["d"])
        for tid, days in per_teacher_days.items():
            annual_days[tid] = len(days)
        for (tid, _wk), days in per_week.items():
            extra_counts[tid] += max(0, len(days) - 1)
        return annual_days, extra_counts

    def plan_daily_week(
        self,
        week_days: List[date],
        teachers: List[Teacher],
        areas: List[sqlite3.Row],
        intervals: List[sqlite3.Row],
        area_names: List[str],
        next_daily_area: Dict[int, int],
        annual_days: Dict[int, int],
        extra_counts: Dict[int, int],
        relax: int = 0,
    ) -> Tuple[List[Tuple[str, str, str, str, str, str, int, int, str]], List[str], List[str]]:
        """Σχεδιάζει μία εβδομάδα ημερήσιας εφημερίας.

        Προτεραιότητες:
        1. μία βασική εφημερία ανά εκπαιδευτικό, κατά προτίμηση στην κύρια ημέρα,
        2. μετακίνηση στην εναλλακτική μόνο όταν χρειάζεται για να χωρέσουν οι θέσεις,
        3. οι πρόσθετες εφημερίες ανατίθενται κυκλικά σε όσους συμμετέχουν στον κύκλο
           και μόνο στην εναλλακτική ημέρα τους,
        4. οι ήδη πραγματοποιημένες/διατηρημένες αναθέσεις της ίδιας εβδομάδας μετρούν.
        """
        if not week_days:
            return [], [], []
        teacher_by_id = {t.id: t for t in teachers}
        monday = week_days[0] - timedelta(days=week_days[0].weekday())
        friday = monday + timedelta(days=4)
        existing = self.db.conn.execute(
            "SELECT teacher_id,d,area_name FROM assignments WHERE mode='daily' AND d BETWEEN ? AND ? ORDER BY d,id",
            (monday.isoformat(), friday.isoformat()),
        ).fetchall()
        existing_days: Dict[int, set] = {t.id: set() for t in teachers}
        existing_areas: Dict[str, set] = {}
        for r in existing:
            if r["teacher_id"] in existing_days:
                existing_days[r["teacher_id"]].add(r["d"])
            existing_areas.setdefault(r["d"], set()).add(r["area_name"])

        meta = {}
        remaining_areas: Dict[str, List[str]] = {}
        for d in week_days:
            day_intervals, partial = self.intervals_for_day(d, intervals)
            if partial:
                limits = [self.area_limit_for_interval(d, r["name"], len(areas)) for r in day_intervals]
                capacity = max(limits) if limits else 0
            else:
                capacity = len(areas)
            allowed_area_names = [a["name"] for a in list(areas)[:capacity]]
            used = existing_areas.get(d.isoformat(), set())
            rem = [a for a in allowed_area_names if a not in used]
            remaining_areas[d.isoformat()] = rem
            day_minutes = sum(minutes_between(r["start_time"], r["end_time"]) for r in day_intervals)
            label = "Ημερήσια εφημερία" if not partial else "Ειδική ημερήσια εφημερία"
            meta[d.isoformat()] = {
                "d": d,
                "label": label,
                "start": day_intervals[0]["start_time"] if day_intervals else "",
                "end": day_intervals[-1]["end_time"] if day_intervals else "",
                "minutes": day_minutes,
            }

        if relax >= 4:
            # Τελευταία λύση: δεν υπάρχουν αρκετοί διαθέσιμοι για όλους τους τομείς. Γεμίζουμε όσους γίνεται.
            for d in week_days:
                n_av = sum(1 for t in teachers if (t.available_on(d.weekday()) or relax >= 3) and not self._is_absent(t.id, d))
                remaining_areas[d.isoformat()] = remaining_areas[d.isoformat()][:n_av]
        total_slots = sum(len(v) for v in remaining_areas.values())
        if total_slots <= 0:
            return [], [], []

        target_by_weekday = {d.weekday(): d for d in week_days}

        def possible_dates(t: Teacher, kind: str) -> List[Tuple[date, int]]:
            possible = [d for d in week_days if (t.available_on(d.weekday()) or relax >= 3) and not self._is_absent(t.id, d) and remaining_areas.get(d.isoformat())]
            alt_days=self.db.teacher_alternate_days(t.id, getattr(t,"alternate_day",-1))
            ranked: List[Tuple[date, int]] = []
            seen = set()
            if kind != "extra" and t.primary_day >= 0:
                d = target_by_weekday.get(t.primary_day)
                if d and d in possible:
                    ranked.append((d, 0)); seen.add(d.isoformat())
            # Οι εναλλακτικές δοκιμάζονται αυστηρά με τη σειρά που δήλωσε ο χρήστης.
            for priority,wd in enumerate(alt_days,1):
                d=target_by_weekday.get(wd)
                if d and d in possible and d.isoformat() not in seen and d.isoformat() not in existing_days.get(t.id,set()):
                    ranked.append((d, (priority-1)*25 if kind=="extra" else priority*25)); seen.add(d.isoformat())
            if kind == "extra":
                if relax >= 1:
                    # Χαλάρωση: όταν δεν βρέθηκε λύση, η πρόσθετη εφημερία επιτρέπεται και σε άλλη διαθέσιμη ημέρα.
                    for d in possible:
                        if d.isoformat() not in seen and d.isoformat() not in existing_days.get(t.id, set()):
                            ranked.append((d, 150 + d.weekday() + (0 if t.available_on(d.weekday()) else 50000))); seen.add(d.isoformat())
                return ranked
            for d in possible:
                if d.isoformat() not in seen:
                    ranked.append((d, 250 + d.weekday() + (0 if t.available_on(d.weekday()) else 50000)))
            return ranked

        base_candidates = [t for t in teachers if not existing_days.get(t.id)]
        base_candidates.sort(key=lambda t: (annual_days.get(t.id, 0), 0 if t.primary_day >= 0 else 1, t.name))
        base_items_all = []
        for t in base_candidates:
            opts = possible_dates(t, "base")
            if opts:
                base_items_all.append(("base", t, opts))

        # Όταν υπάρχουν περισσότεροι εκπαιδευτικοί από τις διαθέσιμες εβδομαδιαίες
        # θέσεις, δεν αφήνουμε σταθερά τους ίδιους εκτός. Δοκιμάζουμε συνδυασμούς
        # με κύριο κριτήριο την ετήσια ισοκατανομή και μετά τη δυνατότητα να
        # ικανοποιηθούν οι κύριες/εναλλακτικές ημέρες. Έτσι δημιουργείται στην
        # πράξη κυκλική απαλλαγή χωρίς ξεχωριστό χειροκίνητο κύκλο.
        if len(base_items_all) <= total_slots:
            base_candidate_sets = [base_items_all]
        else:
            pool_size = min(len(base_items_all), max(total_slots + 6, 14))
            # Προτιμώνται όσοι έχουν λιγότερες ετήσιες ημέρες. Σε ισοβαθμία
            # προηγούνται οι πιο περιορισμένοι ως προς τις δυνατές ημέρες.
            pool = sorted(
                base_items_all,
                key=lambda x: (annual_days.get(x[1].id, 0), len(x[2]), x[1].name),
            )[:pool_size]
            combos = itertools.islice(itertools.combinations(pool, total_slots), COMBO_LIMIT)
            scored = []
            for combo in combos:
                selected_ids = {item[1].id for item in combo}
                projected = {t.id: annual_days.get(t.id, 0) + (1 if t.id in selected_ids else 0) for t in teachers}
                vals = list(projected.values())
                spread = (max(vals) - min(vals)) if vals else 0
                # Δευτερεύον κριτήριο: μικρότερη συνολική τετραγωνική απόκλιση.
                mean = (sum(vals) / len(vals)) if vals else 0
                variance_score = sum((v - mean) ** 2 for v in vals)
                current_load = sum(annual_days.get(item[1].id, 0) for item in combo)
                scored.append((spread, variance_score, current_load, tuple(item[1].name for item in combo), list(combo)))
                if len(scored) > 12000:
                    scored.sort(key=lambda x: x[:4])
                    scored = scored[:1200]
            scored.sort(key=lambda x: x[:4])
            base_candidate_sets = [x[4] for x in scored[:1200]]

        best_assignment = None
        nodes_week = [0]
        selected_extra: Tuple[Teacher, ...] = tuple()
        best_cost = None
        selected_base_ids = set()

        for base_items in base_candidate_sets:
            if nodes_week[0] > NODE_BUDGET and best_assignment is not None:
                break
            extras_needed = max(0, total_slots - len(base_items))
            eligible = [t for t in teachers if (t.extra_cycle or relax >= 2) and possible_dates(t, "extra")]
            eligible.sort(key=lambda t: (extra_counts.get(t.id, 0), annual_days.get(t.id, 0), t.name))

            candidate_sets = [tuple()]
            if extras_needed:
                pool = eligible[: min(len(eligible), max(extras_needed + 10, 14))]
                if len(pool) >= extras_needed:
                    candidate_sets = list(itertools.islice(itertools.combinations(pool, extras_needed), COMBO_LIMIT))
                    candidate_sets.sort(key=lambda combo: (
                        sum(extra_counts.get(t.id, 0) for t in combo),
                        max([extra_counts.get(t.id, 0) for t in combo] or [0]),
                        sum(annual_days.get(t.id, 0) for t in combo),
                        tuple(t.name for t in combo),
                    ))
                    candidate_sets = candidate_sets[:2500]
                else:
                    candidate_sets = []

            for extra_combo in candidate_sets:
                if nodes_week[0] > NODE_BUDGET and best_assignment is not None:
                    break
                items = list(base_items)
                valid_combo = True
                for t in extra_combo:
                    opts = possible_dates(t, "extra")
                    if not opts:
                        valid_combo = False; break
                    items.append(("extra", t, opts))
                if not valid_combo or len(items) != total_slots:
                    continue

                items = sorted(items, key=lambda x: (len(x[2]), 0 if x[0] == "extra" else 1, x[1].name))
                capacities = {d_iso: len(v) for d_iso, v in remaining_areas.items()}
                used_by_teacher = {tid: set(days) for tid, days in existing_days.items()}
                current = {}
                local_best = {"cost": None, "assignment": None}

                def backtrack(i: int, cost: int):
                    nodes_week[0] += 1
                    if nodes_week[0] > NODE_BUDGET and local_best["cost"] is not None:
                        return
                    if local_best["cost"] is not None and cost >= local_best["cost"]:
                        return
                    if i >= len(items):
                        local_best["cost"] = cost
                        local_best["assignment"] = dict(current)
                        return
                    kind, t, opts = items[i]
                    for d, base_cost in opts:
                        ds = d.isoformat()
                        if capacities.get(ds, 0) <= 0:
                            continue
                        if ds in used_by_teacher.setdefault(t.id, set()):
                            continue
                        extra_cost = base_cost + (annual_days.get(t.id, 0) if base_cost >= 200 else 0)
                        capacities[ds] -= 1
                        used_by_teacher[t.id].add(ds)
                        current[(kind, t.id)] = d
                        backtrack(i + 1, cost + extra_cost)
                        current.pop((kind, t.id), None)
                        used_by_teacher[t.id].remove(ds)
                        capacities[ds] += 1

                backtrack(0, 0)
                if local_best["assignment"] is not None:
                    # Πρώτα η ετήσια δικαιοσύνη της επιλογής προσώπων και μετά
                    # το κόστος μετακίνησης από κύρια/εναλλακτική ημέρα.
                    base_ids = {item[1].id for item in base_items}
                    projected = {t.id: annual_days.get(t.id, 0) + (1 if t.id in base_ids else 0) for t in teachers}
                    for t in extra_combo:
                        projected[t.id] = projected.get(t.id, 0) + 1
                    vals = list(projected.values())
                    fairness_spread = (max(vals) - min(vals)) if vals else 0
                    mean = (sum(vals) / len(vals)) if vals else 0
                    fairness_var = sum((v - mean) ** 2 for v in vals)
                    combined_cost = (fairness_spread * 1000000) + (fairness_var * 1000) + local_best["cost"]
                    if best_cost is None or combined_cost < best_cost:
                        best_cost = combined_cost
                        best_assignment = local_best["assignment"]
                        selected_extra = extra_combo
                        selected_base_ids = base_ids

        # Αν υπάρχουν περισσότεροι εκπαιδευτικοί από θέσεις, οι μη επιλεγμένοι
        # αυτής της εβδομάδας είναι οι κυκλικά απαλλασσόμενοι. Δεν χρειάζεται
        # ξεχωριστή αποθήκευση: τα ετήσια σύνολα τούς φέρνουν μπροστά στην
        # επόμενη παραγωγή.
        warnings = []
        if best_assignment is None and relax < 4:
            return self.plan_daily_week(week_days, teachers, areas, intervals, area_names, next_daily_area, annual_days, extra_counts, relax=relax + 1)
        if best_assignment is not None and relax > 0:
            warnings.append(
                f"{fmt_date(monday)}–{fmt_date(friday)}: λόγω απουσιών/περιορισμένης διαθεσιμότητας δεν ήταν δυνατή η τήρηση των δηλωμένων εναλλακτικών ημερών"
                + (" και δεν καλύφθηκαν όλοι οι τομείς." if relax >= 4 else (", και κάποιες εφημερίες ανατέθηκαν εκτός δηλωμένης διαθεσιμότητας." if relax == 3 else "."))
            )
        if best_assignment is None:
            warnings.append(
                f"{fmt_date(monday)}–{fmt_date(friday)}: δεν βρέθηκε πλήρης κατανομή με τις κύριες/εναλλακτικές ημέρες."
            )
            return [], warnings, []

        planned_by_date: Dict[str, List[Teacher]] = {d.isoformat(): [] for d in week_days}
        for (kind, tid), d in best_assignment.items():
            planned_by_date[d.isoformat()].append(teacher_by_id[tid])

        rows = []
        for d in week_days:
            ds = d.isoformat()
            teachers_for_day = planned_by_date.get(ds, [])
            free_areas = list(remaining_areas.get(ds, []))
            # Αντιστοίχιση τομέων με σεβασμό στην εναλλαγή του κάθε εκπαιδευτικού.
            while teachers_for_day and free_areas:
                match_idx = None
                match_area = None
                for i, t in enumerate(teachers_for_day):
                    pref = self.preferred_daily_area(t.id, area_names, next_daily_area)
                    if pref in free_areas:
                        match_idx = i; match_area = pref; break
                if match_idx is None:
                    match_idx = 0; match_area = free_areas[0]
                t = teachers_for_day.pop(match_idx)
                free_areas.remove(match_area)
                m = meta[ds]
                rows.append((ds, DAYS_GR[d.weekday()], m["label"], m["start"], m["end"], match_area, t.id, m["minutes"], "daily"))
                self.mark_daily_area_used(t.id, area_names, next_daily_area)
                annual_days[t.id] = annual_days.get(t.id, 0) + 1

        for t in selected_extra:
            extra_counts[t.id] = extra_counts.get(t.id, 0) + 1
        extra_names = [t.name for t in selected_extra]
        return rows, warnings, extra_names

    def balance_daily_rows_global(
        self,
        rows: List[Tuple[str, str, str, str, str, str, int, int, str]],
        teachers: List[Teacher],
        base_annual_days: Dict[int, int],
        generate_from: date,
        generate_to: date,
    ) -> Tuple[List[Tuple[str, str, str, str, str, str, int, int, str]], int, int]:
        """Τελικό συνολικό πέρασμα ισοκατανομής για την ημερήσια εφημερία.

        Η εβδομαδιαία παραγωγή σέβεται κύρια/εναλλακτικές ημέρες και εβδομαδιαίες
        ανάγκες. Αυτό το δεύτερο πέρασμα εξετάζει ΟΛΟ το υπόλοιπο διάστημα και
        αντικαθιστά μόνο μελλοντικές, μη κλειδωμένες αναθέσεις όταν η αλλαγή
        μειώνει πραγματικά την τελική ετήσια απόκλιση, χωρίς να δημιουργεί
        διπλή εφημερία την ίδια ημέρα ή μη επιτρεπτό εβδομαδιαίο πλήθος.
        """
        if not rows or not teachers:
            return rows, 0, 0
        rows = list(rows)
        tids = {t.id for t in teachers}
        teacher_by_id = {t.id: t for t in teachers}

        # Κλειδωμένες/διατηρημένες αναθέσεις μέσα στο υπό ανακατανομή διάστημα.
        fixed = self.db.conn.execute(
            """SELECT teacher_id,d FROM assignments
               WHERE mode='daily' AND teacher_id IS NOT NULL
                 AND d BETWEEN ? AND ?""",
            (generate_from.isoformat(), generate_to.isoformat()),
        ).fetchall()
        fixed_by_date: Dict[str, set] = {}
        fixed_week_counts: Dict[Tuple[int, str], int] = {}
        for r in fixed:
            tid = r["teacher_id"]
            if tid not in tids:
                continue
            ds = r["d"]
            fixed_by_date.setdefault(ds, set()).add(tid)
            d_obj = parse_date(ds)
            wk = f"{d_obj.isocalendar().year}-{d_obj.isocalendar().week:02d}"
            fixed_week_counts[(tid, wk)] = fixed_week_counts.get((tid, wk), 0) + 1

        def pref_rank(t: Teacher, weekday: int, weekly_count_before: int) -> Optional[int]:
            """0=κύρια, 1..=εναλλακτικές, 100+=λοιπή διαθέσιμη ημέρα.
            Για δεύτερη εφημερία εβδομάδας απαιτείται συμμετοχή στον πρόσθετο κύκλο
            και η ημέρα πρέπει να είναι μία από τις εναλλακτικές.
            """
            if not t.available_on(weekday):
                return None
            alts = self.db.teacher_alternate_days(t.id, getattr(t, "alternate_day", -1))
            if weekly_count_before >= 1:
                if not t.extra_cycle:
                    return None
                if weekday in alts:
                    return 1 + alts.index(weekday)
                return None
            if t.primary_day == weekday:
                return 0
            if weekday in alts:
                return 1 + alts.index(weekday)
            return 100 + weekday

        def rebuild_state():
            counts = {t.id: int(base_annual_days.get(t.id, 0)) for t in teachers}
            generated_by_date: Dict[str, set] = {}
            generated_week_counts: Dict[Tuple[int, str], int] = {}
            week_slots: Dict[str, int] = {}
            for r in rows:
                tid = r[6]; ds = r[0]
                if tid not in counts:
                    continue
                counts[tid] += 1
                generated_by_date.setdefault(ds, set()).add(tid)
                d_obj = parse_date(ds)
                wk = f"{d_obj.isocalendar().year}-{d_obj.isocalendar().week:02d}"
                generated_week_counts[(tid, wk)] = generated_week_counts.get((tid, wk), 0) + 1
                week_slots[wk] = week_slots.get(wk, 0) + 1
            # Προσθέτουμε στα slots της εβδομάδας και τις κλειδωμένες/διατηρημένες.
            for (tid, wk), c in fixed_week_counts.items():
                week_slots[wk] = week_slots.get(wk, 0) + c
            return counts, generated_by_date, generated_week_counts, week_slots

        def metrics(counts):
            vals = [counts.get(t.id, 0) for t in teachers]
            if not vals:
                return (0, 0.0)
            spread = max(vals) - min(vals)
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals)
            return spread, variance

        counts, generated_by_date, generated_week_counts, week_slots = rebuild_state()
        old_spread, _ = metrics(counts)
        changes = 0
        max_passes = max(200, len(rows) * 6)

        for _ in range(max_passes):
            current_spread, current_var = metrics(counts)
            if current_spread <= 1:
                break
            best = None
            best_key = (current_spread, current_var, 9999, "")

            high_order = sorted(teachers, key=lambda t: (-counts.get(t.id, 0), t.name))
            low_order = sorted(teachers, key=lambda t: (counts.get(t.id, 0), t.name))

            for high in high_order:
                high_rows = [i for i, r in enumerate(rows) if r[6] == high.id]
                for idx in high_rows:
                    row = rows[idx]
                    ds = row[0]
                    d_obj = parse_date(ds)
                    wd = d_obj.weekday()
                    wk = f"{d_obj.isocalendar().year}-{d_obj.isocalendar().week:02d}"
                    high_week = fixed_week_counts.get((high.id, wk), 0) + generated_week_counts.get((high.id, wk), 0)
                    total_slots = week_slots.get(wk, 0)
                    nteachers = max(1, len(teachers))
                    min_week = total_slots // nteachers
                    max_week = (total_slots + nteachers - 1) // nteachers

                    for low in low_order:
                        if low.id == high.id:
                            continue
                        if counts.get(low.id, 0) >= counts.get(high.id, 0):
                            continue
                        if low.id in fixed_by_date.get(ds, set()) or low.id in generated_by_date.get(ds, set()):
                            continue
                        low_week = fixed_week_counts.get((low.id, wk), 0) + generated_week_counts.get((low.id, wk), 0)
                        # Μετά τη μετακίνηση πρέπει να παραμένει έγκυρη η εβδομαδιαία κατανομή.
                        if high_week - 1 < min_week:
                            continue
                        if low_week + 1 > max_week:
                            continue
                        if self._is_absent(low.id, d_obj):
                            continue
                        rank = pref_rank(low, wd, low_week)
                        if rank is None:
                            continue

                        new_counts = dict(counts)
                        new_counts[high.id] -= 1
                        new_counts[low.id] += 1
                        new_spread, new_var = metrics(new_counts)
                        if (new_spread, new_var) >= (current_spread, current_var):
                            continue
                        key = (new_spread, new_var, rank, low.name)
                        if key < best_key:
                            best_key = key
                            best = (idx, high, low, ds, wk, new_counts)

            if best is None:
                break
            idx, high, low, ds, wk, new_counts = best
            row = rows[idx]
            rows[idx] = (row[0], row[1], row[2], row[3], row[4], row[5], low.id, row[7], row[8])
            counts = new_counts
            generated_by_date.setdefault(ds, set()).discard(high.id)
            generated_by_date.setdefault(ds, set()).add(low.id)
            generated_week_counts[(high.id, wk)] = max(0, generated_week_counts.get((high.id, wk), 0) - 1)
            generated_week_counts[(low.id, wk)] = generated_week_counts.get((low.id, wk), 0) + 1
            changes += 1

        new_spread, _ = metrics(counts)
        return rows, changes, new_spread - old_spread

    def generate_shift_rows(self, target_days, teachers, total_minutes, week_minutes):
        rows=[]; reserves=[]; warnings=[]
        shifts=self.db.shifts(True)
        if not shifts:
            return rows,reserves,["Δεν έχουν οριστεί ενεργές βάρδιες."]
        # Ετήσιο πλήθος πραγματικών αναθέσεων βάρδιας. Οι αναπληρωματικοί δεν προσμετρώνται.
        annual={t.id:0 for t in teachers}
        for r in self.db.conn.execute("SELECT teacher_id,COUNT(*) c FROM assignments WHERE mode='shifts' GROUP BY teacher_id").fetchall():
            if r["teacher_id"] in annual: annual[r["teacher_id"]]=int(r["c"])
        reserve_counts={t.id:0 for t in teachers}
        for r in self.db.conn.execute("SELECT teacher_id,COUNT(*) c FROM shift_reserves GROUP BY teacher_id").fetchall():
            if r["teacher_id"] in reserve_counts: reserve_counts[r["teacher_id"]]=int(r["c"])
        for d in target_days:
            wd=d.weekday(); week_key=f"{d.isocalendar().year}-{d.isocalendar().week:02d}"
            for sh in shifts:
                ints=self.db.shift_intervals(sh["id"],wd)
                if not ints:
                    continue
                ars=self.db.shift_areas(sh["id"])
                if not ars:
                    continue
                # διάρκεια = άθροισμα πραγματικών διαλειμμάτων/ζωνών της βάρδιας
                mins=sum(max(0,minutes_between(it["start_time"],it["end_time"])) for it in ints)
                start=min(it["start_time"] for it in ints); end=max(it["end_time"] for it in ints)
                used=set()
                candidates=[t for t in teachers if self.db.teacher_shift_available(t,wd,sh["id"]) and not self._is_absent(t.id, d)]
                for ar in ars:
                    available=[t for t in candidates if t.id not in used and self.db.teacher_allowed_for_shift_area(t.id, ar["id"])]
                    if not available:
                        warnings.append(f"{fmt_date(d)} {sh['name']} - {ar['name']}: δεν υπάρχει διαθέσιμος/η εκπαιδευτικός")
                        continue
                    # Πρώτα ετήσια ισοκατανομή, μετά εβδομαδιαία φόρτιση, μετά αλφαβητικά.
                    available.sort(key=lambda t:(annual.get(t.id,0), week_minutes.get((t.id,week_key),0), total_minutes.get(t.id,0), t.name))
                    t=available[0]; used.add(t.id); annual[t.id]=annual.get(t.id,0)+1
                    total_minutes[t.id]=total_minutes.get(t.id,0)+mins; week_minutes[(t.id,week_key)]=week_minutes.get((t.id,week_key),0)+mins
                    rows.append((d.isoformat(),DAYS_GR[wd],sh["name"],start,end,ar["name"],t.id,mins,"shifts",ar["id"]))
                if sh["has_reserve"]:
                    # Ο αναπληρωματικός πρέπει να μπορεί να καλύψει οποιονδήποτε ενεργό τομέα της βάρδιας.
                    rc=[t for t in candidates if t.id not in used and all(self.db.teacher_allowed_for_shift_area(t.id, ar["id"]) for ar in ars)]
                    if rc:
                        rc.sort(key=lambda t:(reserve_counts.get(t.id,0),annual.get(t.id,0),t.name)); rt=rc[0]; reserve_counts[rt.id]=reserve_counts.get(rt.id,0)+1
                        reserves.append((d.isoformat(),sh["id"],rt.id,0,None))
                    else:
                        warnings.append(f"{fmt_date(d)} {sh['name']}: δεν υπάρχει διαθέσιμος/η αναπληρωματικός/ή")
        return rows,reserves,warnings

    def _delete_assignments(self, start: Optional[date] = None, end: Optional[date] = None, include_locked: bool = True) -> int:
        """Διαγράφει αναθέσεις και τις συνδεδεμένες αλλαγές με ασφαλή σειρά."""
        clauses = []
        params = []
        if start is not None:
            clauses.append("d >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("d <= ?")
            params.append(end.isoformat())
        if not include_locked:
            clauses.append("locked=0")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        ids = [r[0] for r in self.db.conn.execute("SELECT id FROM assignments" + where, params).fetchall()]
        # Οι αναπληρωματικοί βάρδιας ακολουθούν το ίδιο διάστημα διαγραφής.
        reserve_clauses=[]; reserve_params=[]
        if start is not None: reserve_clauses.append("d >= ?"); reserve_params.append(start.isoformat())
        if end is not None: reserve_clauses.append("d <= ?"); reserve_params.append(end.isoformat())
        reserve_where=(" WHERE "+" AND ".join(reserve_clauses)) if reserve_clauses else ""
        self.db.conn.execute("DELETE FROM shift_reserves"+reserve_where,reserve_params)
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        self.db.conn.execute(f"DELETE FROM duty_changes WHERE assignment_id IN ({marks})", ids)
        self.db.conn.execute(f"DELETE FROM assignments WHERE id IN ({marks})", ids)
        return len(ids)

    # ---- πρόοδος παραγωγής (χωρίς νήματα: ανανέωση του UI μέσα στον βρόχο, μόνο όταν αργεί) ----
    GEN_PROGRESS_DELAY = 0.8

    def _generate_schedule_impl(self):
        self._absent_cache = None
        mode = self.mode_var.get()
        self.db.set_setting("mode", mode)
        teachers = self.db.active_teachers()
        areas = self.db.areas(True)
        intervals = self.db.intervals(True)
        if not teachers:
            messagebox.showerror("Δεν υπάρχουν εκπαιδευτικοί", "Πρόσθεσε ενεργούς εκπαιδευτικούς που συμμετέχουν σε εφημερίες.")
            return
        if not areas:
            messagebox.showerror("Δεν υπάρχουν τομείς", "Πρόσθεσε ενεργούς τομείς εφημερίας.")
            return
        if not intervals:
            messagebox.showerror("Δεν υπάρχουν διαλείμματα", "Πρόσθεσε ενεργά χρονικά διαστήματα.")
            return
        if mode == "breaks":
            custom_rows = self.db.conn.execute("SELECT COUNT(*) FROM teacher_interval_availability").fetchone()[0]
            if custom_rows == 0:
                if not messagebox.askyesno(
                    "Διαθεσιμότητα ανά διάλειμμα",
                    "Δεν έχει οριστεί ειδική διαθεσιμότητα ανά διάλειμμα. Θα χρησιμοποιηθεί μόνο η γενική διαθεσιμότητα ανά ημέρα.\n\nΝα συνεχιστεί η παραγωγή;"
                ):
                    return

        scope = getattr(self, "regenerate_scope_var", _Var("continue")).get()
        try:
            school_start = parse_date(self.db.get_setting("start_date"))
            school_end = parse_date(self.db.get_setting("end_date"))
            if scope == "all":
                generate_from, generate_to = school_start, school_end
            else:
                generate_from = parse_date(self.regenerate_from_var.get())
                generate_to = school_end if scope == "continue" else parse_date(self.regenerate_to_var.get())
            if generate_to < generate_from:
                raise ValueError("Η ημερομηνία «Έως» πρέπει να είναι ίδια ή μεταγενέστερη από την «Από».")
        except ValueError as e:
            messagebox.showerror("Λάθος ημερομηνία", str(e))
            return

        try:
            _old_snapshot = self._snapshot_old_assignments(generate_from, generate_to) if self.stay_enabled() else []
        except Exception:
            _old_snapshot = []
        try:
            if scope == "continue":
                deleted = self._delete_assignments(generate_from, None, include_locked=False)
                action_text = f"Συνέχιση/ανακατανομή από {fmt_date(generate_from)} με διατήρηση κλειδωμένων"
            elif scope == "range_keep":
                deleted = self._delete_assignments(generate_from, generate_to, include_locked=False)
                action_text = f"Ανακατανομή διαστήματος {fmt_date(generate_from)}–{fmt_date(generate_to)} με διατήρηση κλειδωμένων"
            elif scope == "range":
                if not messagebox.askyesno(
                    "Διαγραφή και αναδημιουργία διαστήματος",
                    f"Θα διαγραφούν ΟΛΕΣ οι αναθέσεις από {fmt_date(generate_from)} έως {fmt_date(generate_to)}, ακόμη και οι κλειδωμένες, και θα δημιουργηθούν ξανά.\n\nΝα συνεχιστεί;"
                ):
                    return
                deleted = self._delete_assignments(generate_from, generate_to, include_locked=True)
                action_text = f"Πλήρης αναδημιουργία διαστήματος {fmt_date(generate_from)}–{fmt_date(generate_to)}"
            else:
                if not messagebox.askyesno(
                    "Πλήρης αναδημιουργία σχολικού έτους",
                    f"Θα διαγραφεί ΟΛΟ το πρόγραμμα του σχολικού έτους {self.db.get_setting('school_year')} και θα παραχθεί από την αρχή.\n\nΝα συνεχιστεί;"
                ):
                    return
                deleted = self._delete_assignments(school_start, school_end, include_locked=True)
                action_text = f"Πλήρης αναδημιουργία σχολικού έτους {self.db.get_setting('school_year')}"
            # Σκόπιμα ΧΩΡΙΣ commit εδώ: η διαγραφή και η νέα παραγωγή γίνονται σε μία συναλλαγή.
        except Exception as e:
            self.db.conn.rollback()
            messagebox.showerror("Σφάλμα διαγραφής", f"Δεν ήταν δυνατή η προετοιμασία της αναδημιουργίας:\n{e}")
            return

        total_minutes = {t.id: 0 for t in teachers}
        week_minutes: Dict[Tuple[int, str], int] = {}
        if scope == "continue":
            previous = self.db.conn.execute(
                "SELECT teacher_id,d,minutes FROM assignments WHERE d < ? OR locked=1",
                (generate_from.isoformat(),),
            ).fetchall()
        elif scope == "range_keep":
            previous = self.db.conn.execute("SELECT teacher_id,d,minutes FROM assignments").fetchall()
        elif scope == "range":
            previous = self.db.conn.execute(
                "SELECT teacher_id,d,minutes FROM assignments WHERE d < ? OR d > ?",
                (generate_from.isoformat(), generate_to.isoformat()),
            ).fetchall()
        else:
            previous = []
        for pr in previous:
            if pr["teacher_id"] in total_minutes:
                total_minutes[pr["teacher_id"]] += int(pr["minutes"] or 0)
                pd = parse_date(pr["d"])
                wk = f"{pd.isocalendar().year}-{pd.isocalendar().week:02d}"
                week_minutes[(pr["teacher_id"], wk)] = week_minutes.get((pr["teacher_id"], wk), 0) + int(pr["minutes"] or 0)

        area_names, next_daily_area, week_area, week_count = self.build_area_rotation_state(teachers, areas, generate_from, mode)
        target_days = [x for x in self.school_days() if generate_from <= x <= generate_to]
        rows = []
        no_candidate = []
        daily_extra_log = []

        if mode == "daily":
            annual_days, extra_counts = self.daily_fairness_state(teachers)
            daily_base_annual_days = dict(annual_days)
            weeks: Dict[Tuple[int, int], List[date]] = {}
            for d in target_days:
                iso = d.isocalendar()
                weeks.setdefault((iso.year, iso.week), []).append(d)
            _week_total = len(weeks)
            for _wi, (_week_key, week_days) in enumerate(sorted(weeks.items()), 1):
                self._gen_progress_step(_wi - 1, _week_total, f"Εβδομάδα {fmt_date(min(week_days))}")
                week_rows, warnings, extra_names = self.plan_daily_week(
                    sorted(week_days), teachers, areas, intervals, area_names, next_daily_area,
                    annual_days, extra_counts,
                )
                rows.extend(week_rows)
                no_candidate.extend(warnings)
                if extra_names:
                    monday = min(week_days) - timedelta(days=min(week_days).weekday())
                    daily_extra_log.append(f"{fmt_date(monday)}: " + ", ".join(extra_names))
                # Οι γραμμές δεν έχουν ακόμη αποθηκευτεί στη βάση. Για να βλέπει η επόμενη
                # εβδομάδα τον σωστό κύκλο, τα extra_counts ενημερώνονται μέσα στο planner.
        elif mode == "shifts":
            shift_rows, reserve_rows, shift_warnings = self.generate_shift_rows(target_days, teachers, total_minutes, week_minutes)
            rows.extend(shift_rows)
            no_candidate.extend(shift_warnings)
        else:
            for _di, d in enumerate(target_days):
                self._gen_progress_step(_di, len(target_days), f"Ημέρα {fmt_date(d)}")
                weekday = d.weekday()
                week_key = f"{d.isocalendar().year}-{d.isocalendar().week:02d}"
                day_intervals, _partial_day = self.intervals_for_day(d, intervals)
                for interval in day_intervals:
                    mins = minutes_between(interval["start_time"], interval["end_time"])
                    interval_area_count = self.area_limit_for_interval(d, interval["name"], len(areas))
                    existing_slot_rows = self.db.conn.execute(
                        """SELECT teacher_id,area_name FROM assignments
                           WHERE d=? AND interval_name=? AND start_time=? AND end_time=?""",
                        (d.isoformat(), interval["name"], interval["start_time"], interval["end_time"]),
                    ).fetchall()
                    used_slot = {r["teacher_id"] for r in existing_slot_rows}
                    used_areas = {r["area_name"] for r in existing_slot_rows}
                    for area in list(areas)[:interval_area_count]:
                        area_name = area["name"]
                        if area_name in used_areas:
                            continue
                        teacher = self.choose_teacher(
                            teachers, weekday, total_minutes, week_minutes, used_slot, week_key,
                            area_name=area_name, preferred_area_by_teacher=None,
                            interval_id=interval["id"], assignment_minutes=mins,
                            fairness_focus=True, allow_reuse_if_needed=False, on_date=d,
                        )
                        if not teacher:
                            no_candidate.append(f"{fmt_date(d)} {DAYS_GR[weekday]} {interval['name']} - {area_name}")
                            continue
                        used_slot.add(teacher.id)
                        total_minutes[teacher.id] += mins
                        week_minutes[(teacher.id, week_key)] = week_minutes.get((teacher.id, week_key), 0) + mins
                        rows.append((d.isoformat(), DAYS_GR[weekday], interval["name"], interval["start_time"], interval["end_time"], area_name, teacher.id, mins, mode))

        balance_changes = 0
        balance_delta = 0
        if mode == "daily" and rows:
            rows, balance_changes, balance_delta = self.balance_daily_rows_global(
                rows, teachers, daily_base_annual_days, generate_from, generate_to
            )
        elif mode == "breaks" and rows:
            rows, balance_changes, balance_delta = self.balance_break_rows(rows, teachers, intervals, total_minutes)

        stay_info = None
        if rows and _old_snapshot and mode in ("daily", "breaks"):
            try:
                rows, _k0, _k1, _n = self._stay_in_place_pass(rows, _old_snapshot, teachers, intervals, mode)
                stay_info = (_k0, _k1, _n, len(_old_snapshot))
            except Exception:
                LOG.exception("Αποτυχία περάσματος «μείνε στη θέση σου»")

        if mode == "shifts":
            self.db.conn.executemany(
                "INSERT INTO assignments(d,weekday,interval_name,start_time,end_time,area_name,teacher_id,minutes,mode,area_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            if 'reserve_rows' in locals():
                self.db.conn.executemany("INSERT OR REPLACE INTO shift_reserves(d,shift_id,teacher_id,activated,replacement_assignment_id) VALUES(?,?,?,?,?)", reserve_rows)
        else:
            # Για παλιούς τρόπους συμπληρώνουμε area_id από την ονομασία όπου είναι δυνατό.
            self.db.conn.executemany(
                "INSERT INTO assignments(d,weekday,interval_name,start_time,end_time,area_name,teacher_id,minutes,mode) VALUES(?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.db.conn.execute("""UPDATE assignments SET area_id=(SELECT ar.id FROM areas ar WHERE ar.name=assignments.area_name LIMIT 1) WHERE area_id IS NULL""")
        self.db.conn.commit()
        self.refresh_all()
        self.generate_text.delete("1.0", "end")
        self.generate_text.insert("end", f"{action_text}\n")
        self.generate_text.insert("end", f"Διαγράφηκαν πριν την αναδημιουργία: {deleted} αναθέσεις.\n")
        self.generate_text.insert("end", f"Δημιουργήθηκαν: {len(rows)} αναθέσεις.\n")
        self.generate_text.insert("end", f"Τρόπος: { {'daily':'ημερήσια εφημερία','breaks':'ανά διάλειμμα','shifts':'ανά βάρδια / ζώνη'}.get(mode,mode) }\n")
        self.generate_text.insert("end", f"Διάστημα παραγωγής: {fmt_date(generate_from)} – {fmt_date(generate_to)}\n")
        if mode == "daily":
            self.generate_text.insert("end", "Ημερήσια λογική: κύρια ημέρα κατά προτεραιότητα · εναλλακτικές με δηλωμένη σειρά προτίμησης · πρόσθετη εφημερία κυκλικά.\n")
            self.generate_text.insert("end", "Εναλλαγή τομέων: ανά εφημερία εκπαιδευτικού.\n")
            if balance_changes:
                self.generate_text.insert("end", f"Συνολική εξισορρόπηση υπολοίπου έτους: {balance_changes} διορθωτικές μετακινήσεις.\n")
            if daily_extra_log:
                self.generate_text.insert("end", "Πρόσθετες εφημερίες που τοποθετήθηκαν:\n")
                for line in daily_extra_log:
                    self.generate_text.insert("end", f"• {line}\n")
        elif mode == "shifts":
            self.generate_text.insert("end", "Λογική βαρδιών: κάθε βάρδια καλύπτει τους επιλεγμένους τομείς με διαφορετικούς εκπαιδευτικούς. Η συμμετοχή εξισορροπείται στο ετήσιο σύνολο.\n")
            self.generate_text.insert("end", "Ο αναπληρωματικός ορίζεται ανά βάρδια αλλά δεν προσμετράται στα σύνολα μέχρι να χρησιμοποιηθεί σε αντικατάσταση.\n")
        else:
            self.generate_text.insert("end", "Εναλλαγή τομέων: επιτρέπεται διαφορετικός τομέας μέσα στην εβδομάδα για καλύτερη ετήσια ισοκατανομή λεπτών.\n")
            self.generate_text.insert("end", f"Δεύτερο πέρασμα εξισορρόπησης: {balance_changes} διορθωτικές μετακινήσεις.\n")
        self.generate_text.insert("end", f"Αναθέσεις εκτός του διαστήματος που λήφθηκαν υπόψη στην ισοκατανομή: {len(previous)}\n")
        self.generate_text.insert("end", f"Ημέρες λειτουργίας που υπολογίστηκαν: {len(target_days)}\n\n")
        if mode == "daily":
            actual_days, actual_extra = self.daily_fairness_state(teachers)
            self.generate_text.insert("end", "Ετήσια σύνολα ημερών ανά εκπαιδευτικό:\n")
            for t in sorted(teachers, key=lambda x: (actual_days.get(x.id, 0), x.name)):
                self.generate_text.insert("end", f"• {t.name}: {actual_days.get(t.id, 0)} ημέρες")
                if t.extra_cycle:
                    self.generate_text.insert("end", f" · πρόσθετες εβδομαδιαίες: {actual_extra.get(t.id, 0)}")
                self.generate_text.insert("end", "\n")
        elif mode == "shifts":
            counts={t.id:0 for t in teachers}
            for r in self.db.conn.execute("SELECT teacher_id,COUNT(*) c FROM assignments WHERE mode='shifts' GROUP BY teacher_id").fetchall():
                if r["teacher_id"] in counts: counts[r["teacher_id"]]=int(r["c"])
            self.generate_text.insert("end", "Ετήσια σύνολα πραγματικών βαρδιών ανά εκπαιδευτικό (χωρίς απλή αναπλήρωση):\n")
            for t in sorted(teachers,key=lambda x:(counts.get(x.id,0),x.name)):
                self.generate_text.insert("end",f"• {t.name}: {counts.get(t.id,0)} αναθέσεις βάρδιας\n")
        else:
            self.generate_text.insert("end", "Ετήσια σύνολα λεπτών ανά εκπαιδευτικό:\n")
            for t in sorted(teachers, key=lambda x: total_minutes.get(x.id, 0)):
                self.generate_text.insert("end", f"• {t.name}: {total_minutes.get(t.id, 0)}΄\n")
        if mode == "daily" and rows:
            teacher_map = {t.id: t for t in teachers}
            primary_ok = alternate_used = other_day = 0
            details = []
            for rr in rows:
                d_iso, tid = rr[0], rr[6]
                t = teacher_map.get(tid)
                if not t or t.primary_day < 0:
                    continue
                wd = parse_date(d_iso).weekday()
                if wd == t.primary_day:
                    primary_ok += 1
                elif wd in self.db.teacher_alternate_days(t.id, getattr(t,"alternate_day",-1)):
                    alternate_used += 1
                    details.append(f"{t.name}: {fmt_date(d_iso)} → εναλλακτική ημέρα")
                else:
                    other_day += 1
                    details.append(f"{t.name}: {fmt_date(d_iso)} → εκτός κύριας/εναλλακτικής")
            self.generate_text.insert("end", "\nΈλεγχος κύριας ημέρας:\n")
            self.generate_text.insert("end", f"• Στην κύρια ημέρα: {primary_ok} αναθέσεις\n")
            self.generate_text.insert("end", f"• Στην εναλλακτική: {alternate_used} αναθέσεις\n")
            self.generate_text.insert("end", f"• Εκτός κύριας/εναλλακτικής: {other_day} αναθέσεις\n")
            if details:
                self.generate_text.insert("end", "Οι αποκλίσεις εμφανίζονται για έλεγχο και προκύπτουν μόνο όταν απαιτείται για να συμπληρωθεί η κατανομή:\n")
                for line in details[:40]:
                    self.generate_text.insert("end", f"  - {line}\n")
        if no_candidate:
            self.generate_text.insert("end", "\nΠροσοχή — χρειάζεται έλεγχος:\n")
            for x in no_candidate[:50]:
                self.generate_text.insert("end", f"- {x}\n")
        if stay_info:
            _k0, _k1, _n, _o = stay_info
            self.generate_text.insert("end", f"\nΜείνε στη θέση σου: {_k1} από {_n} εφημερίες έμειναν ακριβώς όπως ήταν (πριν την προσαρμογή: {_k0}).\n")
        if no_candidate and mode in ("daily", "breaks"):
            try:
                diag = self.diagnose_conflicts(generate_from, generate_to, mode)
            except Exception:
                LOG.exception("Αποτυχία διάγνωσης συγκρούσεων")
                diag = []
            if diag:
                self.generate_text.insert("end", "\nΔιάγνωση συγκρούσεων (γιατί έμειναν κενές θέσεις):\n")
                for line in diag[:30]:
                    self.generate_text.insert("end", line + "\n")
        self._gen_created = len(rows)

    # ======================================================================================
    #  v0.52: ρυθμιζόμενες προτεραιότητες, «μείνε στη θέση σου», διάγνωση συγκρούσεων
    # ======================================================================================
    RULE_DEFAULTS = {"w_stay": 3, "w_day": 4, "w_fair_year": 5, "w_fair_week": 3}

    def rule_weight(self, key: str) -> int:
        try:
            v = int(self.db.get_setting(key, str(self.RULE_DEFAULTS[key])))
        except (TypeError, ValueError):
            v = self.RULE_DEFAULTS[key]
        return max(0, min(5, v))

    def stay_enabled(self) -> bool:
        return self.db.get_setting("stay_in_place", "1") == "1"

    def _snapshot_old_assignments(self, d_from: date, d_to: date) -> list:
        return self.db.conn.execute(
            "SELECT d,interval_name,start_time,end_time,area_name,teacher_id FROM assignments WHERE d BETWEEN ? AND ?",
            (d_from.isoformat(), d_to.isoformat()),
        ).fetchall()

    def _stay_in_place_pass(self, rows, old, teachers, intervals, mode):
        """Ανταλλαγές εκπαιδευτικών ΜΕΣΑ στην ίδια εβδομάδα (ίδια λεπτά) ώστε όσες εφημερίες
        γίνεται να μείνουν όπως ήταν πριν την αναδημιουργία. Οι ανταλλαγές δεν αλλάζουν
        τα σύνολα ανά εκπαιδευτικό, άρα δεν χαλούν την ισοκατανομή· σέβονται διαθεσιμότητα,
        απουσίες και «μία εφημερία ανά ημέρα/ζώνη». Επιστρέφει (rows, κρατήθηκαν_πριν, κρατήθηκαν_μετά, σύνολο)."""
        if not rows or not old or mode not in ("daily", "breaks"):
            return rows, 0, 0, len(rows)
        w_stay, w_day = self.rule_weight("w_stay"), self.rule_weight("w_day")
        rows = [list(r) for r in rows]
        old_exact = {(o["d"], o["interval_name"], o["start_time"], o["end_time"], o["area_name"]): o["teacher_id"] for o in old}
        old_slot: Dict[tuple, set] = {}
        for o in old:
            old_slot.setdefault((o["d"], o["interval_name"], o["start_time"], o["end_time"]), set()).add(o["teacher_id"])

        def kept_count() -> int:
            return sum(1 for r in rows if old_exact.get((r[0], r[2], r[3], r[4], r[5])) == r[6])
        before = kept_count()
        if w_stay <= 0:
            return [tuple(r) for r in rows], before, before, len(rows)

        tmap = {t.id: t for t in teachers}
        alts = {t.id: self.db.teacher_alternate_days(t.id, getattr(t, "alternate_day", -1)) for t in teachers}
        iv_id = {(r["name"], r["start_time"], r["end_time"]): r["id"] for r in intervals}
        daily = (mode == "daily")

        def slot_key(r):
            return (r[0],) if daily else (r[0], r[2], r[3], r[4])

        occ: Dict[tuple, int] = {}
        for r in rows:
            k = (slot_key(r), r[6]); occ[k] = occ.get(k, 0) + 1
        if rows:
            lo, hi = min(r[0] for r in rows), max(r[0] for r in rows)
            for e in self.db.conn.execute("SELECT d,interval_name,start_time,end_time,teacher_id FROM assignments WHERE d BETWEEN ? AND ?", (lo, hi)).fetchall():
                k = (((e["d"],) if daily else (e["d"], e["interval_name"], e["start_time"], e["end_time"])), e["teacher_id"])
                occ[k] = occ.get(k, 0) + 1

        def score(r, tid) -> int:
            if old_exact.get((r[0], r[2], r[3], r[4], r[5])) == tid:
                return 2
            return 1 if tid in old_slot.get((r[0], r[2], r[3], r[4]), ()) else 0

        def cls(tid, d_iso) -> int:
            if not daily:
                return 0
            t = tmap[tid]
            if t.primary_day < 0:
                return 0
            wd = parse_date(d_iso).weekday()
            return 0 if wd == t.primary_day else (1 if wd in alts[tid] else 2)

        def can_take(tid, r) -> bool:
            t = tmap.get(tid)
            if t is None:
                return False
            d = parse_date(r[0])
            if self._is_absent(tid, d):
                return False
            if daily:
                return t.available_on(d.weekday())
            iid = iv_id.get((r[2], r[3], r[4]))
            return iid is not None and self.db.teacher_interval_available(t, d.weekday(), iid)

        def wk(r):
            iso = parse_date(r[0]).isocalendar()
            return (iso.year, iso.week)

        by_week: Dict[tuple, List[int]] = {}
        for i, r in enumerate(rows):
            by_week.setdefault(wk(r), []).append(i)
        for _pass in range(30):
            changed = False
            for idxs in by_week.values():
                for a in range(len(idxs)):
                    for b in range(a + 1, len(idxs)):
                        r1, r2 = rows[idxs[a]], rows[idxs[b]]
                        t1, t2 = r1[6], r2[6]
                        if t1 == t2 or r1[7] != r2[7]:
                            continue
                        gain = score(r1, t2) + score(r2, t1) - score(r1, t1) - score(r2, t2)
                        if gain <= 0:
                            continue
                        delta = cls(t2, r1[0]) + cls(t1, r2[0]) - cls(t1, r1[0]) - cls(t2, r2[0])
                        if w_stay * gain - w_day * delta <= 0:
                            continue
                        if not (can_take(t2, r1) and can_take(t1, r2)):
                            continue
                        k1, k2 = slot_key(r1), slot_key(r2)
                        if k1 != k2:
                            if occ.get((k1, t2), 0) > 0 or occ.get((k2, t1), 0) > 0:
                                continue
                        # εφαρμογή
                        occ[(k1, t1)] -= 1; occ[(k2, t2)] -= 1
                        r1[6], r2[6] = t2, t1
                        occ[(k1, t2)] = occ.get((k1, t2), 0) + 1; occ[(k2, t1)] = occ.get((k2, t1), 0) + 1
                        changed = True
            if not changed:
                break
        return [tuple(r) for r in rows], before, kept_count(), len(rows)

    def diagnose_conflicts(self, d_from: date, d_to: date, mode: str) -> List[str]:
        """Εξηγεί ΓΙΑΤΙ μένουν κενές θέσεις: πόσοι είναι διαθέσιμοι ανά ημέρα/ζώνη και ποιοι λείπουν."""
        self._absent_cache = None
        teachers = self.db.active_teachers()
        areas = self.db.areas(True)
        intervals = self.db.intervals(True)
        if mode not in ("daily", "breaks") or not teachers or not areas or not intervals:
            return []
        cn = self.db.conn
        groups: Dict[tuple, dict] = {}
        for d in self.school_days():
            if d < d_from or d > d_to:
                continue
            wd = d.weekday()
            day_intervals, partial = self.intervals_for_day(d, intervals)
            if not day_intervals:
                continue
            slots = []
            if mode == "daily":
                if partial:
                    limits = [self.area_limit_for_interval(d, r["name"], len(areas)) for r in day_intervals]
                    cap = max(limits) if limits else 0
                else:
                    cap = len(areas)
                filled = cn.execute("SELECT COUNT(DISTINCT area_name) FROM assignments WHERE d=?", (d.isoformat(),)).fetchone()[0]
                slots.append(("Ημερήσια εφημερία", cap, filled, lambda t, d=d, wd=wd: t.available_on(wd)))
            else:
                for iv in day_intervals:
                    cap = self.area_limit_for_interval(d, iv["name"], len(areas))
                    filled = cn.execute(
                        "SELECT COUNT(*) FROM assignments WHERE d=? AND interval_name=? AND start_time=? AND end_time=?",
                        (d.isoformat(), iv["name"], iv["start_time"], iv["end_time"])).fetchone()[0]
                    slots.append((iv["name"], cap, filled, lambda t, wd=wd, iid=iv["id"]: self.db.teacher_interval_available(t, wd, iid)))
            for label, cap, filled, avail_fn in slots:
                if cap <= 0:
                    continue
                avail, why = [], []
                for t in teachers:
                    if self._is_absent(t.id, d):
                        why.append((t, "απουσία"))
                    elif not avail_fn(t):
                        why.append((t, f"δεν είναι διαθέσιμος/η {DAYS_GR[wd]}" + ("" if mode == "daily" else " σε αυτή τη ζώνη")))
                    else:
                        avail.append(t)
                short, empty = len(avail) < cap, filled < cap
                if not (short or empty):
                    continue
                g = groups.setdefault((wd, label), {"dates": [], "cap": cap, "avail": len(avail), "why": why, "avail_names": [t.name for t in avail], "short": short})
                g["dates"].append(d)
                if len(avail) < g["avail"]:
                    g.update(avail=len(avail), why=why, avail_names=[t.name for t in avail], short=short)
        lines: List[str] = []
        for (wd, label), g in sorted(groups.items()):
            ds = g["dates"]
            ex = ", ".join(fmt_date(x) for x in ds[:3]) + ("…" if len(ds) > 3 else "")
            head = f"• {DAYS_GR[wd]} · {label}: χρειάζονται {g['cap']} θέσεις, διαθέσιμοι/ες {g['avail']}"
            if g["avail_names"]:
                head += " (" + ", ".join(g["avail_names"][:6]) + ")"
            lines.append(f"{head} — {len(ds)} ημερομηνίες (π.χ. {ex}).")
            if g["why"]:
                lines.append("    Μη διαθέσιμοι/ες: " + "; ".join(f"{t.name} ({r})" for t, r in g["why"][:8]) + ("…" if len(g["why"]) > 8 else ""))
            if g["short"]:
                fixable = [t.name for t, r in g["why"] if r.startswith("δεν είναι")]
                need = g["cap"] - g["avail"]
                tip = f"    Λύση: χρειάζονται τουλάχιστον {need} ακόμη διαθέσιμοι/ες εκπαιδευτικοί αυτή την ημέρα"
                if fixable:
                    tip += " — π.χ. άνοιξε τη διαθεσιμότητα σε: " + ", ".join(fixable[:4])
                lines.append(tip + ", ή μείωσε τους τομείς της ημέρας.")
            else:
                lines.append("    Υπάρχουν αρκετοί διαθέσιμοι, αλλά οι θέσεις έμειναν κενές λόγω περιορισμών (κλειδωμένες εφημερίες, μία εφημερία ανά ημέρα ή ισοκατανομή). Δοκίμασε ξανά την παραγωγή.")
        return lines

    CHANGE_REASONS = ("Ασθένεια", "Άδεια", "Εκπαιδευτική / υπηρεσιακή ανάγκη", "Συνεννόηση μεταξύ συναδέλφων", "Διόρθωση προγράμματος", "Άλλο")

    def _change_candidates(self, a) -> list:
        """Υποψήφιοι αντικαταστάτες για μια εφημερία, ταξινομημένοι: ο καλύτερος πρώτος.
        Κάθε στοιχείο: dict(t, rank, why, blocked, load). rank 0=αναπληρωματικός, 1=διαθέσιμος κύρια/εναλλακτική,
        2=διαθέσιμος εκτός κύριας/εναλλακτικής, 3=εκτός διαθεσιμότητας (εθελοντικά), 4=αδύνατο (ήδη απασχολημένος/απών)."""
        c = self.db.conn
        d_obj = parse_date(a["d"])
        weekday = d_obj.weekday()
        used = {r["teacher_id"] for r in c.execute(
            "SELECT teacher_id FROM assignments WHERE id<>? AND d=? AND interval_name=? AND start_time=? AND end_time=?",
            (a["id"], a["d"], a["interval_name"], a["start_time"], a["end_time"])).fetchall()}
        loads = {r[0]: int(r[1] or 0) for r in c.execute("SELECT teacher_id, SUM(minutes) FROM assignments GROUP BY teacher_id").fetchall()}
        interval_row = c.execute("SELECT id FROM duty_intervals WHERE name=? LIMIT 1", (a["interval_name"],)).fetchone()
        reserve_id = None
        if a["mode"] == "shifts":
            rr = c.execute("""SELECT sr.teacher_id FROM shift_reserves sr JOIN shifts sh ON sh.id=sr.shift_id
                              WHERE sr.d=? AND sh.name=?""", (a["d"], a["interval_name"])).fetchone()
            reserve_id = int(rr["teacher_id"]) if rr else None
        out = []
        for t in self.db.active_teachers():
            if t.id == a["teacher_id"]:
                continue
            if a["mode"] == "breaks" and interval_row:
                ok_avail = self.db.teacher_interval_available(t, weekday, interval_row["id"])
            elif a["mode"] == "shifts":
                shrow = c.execute("SELECT id FROM shifts WHERE name=? LIMIT 1", (a["interval_name"],)).fetchone()
                ok_avail = self.db.teacher_shift_available(t, weekday, shrow["id"]) if shrow else t.available_on(weekday)
            else:
                ok_avail = t.available_on(weekday)
            primary = int(getattr(t, "primary_day", -1))
            alt_days = self.db.teacher_alternate_days(t.id, getattr(t, "alternate_day", -1))
            blocked = False
            if t.id in used:
                rank, why, blocked = 4, "Έχει ήδη εφημερία την ίδια ώρα", True
            elif self._is_absent(t.id, d_obj):
                rank, why, blocked = 4, "Απουσιάζει εκείνη την ημέρα", True
            elif reserve_id is not None and t.id == reserve_id:
                rank, why = 0, "Αναπληρωματικός της βάρδιας"
            elif ok_avail and weekday == primary:
                rank, why = 1, "Διαθέσιμος/η · κύρια ημέρα του/της"
            elif ok_avail and weekday in alt_days:
                rank, why = 1, "Διαθέσιμος/η · εναλλακτική ημέρα του/της"
            elif ok_avail:
                rank, why = 2, "Διαθέσιμος/η · εκτός κύριας/εναλλακτικής"
            else:
                rank, why = 3, "Εκτός δηλωμένης διαθεσιμότητας · εθελοντικά"
            out.append({"t": t, "rank": rank, "why": why, "blocked": blocked, "load": loads.get(t.id, 0)})
        out.sort(key=lambda x: (x["rank"], x["load"], x["t"].name.casefold()))
        return out

    def _apply_timetable(self, rows, tt: dict, opt: dict) -> dict:
        self.auto_backup("πριν την εισαγωγή ωρολογίου")
        conn = self.db.conn
        intervals = self.db.intervals(True)
        done = created = n_breaks = 0
        skipped = []
        _times = [v for v in tt.get("periods", {}).values() if v[0] and v[1]]
        day_first = min((a for a, _b in _times), default="")
        day_last = max((b for _a, b in _times), default="")
        try:
            for name, t, i in rows:
                if t is None:
                    if not opt["create"]:
                        skipped.append(name)
                        continue
                    conn.execute("INSERT OR IGNORE INTO teachers(name) VALUES(?)", (name,))
                    r = conn.execute("SELECT * FROM teachers WHERE name=?", (name,)).fetchone()
                    t = Teacher(**dict(r))
                    created += 1
                present = set(i["days"])
                if opt["days"]:
                    flags = [1 if d in present else 0 for d in range(5)]
                    conn.execute("UPDATE teachers SET mon=?,tue=?,wed=?,thu=?,fri=? WHERE id=?", (*flags, t.id))
                if opt["primary"]:
                    conn.execute("UPDATE teachers SET primary_day=? WHERE id=?", (i["primary"], t.id))
                    self._set_alternates_nocommit(t.id, i["alternates"])
                elif opt["days"]:
                    alts = [d for d in self.db.teacher_alternate_days(t.id, t.alternate_day) if d in present]
                    self._set_alternates_nocommit(t.id, alts)
                    if t.primary_day not in present:
                        conn.execute("UPDATE teachers SET primary_day=-1 WHERE id=?", (t.id,))
                if opt["breaks"]:
                    for wd in range(5):
                        s_e = i["span"].get(wd)
                        for iv in intervals:
                            ok = int(bool(wd in present and s_e and s_e[0] and s_e[1] and s_e[0] <= iv["start_time"] and s_e[1] >= iv["end_time"]))
                            # Προσέλευση / αποχώρηση: εκτός των ωρών μαθημάτων, διαθέσιμος όποιος ξεκινά την πρώτη ώρα της ημέρας
                            # (προσέλευση) ή τελειώνει την τελευταία (αποχώρηση).
                            if not ok and wd in present and s_e and day_first and day_last:
                                if iv["end_time"] <= day_first and s_e[0] == day_first:
                                    ok = 1
                                elif iv["start_time"] >= day_last and s_e[1] == day_last:
                                    ok = 1
                            conn.execute("INSERT OR REPLACE INTO teacher_interval_availability(teacher_id,weekday,interval_id,available) VALUES(?,?,?,?)", (t.id, wd, iv["id"], ok))
                            n_breaks += 1
                done += 1
            conn.commit()
        except Exception as exc:
            conn.rollback()
            LOG.exception("Εισαγωγή ωρολογίου")
            messagebox.showerror("Εισαγωγή ωρολογίου", f"Η εισαγωγή απέτυχε και δεν έγινε καμία αλλαγή:\n{exc}")
            return {}
        self.refresh_all()
        res = {"updated": done, "created": created, "skipped": skipped, "break_rows": n_breaks}
        msg = f"Ενημερώθηκαν {done} εκπαιδευτικοί" + (f" (νέοι: {created})" if created else "") + "."
        if skipped:
            msg += f"\n\nΔεν αντιστοιχίστηκαν και δεν άλλαξαν ({len(skipped)}):\n" + "\n".join("• " + s for s in skipped[:15]) + ("\n…" if len(skipped) > 15 else "")
        msg += "\n\nΕλέγξτε τη διαθεσιμότητα στην καρτέλα «Εκπαιδευτικοί». Για να εφαρμοστεί στο πρόγραμμα, κάντε ανακατανομή."
        messagebox.showinfo("Εισαγωγή ωρολογίου", msg)
        return res

    def _set_alternates_nocommit(self, teacher_id: int, weekdays: List[int]) -> None:
        clean = []
        for wd in weekdays:
            if 0 <= int(wd) <= 4 and int(wd) not in clean:
                clean.append(int(wd))
        self.db.conn.execute("DELETE FROM teacher_alternate_days WHERE teacher_id=?", (teacher_id,))
        self.db.conn.executemany("INSERT INTO teacher_alternate_days(teacher_id,priority,weekday) VALUES(?,?,?)", [(teacher_id, k + 1, wd) for k, wd in enumerate(clean)])
        self.db.conn.execute("UPDATE teachers SET alternate_day=? WHERE id=?", (clean[0] if clean else -1, teacher_id))

    def import_teachers_csv(self, raw: bytes) -> None:

        # Συμβατότητα με CSV παλαιότερων εκδόσεων:
        # - οι εξαγωγές της εφαρμογής χρησιμοποιούν ελληνικό Excel-friendly ';'
        # - δεχόμαστε επίσης ',', tab και '|', UTF-8 ή Windows Greek (cp1253).
        try:
            raw_text, used_encoding = self._decode_csv(raw)
        except ValueError as e:
            messagebox.showerror("Εισαγωγή CSV", str(e))
            return

        lines = raw_text.splitlines()
        explicit_delimiter = None
        if lines and lines[0].strip().lower().startswith("sep="):
            explicit_delimiter = lines[0].strip()[4:5] or ";"
            lines = lines[1:]
        text = "\n".join(lines)
        if not text.strip():
            messagebox.showwarning("Εισαγωγή CSV", "Το αρχείο είναι κενό.")
            return

        if explicit_delimiter:
            delimiter = explicit_delimiter
        else:
            sample = text[:8192]
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
            except csv.Error:
                first = lines[0] if lines else ""
                delimiter = ";" if first.count(";") >= first.count(",") else ","

        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            messagebox.showwarning("Εισαγωγή CSV", "Δεν βρέθηκε γραμμή επικεφαλίδων στο CSV.")
            return

        # Κανονικοποίηση επικεφαλίδων (κενά/BOM).
        field_map = {str(h or "").replace("\ufeff", "").strip(): h for h in reader.fieldnames}

        def get_value(row, *aliases, default=None):
            for alias in aliases:
                original = field_map.get(alias)
                if original is not None:
                    value = row.get(original)
                    if value is not None:
                        return value
            return default

        known_name_headers = ("Ονοματεπώνυμο", "Εκπαιδευτικός", "name", "Name")
        if not any(h in field_map for h in known_name_headers):
            headers = ", ".join(field_map.keys())
            messagebox.showerror(
                "Μη συμβατό CSV",
                "Το αρχείο δεν φαίνεται να είναι εξαγωγή εκπαιδευτικών.\n\n"
                f"Επικεφαλίδες που βρέθηκαν: {headers}\n\n"
                "Για εισαγωγή εκπαιδευτικών χρειάζεται στήλη 'Ονοματεπώνυμο'."
            )
            return

        def yn(v, default=0):
            if v is None or str(v).strip() == "":
                return default
            txt = str(v).strip().lower()
            return 1 if txt in ("1","ναι","nai","yes","true","x","✓","✔","διαθέσιμος","διαθέσιμη") else 0

        def day_idx(v):
            text_v = str(v or "").strip()
            if text_v in DAYS_GR:
                return DAYS_GR.index(text_v)
            short = {"δε":0,"δευ":0,"δευτέρα":0,"τρ":1,"τρι":1,"τρίτη":1,"τε":2,"τετ":2,"τετάρτη":2,
                     "πε":3,"πεμ":3,"πέμπτη":3,"πα":4,"παρ":4,"παρασκευή":4}
            return short.get(text_v.lower(), -1)

        count = 0
        skipped = 0
        old_format = not any(h in field_map for h in ("Κύρια ημέρα", "primary_day", "Εναλλακτική ημέρα", "alternate_day", "Πρόσθετος κύκλος", "extra_cycle"))

        for row in reader:
            name = str(get_value(row, *known_name_headers, default="") or "").strip()
            if not name:
                skipped += 1
                continue
            alt_text=str(get_value(row, "Εναλλακτικές ημέρες", "alternate_days", default="") or "").strip()
            alt_days=[]
            if alt_text:
                for part in alt_text.replace(";","|").replace(",","|").split("|"):
                    wd=day_idx(part.strip())
                    if wd >= 0 and wd not in alt_days:
                        alt_days.append(wd)
            first_alt=day_idx(get_value(row, "Εναλλακτική ημέρα", "alternate_day"))
            if not alt_days and first_alt >= 0:
                alt_days=[first_alt]
            vals = {
                "name": name,
                "participates": yn(get_value(row, "Συμμετέχει", "participates"), 1),
                "active": yn(get_value(row, "Ενεργός", "active"), 1),
                "mon": yn(get_value(row, "Δευτέρα", "mon"), 1),
                "tue": yn(get_value(row, "Τρίτη", "tue"), 1),
                "wed": yn(get_value(row, "Τετάρτη", "wed"), 1),
                "thu": yn(get_value(row, "Πέμπτη", "thu"), 1),
                "fri": yn(get_value(row, "Παρασκευή", "fri"), 1),
                "primary_day": day_idx(get_value(row, "Κύρια ημέρα", "primary_day")),
                "alternate_day": alt_days[0] if alt_days else -1,
                # Σε παλιό CSV δεν υπήρχε ο πρόσθετος κύκλος. Τον αφήνουμε ανενεργό μέχρι να οριστεί από τον χρήστη.
                "extra_cycle": yn(get_value(row, "Πρόσθετος κύκλος", "extra_cycle"), 0 if old_format else 1),
                "notes": str(get_value(row, "Παρατηρήσεις", "notes", default="") or ""),
            }
            self.db.conn.execute("""INSERT INTO teachers(name,participates,active,mon,tue,wed,thu,fri,primary_day,alternate_day,extra_cycle,notes)
                VALUES(:name,:participates,:active,:mon,:tue,:wed,:thu,:fri,:primary_day,:alternate_day,:extra_cycle,:notes)
                ON CONFLICT(name) DO UPDATE SET participates=excluded.participates, active=excluded.active,
                mon=excluded.mon, tue=excluded.tue, wed=excluded.wed, thu=excluded.thu, fri=excluded.fri,
                primary_day=CASE WHEN excluded.primary_day >= 0 THEN excluded.primary_day ELSE teachers.primary_day END,
                alternate_day=CASE WHEN excluded.alternate_day >= 0 THEN excluded.alternate_day ELSE teachers.alternate_day END,
                extra_cycle=CASE WHEN :old_format = 1 THEN teachers.extra_cycle ELSE excluded.extra_cycle END,
                notes=excluded.notes""", {**vals, "old_format": 1 if old_format else 0})
            tr=self.db.conn.execute("SELECT id,alternate_day FROM teachers WHERE name=?",(name,)).fetchone()
            if tr and (alt_days or first_alt >= 0):
                self.db.set_teacher_alternate_days(int(tr["id"]), alt_days)
            count += 1

        self.db.conn.commit()
        self.refresh_all()
        self.set_status(f"Εισαγωγή CSV: {count} εγγραφές")
        details = f"Έγινε εισαγωγή/ενημέρωση {count} εγγραφών."
        if skipped:
            details += f"\nΠαραλείφθηκαν {skipped} κενές γραμμές."
        if old_format:
            details += ("\n\nΑναγνωρίστηκε CSV παλαιότερης έκδοσης. "
                        "Οι κύριες/εναλλακτικές ημέρες και ο πρόσθετος κύκλος δεν υπήρχαν στο παλιό αρχείο "
                        "και πρέπει να οριστούν από την εφαρμογή.")
        details += f"\n\nΔιαχωριστικό: {repr(delimiter)}  |  Κωδικοποίηση: {used_encoding}"
        messagebox.showinfo("Εισαγωγή CSV", details)

    def import_exceptions_csv(self, raw: bytes) -> None:
        try:
            text, _enc = self._decode_csv(raw)
            first = text.splitlines()[0] if text.splitlines() else ""
            delim = ";" if first.count(";") >= first.count(",") else ","
            reader = csv.DictReader(io.StringIO(text), delimiter=delim)
            imported=skipped=0
            for row in reader:
                d = (row.get("Από_ISO") or row.get("Από") or row.get("d") or "").strip()
                end_d = (row.get("Έως_ISO") or row.get("Έως") or row.get("end_d") or d).strip()
                label = (row.get("Περιγραφή") or row.get("label") or "").strip()
                typ = (row.get("Τύπος") or row.get("type") or "Χωρίς εφημερία").strip()
                cfg = (row.get("config_json") or row.get("config") or "{}").strip() or "{}"
                if not d or not label: skipped += 1; continue
                sd, ed = parse_date(d), parse_date(end_d)
                if ed < sd: sd, ed = ed, sd
                try: json.loads(cfg)
                except Exception: cfg = "{}"
                overlap = self.exception_range_overlap(sd, ed, None)
                if overlap:
                    # Ίδιο ακριβώς διάστημα: ενημέρωση. Διαφορετική επικάλυψη: παράλειψη για ασφάλεια.
                    ov_end = overlap["end_d"] or overlap["d"]
                    if overlap["d"] == sd.isoformat() and ov_end == ed.isoformat():
                        self.db.conn.execute("UPDATE exceptions SET label=?,type=?,config=? WHERE id=?", (label,typ,cfg,overlap["id"]))
                        imported += 1
                    else:
                        skipped += 1
                    continue
                self.db.conn.execute("INSERT INTO exceptions(d,end_d,label,type,config) VALUES(?,?,?,?,?)", (sd.isoformat(),ed.isoformat(),label,typ,cfg))
                imported += 1
            self.db.conn.commit(); self.refresh_all()
            messagebox.showinfo("Εισαγωγή ειδικών ημερών", f"Εισήχθησαν/ενημερώθηκαν: {imported}\nΠαραλείφθηκαν: {skipped}")
        except Exception as e:
            messagebox.showerror("Εισαγωγή ειδικών ημερών", str(e))

    def import_schedule_csv(self, raw_bytes: bytes, replace: bool = False) -> None:
        try:
            raw, _enc = self._decode_csv(raw_bytes)
            first=raw.splitlines()[0] if raw.splitlines() else ""
            delim=";" if first.count(";") >= first.count(",") else ","
            reader=csv.DictReader(io.StringIO(raw), delimiter=delim)
            rows=list(reader)
            if replace:
                self.auto_backup("import-csv")
                self.db.conn.execute("DELETE FROM duty_changes")
                self.db.conn.execute("DELETE FROM assignments")
            imported=skipped=0
            for row in rows:
                d=(row.get("date_iso") or row.get("Ημερομηνία") or "").strip()
                if not d: skipped+=1; continue
                d=parse_date(d).isoformat()
                teacher=(row.get("Εκπαιδευτικός") or row.get("teacher") or "").strip()
                area=(row.get("Τομέας") or row.get("area") or "").strip()
                interval=(row.get("Χρόνος") or row.get("interval") or "Ημερήσια εφημερία").strip()
                st=(row.get("Από") or row.get("start_time") or "08:00").strip()
                en=(row.get("Έως") or row.get("end_time") or "13:20").strip()
                mode=(row.get("Τρόπος") or row.get("mode") or "daily").strip()
                if mode not in ("daily","breaks"): mode = "breaks" if "διά" in mode.lower() else "daily"
                try: mins=int(float(row.get("Λεπτά") or row.get("minutes") or minutes_between(st,en)))
                except Exception: mins=minutes_between(st,en)
                try: locked=int(row.get("locked") or 0)
                except Exception: locked=1 if "κλειδ" in (row.get("Κατάσταση") or "").lower() or "πραγμα" in (row.get("Κατάσταση") or "").lower() else 0
                status_text=(row.get("Κατάσταση") or "").lower()
                entry_type=(row.get("entry_type") or ("manual_completed" if "πραγμα" in status_text else "auto")).strip()
                tr=self.db.conn.execute("SELECT id FROM teachers WHERE name=?",(teacher,)).fetchone()
                if not tr:
                    skipped+=1; continue
                wd=DAYS_GR[parse_date(d).weekday()] if parse_date(d).weekday()<5 else ""
                duplicate=self.db.conn.execute("SELECT 1 FROM assignments WHERE d=? AND interval_name=? AND area_name=?",(d,interval,area)).fetchone()
                if duplicate and not replace:
                    skipped+=1; continue
                self.db.conn.execute("INSERT INTO assignments(d,weekday,interval_name,start_time,end_time,area_name,teacher_id,minutes,mode,locked,entry_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                     (d,wd,interval,st,en,area,int(tr["id"]),mins,mode,locked,entry_type))
                imported+=1
            self.db.conn.commit(); self.refresh_all()
            messagebox.showinfo("Εισαγωγή προγράμματος", f"Εισήχθησαν: {imported}\nΠαραλείφθηκαν: {skipped}\n\nΓια πλήρη μεταφορά με ιστορικό αντικαταστάσεων προτίμησε το «Πλήρες Backup σχολικού έτους».")
        except Exception as e:
            self.db.conn.rollback()
            messagebox.showerror("Εισαγωγή προγράμματος", str(e))

    # ---- εκτύπωση: βοηθητικά
    AREA_PALETTE = ("#D4A017", "#2F6F73", "#8E5EA2", "#C05621", "#2B6CB0", "#4A9D6B", "#B83280", "#718096", "#A0522D", "#2C7A7B")

    def _print_day_note(self, day: date) -> Tuple[str, str]:
        """Για ημέρα χωρίς αναθέσεις: (κλάση CSS, κείμενο). Διακρίνει εκτός περιόδου, αργία/χωρίς εφημερία και πραγματικό κενό."""
        try:
            if day < parse_date(self.db.get_setting("start_date")) or day > parse_date(self.db.get_setting("end_date")):
                return "off-day", "Εκτός περιόδου"
            ex = self.db.exception_dates().get(day.isoformat())
            if ex is not None and self.is_no_duty_exception(ex):
                return "off-day", (ex["label"] or "Χωρίς εφημερία")
        except Exception:
            pass
        return "empty-day", "Δεν υπάρχουν αναθέσεις"

    _MONTHS_GR = ["Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος", "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος"]

    def build_xlsx(self, path) -> bool:
        """Γράφει το αρχείο Excel (Πρόγραμμα, Σύνολα, Απουσίες). False αν δεν υπάρχει πρόγραμμα."""
        year = self.db.get_setting("school_year")
        conn = self.db.conn
        rows = conn.execute(
            "SELECT a.d,a.weekday,a.interval_name,a.start_time,a.end_time,COALESCE(ar.name,a.area_name),t.name,a.minutes,a.mode,a.locked,a.entry_type "
            "FROM assignments a JOIN teachers t ON t.id=a.teacher_id LEFT JOIN areas ar ON ar.id=a.area_id ORDER BY a.d,a.start_time,a.area_name").fetchall()
        if not rows:
            return False
        mode_gr = {"daily": "Ημέρα", "breaks": "Διάλειμμα", "shifts": "Βάρδια"}
        prog = []
        for r in rows:
            status = "Πραγματοποιημένο" if r[10] == "manual_completed" else ("Κλειδωμένο" if r[9] else "Μελλοντικό")
            prog.append((parse_date(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], int(r[7] or 0), mode_gr.get(r[8], r[8]), status))
        tot = {}
        for r in rows:
            t = tot.setdefault(r[6], [0, set(), 0])
            t[0] += 1
            t[1].add(r[0])
            t[2] += int(r[7] or 0)
        for (name,) in conn.execute("SELECT name FROM teachers WHERE active=1 AND participates=1").fetchall():
            tot.setdefault(name, [0, set(), 0])
        mean_days = sum(len(v[1]) for v in tot.values()) / len(tot)
        tot_rows = [(n, v[0], len(v[1]), v[2], v[2] / 60.0, len(v[1]) - mean_days)
                    for n, v in sorted(tot.items(), key=lambda kv: (-len(kv[1][1]), kv[0]))]
        ab_rows = []
        for name, d1, d2, reason in conn.execute(
                "SELECT t.name,a.d_from,a.d_to,a.reason FROM absences a JOIN teachers t ON t.id=a.teacher_id ORDER BY a.d_from,t.name").fetchall():
            ab_rows.append((name, parse_date(d1), parse_date(d2), (parse_date(d2) - parse_date(d1)).days + 1, reason or ""))
        write_xlsx(path, [
            ("Πρόγραμμα", ["Ημερομηνία", "Ημέρα", "Χρόνος", "Από", "Έως", "Τομέας", "Εκπαιδευτικός", "Λεπτά", "Τρόπος", "Κατάσταση"],
             prog, [13, 12, 18, 8, 8, 18, 30, 8, 12, 18]),
            ("Σύνολα", ["Εκπαιδευτικός", "Αναθέσεις", "Ημέρες εφημερίας", "Λεπτά", "Ώρες", "Απόκλιση ημερών από μέσο όρο"],
             tot_rows, [32, 11, 17, 9, 9, 28]),
            ("Απουσίες", ["Εκπαιδευτικός", "Από", "Έως", "Ημέρες", "Αιτιολογία"], ab_rows, [32, 13, 13, 9, 34]),
        ])
        return True

    def write_csv(self, path: Path, headers: List[str], rows) -> None:
        EXPORT_DIR.mkdir(exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(headers)
            for r in rows:
                w.writerow(list(r))





def _xl_col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ======================================================================================
#  Εισαγωγή ωρολογίου προγράμματος (aSc XML ή xlsx) → διαθεσιμότητα εφημεριών
# ======================================================================================
def _plain(text) -> str:
    import unicodedata
    n = unicodedata.normalize("NFD", str(text or "").casefold())
    return "".join(ch for ch in n if not unicodedata.combining(ch)).replace("ς", "σ")


def _name_tokens(name: str) -> List[str]:
    return [t for t in re.split(r"[^0-9a-zα-ω]+", _plain(name)) if t]


def match_teacher_name(name: str, teachers: List["Teacher"]) -> Optional["Teacher"]:
    """Αντιστοίχιση ονόματος ωρολογίου με εκπαιδευτικό της εφαρμογής (χωρίς τόνους, σειρά ονόματος/επωνύμου
    ανεξάρτητη, αρχικά όπως «Γ.» δεκτά). Επιστρέφει None αν δεν υπάρχει μοναδική αντιστοίχιση."""
    want = _name_tokens(name)
    if not want:
        return None
    exact = [t for t in teachers if sorted(_name_tokens(t.name)) == sorted(want)]
    if len(exact) == 1:
        return exact[0]
    loose = []
    for t in teachers:
        have = _name_tokens(t.name)
        short, long_ = (want, have) if len(want) <= len(have) else (have, want)
        if len(short) < 2:
            continue
        pool = list(long_)
        ok = True
        for s in short:
            hit = next((p for p in pool if p.startswith(s.rstrip(".")) or s.startswith(p)), None)
            if hit is None:
                ok = False
                break
            pool.remove(hit)
        if ok:
            loose.append(t)
    return loose[0] if len(loose) == 1 else None


def _hhmm(value) -> str:
    """'8:15', '08.15', 0.34375 (ώρα του Excel) → '08:15'. Κενό αν δεν αναγνωριστεί."""
    if value is None or str(value).strip() == "":
        return ""
    s = str(value).strip()
    try:
        f = float(s.replace(",", "."))
        if 0 <= f < 1:
            m = int(round(f * 24 * 60))
            return f"{m // 60:02d}:{m % 60:02d}"
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})\s*[:.hH]\s*(\d{2})", s)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return ""


def parse_asc_timetable(raw: bytes) -> dict:
    """aSc Timetables XML (Αρχείο → Εξαγωγή → XML) → {'periods': {αριθμός: (από, έως)}, 'teachers': {όνομα: {ημέρα: [ώρες]}}}."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"Το αρχείο δεν είναι έγκυρο XML ({exc}).")
    if root.tag != "timetable":
        raise ValueError("Το αρχείο δεν φαίνεται να είναι XML του aSc Timetables (λείπει το στοιχείο «timetable»).")

    def rows(tag: str) -> List[dict]:
        sec = root.find(tag)
        return [dict(e.attrib) for e in sec] if sec is not None else []
    periods = {}
    for p in rows("periods"):
        periods[int(p.get("period", "0") or 0)] = (_hhmm(p.get("starttime")), _hhmm(p.get("endtime")))
    tnames = {}
    for t in rows("teachers"):
        name = (t.get("name") or f"{t.get('lastname', '')} {t.get('firstname', '')}").strip()
        if name:
            tnames[t["id"]] = name
    lesson_teachers = {l["id"]: [x for x in (l.get("teacherids") or "").split(",") if x] for l in rows("lessons")}
    teachers: Dict[str, Dict[int, set]] = {n: {} for n in tnames.values()}
    cards = rows("cards")
    if not cards:
        raise ValueError("Το XML δεν περιέχει τοποθετημένα μαθήματα (cards). Εξάγετε το πρόγραμμα αφού ολοκληρωθεί η τοποθέτηση.")
    for c in cards:
        bits = c.get("days", "")
        if len(bits) != 5 or set(bits) - {"0", "1"}:
            continue
        try:
            per = int(c.get("period", ""))
        except ValueError:
            continue
        for tid in lesson_teachers.get(c.get("lessonid", ""), []):
            name = tnames.get(tid)
            if not name:
                continue
            for wd, b in enumerate(bits):
                if b == "1":
                    teachers[name].setdefault(wd, set()).add(per)
    return {"source": "aSc", "periods": periods,
            "teachers": {n: {wd: sorted(ps) for wd, ps in d.items()} for n, d in teachers.items() if d}}


def read_xlsx_sheets(raw: bytes) -> Dict[str, List[List[str]]]:
    """Ελάχιστος αναγνώστης .xlsx (zip + XML): όνομα φύλλου → γραμμές κειμένου."""
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("Το αρχείο δεν είναι έγκυρο .xlsx.")
    shared: List[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out: Dict[str, List[List[str]]] = {}
    for sh in wb.find("m:sheets", ns):
        target = rels.get(sh.get("{%s}id" % ns["r"]), "")
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        if path not in z.namelist():
            continue
        grid: Dict[int, Dict[int, str]] = {}
        for row in ET.fromstring(z.read(path)).iter("{%s}row" % ns["m"]):
            for c in row.findall("m:c", ns):
                ref = c.get("r", "")
                col = 0
                for ch in re.match(r"[A-Z]+", ref).group(0):
                    col = col * 26 + ord(ch) - 64
                t = c.get("t")
                if t == "s":
                    v = c.find("m:v", ns)
                    val = shared[int(v.text)] if v is not None and v.text else ""
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
                else:
                    v = c.find("m:v", ns)
                    val = v.text if v is not None and v.text is not None else ""
                rnum = int(re.search(r"\d+", ref).group(0))
                grid.setdefault(rnum, {})[col] = val
        out[sh.get("name", "")] = [[r.get(i, "") for i in range(1, (max(r) if r else 0) + 1)] for _, r in sorted(grid.items())]
    return out


_DAY_KEYS = (("δευ", "mon", "1"), ("τρι", "tue", "2"), ("τετ", "wed", "3"), ("πεμ", "thu", "4"), ("παρ", "fri", "5"))


def _day_index(value) -> Optional[int]:
    s = _plain(value).strip().rstrip(".")
    if not s:
        return None
    for i, keys in enumerate(_DAY_KEYS):
        if s.startswith(keys[0]) or s.startswith(keys[1]) or s == keys[2]:
            return i
    return None


def parse_xlsx_timetable(raw: bytes) -> dict:
    """xlsx με φύλλο «Ωρολόγιο» (στήλες: Εκπαιδευτικός, Ημέρα, Ώρα) και προαιρετικό φύλλο «Ώρες» (Ώρα, Από, Έως)."""
    sheets = read_xlsx_sheets(raw)
    main = None
    times = None
    for name, rows in sheets.items():
        head = [_plain(c) for c in (rows[0] if rows else [])]
        if any(h.startswith("εκπαιδ") or h.startswith("καθηγ") or h.startswith("δασκαλ") for h in head) and any(h.startswith("ημερ") for h in head):
            main = (name, rows)
        elif any(h.startswith("ωρα") for h in head) and any(h.startswith("απο") for h in head):
            times = rows
    if main is None:
        raise ValueError("Δεν βρέθηκε φύλλο με στήλες «Εκπαιδευτικός», «Ημέρα», «Ώρα». Χρησιμοποιήστε το πρότυπο από το κουμπί «Πρότυπο xlsx».")
    head = [_plain(c) for c in main[1][0]]
    def col(*prefixes):
        for i, h in enumerate(head):
            if any(h.startswith(p) for p in prefixes):
                return i
        return -1
    ct, cd, cp = col("εκπαιδ", "καθηγ", "δασκαλ"), col("ημερ"), col("ωρα", "περιοδ")
    if min(ct, cd, cp) < 0:
        raise ValueError("Λείπει μία από τις στήλες «Εκπαιδευτικός», «Ημέρα», «Ώρα».")
    teachers: Dict[str, Dict[int, set]] = {}
    bad = 0
    for r in main[1][1:]:
        r = list(r) + [""] * (max(ct, cd, cp) + 1 - len(r))
        name = r[ct].strip()
        if not name and not r[cd].strip():
            continue
        wd = _day_index(r[cd])
        try:
            per = int(float(str(r[cp]).replace(",", ".")))
        except ValueError:
            per = None
        if not name or wd is None or per is None:
            bad += 1
            continue
        teachers.setdefault(name, {}).setdefault(wd, set()).add(per)
    if not teachers:
        raise ValueError("Δεν βρέθηκαν έγκυρες γραμμές ωρολογίου (ελέγξτε ονόματα, ημέρες και αριθμούς ωρών).")
    periods = {}
    if times:
        th = [_plain(c) for c in times[0]]
        ip = next((i for i, h in enumerate(th) if h.startswith("ωρα")), -1)
        ia = next((i for i, h in enumerate(th) if h.startswith("απο")), -1)
        ie = next((i for i, h in enumerate(th) if h.startswith("εως")), -1)
        for r in times[1:]:
            try:
                periods[int(float(r[ip]))] = (_hhmm(r[ia]), _hhmm(r[ie]))
            except (ValueError, IndexError):
                continue
    return {"source": "xlsx", "periods": periods, "bad_rows": bad,
            "teachers": {n: {wd: sorted(ps) for wd, ps in d.items()} for n, d in teachers.items()}}


def analyze_timetable(tt: dict) -> Dict[str, dict]:
    """Ανά εκπαιδευτικό: ημέρες παρουσίας, ώρες, πρώτη/τελευταία ώρα (με ώρες ρολογιού αν υπάρχουν),
    και πρόταση κύριας/εναλλακτικών (πρώτα οι ημέρες με τις λιγότερες ώρες διδασκαλίας)."""
    out = {}
    per = tt.get("periods", {})
    for name, days in tt["teachers"].items():
        present = sorted(days)
        hours = {wd: len(days[wd]) for wd in present}
        order = sorted(present, key=lambda wd: (hours[wd], wd))
        span = {}
        for wd in present:
            first, last = min(days[wd]), max(days[wd])
            s = per.get(first, ("", ""))[0]
            e = per.get(last, ("", ""))[1]
            span[wd] = (s, e)
        out[name] = {"days": present, "hours": hours, "total": sum(hours.values()), "span": span,
                     "primary": order[0] if order else -1, "alternates": order[1:]}
    return out


def write_xlsx(path, sheets) -> None:
    """Γράφει αρχείο .xlsx μόνο με τη βασική βιβλιοθήκη (zipfile).
    sheets = [(όνομα, κεφαλίδες, γραμμές, πλάτη_στηλών)].
    Τιμές: str -> κείμενο, int/float -> αριθμός, date -> ημερομηνία (ΗΗ/ΜΜ/ΕΕΕΕ), None -> κενό."""
    from xml.sax.saxutils import escape
    bad = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")

    def txt(v) -> str:
        return escape(bad.sub("", str(v)))

    epoch = date(1899, 12, 30)
    used_names = set()

    def sheet_name(n: str, i: int) -> str:
        n = re.sub(r"[\[\]:*?/\\]", " ", n).strip()[:31] or f"Φύλλο{i}"
        base, k = n, 2
        while n.lower() in used_names:
            n = base[:28] + f" {k}"
            k += 1
        used_names.add(n.lower())
        return n

    sheet_xml = []
    names = []
    for si, (name, headers, rows, widths) in enumerate(sheets, 1):
        names.append(sheet_name(name, si))
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
               '<sheetViews><sheetView workbookViewId="0"' + (' tabSelected="1"' if si == 1 else '') + '>'
               '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>',
               '<sheetFormatPr defaultRowHeight="15"/>']
        if widths:
            out.append("<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)) + "</cols>")
        out.append("<sheetData>")
        out.append('<row r="1">' + "".join(f'<c r="{_xl_col(i)}1" s="1" t="inlineStr"><is><t>{txt(h)}</t></is></c>' for i, h in enumerate(headers)) + "</row>")
        for ri, row in enumerate(rows, 2):
            cells = []
            for ci, v in enumerate(row):
                ref = f"{_xl_col(ci)}{ri}"
                if v is None or v == "":
                    continue
                if isinstance(v, bool):
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{"Ναι" if v else "Όχι"}</t></is></c>')
                elif isinstance(v, (datetime, date)):
                    d = v.date() if isinstance(v, datetime) else v
                    cells.append(f'<c r="{ref}" s="2"><v>{(d - epoch).days}</v></c>')
                elif isinstance(v, int):
                    cells.append(f'<c r="{ref}"><v>{v}</v></c>')
                elif isinstance(v, float):
                    cells.append(f'<c r="{ref}" s="3"><v>{repr(round(v, 4))}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{txt(v)}</t></is></c>')
            out.append(f'<row r="{ri}">' + "".join(cells) + "</row>")
        out.append("</sheetData>")
        last = f"{_xl_col(max(len(headers), 1) - 1)}{max(len(rows) + 1, 1)}"
        out.append(f'<autoFilter ref="A1:{last}"/>')
        out.append('<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>')
        out.append('<pageSetup paperSize="9" orientation="landscape"/>')
        out.append("</worksheet>")
        sheet_xml.append("".join(out))
    n = len(sheet_xml)
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, n + 1))
        + '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        + "".join(f'<sheet name="{escape(nm, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>' for i, nm in enumerate(names, 1))
        + '</sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
        + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="2"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/><numFmt numFmtId="165" formatCode="0.0"/></numFmts>'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF2C3E50"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"><alignment horizontal="left"/></xf>'
        '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        for i, x in enumerate(sheet_xml, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", x)
    os.replace(tmp, path)


def write_xlsx_grid(path, title: str, weeks, sheet_name: str = "Πρόγραμμα") -> None:
    """Εβδομαδιαίο πλέγμα (όπως το χειροποίητο Excel του σχολείου): ανά εβδομάδα γραμμή ημερομηνιών, γραμμή ημερών
    και μία γραμμή ανά τομέα. weeks = [{"dates":[5 κείμενα], "days":[5 κείμενα], "rows":[(ετικέτα,[5 κείμενα],[5 bool σημείωσης])]}]."""
    from xml.sax.saxutils import escape
    bad = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
    t = lambda v: escape(bad.sub("", str(v)))
    out = []
    merges = []
    r = 1
    out.append(f'<row r="1" ht="26" customHeight="1"><c r="A1" s="1" t="inlineStr"><is><t xml:space="preserve">{t(title)}</t></is></c></row>')
    merges.append("A1:F1")
    r = 2
    breaks = []
    def cell(col, row, text, style):
        if text == "" or text is None:
            return f'<c r="{_xl_col(col)}{row}" s="{style}"/>'
        return f'<c r="{_xl_col(col)}{row}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{t(text)}</t></is></c>'
    for wi, wk in enumerate(weeks):
        r += 1  # κενή γραμμή πριν από κάθε εβδομάδα
        if wi and wi % 4 == 0:
            breaks.append(r - 1)
        out.append(f'<row r="{r}">' + cell(0, r, "", 2) + "".join(cell(i + 1, r, wk["dates"][i], 2) for i in range(5)) + "</row>")
        r += 1
        out.append(f'<row r="{r}">' + cell(0, r, "", 3) + "".join(cell(i + 1, r, wk["days"][i], 3) for i in range(5)) + "</row>")
        for label, vals, notes in wk["rows"]:
            r += 1
            out.append(f'<row r="{r}" ht="20" customHeight="1">' + cell(0, r, label, 4) + "".join(cell(i + 1, r, vals[i], 6 if notes[i] else 5) for i in range(5)) + "</row>")
    last = r
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        '<sheetViews><sheetView workbookViewId="0" tabSelected="1" showGridLines="0"/></sheetViews><sheetFormatPr defaultRowHeight="15"/>'
        '<cols><col min="1" max="1" width="18" customWidth="1"/><col min="2" max="6" width="24" customWidth="1"/></cols>'
        '<sheetData>' + "".join(out) + '</sheetData>'
        + f'<mergeCells count="{len(merges)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in merges) + '</mergeCells>'
        + '<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5" header="0.3" footer="0.3"/>'
        '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
        + (f'<rowBreaks count="{len(breaks)}" manualBreakCount="{len(breaks)}">' + "".join(f'<brk id="{b}" max="16383" man="1"/>' for b in breaks) + '</rowBreaks>' if breaks else '')
        + '</worksheet>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        f'<sheet name="{escape(sheet_name[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    thin = '<border><left style="thin"><color rgb="FF808080"/></left><right style="thin"><color rgb="FF808080"/></right><top style="thin"><color rgb="FF808080"/></top><bottom style="thin"><color rgb="FF808080"/></bottom><diagonal/></border>'
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="4"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="16"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font><font><i/><sz val="10"/><color rgb="FF7A5A00"/><name val="Calibri"/></font></fonts>'
        '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD9D9D9"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill></fills>'
        f'<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>{thin}</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="7"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    os.replace(tmp, path)


def _undoable(label: str):
    """Διακοσμητής: κρατά στιγμιότυπο πριν από μια αλλαγή προγράμματος, ώστε να γίνεται αναίρεση (Ctrl+Z)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *a, **k):
            if getattr(self, "_undo_busy", False):
                return fn(self, *a, **k)
            try:
                before = self._undo_snapshot()
            except Exception:
                LOG.exception("Αποτυχία στιγμιότυπου αναίρεσης")
                before = None
            self._undo_busy = True
            try:
                return fn(self, *a, **k)
            finally:
                self._undo_busy = False
                if before is not None:
                    try:
                        if self._undo_snapshot() != before:
                            self._undo_push(label, before)
                    except Exception:
                        LOG.exception("Αποτυχία καταγραφής αναίρεσης")
        return wrapper
    return deco


for _name, _label in (
    ("import_schedule_csv", "εισαγωγή προγράμματος από CSV"),
    ("import_teachers_csv", "εισαγωγή εκπαιδευτικών από CSV"),
    ("import_exceptions_csv", "εισαγωγή ειδικών ημερών από CSV"),
):
    setattr(EfimeriesApp, _name, _undoable(_label)(getattr(EfimeriesApp, _name)))


def main():
    setup_logging()
    sys.excepthook = lambda t, v, tb: LOG.error("Μη χειριζόμενο σφάλμα:\n%s", "".join(traceback.format_exception(t, v, tb)))
    app = EfimeriesApp()
    if not app.ok:
        return
    try:
        app.open_web_ui()
    except Exception as e:
        LOG.exception("Εκκίνηση της διεπαφής")
        messagebox.showerror(APP_TITLE, f"Δεν ήταν δυνατό να ανοίξει η εφαρμογή:\n{e}")
        app._on_close()
        return
    app.run()


if __name__ == "__main__":
    main()
