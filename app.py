# -*- coding: utf-8 -*-
"""
Εφημερίες Εκπαιδευτικών - Portable Desktop App v0.42
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
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

APP_TITLE = "Εφημερίες Εκπαιδευτικών"
APP_VERSION = "0.60"
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


def load_storage_root() -> Path:
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



MONTHS_GR_ALL = ["Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος", "Ιούλιος", "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος"]


class _Tip:
    """Απλή υπόδειξη (tooltip) που εμφανίζεται όταν το ποντίκι μείνει λίγο πάνω σε ένα widget."""

    def __init__(self, widget, text: str, delay: int = 550):
        self.widget, self.text, self.delay = widget, text, delay
        self._after = None
        self._win = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self._after = self.widget.after(self.delay, self._show)

    def _show(self):
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            self._win = tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(tw, text=self.text, bg="#2C3E50", fg="white", padx=8, pady=3, font=("Segoe UI", 9)).pack()
        except Exception:
            self._win = None

    def _hide(self, _e=None):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


class SideNav(tk.Frame):
    """Πλευρικό μενού πλοήγησης. Αντικαθιστά το πάνω ttk.Notebook και έχει το ίδιο βασικό API (add/select)."""

    BG, HOVER, ACTIVE = "#2C3E50", "#38506A", "#3F5A76"
    FG, FG_ACTIVE, MUTED = "#D5DEE7", "#FFFFFF", "#A9B7C4"

    def __init__(self, master, content: tk.Misc):
        super().__init__(master, bg=self.BG, width=220)
        self.pack_propagate(False)
        self.content = content
        self._buttons: Dict[tk.Misc, tk.Button] = {}
        self.frames: List[tk.Misc] = []
        self._current: Optional[tk.Misc] = None
        self.on_change = None
        brand = tk.Frame(self, bg=self.BG)
        brand.pack(fill="x", padx=18, pady=(18, 8))
        tk.Label(brand, text="Εφημερίες", bg=self.BG, fg="white", font=("Segoe UI", 17, "bold"), anchor="w").pack(fill="x")
        self.subtitle = tk.Label(brand, text="", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=184)
        self.subtitle.pack(fill="x", pady=(2, 0))
        self.footer = tk.Frame(self, bg=self.BG)
        self.footer.pack(fill="x", side="bottom", padx=10, pady=(4, 12))
        self.body = tk.Frame(self, bg=self.BG)
        self.body.pack(fill="both", expand=True, padx=10)

    def _button(self, parent, text, command) -> tk.Button:
        b = tk.Button(parent, text=text, command=command, anchor="w", relief="flat", bd=0, padx=14, pady=9,
                      bg=self.BG, fg=self.FG, activebackground=self.HOVER, activeforeground=self.FG_ACTIVE,
                      font=("Segoe UI", 10), cursor="hand2", highlightthickness=2,
                      highlightbackground=self.BG, highlightcolor="#8FB8E8", takefocus=1)
        b.pack(fill="x", pady=1)
        b.bind("<Enter>", lambda _e, w=b: self._paint(hover=w))
        b.bind("<Leave>", lambda _e: self._paint())
        return b

    def add_group(self, text: str) -> None:
        tk.Label(self.body, text=text, bg=self.BG, fg=self.MUTED, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=14, pady=(14, 2))

    def add(self, frame: tk.Misc, text: str = "", **_kw) -> None:
        self._buttons[frame] = self._button(self.body, text, lambda f=frame: self.select(f))
        self.frames.append(frame)

    def add_footer(self, text: str, command) -> None:
        self._button(self.footer, text, command)

    def _paint(self, hover: Optional[tk.Misc] = None) -> None:
        for frame, b in self._buttons.items():
            active = frame is self._current
            bg = self.ACTIVE if active else (self.HOVER if b is hover else self.BG)
            b.configure(bg=bg, fg=self.FG_ACTIVE if active else self.FG, font=("Segoe UI", 10, "bold" if active else "normal"))

    def select(self, frame=None):
        if frame is None:
            return str(self._current) if self._current is not None else ""
        if isinstance(frame, int):
            frame = self.frames[frame]
        if frame is self._current:
            return None
        if self._current is not None:
            self._current.pack_forget()
        self._current = frame
        frame.pack(in_=self.content, fill="both", expand=True)
        self._paint()
        if self.on_change:
            try:
                self.on_change(frame)
            except Exception:
                LOG.exception("Σφάλμα στην αλλαγή ενότητας")
        return None

    @property
    def current(self) -> Optional[tk.Misc]:
        return self._current


class EfimeriesApp(tk.Tk):
    def __init__(self):
        super().__init__()
        set_storage_root(load_storage_root())
        ensure_dirs()
        if not self._acquire_storage_lock():
            self.after(50, self.destroy)
            return
        self.active_school_year = prepare_year_storage()
        self.db = DB(year_db_path(self.active_school_year), self.active_school_year)
        self._update_window_title()
        try:
            self.iconbitmap(str(resource_path("app_icon.ico")))
        except Exception:
            pass
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1280, sw - 40)}x{min(740, sh - 90)}+{max(0, (sw - min(1280, sw - 40)) // 2)}+20")
        self.minsize(min(1100, sw - 40), min(640, sh - 90))
        self._restore_window_geometry(sw, sh)
        self.configure(bg="#f4f4f4")
        self.style = ttk.Style(self)
        self._style_ui()
        self._build_ui()
        self._install_clipboard_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_section_changed(self, frame) -> None:
        """Ανανεώνει την ενότητα που ανοίγει, ώστε να δείχνει πάντα τα τρέχοντα δεδομένα."""
        try:
            if frame is self.tab_schedule:
                self.refresh_schedule()
            elif frame is self.tab_home:
                self.refresh_home()
        except Exception:
            LOG.exception("Αποτυχία ανανέωσης ενότητας")

    def _on_f5(self) -> None:
        try:
            self.refresh_all()
            self.notify("Ανανεώθηκε.", ms=2500)
        except Exception:
            LOG.exception("Αποτυχία ανανέωσης (F5)")

    def notify(self, text: str, kind: str = "ok", ms: int = 7000) -> None:
        """Σύντομη ειδοποίηση στη γραμμή κατάστασης (αντί για παράθυρο που θέλει κλικ)."""
        colors = {"ok": "#14532D", "warn": "#8A5300", "error": "#B42318", "info": "#33404B"}
        try:
            prefix = "✓ " if kind == "ok" else ""
            self.status_label.config(text=prefix + text, fg=colors.get(kind, "#33404B"), font=("Segoe UI", 9, "bold"))
            if getattr(self, "_notify_after", None):
                self.after_cancel(self._notify_after)
            self._notify_after = self.after(ms, lambda: self.status_label.config(text="Έτοιμο", fg="#52606D", font=("Segoe UI", 9)))
        except Exception:
            pass

    def _update_window_title(self) -> None:
        school = ""
        year = getattr(self, "active_school_year", "")
        try:
            school = self.db.get_setting("school_name", "").strip()
            year = self.db.get_setting("school_year", year).strip()
        except Exception:
            pass
        bits = [APP_TITLE]
        if school:
            bits.append(school)
        if year:
            bits.append(year)
        self.title(" – ".join(bits) + f" | v{APP_VERSION} {APP_ARCH}")

    def _fit_mapped_toplevel(self, event=None) -> None:
        """Keep popup windows usable on small/high-DPI screens."""
        try:
            if event is None or not isinstance(event.widget, tk.Toplevel):
                return
            win = event.widget
            if win is self:
                return
            self.after_idle(lambda w=win: self._fit_toplevel_to_screen(w))
        except Exception:
            pass

    def _fit_toplevel_to_screen(self, win: tk.Toplevel) -> None:
        try:
            if not win.winfo_exists():
                return
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            max_w, max_h = max(520, sw - 80), max(420, sh - 110)
            req_w = max(win.winfo_reqwidth(), win.winfo_width(), 420)
            req_h = max(win.winfo_reqheight(), win.winfo_height(), 300)
            w, h = min(req_w, max_w), min(req_h, max_h)
            x, y = max(20, (sw - w)//2), max(20, (sh - h)//2 - 20)
            win.geometry(f"{w}x{h}+{x}+{y}")
            win.resizable(True, True)
        except Exception:
            pass

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

    def report_callback_exception(self, exc, val, tb) -> None:
        """Σφάλματα μέσα σε κουμπιά/events: καταγραφή και ορατό μήνυμα (αλλιώς χάνονται σιωπηλά στο .exe)."""
        LOG.error("Μη χειριζόμενο σφάλμα στο UI:\n%s", "".join(traceback.format_exception(exc, val, tb)))
        try:
            messagebox.showerror(
                "Απρόσμενο σφάλμα",
                f"Παρουσιάστηκε σφάλμα:\n{val}\n\nΗ λεπτομέρεια καταγράφηκε στο αρχείο:\n{LOG_PATH or 'logs/efimeries.log'}\n\n"
                "Τα δεδομένα σου δεν έχουν χαθεί. Αν το πρόβλημα επαναλαμβάνεται, στείλε το αρχείο αυτό."
            )
        except Exception:
            pass

    def _restore_window_geometry(self, sw: int, sh: int) -> None:
        """Θυμάται μέγεθος και θέση του παραθύρου από την προηγούμενη χρήση (πάντα εντός της οθόνης)."""
        try:
            st = self._update_state().get("window")
            if not st:
                return
            w = max(900, min(int(st["w"]), sw))
            h = max(600, min(int(st["h"]), sh - 40))
            x = max(0, min(int(st["x"]), sw - 200))
            y = max(0, min(int(st["y"]), sh - 120))
            self.geometry(f"{w}x{h}+{x}+{y}")
            if st.get("zoomed"):
                self.state("zoomed")
        except Exception:
            pass

    def _save_window_geometry(self) -> None:
        try:
            zoomed = self.state() == "zoomed"
            if zoomed:
                self.state("normal")
                self.update_idletasks()
            self._save_update_state(window={"w": self.winfo_width(), "h": self.winfo_height(), "x": self.winfo_x(), "y": self.winfo_y(), "zoomed": zoomed})
        except Exception:
            pass

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

    def change_storage_root(self) -> None:
        global STORAGE_ROOT
        selected = filedialog.askdirectory(title="Επιλογή φακέλου κοινόχρηστων δεδομένων", initialdir=str(STORAGE_ROOT))
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

    def check_shared_storage(self) -> None:
        """Show the exact root/database currently in use and basic record counts."""
        try:
            db_path = year_db_path(self.active_school_year)
            counts = {
                "Εκπαιδευτικοί": self.db.conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0],
                "Αναθέσεις": self.db.conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0],
                "Ειδικές ημέρες": self.db.conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0],
            }
            msg = (
                f"Αρχιτεκτονική: {APP_ARCH}\n"
                f"Ριζικός φάκελος: {STORAGE_ROOT}\n"
                f"Ενεργό σχολικό έτος: {self.active_school_year}\n"
                f"Βάση δεδομένων: {db_path}\n\n"
                + "\n".join(f"{k}: {v}" for k, v in counts.items())
                + "\n\nΓια κοινή χρήση x86/x64 πρέπει και οι δύο εκδόσεις να εμφανίζουν ακριβώς την ίδια διαδρομή βάσης."
            )
            messagebox.showinfo("Έλεγχος κοινόχρηστων δεδομένων", msg)
        except Exception as e:
            messagebox.showerror("Έλεγχος κοινόχρηστων δεδομένων", str(e))

    def reset_storage_root_local(self) -> None:
        if STORAGE_ROOT == app_dir():
            messagebox.showinfo("Φάκελος δεδομένων", "Η εφαρμογή χρησιμοποιεί ήδη τοπικά δεδομένα.")
            return
        if not messagebox.askyesno("Τοπικά δεδομένα", "Να επιστρέψει η εφαρμογή σε τοπικό φάκελο δεδομένων δίπλα στην εφαρμογή;"):
            return
        old_root = STORAGE_ROOT
        try:
            self.db.close(); self._release_storage_lock()
            set_storage_root(app_dir()); ensure_dirs(); save_storage_root(app_dir())
            if not self._acquire_storage_lock():
                raise RuntimeError("Ο τοπικός φάκελος δεδομένων δεν μπόρεσε να κλειδωθεί.")
            self.active_school_year = prepare_year_storage(); self.db = DB(year_db_path(self.active_school_year), self.active_school_year)
            self.sync_school_year_fields(); self.refresh_all()
            try:
                self.storage_path_var.set(str(STORAGE_ROOT))
                if hasattr(self, "storage_db_var"):
                    self.storage_db_var.set(str(year_db_path(self.active_school_year)))
            except Exception: pass
        except Exception as e:
            set_storage_root(old_root); ensure_dirs(); self._acquire_storage_lock()
            self.active_school_year = prepare_year_storage(); self.db = DB(year_db_path(self.active_school_year), self.active_school_year)
            messagebox.showerror("Τοπικά δεδομένα", str(e))

    def _install_clipboard_shortcuts(self) -> None:
        """Κανονικοποιεί αντιγραφή/επικόλληση και προσθέτει δεξί κλικ στα πεδία κειμένου."""
        def paste(event):
            try:
                event.widget.event_generate("<<Paste>>")
                return "break"
            except Exception:
                return None
        def copy(event):
            try:
                event.widget.event_generate("<<Copy>>")
                return "break"
            except Exception:
                return None
        def cut(event):
            try:
                event.widget.event_generate("<<Cut>>")
                return "break"
            except Exception:
                return None
        # Αντικαθιστούμε τις class bindings ώστε η ενέργεια να γίνεται μία φορά, όχι διπλά.
        for cls in ("Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox"):
            try:
                self.bind_class(cls, "<Control-v>", paste)
                self.bind_class(cls, "<Control-V>", paste)
                self.bind_class(cls, "<Shift-Insert>", paste)
                self.bind_class(cls, "<Control-c>", copy)
                self.bind_class(cls, "<Control-C>", copy)
                self.bind_class(cls, "<Control-x>", cut)
                self.bind_class(cls, "<Control-X>", cut)
            except Exception:
                pass

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Αποκοπή", command=lambda: self.focus_get().event_generate("<<Cut>>") if self.focus_get() else None)
        menu.add_command(label="Αντιγραφή", command=lambda: self.focus_get().event_generate("<<Copy>>") if self.focus_get() else None)
        menu.add_command(label="Επικόλληση", command=lambda: self.focus_get().event_generate("<<Paste>>") if self.focus_get() else None)
        def popup(event):
            try:
                event.widget.focus_set()
                if event.widget.winfo_class() in ("Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox"):
                    menu.tk_popup(event.x_root, event.y_root)
                    return "break"
            finally:
                try: menu.grab_release()
                except Exception: pass
        self.bind_all("<Button-3>", popup, add="+")

    def _style_ui(self):
        """Θέμα Sun Valley (sv_ttk, εμφάνιση Windows 11). Αν δεν φορτώσει, χρησιμοποιείται το επίπεδο στυλ."""
        self.sv_theme_active = False
        try:
            try:
                import sv_ttk
            except ImportError:
                base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
                if str(base) not in sys.path:
                    sys.path.insert(0, str(base))
                import sv_ttk
            sv_ttk.set_theme("light", self)
            self.update_idletasks()  # το θέμα ξαναχρωματίζει τα tk widgets στο idle· να γίνει πριν δημιουργηθεί το UI
            # Το sv_ttk ξαναχρωματίζει ΟΛΑ τα tk widgets σε κάθε αλλαγή στυλ· το απενεργοποιούμε μετά την πρώτη εφαρμογή,
            # ώστε να μένουν τα χρώματα της πλαϊνής μπάρας.
            try:
                self.tk.eval("configure_colors")  # μία φορά, πριν δημιουργηθεί το UI: τα tk widgets χωρίς δικό τους χρώμα παίρνουν τα χρώματα του θέματος
                self.tk.eval("proc configure_colors {} {}")
            except tk.TclError:
                pass
            st = self.style
            F = "Segoe UI"
            st.configure("Treeview", rowheight=30)
            st.configure("Treeview.Heading", font=(F, 10, "bold"))
            st.configure("Primary.TButton", foreground="#1F5FBF", font=(F, 10, "bold"))
            st.configure("Danger.TButton", foreground="#B3261E", font=(F, 10, "bold"))
            st.configure("Accent.TButton", font=(F, 10, "bold"))
            st.map("Danger.TButton", foreground=[("disabled", "#9AA5B0"), ("!disabled", "#B3261E")])
            st.map("Primary.TButton", foreground=[("disabled", "#9AA5B0"), ("!disabled", "#1F5FBF")])
            st.configure("TLabelframe.Label", foreground="#34495E", font=(F, 10, "bold"))
            st.configure("TNotebook.Tab", font=(F, 10, "bold"))
            self.sv_theme_active = True
            return
        except Exception:
            try:
                logging.getLogger(__name__).exception("sv_ttk: αποτυχία φόρτωσης, χρήση επίπεδου στυλ")
            except Exception:
                pass
        self._style_ui_flat()

    def _style_ui_flat(self):
        """Μοντέρνα, επίπεδη εμφάνιση πάνω στο ttk (χωρίς εξωτερικές βιβλιοθήκες)."""
        st = self.style
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        BG, CARD, LINE = "#F7F7F5", "#FFFFFF", "#D9DEE3"
        INK, MUTE = "#243240", "#5B6B7A"
        BLUE, BLUE_D, BLUE_L = "#2F6FDE", "#2459B8", "#E6EEFB"
        RED, RED_L = "#B3261E", "#FBEAE8"
        F = "Segoe UI"
        st.configure(".", background=BG, foreground=INK, font=(F, 10), bordercolor=LINE,
                     lightcolor=BG, darkcolor=BG, focuscolor=BLUE)
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=INK, font=(F, 10))
        st.configure("TCheckbutton", background=BG, foreground=INK, font=(F, 10))
        st.configure("TRadiobutton", background=BG, foreground=INK, font=(F, 10))
        st.configure("TLabelframe", background=BG, bordercolor=LINE, relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label", background=BG, foreground="#34495E", font=(F, 10, "bold"))
        st.configure("TSeparator", background=LINE)
        # Κουμπιά
        base = dict(relief="flat", borderwidth=1, focusthickness=2, focuscolor=BLUE)
        st.configure("TButton", font=(F, 10), padding=(12, 7), background="#EDF0F3", foreground=INK, bordercolor=LINE, **base)
        st.map("TButton", background=[("disabled", "#F1F3F5"), ("pressed", "#D5DBE1"), ("active", "#E1E6EB")],
               foreground=[("disabled", "#9AA5B0")], bordercolor=[("focus", BLUE)])
        st.configure("Secondary.TButton", font=(F, 10), padding=(12, 7), background="#EDF0F3", foreground=INK, bordercolor=LINE, **base)
        st.map("Secondary.TButton", background=[("disabled", "#F1F3F5"), ("pressed", "#D5DBE1"), ("active", "#E1E6EB")],
               foreground=[("disabled", "#9AA5B0")])
        st.configure("Primary.TButton", font=(F, 10, "bold"), padding=(12, 7), background=BLUE_L, foreground=BLUE_D, bordercolor="#B9CDF3", **base)
        st.map("Primary.TButton", background=[("disabled", "#F1F3F5"), ("pressed", "#C4D6F6"), ("active", "#D4E2FA")],
               foreground=[("disabled", "#9AA5B0")])
        st.configure("Accent.TButton", font=(F, 10, "bold"), padding=(14, 9), background=BLUE, foreground="white", bordercolor=BLUE_D, **base)
        st.map("Accent.TButton", background=[("disabled", "#B8C6DC"), ("pressed", "#1D4A9B"), ("active", BLUE_D)],
               foreground=[("disabled", "#F4F6F8")], bordercolor=[("disabled", "#B8C6DC")])
        st.configure("Danger.TButton", font=(F, 10, "bold"), padding=(12, 7), background=RED_L, foreground=RED, bordercolor="#EBC3BF", **base)
        st.map("Danger.TButton", background=[("disabled", "#F1F3F5"), ("pressed", "#F2CFCB"), ("active", "#F6DBD8")],
               foreground=[("disabled", "#9AA5B0")])
        # Πεδία
        for w in ("TEntry", "TSpinbox", "TCombobox"):
            st.configure(w, fieldbackground=CARD, background=CARD, foreground=INK, bordercolor=LINE, padding=(6, 5),
                         arrowcolor=MUTE, insertcolor=INK)
            st.map(w, bordercolor=[("focus", BLUE)], fieldbackground=[("disabled", "#EEF0F2")],
                   foreground=[("disabled", "#8A95A0")])
        st.map("TCombobox", fieldbackground=[("readonly", CARD)], selectbackground=[("readonly", CARD)],
               selectforeground=[("readonly", INK)])
        # Καρτέλες
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", padding=(16, 9), font=(F, 10, "bold"), background="#E7EAEE", foreground=MUTE, borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", CARD), ("active", "#F0F2F5")],
               foreground=[("selected", BLUE_D)])
        # Πίνακες
        st.configure("Treeview", rowheight=30, font=(F, 10), fieldbackground=CARD, background=CARD, foreground=INK,
                     bordercolor=LINE, borderwidth=1, lightcolor=CARD, darkcolor=CARD)
        st.map("Treeview", background=[("selected", "#CFE0FA")], foreground=[("selected", "#12306B")])
        st.configure("Treeview.Heading", font=(F, 10, "bold"), foreground="#34495E", background="#EEF1F4",
                     relief="flat", padding=(8, 7), bordercolor=LINE)
        st.map("Treeview.Heading", background=[("active", "#E3E8ED")])
        # Μπάρες
        for orient in ("Vertical", "Horizontal"):
            st.configure(orient + ".TScrollbar", background="#CBD2D9", troughcolor=BG, bordercolor=BG,
                         arrowcolor=MUTE, relief="flat", gripcount=0, arrowsize=14)
            st.map(orient + ".TScrollbar", background=[("active", "#AEB8C2"), ("pressed", "#97A3AF")])
        st.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#E1E6EB", bordercolor=BG,
                     lightcolor=BLUE, darkcolor=BLUE, thickness=14)
        try:
            self.option_add("*TCombobox*Listbox.font", (F, 10))
            self.option_add("*TCombobox*Listbox.selectBackground", "#CFE0FA")
            self.option_add("*TCombobox*Listbox.selectForeground", "#12306B")
        except Exception:
            pass


    def _build_ui(self):
        # Η γραμμή κατάστασης πακετάρεται πρώτη, ώστε να μην κόβεται όταν μικραίνει το παράθυρο.
        self._status_frame = tk.Frame(self, bg="#E9ECEF", height=32)
        self._status_frame.pack(fill="x", side="bottom")
        body = tk.Frame(self, bg="#F4F5F3")
        body.pack(fill="both", expand=True)
        self.content = ttk.Frame(body)
        self.nb = SideNav(body, self.content)
        self.nb.pack(side="left", fill="y")
        self.content.pack(side="left", fill="both", expand=True)
        self.header_status = self.nb.subtitle
        self.tab_home = ttk.Frame(self.content)
        self.tab_settings = ttk.Frame(self.content)
        self.tab_teachers = ttk.Frame(self.content)
        self.tab_organisation = ttk.Frame(self.content)
        self.tab_generate = ttk.Frame(self.content)
        self.tab_schedule = ttk.Frame(self.content)
        self.tab_reports = ttk.Frame(self.content)
        self.nb.add_group("ΚΑΘΗΜΕΡΙΝΑ")
        self.nb.add(self.tab_home, "Αρχική")
        self.nb.add(self.tab_schedule, "Πρόγραμμα")
        self.nb.add(self.tab_reports, "Εκτυπώσεις και σύνολα")
        self.nb.add_group("ΣΧΕΔΙΑΣΜΟΣ")
        self.nb.add(self.tab_generate, "Παραγωγή προγράμματος")
        self.nb.add_group("ΡΥΘΜΙΣΗ")
        self.nb.add(self.tab_teachers, "Εκπαιδευτικοί")
        self.nb.add(self.tab_organisation, "Οργάνωση")
        self.nb.add(self.tab_settings, "Ρυθμίσεις")
        self.nb.add_footer("Βοήθεια / Οδηγός χρήσης", self.open_user_guide)
        self.nb.add_footer("Πληροφορίες / Επικοινωνία", self.open_about_contact)
        self.nb.select(self.tab_home)
        self.after(4000, lambda: self.check_for_updates(manual=False))
        self.nb.on_change = self._on_section_changed
        for i, frame in enumerate(self.nb.frames[:9], start=1):
            self.bind_all(f"<Control-Key-{i}>", lambda _e, f=frame: self.nb.select(f))
        self.bind_all("<F5>", lambda _e: self._on_f5())

        self.org_nb = ttk.Notebook(self.tab_organisation)
        self.org_nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_areas = ttk.Frame(self.org_nb)
        self.tab_calendar = ttk.Frame(self.org_nb)
        self.tab_intervals = ttk.Frame(self.org_nb)
        self.tab_shifts = ttk.Frame(self.org_nb)
        self.org_nb.add(self.tab_areas, text="Τομείς")
        self.org_nb.add(self.tab_calendar, text="Ειδικές ημέρες")
        self.org_nb.add(self.tab_intervals, text="Χρονικές ζώνες")
        self.org_nb.add(self.tab_shifts, text="Βάρδιες / ζώνες")

        self._build_home()
        self._build_settings()
        self._build_teachers()
        self._build_areas()
        self._build_calendar()
        self._build_intervals()
        self._build_shifts()
        self._build_generate()
        self._build_schedule()
        self._build_step3_schedule_ui()
        self._build_reports()

        status = self._status_frame
        status.configure(bg="#EAEDEB")
        self.status_label = tk.Label(status, text="Έτοιμο", bg="#EAEDEB", fg="#52606D", font=("Segoe UI", 9))
        self.status_label.pack(side="left", padx=12, pady=4)
        self.build_status_label = tk.Label(status, text=f"v{APP_VERSION} | {APP_ARCH}", bg="#EAEDEB", fg="#52606D", font=("Segoe UI", 9, "bold"))
        self.build_status_label.pack(side="right", padx=(4, 12))
        self.storage_status_label = tk.Label(status, text=f"Δεδομένα: {STORAGE_ROOT}", bg="#EAEDEB", fg="#7A858C", font=("Segoe UI", 9))
        self.storage_status_label.pack(side="right", padx=4)
        self.refresh_all()


    def labeled_entry(self, parent, label, value="", width=20):
        f = ttk.Frame(parent)
        ttk.Label(f, text=label).pack(anchor="w")
        e = ttk.Entry(f, width=width)
        e.insert(0, value)
        e.pack(fill="x")
        return f, e

    def go_tab(self, tab) -> None:
        self.nb.select(tab)

    def day_name(self, idx: int) -> str:
        return DAYS_GR[idx] if isinstance(idx, int) and 0 <= idx < len(DAYS_GR) else "—"

    def availability_summary(self, t: Teacher) -> str:
        vals = [t.mon, t.tue, t.wed, t.thu, t.fri]
        short = ["Δε", "Τρ", "Τε", "Πε", "Πα"]
        selected = [short[i] for i, v in enumerate(vals) if v]
        return ", ".join(selected) if selected else "Καμία"

    def set_status(self, text: str) -> None:
        try:
            self.status_label.config(text=text)
        except Exception:
            pass

    def _build_home(self):
        f = ttk.Frame(self.tab_home, padding=20)
        f.pack(fill="both", expand=True)

        hero = tk.Frame(f, bg="#2F6F73", padx=22, pady=12)
        hero.pack(fill="x")
        self.home_school_var = tk.StringVar(value="Σχολείο")
        self.home_year_var = tk.StringVar(value="—")
        tk.Label(hero, text="Πρόγραμμα Δημιουργίας Εφημεριών", bg="#2F6F73", fg="white", font=("Segoe UI", 21, "bold")).pack(anchor="w")
        tk.Label(hero, textvariable=self.home_school_var, bg="#2F6F73", fg="#F4D77A", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(6, 0))
        tk.Label(hero, textvariable=self.home_year_var, bg="#2F6F73", fg="#E8F1F1", font=("Segoe UI", 11)).pack(anchor="w", pady=(2, 0))
        self.home_build_var = tk.StringVar(value=f"v{APP_VERSION} · {APP_ARCH}")
        tk.Label(hero, textvariable=self.home_build_var, bg="#2F6F73", fg="#D6E2E2", font=("Segoe UI", 9, "bold")).pack(anchor="e", side="right")

        _wb = ttk.Frame(f)
        _wb.pack(fill="x", pady=(8, 6))
        ttk.Label(_wb, text="Γρήγορη εικόνα και βασικές ενέργειες καθημερινής χρήσης.", foreground="#5F6B76").pack(side="left")
        ttk.Button(_wb, text="Νέα διεπαφή (Edge) — δοκιμαστική", style="Accent.TButton", command=self.open_web_ui).pack(side="right")

        cards = ttk.Frame(f)
        cards.pack(fill="x")
        self.home_teachers_var = tk.StringVar(value="0")
        self.home_assignments_var = tk.StringVar(value="0")
        self.home_mode_var = tk.StringVar(value="—")
        self.home_storage_var = tk.StringVar(value="Τοπικά")
        card_data = [
            ("Εκπαιδευτικοί", self.home_teachers_var),
            ("Αναθέσεις", self.home_assignments_var),
            ("Τρόπος εφημερίας", self.home_mode_var),
            ("Δεδομένα", self.home_storage_var),
        ]
        for i, (title, var) in enumerate(card_data):
            box = ttk.LabelFrame(cards, text=title, padding=(14, 10))
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 5 if i < 3 else 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 14, "bold"), foreground="#2F6F73", wraplength=250).pack(anchor="w")
            cards.columnconfigure(i, weight=1)

        flow = ttk.LabelFrame(f, text="Καθημερινές ενέργειες", padding=14)
        flow.pack(fill="x", pady=(2, 10))
        ttk.Button(flow, text="Δημιουργία προγράμματος", style="Accent.TButton", command=lambda: self.go_tab(self.tab_generate)).pack(side="left", padx=(0, 8))
        ttk.Button(flow, text="Προβολή προγράμματος", style="Primary.TButton", command=lambda: self.go_tab(self.tab_schedule)).pack(side="left", padx=8)
        ttk.Button(flow, text="Αλλαγή εκπαιδευτικού", style="Primary.TButton", command=lambda: self.go_tab(self.tab_schedule)).pack(side="left", padx=8)
        ttk.Button(flow, text="Εκτυπώσεις", style="Primary.TButton", command=lambda: self.go_tab(self.tab_reports)).pack(side="left", padx=8)

        self._build_home_panels(f)

        info = ttk.LabelFrame(f, text="Έλεγχος ρυθμίσεων", padding=8)
        self._home_info = info
        self._home_info_open = False
        self.home_text = tk.Text(info, height=8, wrap="word", font=("Segoe UI", 10), relief="flat", bg="white", padx=10, pady=8)
        self.home_text.pack(fill="both", expand=True)
        bottom = ttk.Frame(f)
        bottom.pack(fill="x", pady=(10, 0), side="bottom")
        ttk.Button(bottom, text="Άνοιγμα φακέλου εξαγωγών", command=lambda: self.open_path(EXPORT_DIR)).pack(side="left")
        self.home_path_var = tk.StringVar(value=str(STORAGE_ROOT))
        ttk.Label(bottom, textvariable=self.home_path_var, foreground="#7A858C").pack(side="right")
        ttk.Button(bottom, text="Έλεγχος ρυθμίσεων", command=self._toggle_home_info).pack(side="left", padx=8)
        self._home_panels_row.pack_forget()
        self._home_panels_row.pack(fill="both", expand=True, pady=(0, 10))


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

    def _undo_ui(self) -> None:
        stack = self.__dict__.get("_undo_stack", [])
        label = stack[-1][0] if stack else None
        try:
            self.sched_undo_btn.config(state="normal" if stack else "disabled")
            self.sched_more_menu.entryconfigure(0, state="normal" if stack else "disabled",
                                                label=f"Αναίρεση: {label}  (Ctrl+Z)" if stack else "Αναίρεση  (Ctrl+Z)")
        except Exception:
            pass

    def _on_ctrl_z(self, event=None):
        w = self.focus_get()
        if w is not None and w.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox"):
            return None
        if getattr(self.nb, "current", None) not in (self.tab_schedule, self.tab_home):
            return None
        self.undo_last()
        return "break"

    def _build_step3_schedule_ui(self) -> None:
        """Κουμπιά και μενού του Βήματος 3 στο «Πρόγραμμα»."""
        self.sched_undo_btn = ttk.Button(self.sched_toolbar_extra, text="↶ Αναίρεση", command=self.undo_last, state="disabled")
        ttk.Button(self.sched_toolbar_extra, text="Απουσία…", command=self.absence_dialog).pack(side="left", padx=(0, 6))
        self.sched_undo_btn.pack(side="left")
        m = self.sched_more_menu
        m.insert_command(0, label="Αναίρεση  (Ctrl+Z)", command=self.undo_last, state="disabled")
        m.insert_separator(1)
        m.insert_command(2, label="Απουσία εκπαιδευτικού για διάστημα…", command=self.absence_dialog)
        m.insert_separator(3)
        m.add_command(label="Εξαγωγή ημερολογίου εκπαιδευτικού (.ics)…", command=self.export_teacher_ics)
        self._extra_actions = []
        self.bind_all("<Control-z>", self._on_ctrl_z)

    # ---- βοηθητικά παράθυρα ----
    def _modal(self, title: str, w: int, h: int) -> tk.Toplevel:
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(w, sw - 40), min(h, sh - 110)
        win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        win.minsize(min(640, w), min(480, h))
        try:
            win.grab_set()
        except tk.TclError:
            pass
        win.bind("<Escape>", lambda _e: win.destroy())
        return win

    @staticmethod
    def _gr_day(d: date) -> str:
        return ("Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή")[d.weekday()]

    @staticmethod
    def _slot_key(r) -> tuple:
        return (r["d"], r["interval_name"], r["start_time"], r["end_time"])

    # ---- εναλλαγή εφημεριών ----
    def _swap_assignments(self, a_id: int, b_id: int, reason: str = "") -> Optional[str]:
        """Ανταλλάσσει τους εκπαιδευτικούς δύο εφημεριών. Επιστρέφει κείμενο σφάλματος ή None."""
        c = self.db.conn
        a = c.execute(self.SCHEDULE_SQL + " WHERE a.id=?", (a_id,)).fetchone()
        b = c.execute(self.SCHEDULE_SQL + " WHERE a.id=?", (b_id,)).fetchone()
        if not a or not b or a_id == b_id:
            return "Δεν βρέθηκαν οι εφημερίες."
        if a["locked"] or b["locked"]:
            return "Δεν γίνεται εναλλαγή με κλειδωμένη/πραγματοποιημένη εφημερία."
        if a["teacher_id"] == b["teacher_id"]:
            return "Και οι δύο εφημερίες ανήκουν ήδη στον/στην ίδιο/α εκπαιδευτικό."

        def clash(teacher_id, target, ignore_id):
            return c.execute(
                "SELECT 1 FROM assignments WHERE teacher_id=? AND id NOT IN (?,?) AND d=? AND interval_name=? AND start_time=? AND end_time=?",
                (teacher_id, a_id, b_id, target["d"], target["interval_name"], target["start_time"], target["end_time"])).fetchone()
        if clash(b["teacher_id"], a, b_id):
            return f"Ο/Η {b['teacher']} έχει ήδη άλλη εφημερία την ίδια ημέρα και ώρα με την πρώτη εφημερία."
        if clash(a["teacher_id"], b, a_id):
            return f"Ο/Η {a['teacher']} έχει ήδη άλλη εφημερία την ίδια ημέρα και ώρα με τη δεύτερη εφημερία."
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            c.execute("UPDATE assignments SET teacher_id=? WHERE id=?", (b["teacher_id"], a_id))
            c.execute("UPDATE assignments SET teacher_id=? WHERE id=?", (a["teacher_id"], b_id))
            for mine, other, tgt in ((a, b, a_id), (b, a, b_id)):
                c.execute(
                    "INSERT INTO duty_changes(assignment_id,changed_at,change_type,old_teacher_id,new_teacher_id,old_teacher_name,new_teacher_name,reason) VALUES(?,?,?,?,?,?,?,?)",
                    (tgt, now, "Εναλλαγή εφημεριών", mine["teacher_id"], other["teacher_id"], mine["teacher"], other["teacher"],
                     (reason + " | " if reason else "") + f"Εναλλαγή με εφημερία {fmt_date(other['d'])} ({other['display_area']})."))
            c.commit()
        except Exception:
            c.rollback()
            raise
        return None

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

    def absence_dialog(self) -> None:
        teachers = self.db.active_teachers()
        if not teachers:
            messagebox.showerror("Εκπαιδευτικοί", "Δεν υπάρχουν ενεργοί εκπαιδευτικοί.")
            return
        win = self._modal("Απουσίες εκπαιδευτικών", 860, 780)
        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)
        foot = ttk.Frame(outer)
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(outer, text="Η απουσία αφορά ολόκληρες ημέρες. Ο αλγόριθμος δεν βάζει τον/την εκπαιδευτικό σε εφημερία αυτές τις ημέρες, και αν οι διαθέσιμοι δεν φτάνουν για τους τομείς "
                              "καλύπτει το κενό με πρόσθετη εφημερία άλλου/ης. Η διαφορά αναπληρώνεται αργότερα, όσο το επιτρέπουν οι δηλωμένες ημέρες του/της.",
                  foreground="#5F6B76", wraplength=820, justify="left").pack(anchor="w", pady=(0, 8))
        box = ttk.LabelFrame(outer, text="Νέα απουσία", padding=10)
        box.pack(fill="x")
        names = [t.name for t in teachers]
        cur_t = None
        if getattr(self, "sched_sel_id", None):
            r = self.db.conn.execute("SELECT teacher_id FROM assignments WHERE id=?", (self.sched_sel_id,)).fetchone()
            cur_t = r[0] if r else None
        tv = tk.StringVar(value=next((t.name for t in teachers if t.id == cur_t), names[0]))
        ttk.Label(box, text="Εκπαιδευτικός").grid(row=0, column=0, sticky="w")
        ttk.Combobox(box, textvariable=tv, values=names, state="readonly", width=28).grid(row=1, column=0, padx=(0, 12), sticky="w")
        d1v, d2v = tk.StringVar(value=fmt_date(date.today())), tk.StringVar(value=fmt_date(date.today()))
        ttk.Label(box, text="Από (ΗΗ/ΜΜ/ΕΕΕΕ)").grid(row=0, column=1, sticky="w")
        e1 = ttk.Entry(box, textvariable=d1v, width=12)
        e1.grid(row=1, column=1, sticky="w")
        self._cal_button(box, e1).grid(row=1, column=2, padx=(2, 10))
        ttk.Label(box, text="Έως").grid(row=0, column=3, sticky="w")
        e2 = ttk.Entry(box, textvariable=d2v, width=12)
        e2.grid(row=1, column=3, sticky="w")
        self._cal_button(box, e2).grid(row=1, column=4, padx=(2, 10))
        rv = tk.StringVar(value="")
        ttk.Label(box, text="Αιτία (προαιρετικά)").grid(row=0, column=5, sticky="w")
        ttk.Entry(box, textvariable=rv, width=22).grid(row=1, column=5, sticky="w")

        how_var = tk.StringVar(value="cover")
        hbox = ttk.Frame(outer)
        hbox.pack(fill="x", pady=(8, 2))
        ttk.Label(hbox, text="Τι θα γίνει με τις ήδη προγραμματισμένες εφημερίες;", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Radiobutton(hbox, text="Μόνο αντικατάσταση των εφημεριών του/της στο διάστημα (προτείνονται αντικαταστάτες) — για σύντομη απουσία", variable=how_var, value="cover").pack(anchor="w", pady=1)
        ttk.Radiobutton(hbox, text="Ανακατανομή του διαστήματος της απουσίας (οι κλειδωμένες/πραγματοποιημένες μένουν)", variable=how_var, value="range").pack(anchor="w", pady=1)
        ttk.Radiobutton(hbox, text="Ανακατανομή από την αρχή της απουσίας έως το τέλος του υπάρχοντος προγράμματος — με αναπλήρωση (αλλάζει και το μελλοντικό πρόγραμμα)", variable=how_var, value="tail").pack(anchor="w", pady=1)
        tree = ttk.Treeview(outer, columns=("d", "area", "new", "note"), show="headings", selectmode="none", height=2)
        for col, txt, wd in (("d", "Ημερομηνία", 150), ("area", "Τομέας", 170), ("new", "Αντικαταστάτης/ρια", 220), ("note", "Σημείωση", 260)):
            tree.heading(col, text=txt)
            tree.column(col, width=wd, anchor="w")
        tree.pack(fill="x")
        info = ttk.Label(outer, text="", foreground="#34495E", wraplength=820, justify="left")
        info.pack(anchor="w", pady=6)
        state = {"plan": [], "ok": False, "n": 0, "regen": None}

        def _auto_how(*_a):
            try:
                a, b = parse_date(d1v.get()), parse_date(d2v.get())
            except ValueError:
                return
            if b < a:
                a, b = b, a
            n = sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)
            how_var.set("cover" if n <= 2 else ("range" if n <= 4 else "tail"))
        d1v.trace_add("write", _auto_how)
        d2v.trace_add("write", _auto_how)

        def read_form():
            try:
                a, b = parse_date(d1v.get()), parse_date(d2v.get())
            except ValueError as e:
                messagebox.showerror("Ημερομηνία", str(e), parent=win)
                return None
            if b < a:
                a, b = b, a
            return next(t for t in teachers if t.name == tv.get()), a, b

        def preview():
            f = read_form()
            if not f:
                return
            t, a, b = f
            self._absent_cache = None
            plan, locked_n = self._plan_absence(t.id, a, b)
            tree.delete(*tree.get_children())
            for i, it in enumerate(plan):
                tm = "" if it["mode"] == "daily" else f" · {it['start']}"
                tree.insert("", "end", iid=str(i), values=(f"{self._gr_day(parse_date(it['d']))} {fmt_date(it['d'])}", it["area"] + tm, it["new_name"] or "—", it["note"]))
            state["plan"] = plan
            miss = sum(1 for it in plan if not it["new_id"])
            parts = [f"{len(plan)} προγραμματισμένες εφημερίες θα περάσουν σε άλλους." if plan else "Δεν υπάρχουν μη κλειδωμένες εφημερίες στο διάστημα."]
            if locked_n:
                parts.append(f"{locked_n} κλειδωμένες/πραγματοποιημένες δεν αλλάζουν.")
            if miss:
                parts.append(f"{miss} δεν βρήκαν αντικαταστάτη και θα μείνουν ως έχουν.")
            info.config(text=" ".join(parts))

        def register():
            f = read_form()
            if not f:
                return
            t, a, b = f
            how = how_var.get()
            if how in ("range", "tail"):
                today = date.today()
                if b < today:
                    messagebox.showwarning("Απουσία στο παρελθόν", "Η απουσία έχει ήδη περάσει, δεν υπάρχει μελλοντικό πρόγραμμα να ανακατανεμηθεί. "
                                           "Επίλεξε «Μόνο αντικατάσταση των εφημεριών».", parent=win)
                    return
                start = max(a, today)
                end = b
                if how == "tail":
                    last = self.db.conn.execute("SELECT MAX(d) FROM assignments").fetchone()[0]
                    if last:
                        end = max(b, parse_date(last))
                if end < start:
                    end = start
                txt = (f"Θα ανακατανεμηθεί το πρόγραμμα από {fmt_date(start)} έως {fmt_date(end)}"
                       + (" (έως το τέλος του υπάρχοντος προγράμματος, ώστε να γίνει και η αναπλήρωση)" if how == "tail" else "") + ".\n\n"
                       "• Οι κλειδωμένες/πραγματοποιημένες εφημερίες δεν αλλάζουν.\n"
                       "• Οι εφημερίες των άλλων εκπαιδευτικών στο διάστημα μπορεί να αλλάξουν.\n"
                       + ("• Ημέρες πριν από σήμερα δεν αλλάζουν.\n" if a < today else "")
                       + "• Θα παρθεί αυτόματο αντίγραφο ασφαλείας και θα δεις σύνοψη πριν κρατηθεί το νέο πρόγραμμα (Όχι = επαναφορά).\n\nΣυνέχεια;")
                if not messagebox.askyesno("Ανακατανομή λόγω απουσίας", txt, parent=win):
                    return
                self._add_absence(t.id, a, b, rv.get().strip())
                state["ok"] = True
                state["regen"] = ("range_keep", start, end, t.id, t.name, a, b)
                win.destroy()
                return
            self._add_absence(t.id, a, b, rv.get().strip())
            n = 0
            if how == "cover":
                plan, _l = self._plan_absence(t.id, a, b)
                n = self._apply_absence_plan(t.id, plan, rv.get().strip() or "Απουσία", False)
                done = [it for it in plan if it["new_id"]]
                miss = [it for it in plan if not it["new_id"]]
                lines = [f"Η απουσία του/της {t.name} ({fmt_date(a)}–{fmt_date(b)}) καταχωρήθηκε.", ""]
                if not plan:
                    lines.append("Δεν υπήρχαν μη κλειδωμένες εφημερίες στο διάστημα, άρα δεν χρειάστηκε αντικατάσταση.")
                else:
                    lines.append(f"Αντικαταστάθηκαν {len(done)} από {len(plan)} εφημερίες:")
                    for it in done[:12]:
                        lines.append(f"  • {fmt_date(it['d'])} · {it['area']} → {it['new_name']}")
                    if len(done) > 12:
                        lines.append(f"  … και {len(done) - 12} ακόμη.")
                    if miss:
                        lines.append("")
                        lines.append(f"{len(miss)} εφημερίες ΔΕΝ βρήκαν αντικαταστάτη και έμειναν ως είχαν. Ελέγξτε τες στο Πρόγραμμα.")
                messagebox.showinfo("Απουσία καταχωρήθηκε", "\n".join(lines), parent=win)
            state["ok"], state["n"] = True, n
            fill_list()
            tree.delete(*tree.get_children())
            info.config(text=f"Η απουσία καταχωρήθηκε" + (f" και καλύφθηκαν {n} εφημερίες." if n else "."))
            self.refresh_all()

        btns = ttk.Frame(box)
        btns.grid(row=2, column=0, columnspan=6, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Εύρεση αντικαταστατών", command=preview).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Καταχώρηση απουσίας", style="Accent.TButton", command=register).pack(side="left")

        lst = ttk.LabelFrame(outer, text="Καταχωρημένες απουσίες", padding=8)
        lst.pack(fill="both", expand=True, pady=(8, 0))
        at = ttk.Treeview(lst, columns=("t", "a", "b", "r"), show="headings", selectmode="browse", height=3)
        for col, txt, wd in (("t", "Εκπαιδευτικός", 240), ("a", "Από", 120), ("b", "Έως", 120), ("r", "Αιτία", 300)):
            at.heading(col, text=txt)
            at.column(col, width=wd, anchor="w")
        at.pack(fill="both", expand=True)

        def fill_list():
            at.delete(*at.get_children())
            for r in self._absence_rows():
                at.insert("", "end", iid=str(r["id"]), values=(r["teacher"], fmt_date(r["d_from"]), fmt_date(r["d_to"]), r["reason"] or ""))

        def delete_sel():
            sel = at.selection()
            if not sel:
                messagebox.showwarning("Επιλογή", "Διάλεξε μια απουσία από τη λίστα.", parent=win)
                return
            if messagebox.askyesno("Διαγραφή απουσίας", "Να διαγραφεί η απουσία; Οι εφημερίες που έχουν ήδη αλλάξει δεν επανέρχονται· μπορείς να ξανακάνεις ανακατανομή.", parent=win):
                self._delete_absence(int(sel[0]))
                state["ok"] = True
                fill_list()
                self.refresh_all()
        fill_list()
        ttk.Button(foot, text="Κλείσιμο", command=win.destroy).pack(side="right")
        ttk.Button(foot, text="Διαγραφή επιλεγμένης απουσίας", command=delete_sel).pack(side="left")
        self.wait_window(win)
        if state["regen"]:
            self._regenerate_for_absence(*state["regen"])
        elif state["ok"]:
            self.refresh_all()
            self.notify("Οι απουσίες ενημερώθηκαν. (Ctrl+Z για αναίρεση)")

    def _regenerate_for_absence(self, scope: str, d_from: date, d_to: date, tid: int = 0, tname: str = "", abs_a=None, abs_b=None) -> None:
        """Ανακατανομή λόγω απουσίας: χρησιμοποιεί την κανονική παραγωγή (backup, σύνοψη, επαναφορά, αναίρεση)."""
        def _key():
            return sorted((r["d"], r["interval_name"], r["area_name"], r["teacher_id"]) for r in
                          self.db.conn.execute("SELECT d,interval_name,area_name,teacher_id FROM assignments").fetchall())

        def _abs_count():
            if not (tid and abs_a):
                return 0
            return self.db.conn.execute("SELECT COUNT(*) FROM assignments WHERE teacher_id=? AND d BETWEEN ? AND ?",
                                        (tid, abs_a.isoformat(), abs_b.isoformat())).fetchone()[0]
        before_key = _key()
        abs_before = _abs_count()
        keep = (self.regenerate_scope_var.get(), self.regenerate_from_var.get(), self.regenerate_to_var.get())
        try:
            self.regenerate_scope_var.set(scope)
            self.regenerate_from_var.set(fmt_date(d_from))
            self.regenerate_to_var.set(fmt_date(d_to))
            self.generate_schedule()
        finally:
            self.regenerate_scope_var.set(keep[0])
            self.regenerate_from_var.set(keep[1])
            self.regenerate_to_var.set(keep[2])
        self.refresh_all()
        try:
            after_key = _key()
            if after_key == before_key:
                self.notify(("Η απουσία καταχωρήθηκε. " if tname else "") + "Δεν έγιναν αλλαγές στο πρόγραμμα (ακύρωση ή επαναφορά του προηγούμενου).", kind="info", ms=10000)
            else:
                from collections import Counter
                changed = sum((Counter(before_key) - Counter(after_key)).values())
                if tname:
                    moved = max(abs_before - _abs_count(), 0)
                    self.notify(f"Απουσία {tname}: {moved} εφημερίες του/της πέρασαν σε άλλους· συνολικά άλλαξαν {changed} αναθέσεις. (Ctrl+Z για αναίρεση)", ms=14000)
                else:
                    self.notify(f"Ανακατανομή: άλλαξαν {changed} αναθέσεις. (Ctrl+Z για αναίρεση)", ms=12000)
        except Exception:
            LOG.exception("Σύνοψη ανακατανομής απουσίας")


    def _lock_past_assignments(self) -> None:
        today = date.today()
        n = self.db.conn.execute("SELECT COUNT(*) FROM assignments WHERE d<? AND locked=0", (today.isoformat(),)).fetchone()[0]
        if not n:
            self.notify("Δεν υπάρχουν παρελθοντικές εφημερίες χωρίς κλείδωμα.", kind="info")
            return
        if not messagebox.askyesno("Κλείδωμα πραγματοποιημένων",
                                   f"Θα κλειδωθούν {n} εφημερίες που έχουν ήδη περάσει (έως και χθες), ώστε να θεωρούνται πραγματοποιημένες και να μην αλλάξουν σε επόμενη ανακατανομή.\n\nΣυνέχεια;"):
            return
        self.auto_backup("lock-past")
        self.db.conn.execute("UPDATE assignments SET locked=1 WHERE d<?", (today.isoformat(),))
        self.db.conn.commit()
        if hasattr(self, "regenerate_from_var"):
            self.regenerate_from_var.set(fmt_date(today))
        self.refresh_all()
        self.notify(f"Κλειδώθηκαν {n} εφημερίες έως και χθες. (Ctrl+Z για αναίρεση)")

    # ---- ημερολόγιο .ics ----
    @staticmethod
    def _ics_escape(s) -> str:
        return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")

    @staticmethod
    def _ics_fold(line: str) -> str:
        b = line.encode("utf-8")
        parts = []
        while len(b) > 75:
            cut = 75
            while (b[cut] & 0xC0) == 0x80:
                cut -= 1
            parts.append(b[:cut].decode("utf-8"))
            b = b" " + b[cut:]
        parts.append(b.decode("utf-8"))
        return "\r\n".join(parts)

    def _write_ics(self, path, teacher_id: int, only_future: bool = False) -> int:
        c = self.db.conn
        t = c.execute("SELECT name FROM teachers WHERE id=?", (teacher_id,)).fetchone()
        if not t:
            raise ValueError("Άγνωστος/η εκπαιδευτικός")
        sql = self.SCHEDULE_SQL + " WHERE a.teacher_id=?" + (" AND a.d>=?" if only_future else "") + " ORDER BY a.d, a.start_time"
        rows = c.execute(sql, (teacher_id, date.today().isoformat()) if only_future else (teacher_id,)).fetchall()
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Efimeries Portable//EL", "CALSCALE:GREGORIAN",
                 "X-WR-CALNAME:" + self._ics_escape(f"Εφημερίες – {t['name']}")]
        for r in rows:
            d = parse_date(r["d"]).strftime("%Y%m%d")
            s = r["start_time"].replace(":", "") + "00"
            e = r["end_time"].replace(":", "") + "00"
            desc = f"Τρόπος: {r['interval_name']}"
            if r["replaced"] and r["old_teacher"]:
                desc += f" · αντί {r['old_teacher']}"
            lines += ["BEGIN:VEVENT", f"UID:efimeria-{r['id']}-{teacher_id}@efimeries.local", f"DTSTAMP:{stamp}",
                      f"DTSTART:{d}T{s}", f"DTEND:{d}T{e}", "SUMMARY:" + self._ics_escape(f"Εφημερία · {r['display_area']}"),
                      "DESCRIPTION:" + self._ics_escape(desc), "END:VEVENT"]
        lines.append("END:VCALENDAR")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\r\n".join(self._ics_fold(x) for x in lines) + "\r\n")
        return len(rows)

    def export_teacher_ics(self) -> None:
        teachers = self.db.active_teachers()
        if not teachers:
            messagebox.showerror("Εκπαιδευτικοί", "Δεν υπάρχουν ενεργοί εκπαιδευτικοί.")
            return
        win = self._modal("Ημερολόγιο εκπαιδευτικού (.ics)", 460, 230)
        f = ttk.Frame(win, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Αρχείο για Google Calendar, Outlook, Apple Calendar ή κινητό.", foreground="#5F6B76").pack(anchor="w", pady=(0, 10))
        tv = tk.StringVar(value=teachers[0].name)
        ttk.Combobox(f, textvariable=tv, values=[t.name for t in teachers], state="readonly", width=34).pack(anchor="w")
        fut = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Μόνο οι εφημερίες από σήμερα και μετά", variable=fut).pack(anchor="w", pady=10)
        res = {}

        def go():
            t = next(t for t in teachers if t.name == tv.get())
            safe = re.sub(r"[^\w\-]+", "_", t.name, flags=re.UNICODE).strip("_") or "ekpaideftikos"
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            path = filedialog.asksaveasfilename(parent=win, title="Αποθήκευση ημερολογίου", defaultextension=".ics", initialdir=str(EXPORT_DIR),
                                                initialfile=f"Efimeries_{safe}.ics", filetypes=[("Ημερολόγιο", "*.ics")])
            if not path:
                return
            res["n"] = self._write_ics(path, t.id, fut.get())
            res["path"] = path
            win.destroy()
        row = ttk.Frame(f)
        row.pack(fill="x", side="bottom")
        ttk.Button(row, text="Ακύρωση", command=win.destroy).pack(side="right", padx=5)
        ttk.Button(row, text="Αποθήκευση…", style="Accent.TButton", command=go).pack(side="right")
        self.wait_window(win)
        if res:
            self.notify(f"Αποθηκεύτηκαν {res['n']} εφημερίες στο {res['path']}.")

    # ---- αρχική: Σήμερα / Χρειάζεται προσοχή / ισοκατανομή ----
    def _build_home_panels(self, parent) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="both", expand=True, pady=(0, 10))
        self._home_panels_row = row
        for i in range(3):
            row.columnconfigure(i, weight=1, uniform="hp")
        row.rowconfigure(0, weight=1)
        self.home_today_box = ttk.LabelFrame(row, text="Σήμερα", padding=12)
        self.home_today_box.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.home_attn_box = ttk.LabelFrame(row, text="Χρειάζεται προσοχή", padding=12)
        self.home_attn_box.grid(row=0, column=1, sticky="nsew", padx=5)
        fair = ttk.LabelFrame(row, text="Ισοκατανομή (συνολικός χρόνος)", padding=12)
        fair.grid(row=0, column=2, sticky="nsew", padx=(5, 0))
        self.home_fair_canvas = tk.Canvas(fair, height=150, bg="white", highlightthickness=0)
        self.home_fair_canvas.pack(fill="both", expand=True)
        self.home_fair_canvas.bind("<Configure>", lambda _e: self._draw_fairness())

    def _toggle_home_info(self) -> None:
        if self._home_info_open:
            self._home_info.pack_forget()
        else:
            self._home_info.pack(fill="x", side="bottom", before=self._home_panels_row)
        self._home_info_open = not self._home_info_open

    def _clear(self, widget) -> None:
        for ch in widget.winfo_children():
            ch.destroy()

    def _home_today(self) -> None:
        box = self.home_today_box
        self._clear(box)
        today = date.today()
        c = self.db.conn
        rows = c.execute(self.SCHEDULE_SQL + " WHERE a.d=? ORDER BY a.start_time, display_area", (today.isoformat(),)).fetchall()
        ttk.Label(box, text=f"{self._gr_day(today)} {fmt_date(today)}", font=("Segoe UI", 11, "bold"), foreground="#2F6F73").pack(anchor="w")
        try:
            away = [r["teacher"] for r in self._absence_rows() if r["d_from"] <= today.isoformat() <= r["d_to"]]
            if away:
                ttk.Label(box, text="Σε απουσία: " + ", ".join(away), foreground="#8A5A00", wraplength=260, justify="left").pack(anchor="w", pady=(2, 0))
        except Exception:
            pass

        def lines(rs, compact=False):
            for r in rs:
                fr = ttk.Frame(box)
                fr.pack(fill="x", pady=1)
                tm = "" if r["mode"] == "daily" else f"{r['start_time']} · "
                ttk.Label(fr, text=f"{tm}{r['display_area']}", foreground="#5F6B76").pack(side="left")
                ttk.Label(fr, text=r["teacher"] + ("  ✓" if r["locked"] else ""), font=("Segoe UI", 10, "bold")).pack(side="right")
        if rows:
            lines(rows)
        else:
            try:
                school = today in set(self.school_days())
            except Exception:
                school = today.weekday() < 5
            ex = self.db.exception_dates().get(today.isoformat())
            why = "Σαββατοκύριακο" if today.weekday() >= 5 else (f"Ημέρα χωρίς εφημερία: {ex['label']}" if ex is not None and not school else
                                                              "Δεν υπάρχουν εφημερίες σήμερα")
            ttk.Label(box, text=why, foreground="#5F6B76").pack(anchor="w", pady=6)
        nxt = c.execute("SELECT MIN(d) FROM assignments WHERE d>?", (today.isoformat(),)).fetchone()[0]
        if nxt:
            nd = parse_date(nxt)
            ttk.Separator(box).pack(fill="x", pady=8)
            ttk.Label(box, text=f"Επόμενη: {self._gr_day(nd)} {fmt_date(nd)}", font=("Segoe UI", 10, "bold"), foreground="#34495E").pack(anchor="w")
            lines(c.execute(self.SCHEDULE_SQL + " WHERE a.d=? ORDER BY a.start_time, display_area LIMIT 6", (nxt,)).fetchall(), True)
        ttk.Button(box, text="Άνοιγμα προγράμματος", command=lambda: self.go_tab(self.tab_schedule)).pack(anchor="w", pady=(10, 0))

    def _home_attention_items(self) -> list:
        """Λίστα (επίπεδο, κείμενο, ετικέτα κουμπιού, ενέργεια). Επίπεδα: bad, warn, ok."""
        c = self.db.conn
        today = date.today()
        items = []
        total = c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
        if not total:
            return [("bad", "Δεν έχει δημιουργηθεί πρόγραμμα εφημεριών.", "Δημιουργία", lambda: self.go_tab(self.tab_generate))]
        past = c.execute("SELECT COUNT(*) FROM assignments WHERE d<? AND locked=0", (today.isoformat(),)).fetchone()[0]
        if past:
            items.append(("warn", f"{past} εφημερίες έχουν περάσει χωρίς να κλειδωθούν ως πραγματοποιημένες. Κλείδωσέ τες ώστε να μην αλλάξουν σε επόμενη ανακατανομή.",
                          "Κλείδωμα τώρα", self._lock_past_assignments))
        try:
            sdays = self.school_days()
        except Exception:
            sdays = []
        have = {r[0] for r in c.execute("SELECT DISTINCT d FROM assignments")}
        horizon = today + timedelta(days=14)
        gaps = [d for d in sdays if today <= d <= horizon and d.isoformat() not in have]
        if gaps:
            shown = ", ".join(f"{self._gr_day(d)[:3]} {d.strftime('%d/%m')}" for d in gaps[:5]) + ("…" if len(gaps) > 5 else "")
            soon = gaps[0] <= today + timedelta(days=5)
            items.append(("bad" if soon else "warn", f"Εργάσιμες ημέρες χωρίς εφημερία στις επόμενες 2 εβδομάδες: {shown}.", "Δες πρόγραμμα", lambda: self.go_tab(self.tab_schedule)))
        last = c.execute("SELECT MAX(d) FROM assignments").fetchone()[0]
        if sdays and last and parse_date(last) < sdays[-1]:
            items.append(("warn", f"Το πρόγραμμα φτάνει έως {fmt_date(last)}, ενώ η περίοδος λήγει {fmt_date(sdays[-1])}.", "Ανακατανομή", lambda: self.go_tab(self.tab_generate)))
        sums = {r[0]: int(r[1]) for r in c.execute("SELECT teacher_id, COALESCE(SUM(minutes),0) FROM assignments GROUP BY teacher_id")}
        act = self.db.active_teachers()
        if len(act) > 1:
            vals = sorted(((sums.get(t.id, 0), t.name) for t in act))
            spread = vals[-1][0] - vals[0][0]
            if spread > 60:
                items.append(("warn", f"Διαφορά φόρτου {spread}΄ ανάμεσα σε {vals[-1][1]} (περισσότερο) και {vals[0][1]} (λιγότερο).", "Δες κατανομή", lambda: self.go_tab(self.tab_reports)))
        dup = c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM assignments GROUP BY teacher_id,d,start_time,end_time HAVING COUNT(*)>1)").fetchone()[0]
        if dup:
            items.append(("bad", f"{dup} περιπτώσεις εκπαιδευτικού με δύο εφημερίες την ίδια ώρα.", "Δες πρόγραμμα", lambda: self.go_tab(self.tab_schedule)))
        try:
            off = 0
            for r in c.execute("SELECT a.d, t.mon, t.tue, t.wed, t.thu, t.fri FROM assignments a JOIN teachers t ON t.id=a.teacher_id WHERE a.mode='daily' AND a.locked=0 AND a.d>=?", (today.isoformat(),)):
                wd = parse_date(r[0]).weekday()
                if wd < 5 and not r[1 + wd]:
                    off += 1
            if off:
                items.append(("warn", f"{off} μελλοντικές εφημερίες έχουν ανατεθεί εκτός δηλωμένης διαθεσιμότητας (π.χ. λόγω απουσιών ή έλλειψης διαθέσιμων). Έλεγξέ τες.", "Δες πρόγραμμα", lambda: self.go_tab(self.tab_schedule)))
        except Exception:
            LOG.exception("Έλεγχος διαθεσιμότητας αρχικής")
        try:
            today_iso = today.isoformat()
            for ab in self._absence_rows(only_current=True):
                n_left = c.execute("SELECT COUNT(*) FROM assignments WHERE teacher_id=? AND locked=0 AND d BETWEEN ? AND ?",
                                   (ab["teacher_id"], max(ab["d_from"], today_iso), ab["d_to"])).fetchone()[0]
                span = fmt_date(ab["d_from"]) + (" – " + fmt_date(ab["d_to"]) if ab["d_to"] != ab["d_from"] else "")
                if n_left:
                    items.append(("bad", f"{ab['teacher']} απουσιάζει ({span}) αλλά έχει ακόμη {n_left} εφημερίες εκείνες τις ημέρες.", "Κάλυψη", self.absence_dialog))
                elif ab["d_from"] <= today_iso <= ab["d_to"]:
                    items.append(("info", f"{ab['teacher']} απουσιάζει ({span}).", "Απουσίες", self.absence_dialog))
        except Exception:
            LOG.exception("Έλεγχος απουσιών αρχικής")
        if not items:
            items.append(("ok", "Όλα εντάξει: δεν εντοπίστηκε κάτι που να χρειάζεται ενέργεια.", "", None))
        return items

    def _home_attention(self) -> None:
        box = self.home_attn_box
        self._clear(box)
        colors = {"bad": "#B42318", "warn": "#B7791F", "ok": "#2F855A", "info": "#2B6CB0"}
        for level, text, label, cb in self._home_attention_items():
            fr = ttk.Frame(box)
            fr.pack(fill="x", pady=(0, 8))
            tk.Label(fr, text="●" if level != "ok" else "✓", fg=colors[level], bg=self._home_bg(), font=("Segoe UI", 11, "bold")).pack(side="left", anchor="n", padx=(0, 6))
            inner = ttk.Frame(fr)
            inner.pack(side="left", fill="x", expand=True)
            ttk.Label(inner, text=text, wraplength=240, justify="left").pack(anchor="w")
            if cb:
                ttk.Button(inner, text=label, command=cb).pack(anchor="w", pady=(3, 0))

    def _home_bg(self) -> str:
        try:
            return ttk.Style().lookup("TFrame", "background") or self.cget("bg")
        except Exception:
            return self.cget("bg")

    def _draw_fairness(self) -> None:
        cv = self.home_fair_canvas
        cv.delete("all")
        w, h = max(cv.winfo_width(), 200), max(cv.winfo_height(), 100)
        act = self.db.active_teachers()
        sums = {r[0]: int(r[1]) for r in self.db.conn.execute("SELECT teacher_id, COALESCE(SUM(minutes),0) FROM assignments GROUP BY teacher_id")}
        data = sorted(((sums.get(t.id, 0), t.name) for t in act), reverse=True)
        if not data or max(v for v, _ in data) == 0:
            cv.create_text(w / 2, h / 2, text="Δεν υπάρχουν δεδομένα ακόμη.", fill="#7A858C", font=("Segoe UI", 10))
            return
        mean = sum(v for v, _ in data) / len(data)
        top = max(v for v, _ in data)
        left, right, head = 118, 62, 18
        row_h = max(12, min(24, (h - head - 4) // len(data)))
        scale = max(top, mean) * 1.05
        span = max(w - left - right, 40)
        font = ("Segoe UI", 8 if row_h < 18 else 9)
        for i, (v, name) in enumerate(data):
            y = head + i * row_h
            nm = name if len(name) <= 18 else name[:17] + "…"
            cv.create_text(left - 6, y + row_h / 2, text=nm, anchor="e", font=font, fill="#33404B")
            diff = v - mean
            color = "#2B6CB0" if abs(diff) <= 0.1 * mean else ("#C05621" if diff > 0 else "#4A9DB5")
            cv.create_rectangle(left, y + 2, left + span * v / scale, y + row_h - 3, fill=color, outline="")
            cv.create_text(left + span * v / scale + 4, y + row_h / 2, text=self._hm(v), anchor="w", font=font, fill="#33404B")
        x = left + span * mean / scale
        cv.create_line(x, head - 4, x, head + len(data) * row_h, fill="#1F2933", dash=(3, 2), width=1)
        cv.create_text(x, 2, text=f"μέσος όρος {self._hm(mean)}", anchor="n", font=("Segoe UI", 8), fill="#1F2933")

    def _refresh_home_panels(self) -> None:
        try:
            self._home_today()
            self._home_attention()
            self._draw_fairness()
        except Exception:
            LOG.exception("Αποτυχία ανανέωσης αρχικής")

    # ---- έλεγχος αποτελέσματος παραγωγής ----
    def _gen_metrics(self) -> dict:
        c = self.db.conn
        sums = {r[0]: int(r[1]) for r in c.execute("SELECT teacher_id, COALESCE(SUM(minutes),0) FROM assignments GROUP BY teacher_id")}
        vals = [sums.get(t.id, 0) for t in self.db.active_teachers()] or [0]
        keys = {(r[0], r[1], r[2], r[3], r[4]) for r in c.execute("SELECT d,interval_name,start_time,area_name,teacher_id FROM assignments")}
        return {"n": c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], "spread": max(vals) - min(vals), "keys": keys}

    def _keep_or_restore(self, title: str, msg: str, gaps: int = 0) -> bool:
        """Εμφανής σύνοψη με δύο σαφή κουμπιά. True = κράτησε το νέο πρόγραμμα, False = επαναφορά."""
        if getattr(messagebox.askyesno, "__module__", "") != "tkinter.messagebox":
            # (δοκιμές: το askyesno έχει αντικατασταθεί) – ίδια λογική με απλό ναι/όχι
            return messagebox.askyesno(title, msg + "\nΝα κρατηθεί το νέο πρόγραμμα;")
        win = self._modal(title, 640, 560)
        result = {"keep": True}
        outer = ttk.Frame(win, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Το νέο πρόγραμμα είναι έτοιμο", font=("Segoe UI", 14, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(outer, text=("⚠ Υπάρχουν σημεία που χρειάζονται έλεγχο." if gaps else "✔ Δεν βρέθηκαν σημεία προς έλεγχο."),
                  foreground=("#B3261E" if gaps else "#1B7F3B"), font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4, 8))
        foot = ttk.Frame(outer)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        txt = tk.Text(outer, wrap="word", height=14, relief="flat", bg="#F5F7F9", padx=10, pady=8, font=("Segoe UI", 10))
        txt.insert("1.0", msg)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)
        ttk.Label(foot, text="«Επαναφορά» φέρνει πίσω το προηγούμενο πρόγραμμα ακριβώς όπως ήταν.", foreground="#5F6B76").pack(anchor="w", pady=(0, 8))
        def keep():
            result["keep"] = True; win.destroy()
        def restore():
            result["keep"] = False; win.destroy()
        ttk.Button(foot, text="Κράτησε το νέο πρόγραμμα", style="Accent.TButton", command=keep).pack(side="right")
        ttk.Button(foot, text="Επαναφορά προηγούμενου", style="Danger.TButton", command=restore).pack(side="right", padx=8)
        win.protocol("WM_DELETE_WINDOW", keep)
        win.bind("<Escape>", lambda _e: keep())
        self._keep_restore_win = win
        self.wait_window(win)
        return result["keep"]

    def _review_generation(self, pre: dict, snap: dict) -> None:
        """Μετά την παραγωγή: δείχνει σύνοψη πριν/μετά και επιτρέπει ακύρωση με πλήρη επαναφορά."""
        if self._undo_snapshot() == snap:
            return  # δεν άλλαξε τίποτα (π.χ. έλλειψη δεδομένων)
        post = self._gen_metrics()
        gaps = 0
        try:
            txt = self.generate_text.get("1.0", "end")
            if "Προσοχή — χρειάζεται έλεγχος:" in txt:
                gaps = sum(1 for ln in txt.split("Προσοχή — χρειάζεται έλεγχος:", 1)[1].splitlines() if ln.startswith("- "))
        except Exception:
            pass
        new = len(post["keys"] - pre["keys"])
        msg = (f"Νέο πρόγραμμα: {post['n']} αναθέσεις (πριν: {pre['n']}).\n"
               f"Αναθέσεις που διαφέρουν από πριν: {new}.\n"
               f"Διαφορά φόρτου μεταξύ πιο και λιγότερο φορτωμένου/ης: {post['spread']}΄ (πριν: {pre['spread']}΄).\n")
        msg += (f"⚠ Σημεία που χρειάζονται έλεγχο: {gaps} (δες τη λίστα στην οθόνη παραγωγής).\n" if gaps else "Δεν υπάρχουν σημεία προς έλεγχο.\n")
        try:
            rows_abs = self._absence_rows(only_current=True)
            if rows_abs:
                days = {r[0]: int(r[1]) for r in self.db.conn.execute("SELECT teacher_id, COUNT(DISTINCT d) FROM assignments GROUP BY teacher_id")}
                act = [t.id for t in self.db.active_teachers()]
                mean = sum(days.get(i, 0) for i in act) / max(1, len(act))
                msg += "\nΑπουσίες και αναπλήρωση (ημέρες εφημερίας στο έτος):\n"
                seen_t = set()
                for ab in rows_abs:
                    if ab["teacher_id"] in seen_t:
                        continue
                    seen_t.add(ab["teacher_id"])
                    n = days.get(ab["teacher_id"], 0)
                    mean_s = f"{mean:.1f}".replace(".", ",")
                    st = "έχει αναπληρωθεί" if n >= mean - 0.5 else "υπολείπεται " + f"{mean - n:.1f}".replace(".", ",") + " ημ. από τον μέσο όρο"
                    msg += f"• {ab['teacher']}: {n} ημέρες (μέσος όρος {mean_s}) — {st}\n"
        except Exception:
            LOG.exception("Σύνοψη απουσιών")
        if self._keep_or_restore("Αποτέλεσμα παραγωγής", msg, gaps):
            return
        self._undo_restore(snap)
        self.refresh_all()
        try:
            self.generate_text.insert("1.0", "Η παραγωγή ακυρώθηκε και επανήλθε το προηγούμενο πρόγραμμα.\n\n")
        except Exception:
            pass
        self.notify("Η παραγωγή ακυρώθηκε. Επανήλθε το προηγούμενο πρόγραμμα.", kind="info")

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

    def check_for_updates(self, manual: bool = False) -> None:
        """Έλεγχος σε παρασκήνιο. Αυτόματος: σιωπηλός σε σφάλμα, το πολύ μία φορά το 24ωρο. Χειροκίνητος: πάντα απάντηση."""
        if getattr(self, "_update_busy", False):
            return
        if not manual:
            if os.environ.get("EFIMERIES_NO_UPDATE_CHECK"):
                return
            st = self._update_state()
            if st.get("auto", True) is False:
                return
            if time.time() - float(st.get("last_check", 0) or 0) < 24 * 3600:
                return
        import threading
        self._update_busy = True
        box: dict = {}

        def work():
            try:
                box["rel"] = self._fetch_latest_release()
            except Exception as exc:
                box["err"] = exc
        threading.Thread(target=work, daemon=True).start()

        def poll():
            if not box:
                self.after(250, poll)
                return
            self._update_busy = False
            try:
                self._on_update_result(box, manual)
            except Exception:
                LOG.exception("Αποτέλεσμα ελέγχου έκδοσης")
        self.after(250, poll)

    def _on_update_result(self, box: dict, manual: bool) -> None:
        if "err" in box:
            LOG.info("Έλεγχος έκδοσης: αποτυχία (%s)", box["err"])
            if manual:
                messagebox.showinfo("Έλεγχος έκδοσης", "Δεν ήταν δυνατός ο έλεγχος. Βεβαιωθείτε ότι υπάρχει σύνδεση στο Internet και δοκιμάστε ξανά αργότερα.")
            return
        rel = box["rel"]
        self._save_update_state(last_check=time.time())
        newer = self._version_tuple(rel["tag"]) > self._version_tuple(APP_VERSION)
        if not newer:
            if manual:
                messagebox.showinfo("Έλεγχος έκδοσης", f"Έχετε την τελευταία έκδοση (v{APP_VERSION}).")
            return
        if not manual and self._update_state().get("skip") == rel["tag"]:
            return
        self._show_update_dialog(rel)

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

    def download_and_install_update(self, asset: dict, tag: str) -> None:
        """Κατεβάζει το Setup, κλείνει την εφαρμογή (με αυτόματο backup) και το τρέχει. Τα δεδομένα δεν αγγίζονται."""
        import threading
        dest = Path(tempfile.gettempdir()) / asset["name"]
        win = self._modal("Λήψη ενημέρωσης", 440, 170)
        ttk.Label(win, text=f"Λήψη της έκδοσης {tag}…", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(18, 6))
        bar = ttk.Progressbar(win, mode="determinate", maximum=100)
        bar.pack(fill="x", padx=18)
        info = ttk.Label(win, text="", foreground="#5F6B76")
        info.pack(anchor="w", padx=18, pady=6)
        box: dict = {"p": (0, 0)}

        def work():
            try:
                self._download_file(asset["url"], dest, lambda d, t: box.__setitem__("p", (d, t)))
                box["ok"] = True
            except Exception as exc:
                box["err"] = exc
        threading.Thread(target=work, daemon=True).start()

        def poll():
            d, t = box["p"]
            if t:
                bar["value"] = 100 * d / t
                info.config(text=f"{d / 1048576:.1f} / {t / 1048576:.1f} MB")
            if "err" in box:
                win.destroy()
                LOG.info("Αποτυχία λήψης ενημέρωσης: %s", box["err"])
                messagebox.showerror("Λήψη ενημέρωσης", "Η λήψη απέτυχε. Δοκιμάστε ξανά αργότερα ή κατεβάστε το Setup από τη σελίδα του αποθετηρίου.")
                return
            if "ok" in box:
                win.destroy()
                try:
                    if asset.get("size") and dest.stat().st_size != int(asset["size"]):
                        raise OSError("το μέγεθος του αρχείου δεν ταιριάζει")
                except Exception as exc:
                    messagebox.showerror("Λήψη ενημέρωσης", f"Το αρχείο που κατέβηκε δεν είναι έγκυρο ({exc}). Δοκιμάστε ξανά.")
                    return
                self._launch_setup(dest)
                return
            self.after(150, poll)
        self.after(150, poll)

    def _launch_setup(self, path: Path) -> None:
        if not messagebox.askyesno("Ενημέρωση", "Η εφαρμογή θα κλείσει για να γίνει η εγκατάσταση της νέας έκδοσης.\nΤα δεδομένα σας δεν επηρεάζονται και θα ανοίξει ξανά μετά. Να συνεχίσω;"):
            return
        try:
            import subprocess
            subprocess.Popen([str(path), "/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS", "/LAUNCH=1"], close_fds=True)
        except Exception as exc:
            messagebox.showerror("Ενημέρωση", f"Δεν ήταν δυνατή η εκκίνηση της εγκατάστασης:\n{exc}")
            return
        self._on_close()

    def _show_update_dialog(self, rel: dict) -> None:
        win = self._modal("Νέα έκδοση διαθέσιμη", 560, 440)
        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Διαθέσιμη νέα έκδοση: {rel['tag']}", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(body, text=f"Έχετε την v{APP_VERSION}. Τα δεδομένα σας δεν επηρεάζονται· η νέα έκδοση χρησιμοποιεί τον ίδιο φάκελο Shared.", wraplength=520, foreground="#5F6B76").pack(anchor="w", pady=(4, 8))
        foot = ttk.Frame(body)
        foot.pack(side="bottom", fill="x", pady=(10, 0))
        txt = tk.Text(body, wrap="word", height=10, relief="flat", bg="#F5F7F9", padx=8, pady=6, font=("Segoe UI", 10))
        txt.insert("1.0", (rel.get("body") or "Δεν υπάρχουν σημειώσεις έκδοσης.")[:3000])
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)
        def open_page():
            webbrowser.open(rel["url"])
            win.destroy()
        asset = self._setup_asset(rel)
        if asset:
            def install_now():
                win.destroy()
                self.download_and_install_update(asset, rel["tag"])
            ttk.Button(foot, text="Λήψη και εγκατάσταση τώρα", style="Accent.TButton", command=install_now).pack(side="right", padx=(0, 8))
        def skip():
            self._save_update_state(skip=rel["tag"])
            win.destroy()
        ttk.Button(foot, text="Άνοιγμα σελίδας λήψης", style="Accent.TButton", command=open_page).pack(side="right")
        ttk.Button(foot, text="Αργότερα", command=win.destroy).pack(side="right", padx=8)
        ttk.Button(foot, text="Όχι για αυτή την έκδοση", command=skip).pack(side="left")
        self._update_win = win

    def open_user_guide(self) -> None:
        """Open the bundled illustrated PDF user guide."""
        guide = resource_path("Odigos_Chrisis_Efimeries.pdf")
        if not guide.exists():
            messagebox.showerror(
                "Οδηγός χρήσης",
                "Δεν βρέθηκε το ενσωματωμένο αρχείο του οδηγού χρήσης.\n"
                "Βεβαιώσου ότι χρησιμοποιείς ολόκληρο τον φάκελο της εφαρμογής."
            )
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(guide))
            else:
                webbrowser.open(guide.resolve().as_uri())
            self.set_status("Άνοιγμα οδηγού χρήσης")
        except Exception as exc:
            messagebox.showerror(
                "Οδηγός χρήσης",
                f"Δεν ήταν δυνατό να ανοίξει ο οδηγός χρήσης.\n\n{exc}"
            )

    def open_about_contact(self) -> None:
        """Show application information, support contact and guide shortcut."""
        win = tk.Toplevel(self)
        win.title("Πληροφορίες / Επικοινωνία")
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=22)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body, text="Πρόγραμμα Δημιουργίας Εφημεριών",
            font=("Segoe UI", 15, "bold"), foreground="#34495E"
        ).pack(anchor="w")
        ttk.Label(
            body, text=f"Έκδοση v{APP_VERSION} · {'64-bit' if APP_ARCH == 'x64' else '32-bit'}" + (" · εγκατεστημένη" if (app_dir() / "installed.flag").exists() else " · portable"), foreground="#5F6B76"
        ).pack(anchor="w", pady=(2, 8))
        loc = ttk.Frame(body)
        loc.pack(fill="x", pady=(0, 10))
        ttk.Label(loc, text="Φάκελος δεδομένων:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(loc, text=str(STORAGE_ROOT), wraplength=460, foreground="#34495E").pack(anchor="w")
        _shared = str(STORAGE_ROOT).startswith("\\\\")
        ttk.Label(loc, text=("Κοινόχρηστος φάκελος δικτύου: ανοίγετε την εφαρμογή μόνο από έναν υπολογιστή τη φορά." if _shared else "Για χρήση από δύο υπολογιστές: Ρυθμίσεις → Τοποθεσία δεδομένων."),
                  foreground="#7A858C", font=("Segoe UI", 8), wraplength=460).pack(anchor="w")
        ttk.Button(loc, text="Άνοιγμα φακέλου δεδομένων", command=lambda: self.open_path(STORAGE_ROOT)).pack(anchor="w", pady=(4, 0))

        ttk.Label(
            body,
            text="Για πληροφορίες, αναφορά προβλημάτων ή προτάσεις βελτίωσης:",
            wraplength=460
        ).pack(anchor="w")
        ttk.Label(
            body, text="Κοκολόγος Σταμάτης, ΠΕ70", font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(10, 2))

        email = tk.Label(
            body, text="Αναφορά προβλήματος / πρότασης (GitHub)", fg="#1565C0", cursor="hand2",
            font=("Segoe UI", 10, "underline")
        )
        email.pack(anchor="w")
        email.bind("<Button-1>", lambda _e: webbrowser.open(f"https://github.com/{UPDATE_REPO}/issues"))

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=16)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(
            buttons, text="Άνοιγμα Οδηγού Χρήσης", style="Primary.TButton",
            command=self.open_user_guide
        ).pack(side="left")
        ttk.Button(buttons, text="Κλείσιμο", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Έλεγχος για νέα έκδοση", command=lambda: self.check_for_updates(manual=True)).pack(side="left", padx=8)
        auto_var = tk.BooleanVar(value=self._update_state().get("auto", True) is not False)
        ttk.Checkbutton(body, text="Αυτόματος έλεγχος για νέα έκδοση (μία φορά το 24ωρο, στην εκκίνηση)", variable=auto_var,
                        command=lambda: self._save_update_state(auto=bool(auto_var.get()))).pack(anchor="w", pady=(10, 0))
        ttk.Label(body, text="Ο έλεγχος επικοινωνεί μόνο με το GitHub και δεν στέλνει δεδομένα σχολείου.", foreground="#7A858C", font=("Segoe UI", 8)).pack(anchor="w")

        win.update_idletasks()
        w = max(520, win.winfo_reqwidth())
        h = max(260, win.winfo_reqheight())
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.grab_set()
        win.focus_force()

    def _build_settings(self):
        outer = ttk.Frame(self.tab_settings, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Ρυθμίσεις εφαρμογής", font=("Segoe UI", 18, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(outer, text="Διαχειρίσου το ενεργό σχολικό έτος και τις βασικές ημερομηνίες λειτουργίας.", foreground="#5F6B76").pack(anchor="w", pady=(2, 12))

        sy_box = ttk.LabelFrame(outer, text="Σχολικό έτος", padding=12)
        sy_box.pack(fill="x", pady=(0, 12))

        row1 = ttk.Frame(sy_box)
        row1.pack(fill="x")
        ttk.Label(row1, text="Ενεργό σχολικό έτος").pack(side="left", padx=(0, 8))
        self.settings_school_year_cb = ttk.Combobox(row1, values=self.available_school_years(), width=16, state="readonly")
        self.settings_school_year_cb.set(self.db.get_setting("school_year", self.active_school_year))
        self.settings_school_year_cb.pack(side="left", padx=(0, 8))
        ttk.Button(row1, text="Άνοιγμα", style="Primary.TButton", command=self.open_selected_school_year).pack(side="left", padx=(0, 16))
        ttk.Button(row1, text="+ Νέο σχολικό έτος", style="Accent.TButton", command=self.new_school_year_dialog).pack(side="left", padx=4)
        ttk.Button(row1, text="Διαγραφή σχολικού έτους", style="Danger.TButton", command=self.delete_school_year).pack(side="left", padx=4)

        sep = ttk.Separator(sy_box, orient="horizontal")
        sep.pack(fill="x", pady=12)

        row2 = ttk.Frame(sy_box)
        row2.pack(fill="x")
        ttk.Label(row2, text="Έναρξη μαθημάτων").pack(side="left", padx=(0, 6))
        self.settings_start_e = ttk.Entry(row2, width=16)
        self.settings_start_e.insert(0, ui_date(self.db.get_setting("start_date")))
        self.settings_start_e.pack(side="left", padx=(0, 4))
        self._cal_button(row2, self.settings_start_e).pack(side="left", padx=(0, 14))
        ttk.Label(row2, text="Λήξη μαθημάτων").pack(side="left", padx=(0, 6))
        self.settings_end_e = ttk.Entry(row2, width=16)
        self.settings_end_e.insert(0, ui_date(self.db.get_setting("end_date")))
        self.settings_end_e.pack(side="left", padx=(0, 4))
        self._cal_button(row2, self.settings_end_e).pack(side="left", padx=(0, 14))
        ttk.Button(row2, text="Αποθήκευση ημερομηνιών", command=self.save_settings_school_year).pack(side="left")

        ttk.Label(
            sy_box,
            text="Κάθε σχολικό έτος αποθηκεύεται σε ξεχωριστό αρχείο δεδομένων. Αλλαγές σε ένα έτος δεν επηρεάζουν τα υπόλοιπα.",
            wraplength=980, foreground="#555555"
        ).pack(anchor="w", pady=(10, 0))

        identity = ttk.LabelFrame(outer, text="Σχολείο και εκτυπώσεις", padding=12)
        identity.pack(fill="x", pady=(0, 12))
        i1 = ttk.Frame(identity); i1.pack(fill="x", pady=3)
        ttk.Label(i1, text="Ονομασία σχολείου", width=20).pack(side="left")
        self.school_name_e = ttk.Entry(i1)
        self.school_name_e.insert(0, self.db.get_setting("school_name", ""))
        self.school_name_e.pack(side="left", fill="x", expand=True, padx=(0, 8))
        i2 = ttk.Frame(identity); i2.pack(fill="x", pady=3)
        ttk.Label(i2, text="Τηλέφωνο", width=20).pack(side="left")
        self.school_phone_e = ttk.Entry(i2, width=24); self.school_phone_e.insert(0, self.db.get_setting("school_phone", "")); self.school_phone_e.pack(side="left", padx=(0, 14))
        ttk.Label(i2, text="Email").pack(side="left", padx=(0, 6))
        self.school_email_e = ttk.Entry(i2, width=36); self.school_email_e.insert(0, self.db.get_setting("school_email", "")); self.school_email_e.pack(side="left", padx=(0, 14))
        ttk.Label(i2, text="Μέγεθος εκτύπωσης").pack(side="left", padx=(0, 6))
        self.print_size_cb = ttk.Combobox(i2, state="readonly", width=13, values=["Συμπαγές", "Κανονικό", "Μεγάλο"])
        size_map = {"compact":"Συμπαγές", "normal":"Κανονικό", "large":"Μεγάλο"}
        self.print_size_cb.set(size_map.get(self.db.get_setting("print_size", "normal"), "Κανονικό"))
        self.print_size_cb.pack(side="left")
        ttk.Button(identity, text="Αποθήκευση στοιχείων σχολείου / εκτύπωσης", style="Primary.TButton", command=self.save_school_identity).pack(anchor="w", pady=(7, 0))

        guide = ttk.LabelFrame(outer, text="Όταν δημιουργείς νέο σχολικό έτος", padding=12)
        guide.pack(fill="x", pady=(0, 12))
        ttk.Label(guide, text="Μπορείς να αντιγράψεις εκπαιδευτικούς, τομείς, χρονικές ζώνες και στοιχεία σχολείου από το τρέχον έτος. Το πρόγραμμα, οι πραγματοποιημένες εφημερίες, οι αντικαταστάσεις και οι ειδικές ημέρες δεν αντιγράφονται.", wraplength=980, foreground="#555555").pack(anchor="w")

        shared = ttk.LabelFrame(outer, text="Τοποθεσία δεδομένων / κοινόχρηστη χρήση", padding=12)
        shared.pack(fill="x", pady=(0, 12))
        ttk.Label(shared, text="Τα x86 και x64 μπορούν να χρησιμοποιούν τον ίδιο φάκελο data / backups / exports. Για ασφάλεια επιτρέπεται μία ενεργή εφαρμογή κάθε φορά.", wraplength=980, foreground="#555555").pack(anchor="w")
        sr = ttk.Frame(shared); sr.pack(fill="x", pady=(8,0))
        self.storage_path_var = tk.StringVar(value=str(STORAGE_ROOT))
        ttk.Entry(sr, textvariable=self.storage_path_var, state="readonly").pack(side="left", fill="x", expand=True, padx=(0,8))
        ttk.Button(sr, text="Επιλογή κοινόχρηστου φακέλου…", style="Primary.TButton", command=self.change_storage_root).pack(side="left", padx=3)
        ttk.Button(sr, text="Χρήση τοπικού φακέλου", command=self.reset_storage_root_local).pack(side="left", padx=3)
        diag = ttk.Frame(shared); diag.pack(fill="x", pady=(7,0))
        self.storage_db_var = tk.StringVar(value=str(year_db_path(self.active_school_year)))
        ttk.Label(diag, text="Ενεργή βάση:", foreground="#5F6B76").pack(side="left")
        ttk.Label(diag, textvariable=self.storage_db_var, foreground="#34495E").pack(side="left", padx=(5,10))
        ttk.Button(diag, text="Έλεγχος κοινόχρηστων δεδομένων", command=self.check_shared_storage).pack(side="right")

        storage = ttk.LabelFrame(outer, text="Αποθήκευση / μεταφορά σε νέα έκδοση", padding=12)
        storage.pack(fill="x")
        ttk.Label(storage, text=f"Φάκελος σχολικών ετών: {YEARS_DIR}", foreground="#5F6B76").pack(anchor="w")
        sb = ttk.Frame(storage); sb.pack(fill="x", pady=(8,0))
        ttk.Button(sb, text="Πλήρες Backup σχολικού έτους", style="Accent.TButton", command=self.export_full_backup).pack(side="left", padx=(0,5))
        ttk.Button(sb, text="Επαναφορά / εισαγωγή Backup", command=self.import_full_backup).pack(side="left", padx=3)
        ttk.Button(sb, text="Άνοιγμα φακέλου δεδομένων", command=lambda: self.open_path(YEARS_DIR)).pack(side="left", padx=12)
        ttk.Label(storage, text="Το πλήρες backup περιλαμβάνει εκπαιδευτικούς, διαθεσιμότητες, ειδικές ημέρες, τομείς, χρονικές ζώνες, πρόγραμμα, πραγματοποιημένες εφημερίες, αντικαταστάσεις και ρυθμίσεις.", wraplength=980, foreground="#555555").pack(anchor="w", pady=(8,0))

    def _build_teachers(self):
        outer = ttk.Frame(self.tab_teachers, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Εκπαιδευτικοί", font=("Segoe UI", 18, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(outer, text="Όρισε συμμετοχή, διαθεσιμότητα, κύρια ημέρα και εναλλακτικές ημέρες με σειρά προτεραιότητας. Στις βάρδιες μπορείς προαιρετικά να περιορίσεις και τους τομείς κάθε εκπαιδευτικού.", foreground="#5F6B76", wraplength=1050).pack(anchor="w", pady=(2, 10))

        primary = ttk.Frame(outer)
        primary.pack(fill="x", pady=(0, 6))
        ttk.Button(primary, text="+ Νέος εκπαιδευτικός", style="Accent.TButton", command=self.add_teacher).pack(side="left", padx=(0, 6))
        ttk.Button(primary, text="Επεξεργασία", command=self.edit_teacher).pack(side="left", padx=3)
        ttk.Button(primary, text="Διαθεσιμότητα ανά διάλειμμα", command=self.edit_teacher_interval_availability).pack(side="left", padx=3)
        ttk.Button(primary, text="Διαθεσιμότητα ανά βάρδια", command=self.edit_teacher_shift_availability).pack(side="left", padx=3)
        ttk.Button(primary, text="Τομείς βαρδιών", command=self.edit_teacher_shift_areas).pack(side="left", padx=3)
        ttk.Button(primary, text="Διαγραφή", style="Danger.TButton", command=self.delete_teacher).pack(side="left", padx=3)

        secondary = ttk.LabelFrame(outer, text="Εισαγωγή / εξαγωγή", padding=(8, 6))
        secondary.pack(fill="x", pady=(0, 8))
        ttk.Button(secondary, text="Εισαγωγή ωρολογίου (aSc XML / Excel)…", style="Primary.TButton", command=self.import_timetable).pack(side="left", padx=3)
        ttk.Button(secondary, text="Πρότυπο xlsx ωρολογίου", command=self.export_timetable_template).pack(side="left", padx=3)
        ttk.Button(secondary, text="Εισαγωγή εκπαιδευτικών από CSV", command=self.import_teachers_csv).pack(side="left", padx=3)
        ttk.Button(secondary, text="Εξαγωγή εκπαιδευτικών σε CSV", command=self.export_teachers_csv).pack(side="left", padx=3)

        cols = ("name", "participates", "availability", "primary", "alternate", "extra_cycle", "break_avail", "notes")
        self.teachers_tree = ttk.Treeview(outer, columns=cols, show="headings")
        headings = ["Ονοματεπώνυμο", "Εφημερίες", "Διαθέσιμες ημέρες", "Κύρια", "Εναλλακτικές", "Πρόσθετος κύκλος", "Ανά διάλειμμα", "Παρατηρήσεις"]
        widths = [225, 80, 145, 95, 210, 120, 120, 220]
        for c, h, w in zip(cols, headings, widths):
            self.teachers_tree.heading(c, text=h)
            self.teachers_tree.column(c, width=w, anchor="center" if c not in ("name", "availability", "notes") else "w")
        self.teachers_tree.pack(fill="both", expand=True)
        self.teachers_tree.bind("<Double-1>", lambda _e: self.edit_teacher())


    def _build_areas(self):
        outer = ttk.Frame(self.tab_areas, padding=10)
        outer.pack(fill="both", expand=True)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Προσθήκη", command=self.add_area).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Επεξεργασία", command=self.edit_area).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Διαγραφή", command=self.delete_area).pack(side="left", padx=3)
        cols = ("name", "description", "active", "sort_order")
        self.areas_tree = ttk.Treeview(outer, columns=cols, show="headings")
        for c, h, w in zip(cols, ["Τομέας", "Περιγραφή", "Ενεργός", "Σειρά"], [180, 650, 80, 70]):
            self.areas_tree.heading(c, text=h)
            self.areas_tree.column(c, width=w, anchor="center" if c in ("active", "sort_order") else "w")
        self.areas_tree.pack(fill="both", expand=True)

    def _build_calendar(self):
        outer = ttk.Frame(self.tab_calendar, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Ειδικές ημέρες / διαστήματα", font=("Segoe UI", 17, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(outer, text="Καταχώρησε μία ημέρα ή ολόκληρο διάστημα (π.χ. διακοπές Χριστουγέννων/Πάσχα). Ο τύπος «Χωρίς εφημερία» αφαιρεί όλες τις ημέρες του διαστήματος από την παραγωγή. Σε γιορτές/εκδρομές μπορείς να ορίσεις ειδικές χρονικές ζώνες.", foreground="#5F6B76", wraplength=1000).pack(anchor="w", pady=(2, 10))
        ex = ttk.LabelFrame(outer, text="Ημερολόγιο εξαιρέσεων και ειδικής εφημερίας", padding=10)
        ex.pack(fill="both", expand=True)
        toolbar = ttk.Frame(ex)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="+ Νέα ημέρα / διάστημα", style="Accent.TButton", command=self.add_exception).pack(side="left", padx=(0, 5))
        ttk.Button(toolbar, text="Επεξεργασία", command=self.edit_exception).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Διαγραφή", style="Danger.TButton", command=self.delete_exception).pack(side="left", padx=3)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Εξαγωγή ειδικών ημερών CSV", command=self.export_exceptions_csv).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Εισαγωγή ειδικών ημερών CSV", command=self.import_exceptions_csv).pack(side="left", padx=3)
        cols = ("d", "end_d", "label", "type")
        self.exceptions_tree = ttk.Treeview(ex, columns=cols, show="headings")
        for c, h, w in zip(cols, ["Από", "Έως", "Περιγραφή", "Τύπος"], [115, 115, 500, 220]):
            self.exceptions_tree.heading(c, text=h)
            self.exceptions_tree.column(c, width=w, anchor="center" if c in ("d", "end_d") else "w")
        self.exceptions_tree.pack(fill="both", expand=True)
        self.exceptions_tree.bind("<Double-1>", lambda _e: self.edit_exception())


    def _build_intervals(self):
        outer = ttk.Frame(self.tab_intervals, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Τα διαλείμματα χρησιμοποιούνται κυρίως στη λειτουργία «ανά διάλειμμα». Στην ημερήσια λειτουργία χρησιμοποιούνται για υπολογισμό συνολικού χρόνου.", wraplength=1000).pack(anchor="w", pady=(0, 10))
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Προσθήκη", command=self.add_interval).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Επεξεργασία", command=self.edit_interval).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Διαγραφή", command=self.delete_interval).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Προεπιλογή διαλειμμάτων Δημοτικού", command=self.reset_default_intervals).pack(side="left", padx=12)
        cols = ("name", "start_time", "end_time", "minutes", "active", "sort_order")
        self.intervals_tree = ttk.Treeview(outer, columns=cols, show="headings")
        for c, h, w in zip(cols, ["Όνομα", "Από", "Έως", "Λεπτά", "Ενεργό", "Σειρά"], [200, 90, 90, 80, 80, 70]):
            self.intervals_tree.heading(c, text=h)
            self.intervals_tree.column(c, width=w, anchor="center")
        self.intervals_tree.pack(fill="both", expand=True)

    def _build_shifts(self):
        outer = ttk.Frame(self.tab_shifts, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Βάρδιες / ζώνες", font=("Segoe UI", 17, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(outer, text="Κάθε βάρδια χτίζεται από τις χρονικές ζώνες/διαλείμματα που επιλέγεις. Μπορείς να ορίσεις γενική σύνθεση για όλη την εβδομάδα και διαφορετική σύνθεση σε συγκεκριμένη ημέρα.", wraplength=1050, foreground="#5F6B76").pack(anchor="w", pady=(2,10))
        tb=ttk.Frame(outer); tb.pack(fill="x", pady=(0,8))
        ttk.Button(tb,text="+ Νέα βάρδια",style="Accent.TButton",command=self.add_shift).pack(side="left",padx=(0,5))
        ttk.Button(tb,text="Επεξεργασία",command=self.edit_shift).pack(side="left",padx=3)
        ttk.Button(tb,text="Διαγραφή",style="Danger.TButton",command=self.delete_shift).pack(side="left",padx=3)
        cols=("name","intervals","areas","reserve","active")
        self.shifts_tree=ttk.Treeview(outer,columns=cols,show="headings")
        for c,h,w in zip(cols,["Βάρδια","Γενικά διαλείμματα","Τομείς","Αναπληρωματικός","Ενεργή"],[180,430,280,140,80]):
            self.shifts_tree.heading(c,text=h); self.shifts_tree.column(c,width=w,anchor="center" if c in ("reserve","active") else "w")
        self.shifts_tree.pack(fill="both",expand=True)
        self.shifts_tree.bind("<Double-1>",lambda _e:self.edit_shift())

    def refresh_shifts(self):
        self.shifts_tree.delete(*self.shifts_tree.get_children())
        for sh in self.db.shifts(False):
            ints=self.db.shift_intervals(sh["id"],0)
            # Για τη λίστα δείχνουμε τις γενικές (-1), όχι τυχόν override Δευτέρας.
            general=self.db.conn.execute("""SELECT di.name FROM duty_intervals di JOIN shift_interval_map m ON m.interval_id=di.id WHERE m.shift_id=? AND m.weekday=-1 ORDER BY di.sort_order""",(sh["id"],)).fetchall()
            ars=self.db.shift_areas(sh["id"])
            self.shifts_tree.insert("", "end", iid=str(sh["id"]), values=(sh["name"],", ".join(r["name"] for r in general) or "—",", ".join(r["name"] for r in ars) or "—","Ναι" if sh["has_reserve"] else "Όχι","Ναι" if sh["active"] else "Όχι"))

    def shift_dialog(self, title: str, row=None):
        win=tk.Toplevel(self); win.title(title); win.transient(self); win.grab_set(); win.geometry("860x720")
        f=ttk.Frame(win,padding=14); f.pack(fill="both",expand=True)
        top=ttk.Frame(f); top.pack(fill="x")
        ttk.Label(top,text="Όνομα βάρδιας",width=18).pack(side="left")
        name=ttk.Entry(top); name.pack(side="left",fill="x",expand=True); name.insert(0,row["name"] if row else "")
        active=tk.IntVar(value=row["active"] if row else 1); reserve=tk.IntVar(value=row["has_reserve"] if row else 1); order=tk.IntVar(value=row["sort_order"] if row else 0)
        opts=ttk.Frame(f); opts.pack(fill="x",pady=8)
        ttk.Checkbutton(opts,text="Ενεργή",variable=active).pack(side="left")
        ttk.Checkbutton(opts,text="Ορισμός αναπληρωματικού",variable=reserve).pack(side="left",padx=18)
        ttk.Label(opts,text="Σειρά").pack(side="left"); ttk.Spinbox(opts,from_=0,to=99,textvariable=order,width=6).pack(side="left",padx=5)
        intervals=self.db.intervals(False); areas=self.db.areas(False)
        existing_general=set(); existing_overrides={i:set() for i in range(5)}; existing_areas=set()
        if row:
            existing_general={r[0] for r in self.db.conn.execute("SELECT interval_id FROM shift_interval_map WHERE shift_id=? AND weekday=-1",(row["id"],)).fetchall()}
            for wd in range(5): existing_overrides[wd]={r[0] for r in self.db.conn.execute("SELECT interval_id FROM shift_interval_map WHERE shift_id=? AND weekday=?",(row["id"],wd)).fetchall()}
            existing_areas={r[0] for r in self.db.conn.execute("SELECT area_id FROM shift_area_map WHERE shift_id=?",(row["id"],)).fetchall()}
        nb=ttk.Notebook(f); nb.pack(fill="both",expand=True,pady=(4,8))
        tab_general=ttk.Frame(nb,padding=10); nb.add(tab_general,text="Γενικά για όλες τις ημέρες")
        ttk.Label(tab_general,text="Διαλείμματα/χρονικές ζώνες που ανήκουν στη βάρδια:",font=("Segoe UI",10,"bold")).pack(anchor="w")
        gen_vars={}
        for it in intervals:
            v=tk.IntVar(value=1 if it["id"] in existing_general else 0); gen_vars[it["id"]]=v
            ttk.Checkbutton(tab_general,text=f"{it['name']}  ({it['start_time']}–{it['end_time']})",variable=v).pack(anchor="w",pady=2)
        ttk.Label(tab_general,text="Τομείς που καλύπτει η βάρδια:",font=("Segoe UI",10,"bold")).pack(anchor="w",pady=(12,2))
        area_vars={}
        for ar in areas:
            v=tk.IntVar(value=1 if (ar["id"] in existing_areas or not row) else 0); area_vars[ar["id"]]=v
            ttk.Checkbutton(tab_general,text=ar["name"],variable=v).pack(anchor="w",pady=2)
        override_vars={}
        for wd in range(5):
            tab=ttk.Frame(nb,padding=10); nb.add(tab,text=DAYS_GR[wd])
            ttk.Label(tab,text="Άφησε όλα αποεπιλεγμένα για να ισχύει η γενική σύνθεση. Αν επιλέξεις έστω ένα, η ημέρα θα χρησιμοποιεί μόνο αυτά.",wraplength=760,foreground="#5F6B76").pack(anchor="w",pady=(0,8))
            override_vars[wd]={}
            for it in intervals:
                v=tk.IntVar(value=1 if it["id"] in existing_overrides[wd] else 0); override_vars[wd][it["id"]]=v
                ttk.Checkbutton(tab,text=f"{it['name']}  ({it['start_time']}–{it['end_time']})",variable=v).pack(anchor="w",pady=2)
        result={}
        def ok():
            if not name.get().strip(): messagebox.showerror("Βάρδια","Δώσε όνομα βάρδιας.",parent=win); return
            if not any(v.get() for v in gen_vars.values()): messagebox.showerror("Βάρδια","Επίλεξε τουλάχιστον μία γενική χρονική ζώνη.",parent=win); return
            if not any(v.get() for v in area_vars.values()): messagebox.showerror("Βάρδια","Επίλεξε τουλάχιστον έναν τομέα.",parent=win); return
            result.update(name=name.get().strip(),active=active.get(),has_reserve=reserve.get(),sort_order=order.get(),general=[iid for iid,v in gen_vars.items() if v.get()],areas=[aid for aid,v in area_vars.items() if v.get()],overrides={wd:[iid for iid,v in mp.items() if v.get()] for wd,mp in override_vars.items()})
            win.destroy()
        b=ttk.Frame(f); b.pack(fill="x"); ttk.Button(b,text="Ακύρωση",command=win.destroy).pack(side="right",padx=5); ttk.Button(b,text="Αποθήκευση",style="Accent.TButton",command=ok).pack(side="right")
        self.wait_window(win); return result or None

    def add_shift(self):
        data=self.shift_dialog("Νέα βάρδια")
        if not data:return
        try:
            cur=self.db.conn.execute("INSERT INTO shifts(name,active,sort_order,has_reserve) VALUES(?,?,?,?)",(data["name"],data["active"],data["sort_order"],data["has_reserve"])); sid=cur.lastrowid
            self._save_shift_maps(sid,data); self.db.conn.commit(); self.refresh_all()
        except Exception as e: self.db.conn.rollback(); messagebox.showerror("Βάρδια",str(e))

    def _save_shift_maps(self,sid:int,data:dict):
        self.db.conn.execute("DELETE FROM shift_interval_map WHERE shift_id=?",(sid,)); self.db.conn.execute("DELETE FROM shift_area_map WHERE shift_id=?",(sid,))
        self.db.conn.executemany("INSERT INTO shift_interval_map(shift_id,weekday,interval_id) VALUES(?, -1, ?)",[(sid,i) for i in data["general"]])
        for wd,ids in data["overrides"].items():
            self.db.conn.executemany("INSERT INTO shift_interval_map(shift_id,weekday,interval_id) VALUES(?,?,?)",[(sid,wd,i) for i in ids])
        self.db.conn.executemany("INSERT INTO shift_area_map(shift_id,area_id) VALUES(?,?)",[(sid,a) for a in data["areas"]])

    def edit_shift(self):
        sid=self.selected_id(self.shifts_tree)
        if sid is None:return
        row=self.db.conn.execute("SELECT * FROM shifts WHERE id=?",(sid,)).fetchone(); data=self.shift_dialog("Επεξεργασία βάρδιας",row)
        if not data:return
        try:
            self.db.conn.execute("UPDATE shifts SET name=?,active=?,sort_order=?,has_reserve=? WHERE id=?",(data["name"],data["active"],data["sort_order"],data["has_reserve"],sid)); self._save_shift_maps(sid,data); self.db.conn.commit(); self.refresh_all()
        except Exception as e:self.db.conn.rollback();messagebox.showerror("Βάρδια",str(e))

    def delete_shift(self):
        sid=self.selected_id(self.shifts_tree)
        if sid is None:return
        if not messagebox.askyesno("Διαγραφή βάρδιας","Να διαγραφεί η βάρδια και οι ρυθμίσεις της; Το ήδη παραχθέν πρόγραμμα δεν διαγράφεται."):return
        try:self.db.conn.execute("DELETE FROM shifts WHERE id=?",(sid,));self.db.conn.commit();self.refresh_all()
        except Exception as e:self.db.conn.rollback();messagebox.showerror("Βάρδια",str(e))

    def edit_teacher_shift_areas(self):
        tid=self.selected_id(self.teachers_tree)
        if tid is None:
            messagebox.showwarning("Επιλογή", "Επίλεξε πρώτα εκπαιδευτικό.")
            return
        teacher=self.db.conn.execute("SELECT * FROM teachers WHERE id=?",(tid,)).fetchone()
        areas=self.db.areas(True)
        if not areas:
            messagebox.showwarning("Τομείς", "Δεν υπάρχουν ενεργοί τομείς.")
            return
        current=self.db.teacher_shift_allowed_areas(tid)
        unrestricted=not current
        win=tk.Toplevel(self); win.title(f"Τομείς βαρδιών — {teacher['name']}"); win.transient(self); win.grab_set(); win.geometry("620x520")
        f=ttk.Frame(win,padding=16); f.pack(fill="both",expand=True)
        ttk.Label(f,text="Επιτρεπόμενοι τομείς σε βάρδιες",font=("Segoe UI",15,"bold")).pack(anchor="w")
        ttk.Label(f,text="Χρησιμοποίησε περιορισμό μόνο όταν ο/η εκπαιδευτικός μπορεί να εφημερεύει σε συγκεκριμένους χώρους/κτίρια. Αν είναι επιλεγμένοι όλοι οι τομείς, θεωρείται χωρίς περιορισμό.",wraplength=570,foreground="#5F6B76").pack(anchor="w",pady=(3,10))
        vars={}
        box=ttk.LabelFrame(f,text="Τομείς",padding=10); box.pack(fill="both",expand=True)
        for ar in areas:
            v=tk.IntVar(value=1 if unrestricted or int(ar['id']) in current else 0); vars[int(ar['id'])]=v
            ttk.Checkbutton(box,text=ar['name'],variable=v).pack(anchor="w",pady=3)
        btns=ttk.Frame(f); btns.pack(fill="x",pady=(10,0))
        def all_on():
            for v in vars.values(): v.set(1)
        def save():
            chosen=[aid for aid,v in vars.items() if v.get()]
            if not chosen:
                messagebox.showwarning("Τομείς", "Επίλεξε τουλάχιστον έναν τομέα.",parent=win); return
            self.db.set_teacher_shift_allowed_areas(tid,chosen,list(vars.keys())); win.destroy(); self.refresh_all()
        ttk.Button(btns,text="Όλοι οι τομείς",command=all_on).pack(side="left")
        ttk.Button(btns,text="Ακύρωση",command=win.destroy).pack(side="right",padx=5)
        ttk.Button(btns,text="Αποθήκευση",style="Accent.TButton",command=save).pack(side="right")
        self.wait_window(win)

    def edit_teacher_shift_availability(self):
        tid=self.selected_id(self.teachers_tree)
        if tid is None: messagebox.showwarning("Επιλογή","Επίλεξε πρώτα εκπαιδευτικό."); return
        teacher=self.db.conn.execute("SELECT * FROM teachers WHERE id=?",(tid,)).fetchone(); shifts=self.db.shifts(True)
        if not shifts: messagebox.showwarning("Βάρδιες","Δεν υπάρχουν ενεργές βάρδιες."); return
        existing=self.db.get_teacher_shift_availability(tid)
        win=tk.Toplevel(self); win.title(f"Διαθεσιμότητα ανά βάρδια — {teacher['name']}"); win.transient(self); win.grab_set(); win.geometry("780x520")
        f=ttk.Frame(win,padding=14); f.pack(fill="both",expand=True)
        ttk.Label(f,text="Ενεργοποίησε τις βάρδιες στις οποίες μπορεί να εφημερεύει ο/η εκπαιδευτικός. Αν δεν έχει οριστεί ειδική τιμή, θεωρείται διαθέσιμος/η όταν είναι διαθέσιμος/η την ημέρα.",wraplength=740).pack(anchor="w",pady=(0,10))
        grid=ttk.Frame(f); grid.pack(fill="both",expand=True)
        ttk.Label(grid,text="Βάρδια",font=("Segoe UI",10,"bold")).grid(row=0,column=0,sticky="w",padx=5,pady=4)
        for wd in range(5): ttk.Label(grid,text=DAYS_GR[wd],font=("Segoe UI",10,"bold")).grid(row=0,column=wd+1,padx=8)
        vars={}
        for r,sh in enumerate(shifts,1):
            ttk.Label(grid,text=sh["name"]).grid(row=r,column=0,sticky="w",padx=5,pady=4)
            for wd in range(5):
                default=existing.get((wd,sh["id"]),1); v=tk.IntVar(value=default); vars[(wd,sh["id"])]=v
                ttk.Checkbutton(grid,variable=v).grid(row=r,column=wd+1,padx=12)
        def save(): self.db.set_teacher_shift_availability(tid,{k:v.get() for k,v in vars.items()}); win.destroy(); self.refresh_all()
        b=ttk.Frame(f); b.pack(fill="x",pady=(10,0)); ttk.Button(b,text="Ακύρωση",command=win.destroy).pack(side="right",padx=5); ttk.Button(b,text="Αποθήκευση",style="Accent.TButton",command=save).pack(side="right")
        self.wait_window(win)

    def _build_generate(self):
        f = ttk.Frame(self.tab_generate, padding=18)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Παραγωγή προγράμματος", font=("Segoe UI", 18, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(f, text="Επίλεξε τρόπο, διάστημα και ενέργεια. Οι ήδη πραγματοποιημένες εφημερίες μπορούν να παραμείνουν κλειδωμένες.", foreground="#5F6B76", wraplength=1000).pack(anchor="w", pady=(2, 12))

        self.mode_var = tk.StringVar(value=self.db.get_setting("mode", "daily"))
        box = ttk.LabelFrame(f, text="1 · Τρόπος εφημερίας", padding=10)
        box.pack(fill="x", pady=(0, 10))
        mrow = ttk.Frame(box)
        mrow.pack(fill="x")
        for _txt, _val in (("Ανά ημέρα", "daily"), ("Ανά διάλειμμα", "breaks"), ("Ανά βάρδια / ζώνη", "shifts")):
            ttk.Radiobutton(mrow, text=_txt, variable=self.mode_var, value=_val).pack(side="left", padx=(0, 26))
        mode_desc = ttk.Label(box, text="", foreground="#5F6B76", wraplength=1000, justify="left")
        mode_desc.pack(anchor="w", pady=(6, 0))
        _MODE_DESC = {
            "daily": "Ένας/μία εκπαιδευτικός κρατά την εφημερία όλης της ημέρας. Χρησιμοποιεί κύρια/εναλλακτική ημέρα και κυκλική πρόσθετη εφημερία.",
            "breaks": "Ανεξάρτητη ανάθεση σε κάθε χρονική ζώνη (διάλειμμα). Χρησιμοποιεί διαθεσιμότητα ανά ημέρα/διάλειμμα και εξισορρόπηση ετήσιου χρόνου.",
            "shifts": "Κατάλληλο για Γυμνάσιο/Λύκειο: οι βάρδιες αποτελούνται από επιλεγμένα διαλείμματα, με τομείς ανά βάρδια, κυκλική συμμετοχή και αναπληρωματικό.",
        }
        self.mode_var.trace_add("write", lambda *_: mode_desc.config(text=_MODE_DESC.get(self.mode_var.get(), "")))
        mode_desc.config(text=_MODE_DESC.get(self.mode_var.get(), ""))

        scope_box = ttk.LabelFrame(f, text="2 · Τι θα γίνει με το υπάρχον πρόγραμμα;", padding=10)
        scope_box.pack(fill="x", pady=(0, 10))
        self.regenerate_scope_var = tk.StringVar(value="continue")
        ttk.Radiobutton(scope_box, text="Συνέχιση / ανακατανομή από ημερομηνία και μετά  (η συνηθισμένη επιλογή)", variable=self.regenerate_scope_var, value="continue").pack(anchor="w", pady=(2, 0))
        ttk.Label(scope_box, text="Κρατά ό,τι είναι κλειδωμένο ή πραγματοποιημένο και ξαναϋπολογίζει όλο το υπόλοιπο έως το τέλος του σχολικού έτους.", foreground="#5F6B76").pack(anchor="w", padx=24)
        ttk.Radiobutton(scope_box, text="Αναδημιουργία συγκεκριμένου διαστήματος", variable=self.regenerate_scope_var, value="range").pack(anchor="w", pady=(8, 0))
        ttk.Radiobutton(scope_box, text="Αναδημιουργία όλου του σχολικού έτους", variable=self.regenerate_scope_var, value="all").pack(anchor="w", pady=(4, 0))
        warn_lbl = ttk.Label(scope_box, text="⚠ Προσοχή: αυτές οι δύο επιλογές σβήνουν ΟΛΕΣ τις εφημερίες του διαστήματος, ακόμη και τις κλειδωμένες/πραγματοποιημένες. Θα παρθεί πρώτα αυτόματο αντίγραφο ασφαλείας και θα ζητηθεί επιβεβαίωση.",
                             foreground="#B3261E", wraplength=1000, justify="left")

        dates = ttk.Frame(scope_box)
        self.regenerate_from_var = tk.StringVar(value=ui_date(self.db.get_setting("start_date", "2026-09-11")))
        self.regenerate_to_var = tk.StringVar(value=ui_date(self.db.get_setting("end_date", "2027-06-15")))
        from_part = ttk.Frame(dates)
        from_part.pack(side="left")
        ttk.Label(from_part, text="Από").pack(side="left")
        self.regenerate_from_e = ttk.Entry(from_part, textvariable=self.regenerate_from_var, width=14)
        self.regenerate_from_e.pack(side="left", padx=(5, 2))
        self._cal_button(from_part, self.regenerate_from_e).pack(side="left", padx=(0, 14))
        lock_btn = ttk.Button(dates, text="Κλείδωμα πραγματοποιημένων έως…", command=self.lock_completed_until)
        lock_btn.pack(side="left", padx=8)
        to_part = ttk.Frame(dates)
        ttk.Label(to_part, text="Έως").pack(side="left")
        self.regenerate_to_e = ttk.Entry(to_part, textvariable=self.regenerate_to_var, width=14)
        self.regenerate_to_e.pack(side="left", padx=(5, 2))
        self._cal_button(to_part, self.regenerate_to_e).pack(side="left", padx=(0, 14))
        info_lbl = ttk.Label(scope_box, text="", foreground="#34495E", wraplength=1000, justify="left")

        def refresh_scope(*_a):
            scope = self.regenerate_scope_var.get()
            for w in (warn_lbl, dates, info_lbl):
                w.pack_forget()
            to_part.pack_forget()
            if scope != "continue":
                warn_lbl.pack(anchor="w", pady=(8, 0))
            if scope != "all":
                dates.pack(fill="x", pady=(10, 2))
                if scope == "range":
                    to_part.pack(side="left", padx=(0, 14), before=lock_btn)
            info_lbl.pack(anchor="w", pady=(6, 0))
            try:
                school_start = parse_date(self.db.get_setting("start_date"))
                school_end = parse_date(self.db.get_setting("end_date"))
                if scope == "all":
                    a_, b_ = school_start, school_end
                else:
                    a_ = parse_date(self.regenerate_from_var.get())
                    b_ = school_end if scope == "continue" else parse_date(self.regenerate_to_var.get())
                cn = self.db.conn
                unl = cn.execute("SELECT COUNT(*) FROM assignments WHERE d BETWEEN ? AND ? AND locked=0", (a_.isoformat(), b_.isoformat())).fetchone()[0]
                lk = cn.execute("SELECT COUNT(*) FROM assignments WHERE d BETWEEN ? AND ? AND locked=1", (a_.isoformat(), b_.isoformat())).fetchone()[0]
                if scope == "continue":
                    txt = f"Θα ξαναϋπολογιστούν {unl} μη κλειδωμένες εφημερίες από {fmt_date(a_)} έως {fmt_date(b_)}. Οι {lk} κλειδωμένες μένουν."
                else:
                    txt = f"Θα διαγραφούν {unl + lk} εφημερίες ({lk} από αυτές κλειδωμένες) από {fmt_date(a_)} έως {fmt_date(b_)} και θα δημιουργηθούν ξανά."
                info_lbl.config(text=txt)
            except Exception:
                info_lbl.config(text="")
        self.regenerate_scope_var.trace_add("write", refresh_scope)
        self.regenerate_from_var.trace_add("write", refresh_scope)
        self.regenerate_to_var.trace_add("write", refresh_scope)
        self._refresh_gen_scope = refresh_scope
        refresh_scope()

        rules = ttk.LabelFrame(f, text="3 · Προτεραιότητες (προαιρετικό)", padding=10)
        rules.pack(fill="x", pady=(0, 10))
        self.stay_var = tk.BooleanVar(value=self.stay_enabled())
        def _save_stay(*_a):
            self.db.set_setting("stay_in_place", "1" if self.stay_var.get() else "0")
        self.stay_var.trace_add("write", _save_stay)
        ttk.Checkbutton(rules, text="Μείνε στη θέση σου: στην αναδημιουργία κράτα όσες εφημερίες γίνεται όπως ήταν", variable=self.stay_var).pack(anchor="w")
        grid = ttk.Frame(rules)
        grid.pack(anchor="w", pady=(8, 0))
        self.weight_vars = {}
        for r_i, (key, label) in enumerate((
            ("w_stay", "Σταθερότητα (να μη μετακινούνται οι υπάρχουσες)"),
            ("w_day", "Κύρια / εναλλακτική ημέρα του εκπαιδευτικού"),
            ("w_fair_year", "Ισοκατανομή ετήσιου φορτίου"),
            ("w_fair_week", "Ισοκατανομή μέσα στην εβδομάδα"),
        )):
            var = tk.StringVar(value=str(self.rule_weight(key)))
            self.weight_vars[key] = var
            def _save_w(*_a, _k=key, _v=var):
                try:
                    self.db.set_setting(_k, str(max(0, min(5, int(_v.get())))))
                except ValueError:
                    pass
            var.trace_add("write", _save_w)
            ttk.Label(grid, text=label).grid(row=r_i, column=0, sticky="w", padx=(0, 12), pady=2)
            ttk.Spinbox(grid, from_=0, to=5, width=4, textvariable=var).grid(row=r_i, column=1, pady=2)
        ttk.Label(rules, text="Κλίμακα 0–5 (0 = αγνόηση, 5 = πολύ σημαντικό). Πάντα υποχρεωτικά, δεν αλλάζουν: διαθεσιμότητα, απουσίες, μία εφημερία ανά ημέρα/ζώνη, αριθμός θέσεων ανά τομέα.",
                  foreground="#5F6B76", wraplength=1000, justify="left").pack(anchor="w", pady=(8, 0))

        actions = ttk.LabelFrame(f, text="4 · Εκτέλεση", padding=10)
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="Δημιουργία / αναδημιουργία προγράμματος", style="Accent.TButton", command=self.generate_schedule).pack(side="left")
        ttk.Button(actions, text="Διάγνωση συγκρούσεων…", command=self.show_conflict_diagnosis).pack(side="left", padx=10)
        ttk.Button(actions, text="Διαγραφή προγράμματος…", style="Danger.TButton", command=self.delete_schedule_dialog).pack(side="left", padx=10)

        result_box = ttk.LabelFrame(f, text="Αποτέλεσμα / έλεγχος", padding=8)
        result_box.pack(fill="both", expand=True)
        self.generate_text = tk.Text(result_box, height=13, wrap="word", font=("Segoe UI", 10), relief="flat", bg="white", padx=8, pady=8)
        self.generate_text.pack(fill="both", expand=True)


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

    @staticmethod
    def _fold(text: str) -> str:
        """Πεζά και χωρίς τόνους, για αναζήτηση ονομάτων."""
        import unicodedata
        norm = unicodedata.normalize("NFD", str(text or "").casefold())
        return "".join(ch for ch in norm if not unicodedata.combining(ch)).replace("ς", "σ")

    @staticmethod
    def _assign_status(r) -> Tuple[str, str]:
        if r["replaced"]:
            return "Αντικατάσταση", "rep"
        if r["entry_type"] == "manual_completed":
            return "Πραγματοποιημένη", "locked"
        if r["locked"]:
            return "Κλειδωμένη", "locked"
        return "Προγραμματισμένη", ""

    def selected_assignment_id(self) -> Optional[int]:
        sid = getattr(self, "sched_sel_id", None)
        if sid is None:
            return None
        ok = self.db.conn.execute("SELECT 1 FROM assignments WHERE id=?", (sid,)).fetchone()
        return sid if ok else None

    def _build_schedule(self):
        self.sched_view = tk.StringVar(value="list")
        self.sched_search = tk.StringVar()
        self.sched_area = tk.StringVar(value=self.ALL_AREAS)
        self.sched_status = tk.StringVar(value=self.STATUS_FILTERS[0])
        self.sched_from = tk.StringVar()
        self.sched_to = tk.StringVar()
        self.week_start: Optional[date] = None
        self.sched_sel_id: Optional[int] = None
        self._sched_after = None
        self._sched_guard = False
        self._week_cells: Dict[int, tk.Button] = {}

        page = ttk.Frame(self.tab_schedule, padding=(24, 18, 24, 8))
        page.pack(fill="both", expand=True)

        head = ttk.Frame(page)
        head.pack(fill="x")
        titles = ttk.Frame(head)
        titles.pack(side="left")
        ttk.Label(titles, text="Πρόγραμμα", font=("Segoe UI", 20, "bold"), foreground="#34495E").pack(anchor="w")
        self.sched_subtitle = ttk.Label(titles, text="", foreground="#5F6B76")
        self.sched_subtitle.pack(anchor="w")
        seg = tk.Frame(head, bg="#C5CCD3", padx=1, pady=1)
        seg.pack(side="right", anchor="s")
        self._seg_buttons = {}
        for text, val in (("Λίστα", "list"), ("Εβδομάδα", "week")):
            b = tk.Button(seg, text=text, relief="flat", bd=0, padx=18, pady=6, font=("Segoe UI", 10), cursor="hand2",
                          command=lambda v=val: self._sched_set_view(v), highlightthickness=0)
            b.pack(side="left")
            self._seg_buttons[val] = b

        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(12, 8))
        ttk.Button(bar, text="+ Πραγματοποιημένη εφημερία", style="Accent.TButton", command=self.add_completed_assignment).pack(side="left")
        ttk.Button(bar, text="Αλλαγή εκπαιδευτικού", command=self.replace_selected_assignment).pack(side="left", padx=6)
        self.sched_toolbar_extra = ttk.Frame(bar)
        self.sched_toolbar_extra.pack(side="left")
        more = ttk.Menubutton(bar, text="Περισσότερα ▾")
        more.pack(side="right")
        menu = tk.Menu(more, tearoff=0)
        menu.add_command(label="Κλείδωμα πραγματοποιημένων έως…", command=self.lock_completed_until)
        menu.add_command(label="Ξεκλείδωμα επιλεγμένης εφημερίας", command=self.unlock_selected_assignment)
        menu.add_command(label="Ανακατανομή από επιλεγμένη ημέρα", command=self.set_regenerate_from_selected)
        menu.add_separator()
        menu.add_command(label="Εισαγωγή προγράμματος από CSV", command=self.import_schedule_csv)
        menu.add_separator()
        menu.add_command(label="Διαγραφή προγράμματος…", foreground="#B42318", command=self.delete_schedule_dialog)
        more["menu"] = menu
        self.sched_more_menu = menu
        self._export_menubutton(bar).pack(side="right", padx=6)
        ttk.Button(bar, text="Εκτύπωση…", command=lambda: self.print_dialog("week")).pack(side="right", padx=6)

        holder = ttk.Frame(page)
        holder.pack(fill="x", pady=(0, 6))
        self.sched_filter_bar = ttk.Frame(holder)
        self.sched_week_bar = ttk.Frame(holder)
        self._build_sched_filters(self.sched_filter_bar)
        self._build_sched_weekbar(self.sched_week_bar)
        legend = ttk.Frame(holder)
        legend.pack(side="right", anchor="s")
        for text, bg, fg in (("Κλειδωμένη", self.COL_LOCKED, "#33404B"), ("Αντικατάσταση", "#FFE4A8", "#6B4100"), ("Σήμερα", self.COL_TODAY, "#1D4E89")):
            tk.Label(legend, text=text, bg=bg, fg=fg, font=("Segoe UI", 9, "bold"), padx=8, pady=2, highlightthickness=1, highlightbackground="#C5CCD3").pack(side="left", padx=3)

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(4, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.sched_main = ttk.Frame(body)
        self.sched_main.grid(row=0, column=0, sticky="nsew")
        detail = ttk.Frame(body, width=284)
        detail.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        detail.pack_propagate(False)
        self._build_sched_detail(detail)

        # --- προβολή λίστας ---
        self.sched_list_frame = ttk.Frame(self.sched_main)
        cols = ("time", "interval", "area", "teacher", "minutes", "status")
        tree = ttk.Treeview(self.sched_list_frame, columns=cols, show="tree headings", selectmode="browse")
        tree.heading("#0", text="Ημερομηνία")
        tree.column("#0", width=186, stretch=False)
        for c, h, w, anc in (("time", "Ώρα", 105, "center"), ("interval", "Διάλειμμα", 150, "w"), ("area", "Τομέας", 150, "w"),
                             ("teacher", "Εκπαιδευτικός", 230, "w"), ("minutes", "Λεπτά", 60, "center"), ("status", "Κατάσταση", 178, "center")):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor=anc, stretch=(c == "teacher"))
        vsb = ttk.Scrollbar(self.sched_list_frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(self.sched_list_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        tree.bind("<Configure>", lambda _e: self._sched_fit_columns())
        bold = ("Segoe UI", 10, "bold")
        tree.tag_configure("group", background=self.COL_GROUP, foreground="#34495E", font=bold)
        tree.tag_configure("grouptoday", background="#DCEBFA", foreground="#1D4E89", font=bold)
        tree.tag_configure("locked", background=self.COL_LOCKED, foreground="#4A5560")
        tree.tag_configure("rep", background=self.COL_REP)
        tree.tag_configure("today", background=self.COL_TODAY)
        tree.bind("<<TreeviewSelect>>", self._sched_tree_selected)
        tree.bind("<Double-1>", self._sched_tree_double)
        tree.bind("<Button-3>", self._sched_tree_context)
        self.schedule_tree = tree

        # --- προβολή εβδομάδας ---
        self.sched_week_frame = ttk.Frame(self.sched_main)
        wrap, self.week_inner = self._make_scroll_area(self.sched_week_frame)
        wrap.pack(fill="both", expand=True)

        self.sched_ctx_menu = tk.Menu(self, tearoff=0)
        self._sched_apply_view()

    # ---- βοηθητικά UI ----
    def _make_scroll_area(self, parent):
        wrap = ttk.Frame(parent)
        canvas = tk.Canvas(wrap, bg="white", highlightthickness=1, highlightbackground="#DDE1E5")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        inner = tk.Frame(canvas, bg="white")
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)) if e.delta else 0, "units")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return wrap, inner

    def _build_sched_filters(self, f):
        def col(label, w, pad=(0, 12)):
            c = ttk.Frame(f)
            c.pack(side="left", padx=pad)
            ttk.Label(c, text=label, foreground="#5F6B76", font=("Segoe UI", 9)).pack(anchor="w")
            return c
        c = col("Αναζήτηση εκπαιδευτικού", 22)
        self.sched_search_e = ttk.Entry(c, textvariable=self.sched_search, width=24)
        self.sched_search_e.pack()
        c = col("Τομέας", 18)
        self.sched_area_cb = ttk.Combobox(c, textvariable=self.sched_area, state="readonly", width=19, values=[self.ALL_AREAS])
        self.sched_area_cb.pack()
        c = col("Κατάσταση", 26)
        ttk.Combobox(c, textvariable=self.sched_status, state="readonly", width=28, values=self.STATUS_FILTERS).pack()
        c = col("Διάστημα (από – έως)", 30)
        row = ttk.Frame(c)
        row.pack()
        e1 = ttk.Entry(row, textvariable=self.sched_from, width=11)
        e1.pack(side="left")
        self._cal_button(row, e1).pack(side="left", padx=(2, 6))
        e2 = ttk.Entry(row, textvariable=self.sched_to, width=11)
        e2.pack(side="left")
        self._cal_button(row, e2).pack(side="left", padx=(2, 8))
        ttk.Button(row, text="Όλο το έτος", command=self._sched_full_range).pack(side="left")
        for var in (self.sched_search, self.sched_area, self.sched_status, self.sched_from, self.sched_to):
            var.trace_add("write", self._sched_filter_changed)
        self.bind_all("<Control-f>", lambda _e: self._focus_search())

    def _build_sched_weekbar(self, f):
        ttk.Button(f, text="‹", width=3, command=lambda: self._week_shift(-1)).pack(side="left")
        self.week_label = ttk.Label(f, text="", font=("Segoe UI", 12, "bold"), foreground="#34495E", width=26, anchor="center")
        self.week_label.pack(side="left", padx=6)
        ttk.Button(f, text="›", width=3, command=lambda: self._week_shift(1)).pack(side="left")
        ttk.Button(f, text="Αυτή την εβδομάδα", command=self._week_today).pack(side="left", padx=10)

    def _build_sched_detail(self, parent):
        card = tk.Frame(parent, bg="white", highlightthickness=1, highlightbackground="#DDE1E5")
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg="white", padx=16, pady=14)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, text="ΕΠΙΛΕΓΜΕΝΗ ΕΦΗΜΕΡΙΑ", bg="white", fg="#5F6B76", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.sd_title = tk.Label(inner, text="Επίλεξε μια εφημερία", bg="white", fg="#34495E", font=("Segoe UI", 13, "bold"), wraplength=244, justify="left", anchor="w")
        self.sd_title.pack(fill="x", pady=(6, 0))
        self.sd_date = tk.Label(inner, text="", bg="white", fg="#5F6B76", anchor="w")
        self.sd_date.pack(fill="x")
        self.sd_vals = {}
        for key, label in (("area", "Τομέας"), ("teacher", "Εκπαιδευτικός"), ("status", "Κατάσταση")):
            tk.Label(inner, text=label, bg="white", fg="#5F6B76", font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(10, 0))
            v = tk.Label(inner, text="—", bg="white", fg="#1F2933", font=("Segoe UI", 10, "bold"), anchor="w", justify="left", wraplength=244)
            v.pack(fill="x")
            self.sd_vals[key] = v
        lrow = tk.Frame(inner, bg="white")
        lrow.pack(fill="x", pady=(12, 3))
        tk.Label(lrow, text="Φορτίο έτους", bg="white", fg="#5F6B76", font=("Segoe UI", 9)).pack(side="left")
        self.sd_load = tk.Label(lrow, text="", bg="white", fg="#5F6B76", font=("Segoe UI", 9))
        self.sd_load.pack(side="right")
        self.sd_meter = tk.Canvas(inner, height=10, bg="white", highlightthickness=0)
        self.sd_meter.pack(fill="x")
        self.sd_diff = tk.Label(inner, text="", bg="white", fg="#5F6B76", font=("Segoe UI", 9), anchor="w")
        self.sd_diff.pack(fill="x", pady=(3, 0))
        self.sd_actions = ttk.Frame(inner)
        self.sd_actions.pack(fill="x", pady=(14, 0))
        self.sd_btn_replace = ttk.Button(self.sd_actions, text="Αλλαγή εκπαιδευτικού…", style="Accent.TButton", command=self.replace_selected_assignment)
        self.sd_btn_extra = ttk.Frame(self.sd_actions)
        self.sd_btn_lock = ttk.Button(self.sd_actions, text="Κλείδωμα ως πραγματοποιημένη", command=self.lock_selected_assignment)
        self.sd_btn_unlock = ttk.Button(self.sd_actions, text="Ξεκλείδωμα εφημερίας", command=self.unlock_selected_assignment)
        self.sd_btn_regen = ttk.Button(self.sd_actions, text="Ανακατανομή από αυτή την ημέρα", command=self.set_regenerate_from_selected)
        tk.Label(inner, text="Ιστορικό", bg="white", fg="#5F6B76", font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(14, 0), side="bottom")
        self.sd_hist = tk.Label(inner, text="", bg="white", fg="#33404B", font=("Segoe UI", 9), anchor="w", justify="left", wraplength=244)
        self.sd_hist.pack(fill="x", side="bottom")

    # ---- προβολή / φίλτρα ----
    def _sched_set_view(self, view: str) -> None:
        self.sched_view.set(view)
        self._sched_apply_view()
        self.refresh_schedule()

    def _sched_apply_view(self) -> None:
        view = self.sched_view.get()
        for val, b in self._seg_buttons.items():
            on = val == view
            b.configure(bg="#34495E" if on else "white", fg="white" if on else "#1F2933", activebackground="#34495E" if on else "#F3F5F7",
                        activeforeground="white" if on else "#1F2933", font=("Segoe UI", 10, "bold" if on else "normal"))
        for w in (self.sched_filter_bar, self.sched_week_bar, self.sched_list_frame, self.sched_week_frame):
            w.pack_forget()
        if view == "list":
            self.sched_filter_bar.pack(side="left")
            self.sched_list_frame.pack(fill="both", expand=True)
        else:
            self.sched_week_bar.pack(side="left")
            self.sched_week_frame.pack(fill="both", expand=True)

    def _focus_search(self) -> None:
        if self.nb.current is self.tab_schedule:
            self._sched_set_view("list") if self.sched_view.get() != "list" else None
            self.sched_search_e.focus_set()
            self.sched_search_e.select_range(0, "end")

    def _sched_filter_changed(self, *_a) -> None:
        if self._sched_guard:
            return
        if self._sched_after:
            try:
                self.after_cancel(self._sched_after)
            except Exception:
                pass
        self._sched_after = self.after(250, self._sched_filter_apply)

    def _sched_filter_apply(self) -> None:
        self._sched_after = None
        for var in (self.sched_from, self.sched_to):
            txt = var.get().strip()
            if txt:
                try:
                    parse_date(txt)
                except ValueError:
                    return  # ημιτελής ημερομηνία: περιμένουμε να ολοκληρωθεί
        self.refresh_schedule()

    def _sched_full_range(self) -> None:
        lo, hi = self._assign_bounds()
        self._sched_guard = True
        self.sched_from.set(fmt_date(lo) if lo else "")
        self.sched_to.set(fmt_date(hi) if hi else "")
        self._sched_guard = False
        self.refresh_schedule()

    def _assign_bounds(self):
        r = self.db.conn.execute("SELECT MIN(d), MAX(d) FROM assignments").fetchone()
        if not r or not r[0]:
            return None, None
        return parse_date(r[0]), parse_date(r[1])

    def _default_list_range(self):
        lo, hi = self._assign_bounds()
        if lo is None:
            return None, None
        start = min(max(date.today() - timedelta(days=7), lo), hi)
        return start, hi

    def _sched_fit_columns(self) -> None:
        """Προσαρμόζει τα πλάτη των στηλών στο διαθέσιμο πλάτος (όνομα, προτιμώμενο, ελάχιστο, βάρος ελαστικότητας)."""
        tree = self.schedule_tree
        shown = list(tree["displaycolumns"])
        if shown == ["#all"]:
            shown = list(tree["columns"])
        spec = [("#0", 186, 160, 0)] + [s for s in (("time", 100, 92, 0), ("interval", 120, 70, 1), ("area", 140, 80, 1),
                                                    ("teacher", 200, 110, 3), ("minutes", 56, 48, 0), ("status", 178, 150, 0)) if s[0] in shown]
        avail = max(tree.winfo_width() - 6, 300)
        pref = sum(p for _, p, _, _ in spec)
        if avail >= pref:
            wsum = sum(w for *_, w in spec if w) or 1
            widths = {n: p + ((avail - pref) * w / wsum if w else 0) for n, p, _m, w in spec}
        else:
            cap = sum(p - m for _, p, m, w in spec if w) or 1
            ratio = min(1.0, (pref - avail) / cap)
            widths = {n: (p - (p - m) * ratio if w else p) for n, p, m, w in spec}
        for n, wd in widths.items():
            tree.column(n, width=int(wd), stretch=False)

    # ---- ανανέωση ----
    def refresh_schedule(self):
        if not hasattr(self, "schedule_tree"):
            return
        names = [self.ALL_AREAS] + [r["name"] for r in self.db.areas(True)]
        self.sched_area_cb["values"] = names
        if self.sched_area.get() not in names:
            self.sched_area.set(self.ALL_AREAS)
        total = self.db.conn.execute("SELECT COUNT(*), COALESCE(SUM(locked),0) FROM assignments").fetchone()
        self.sched_subtitle.config(text=f"{total[0]} αναθέσεις στο σχολικό έτος · {total[1]} κλειδωμένες")
        if self.sched_view.get() == "list":
            self._render_list()
        else:
            self._render_week()
        self._sched_update_detail()

    def _render_list(self) -> None:
        tree = self.schedule_tree
        lo_default, hi_default = self._default_list_range()
        if not self.sched_from.get().strip() and not self.sched_to.get().strip() and lo_default:
            self._sched_guard = True
            self.sched_from.set(fmt_date(lo_default))
            self.sched_to.set(fmt_date(hi_default))
            self._sched_guard = False
        try:
            d1 = parse_date(self.sched_from.get()).isoformat() if self.sched_from.get().strip() else "0000-00-00"
            d2 = parse_date(self.sched_to.get()).isoformat() if self.sched_to.get().strip() else "9999-12-31"
        except ValueError:
            return
        rows = self.db.conn.execute(self.SCHEDULE_SQL + " WHERE a.d BETWEEN ? AND ? ORDER BY a.d, a.start_time, display_area", (d1, d2)).fetchall()
        needle = self._fold(self.sched_search.get().strip())
        area = self.sched_area.get()
        stat = self.sched_status.get()
        keep = []
        for r in rows:
            text, tag = self._assign_status(r)
            if needle and needle not in self._fold(r["teacher"]) and needle not in self._fold(r["old_teacher"] or ""):
                continue
            if area != self.ALL_AREAS and r["display_area"] != area:
                continue
            if stat == self.STATUS_FILTERS[1] and tag != "":
                continue
            if stat == self.STATUS_FILTERS[2] and not (r["locked"] or r["entry_type"] == "manual_completed"):
                continue
            if stat == self.STATUS_FILTERS[3] and not r["replaced"]:
                continue
            keep.append((r, text, tag))
        tree.delete(*tree.get_children())
        # Στον ημερήσιο τρόπο οι στήλες «Ώρα» και «Διάλειμμα» είναι ίδιες σε όλες τις γραμμές: τις κρύβουμε.
        daily = bool(keep) and all(r["mode"] == "daily" for r, _t, _g in keep)
        tree["displaycolumns"] = ("area", "teacher", "minutes", "status") if daily else ("time", "interval", "area", "teacher", "minutes", "status")
        self._sched_fit_columns()
        today_iso = date.today().isoformat()
        group = None
        last_iid = None
        today_group = None
        for r, text, tag in keep:
            if group != r["d"]:
                group = r["d"]
                dd = parse_date(group)
                is_today = group == today_iso
                label = f"{DAYS_GR[dd.weekday()][:3]}. {dd.strftime('%d/%m/%Y')}" + ("  ·  Σήμερα" if is_today else "")
                tree.insert("", "end", iid=f"g:{group}", text=label, open=True, tags=("grouptoday" if is_today else "group",))
                if is_today:
                    today_group = f"g:{group}"
            teacher = r["teacher"] + (f"   (αντί {r['old_teacher']})" if r["replaced"] and r["old_teacher"] else "")
            row_tag = tag or ("today" if r["d"] == today_iso else "")
            tree.insert(f"g:{group}", "end", iid=str(r["id"]), text="",
                        values=(f"{r['start_time']}–{r['end_time']}", r["interval_name"], r["display_area"], teacher, r["minutes"], text),
                        tags=(row_tag,) if row_tag else ())
            last_iid = str(r["id"])
        if not keep:
            tree.insert("", "end", iid="g:none", text="Δεν υπάρχουν αναθέσεις με αυτά τα φίλτρα.", tags=("group",))
        sel = str(self.sched_sel_id) if self.sched_sel_id is not None else None
        if sel and tree.exists(sel):
            self._sched_guard = True
            tree.selection_set(sel)
            self._sched_guard = False
            tree.see(sel)
        elif today_group and last_iid:
            tree.see(last_iid)
            tree.see(today_group)

    # ---- επιλογή ----
    def _sched_select(self, sid: Optional[int], from_tree: bool = False) -> None:
        self.sched_sel_id = sid
        if not from_tree and self.sched_view.get() == "list" and sid is not None and self.schedule_tree.exists(str(sid)):
            self._sched_guard = True
            self.schedule_tree.selection_set(str(sid))
            self._sched_guard = False
            self.schedule_tree.see(str(sid))
        if self.sched_view.get() == "week":
            self._week_restyle()
        self._sched_update_detail()

    def _sched_tree_selected(self, _e=None) -> None:
        if self._sched_guard:
            return
        sel = self.schedule_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("g:"):
            self._sched_guard = True
            self.schedule_tree.selection_remove(iid)
            self._sched_guard = False
            return
        self._sched_select(int(iid), from_tree=True)

    def _sched_tree_double(self, event) -> None:
        iid = self.schedule_tree.identify_row(event.y)
        if iid and not iid.startswith("g:"):
            self.replace_selected_assignment()

    def _sched_tree_context(self, event) -> None:
        iid = self.schedule_tree.identify_row(event.y)
        if not iid or iid.startswith("g:"):
            return
        self.schedule_tree.selection_set(iid)
        self._sched_select(int(iid), from_tree=True)
        self._sched_popup(event)

    def _sched_popup(self, event) -> None:
        m = self.sched_ctx_menu
        m.delete(0, "end")
        row = self.db.conn.execute("SELECT locked FROM assignments WHERE id=?", (self.sched_sel_id,)).fetchone()
        m.add_command(label="Αλλαγή εκπαιδευτικού…", command=self.replace_selected_assignment)
        for label, cmd in self._sched_extra_actions():
            m.add_command(label=label, command=cmd)
        if row and row["locked"]:
            m.add_command(label="Ξεκλείδωμα εφημερίας", command=self.unlock_selected_assignment)
        else:
            m.add_command(label="Κλείδωμα ως πραγματοποιημένη", command=self.lock_selected_assignment)
        m.add_command(label="Ανακατανομή από αυτή την ημέρα", command=self.set_regenerate_from_selected)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _sched_extra_actions(self):
        """Σημείο επέκτασης: επιπλέον ενέργειες (π.χ. εναλλαγή) που προστίθενται σε μενού και πάνελ."""
        return getattr(self, "_extra_actions", [])

    # ---- πάνελ λεπτομερειών ----
    def _teacher_minutes_stats(self, teacher_id: int):
        rows = self.db.conn.execute("SELECT teacher_id, COALESCE(SUM(minutes),0) FROM assignments GROUP BY teacher_id").fetchall()
        sums = {r[0]: int(r[1]) for r in rows}
        ids = [t.id for t in self.db.active_teachers()]
        vals = [sums.get(i, 0) for i in ids] or [0]
        mean = sum(vals) / len(vals)
        return sums.get(teacher_id, 0), mean, max(vals)

    @staticmethod
    def _hm(minutes: float) -> str:
        m = int(round(minutes))
        return f"{m // 60}ω {m % 60:02d}΄"

    def _sched_update_detail(self) -> None:
        if not hasattr(self, "sd_title"):
            return
        sid = self.selected_assignment_id()
        for b in (self.sd_btn_replace, self.sd_btn_lock, self.sd_btn_unlock, self.sd_btn_regen, self.sd_btn_extra):
            b.pack_forget()
        if sid is None:
            self.sd_title.config(text="Επίλεξε μια εφημερία")
            self.sd_date.config(text="Κάνε κλικ σε μια γραμμή για να δεις λεπτομέρειες και ενέργειες.")
            for v in self.sd_vals.values():
                v.config(text="—")
            self.sd_load.config(text="")
            self.sd_diff.config(text="")
            self.sd_meter.delete("all")
            self.sd_hist.config(text="")
            return
        r = self.db.conn.execute(self.SCHEDULE_SQL + " WHERE a.id=?", (sid,)).fetchone()
        text, tag = self._assign_status(r)
        dd = parse_date(r["d"])
        self.sd_title.config(text=f"{r['interval_name']} · {r['start_time']}–{r['end_time']}")
        self.sd_date.config(text=f"{DAYS_GR[dd.weekday()]} {dd.strftime('%d/%m/%Y')}")
        status_line = text + (" · κλειδωμένη" if r["replaced"] and r["locked"] else "")
        self.sd_vals["area"].config(text=r["display_area"])
        self.sd_vals["teacher"].config(text=r["teacher"] + (f"\n(αντί {r['old_teacher']})" if r["replaced"] and r["old_teacher"] else ""))
        self.sd_vals["status"].config(text=status_line)
        mine, mean, top = self._teacher_minutes_stats(r["teacher_id"])
        self.sd_load.config(text=self._hm(mine))
        diff = round(mine - mean)
        self.sd_diff.config(text=("στον μέσο όρο" if diff == 0 else f"{abs(diff)}΄ {'πάνω' if diff > 0 else 'κάτω'} από τον μέσο όρο") + " · γραμμή = μέσος όρος")
        self.sd_meter.update_idletasks()
        w = max(self.sd_meter.winfo_width(), 100)
        scale = max(top, mean, mine, 1) * 1.1
        self.sd_meter.delete("all")
        self.sd_meter.create_rectangle(0, 1, w, 9, fill="#E3E7EB", outline="")
        self.sd_meter.create_rectangle(0, 1, w * mine / scale, 9, fill="#2B6CB0", outline="")
        self.sd_meter.create_line(w * mean / scale, 0, w * mean / scale, 10, fill="#1F2933", width=2)
        hist = self.db.conn.execute("SELECT changed_at, change_type, old_teacher_name, new_teacher_name FROM duty_changes WHERE assignment_id=? ORDER BY id DESC LIMIT 2", (sid,)).fetchall()
        self.sd_hist.config(text="\n".join(f"{h['changed_at'][:16]} · {h['change_type']}" for h in hist) or "Δεν υπάρχουν αλλαγές.")
        self.sd_btn_replace.pack(fill="x", pady=(0, 6))
        self.sd_btn_extra.pack(fill="x")
        if r["locked"]:
            self.sd_btn_unlock.pack(fill="x", pady=(0, 6))
        else:
            self.sd_btn_lock.pack(fill="x", pady=(0, 6))
        self.sd_btn_regen.pack(fill="x")

    def lock_selected_assignment(self) -> None:
        """Κλειδώνει μόνο την επιλεγμένη εφημερία ως πραγματοποιημένη."""
        sid = self.selected_assignment_id()
        if sid is None:
            messagebox.showwarning("Επιλογή", "Επίλεξε πρώτα μία εφημερία από το πρόγραμμα.")
            return
        row = self.db.conn.execute("SELECT a.locked, a.teacher_id, t.name FROM assignments a JOIN teachers t ON t.id=a.teacher_id WHERE a.id=?", (sid,)).fetchone()
        if row["locked"]:
            self.notify("Η εφημερία είναι ήδη κλειδωμένη.", kind="info")
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.db.conn.execute("UPDATE assignments SET locked=1 WHERE id=?", (sid,))
        self.db.conn.execute(
            "INSERT INTO duty_changes(assignment_id,changed_at,change_type,old_teacher_id,new_teacher_id,old_teacher_name,new_teacher_name,reason) VALUES(?,?,?,?,?,?,?,?)",
            (sid, now, "Κλείδωμα ως πραγματοποιημένη", row["teacher_id"], row["teacher_id"], row["name"], row["name"], "Κλείδωμα μεμονωμένης εφημερίας."))
        self.db.conn.commit()
        self.refresh_all()
        self.notify("Η εφημερία κλειδώθηκε ως πραγματοποιημένη.")

    # ---- προβολή εβδομάδας ----
    def _default_week_start(self) -> date:
        lo, hi = self._assign_bounds()
        base = date.today()
        if lo and base < lo:
            base = lo
        elif hi and base > hi:
            base = hi
        return base - timedelta(days=base.weekday())

    def _week_shift(self, weeks: int) -> None:
        self.week_start = (self.week_start or self._default_week_start()) + timedelta(weeks=weeks)
        self.refresh_schedule()

    def _week_today(self) -> None:
        self.week_start = None
        self.refresh_schedule()

    @staticmethod
    def _area_short(name: str) -> str:
        m = re.search(r"\(([^)]{1,3})\)\s*$", name or "")
        return m.group(1) if m else (name or "")[:2]

    def _render_week(self) -> None:
        if self.week_start is None:
            self.week_start = self._default_week_start()
        w1 = self.week_start
        w2 = w1 + timedelta(days=4)
        self.week_label.config(text=f"{w1.strftime('%d/%m')} – {w2.strftime('%d/%m/%Y')}")
        for ch in self.week_inner.winfo_children():
            ch.destroy()
        self._week_cells = {}
        rows = self.db.conn.execute(self.SCHEDULE_SQL + " WHERE a.d BETWEEN ? AND ?", (w1.isoformat(), w2.isoformat())).fetchall()
        area_order = {r["name"]: i for i, r in enumerate(self.db.areas(False))}
        days = [w1 + timedelta(days=i) for i in range(5)]
        try:
            school_days = set(self.school_days())
        except Exception:
            school_days = set(days)
        exceptions = self.db.exception_dates()
        inner = self.week_inner
        inner.columnconfigure(0, weight=0, minsize=190)
        for c in range(1, 6):
            inner.columnconfigure(c, weight=1, uniform="day")
        today = date.today()
        for i, dd in enumerate(days):
            ex = exceptions.get(dd.isoformat())
            sub = f"\n{ex['label']}" if ex is not None and dd not in school_days else ""
            is_today = dd == today
            tk.Label(inner, text=f"{DAYS_GR[dd.weekday()]} {dd.strftime('%d/%m')}" + ("  ·  Σήμερα" if is_today else "") + sub,
                     bg="#DCEBFA" if is_today else "#F7F8F9", fg="#1D4E89" if is_today else "#34495E", font=("Segoe UI", 10, "bold"),
                     anchor="w", justify="left", padx=10, pady=8).grid(row=0, column=i + 1, sticky="nsew", padx=(1, 0), pady=(0, 1))
        tk.Label(inner, text="Διάλειμμα · Τομέας", bg="#F7F8F9", fg="#34495E", font=("Segoe UI", 10, "bold"), anchor="w", padx=12, pady=8).grid(row=0, column=0, sticky="nsew", pady=(0, 1))
        keys = {}
        for r in rows:
            key = (r["start_time"], r["interval_name"], area_order.get(r["display_area"], 99), r["display_area"])
            keys.setdefault(key, {})
            keys[key].setdefault(r["d"], []).append(r)
        reserves = self.db.conn.execute(
            "SELECT sr.d, sh.name AS shift, t.name AS teacher FROM shift_reserves sr JOIN shifts sh ON sh.id=sr.shift_id JOIN teachers t ON t.id=sr.teacher_id WHERE sr.d BETWEEN ? AND ?",
            (w1.isoformat(), w2.isoformat())).fetchall()
        res_map = {}
        for rr in reserves:
            res_map.setdefault(rr["shift"], {})[rr["d"]] = rr["teacher"]
        if not keys and not res_map:
            tk.Label(inner, text="Δεν υπάρχουν αναθέσεις αυτή την εβδομάδα.", bg="white", fg="#5F6B76", pady=30).grid(row=1, column=0, columnspan=6)
            return
        grid_row = 1
        last_interval = None
        for key in sorted(keys):
            start, interval, _o, area = key
            first = interval != last_interval
            last_interval = interval
            lab = tk.Frame(inner, bg="white", highlightthickness=0)
            lab.grid(row=grid_row, column=0, sticky="nsew", pady=(1 if first else 0, 0))
            if first:
                tk.Label(lab, text=f"{interval}", bg="white", fg="#34495E", font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=12, pady=(6, 0))
                tm = f"{start}–{next(iter(next(iter(keys[key].values()))))['end_time']}"
                tk.Label(lab, text=tm, bg="white", fg="#5F6B76", font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=12)
            tk.Label(lab, text=area, bg="white", fg="#5F6B76", font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=12, pady=(0, 6))
            for i, dd in enumerate(days):
                items = keys[key].get(dd.isoformat(), [])
                cell = self._week_cell(inner, items, dd, dd in school_days, len(keys[key]) > 0)
                cell.grid(row=grid_row, column=i + 1, sticky="nsew", padx=(1, 0), pady=(1 if first else 0, 0))
            grid_row += 1
        for shift, per_day in res_map.items():
            tk.Label(inner, text=f"{shift}\nΑναπληρωματικός", bg="white", fg="#5F6B76", font=("Segoe UI", 9), anchor="w", justify="left", padx=12, pady=6).grid(row=grid_row, column=0, sticky="nsew", pady=(1, 0))
            for i, dd in enumerate(days):
                tk.Label(inner, text=per_day.get(dd.isoformat(), "—"), bg="#F7F8F9", fg="#5F6B76", anchor="w", padx=10).grid(row=grid_row, column=i + 1, sticky="nsew", padx=(1, 0), pady=(1, 0))
            grid_row += 1
        self._week_restyle()

    def _week_cell(self, parent, items, dd: date, is_school_day: bool, expected: bool):
        if not items:
            if not is_school_day:
                return tk.Label(parent, text="—", bg="#F1F3F5", fg="#7A858C", anchor="center")
            btn = tk.Label(parent, text="Κενό — δεν έχει οριστεί", bg="#FEF3F2", fg="#B42318", font=("Segoe UI", 9, "bold"), anchor="w", padx=10, pady=8,
                           highlightthickness=1, highlightbackground="#E7B4AF")
            return btn
        r = items[0]
        text, tag = self._assign_status(r)
        bg = {"rep": "#FFF8E6", "locked": self.COL_LOCKED, "": (self.COL_TODAY if dd == date.today() else "white")}[tag]
        label = r["teacher"] + (f"\nαντί {r['old_teacher']}" if r["replaced"] and r["old_teacher"] else "") + ("".join(f"\n{x['teacher']}" for x in items[1:]))
        btn = tk.Button(parent, text=label, wraplength=110, bg=bg, activebackground="#DCEBFA", fg="#1F2933", relief="flat", bd=0, anchor="w", justify="left",
                        font=("Segoe UI", 10), padx=10, pady=7, cursor="hand2", highlightthickness=2, highlightbackground=bg, highlightcolor="#2B6CB0",
                        command=lambda i=r["id"]: self._sched_select(i))
        btn.bind("<Double-Button-1>", lambda _e, i=r["id"]: (self._sched_select(i), self.replace_selected_assignment()))
        btn.bind("<Button-3>", lambda e, i=r["id"]: (self._sched_select(i), self._sched_popup(e)))
        btn._base_bg = bg
        self._week_cells[r["id"]] = btn
        return btn

    def _week_restyle(self) -> None:
        for sid, btn in self._week_cells.items():
            try:
                sel = sid == self.sched_sel_id
                btn.configure(highlightbackground="#2B6CB0" if sel else btn._base_bg, bg="#DCEBFA" if sel else btn._base_bg)
            except tk.TclError:
                pass


    def _build_reports(self):
        outer = ttk.Frame(self.tab_reports, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Εκτυπώσεις και σύνολα", font=("Segoe UI", 18, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(outer, text="Διάλεξε περίοδο και μετά εκτύπωσε πρόγραμμα ή δες τα σύνολα. Τα σύνολα προσαρμόζονται στον τρόπο εφημερίας.",
                  foreground="#5F6B76").pack(anchor="w", pady=(2, 10))

        # ---- περίοδος
        card = ttk.LabelFrame(outer, text="Περίοδος", padding=(12, 8))
        card.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(card)
        row.pack(fill="x")

        def date_col(label, value, attr):
            col = ttk.Frame(row)
            col.pack(side="left", padx=(0, 14))
            ttk.Label(col, text=label, foreground="#5F6B76", font=("Segoe UI", 9)).pack(anchor="w")
            line = ttk.Frame(col)
            line.pack()
            e = ttk.Entry(line, width=12)
            e.insert(0, ui_date(value))
            e.pack(side="left")
            self._cal_button(line, e).pack(side="left", padx=(3, 0))
            e.bind("<Return>", lambda _e: self.refresh_reports())
            e.bind("<FocusOut>", lambda _e: self.refresh_reports())
            setattr(self, attr, e)
        date_col("Από (ΗΗ/ΜΜ/ΕΕΕΕ)", self.db.get_setting("start_date"), "report_from_e")
        date_col("Έως", self.db.get_setting("end_date"), "report_to_e")
        col = ttk.Frame(row)
        col.pack(side="left", padx=(8, 14))
        ttk.Label(col, text="ή ολόκληρος μήνας", foreground="#5F6B76", font=("Segoe UI", 9)).pack(anchor="w")
        self.report_month_cb = ttk.Combobox(col, width=16, state="readonly", values=["Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος", "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος"])
        self.report_month_cb.pack()
        self.report_month_cb.bind("<<ComboboxSelected>>", lambda _e: self.set_report_month())
        ttk.Button(row, text="Όλο το έτος", command=self._report_full_year).pack(side="left", pady=(16, 0))

        # ---- εκτύπωση / εξαγωγή
        actions = ttk.LabelFrame(outer, text="Εκτύπωση και εξαγωγή", padding=(12, 8))
        actions.pack(fill="x", pady=(0, 10))
        top = ttk.Frame(actions)
        top.pack(fill="x")
        ttk.Button(top, text="Εκτύπωση…", style="Accent.TButton", command=lambda: self.print_dialog("period")).pack(side="left")
        self._export_menubutton(top).pack(side="left", padx=10)
        ttk.Label(top, text="Εβδομάδα, μήνας, περίοδος, ανά εκπαιδευτικό ή σύνολα – από ένα παράθυρο.", foreground="#7A858C").pack(side="left", padx=8)
        self.report_teacher_cb = ttk.Combobox(actions, width=34, state="readonly")

        # ---- σύνολα
        ttk.Label(outer, text="Σύνολα περιόδου", font=("Segoe UI", 11, "bold"), foreground="#34495E").pack(anchor="w", pady=(2, 4))
        wrap = ttk.Frame(outer)
        wrap.pack(fill="both", expand=True)
        self.report_tree = ttk.Treeview(wrap, columns=("teacher", "assignments", "minutes", "diff"), show="headings", height=8)
        for c, h, w, a in (("teacher", "Εκπαιδευτικός", 300, "w"), ("assignments", "Εφημερίες", 130, "center"),
                           ("minutes", "Συνολικός χρόνος", 150, "center"), ("diff", "Σε σχέση με τον μέσο όρο", 200, "center")):
            self.report_tree.heading(c, text=h)
            self.report_tree.column(c, width=w, anchor=a, stretch=True)
        self.report_tree.tag_configure("over", background="#FFF3E0")
        self.report_tree.tag_configure("under", background="#E8F1F8")
        self.report_tree.tag_configure("avg", background="#EEF2F6", font=("Segoe UI", 10, "bold"))
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.report_tree.yview)
        self.report_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.report_tree.pack(side="left", fill="both", expand=True)

    def _report_full_year(self) -> None:
        for e, key in ((self.report_from_e, "start_date"), (self.report_to_e, "end_date")):
            e.delete(0, "end")
            e.insert(0, ui_date(self.db.get_setting(key)))
        self.refresh_reports()


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

    def refresh_school_year_selector(self) -> None:
        years = self.available_school_years()
        current = self.db.get_setting("school_year", self.active_school_year)
        try:
            self.settings_school_year_cb.configure(values=years)
            self.settings_school_year_cb.set(current)
        except Exception:
            pass

    def sync_school_year_fields(self) -> None:
        sy = self.db.get_setting("school_year", self.active_school_year)
        start = self.db.get_setting("start_date", self.dates_from_school_year(sy)[0])
        end = self.db.get_setting("end_date", self.dates_from_school_year(sy)[1])
        self.active_school_year = sy
        self.refresh_school_year_selector()
        try:
            self.school_name_e.delete(0, "end"); self.school_name_e.insert(0, self.db.get_setting("school_name", ""))
            self.school_phone_e.delete(0, "end"); self.school_phone_e.insert(0, self.db.get_setting("school_phone", ""))
            self.school_email_e.delete(0, "end"); self.school_email_e.insert(0, self.db.get_setting("school_email", ""))
            rev = {"compact":"Συμπαγές", "normal":"Κανονικό", "large":"Μεγάλο"}
            self.print_size_cb.set(rev.get(self.db.get_setting("print_size", "normal"), "Κανονικό"))
        except Exception:
            pass
        for attr, value in [
            ("settings_start_e", start), ("settings_end_e", end),
            ("start_e", start), ("end_e", end),
            ("report_from_e", start), ("report_to_e", end),
            ("regenerate_from_e", start), ("regenerate_to_e", end)
        ]:
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                widget.delete(0, "end")
                widget.insert(0, ui_date(value))
            except Exception:
                pass
        try:
            self.mode_var.set(self.db.get_setting("mode", "daily"))
        except Exception:
            pass

    def save_school_identity(self) -> None:
        try:
            size_map = {"Συμπαγές":"compact", "Κανονικό":"normal", "Μεγάλο":"large"}
            self.db.set_setting("school_name", self.school_name_e.get().strip())
            self.db.set_setting("school_phone", self.school_phone_e.get().strip())
            self.db.set_setting("school_email", self.school_email_e.get().strip())
            self.db.set_setting("print_size", size_map.get(self.print_size_cb.get(), "normal"))
            self.set_status("Αποθηκεύτηκαν τα στοιχεία σχολείου και οι ρυθμίσεις εκτύπωσης")
            messagebox.showinfo("Ρυθμίσεις", "Αποθηκεύτηκαν τα στοιχεία σχολείου και η ρύθμιση μεγέθους εκτύπωσης.")
        except Exception as e:
            messagebox.showerror("Ρυθμίσεις", str(e))

    def save_settings_school_year(self) -> None:
        try:
            start = parse_date(self.settings_start_e.get()).isoformat()
            end = parse_date(self.settings_end_e.get()).isoformat()
            if parse_date(start) > parse_date(end):
                raise ValueError("Η έναρξη δεν μπορεί να είναι μετά τη λήξη.")
            self.db.set_setting("start_date", start)
            self.db.set_setting("end_date", end)
            self.sync_school_year_fields()
            self.refresh_all()
            messagebox.showinfo("Ρυθμίσεις", f"Αποθηκεύτηκαν οι ημερομηνίες για το {self.active_school_year}.")
        except Exception as e:
            messagebox.showerror("Σφάλμα", str(e))

    def open_selected_school_year(self) -> None:
        sy = self.settings_school_year_cb.get().strip()
        try:
            sy = normalize_school_year(sy)
        except Exception as e:
            messagebox.showerror("Σχολικό έτος", str(e))
            return
        if sy == self.active_school_year:
            self.sync_school_year_fields()
            return
        self.switch_school_year(sy, show_message=True)

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

    def new_school_year_dialog(self) -> None:
        win = tk.Toplevel(self)
        win.title("Νέο σχολικό έτος")
        win.transient(self)
        win.grab_set()
        win.geometry("650x540")
        win.minsize(610, 500)

        outer = ttk.Frame(win, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Δημιουργία νέου σχολικού έτους", font=("Segoe UI", 15, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(outer, text="Το νέο έτος ξεκινά χωρίς πρόγραμμα, πραγματοποιημένες εφημερίες, αντικαταστάσεις ή ειδικές ημέρες.", wraplength=590, foreground="#5F6B76").pack(anchor="w", pady=(3, 12))

        form = ttk.LabelFrame(outer, text="Νέο έτος", padding=10)
        form.pack(fill="x")
        r1 = ttk.Frame(form); r1.pack(fill="x", pady=4)
        ttk.Label(r1, text="Σχολικό έτος", width=18).pack(side="left")
        year_cb = ttk.Combobox(r1, values=self.school_year_options(), state="readonly", width=18)
        current_start = int(self.active_school_year.split("-")[0])
        suggested = f"{current_start+1}-{current_start+2}"
        year_cb.set(suggested if suggested in self.school_year_options() else self.school_year_options()[0])
        year_cb.pack(side="left")

        r2 = ttk.Frame(form); r2.pack(fill="x", pady=4)
        ttk.Label(r2, text="Έναρξη", width=18).pack(side="left")
        start_e = ttk.Entry(r2, width=18); start_e.pack(side="left", padx=(0, 5))
        self._cal_button(r2, start_e).pack(side="left")
        r3 = ttk.Frame(form); r3.pack(fill="x", pady=4)
        ttk.Label(r3, text="Λήξη", width=18).pack(side="left")
        end_e = ttk.Entry(r3, width=18); end_e.pack(side="left", padx=(0, 5))
        self._cal_button(r3, end_e).pack(side="left")

        def fill_dates(_event=None):
            try:
                a, b = self.dates_from_school_year(year_cb.get())
                start_e.delete(0, "end"); start_e.insert(0, ui_date(a))
                end_e.delete(0, "end"); end_e.insert(0, ui_date(b))
            except Exception:
                pass
        year_cb.bind("<<ComboboxSelected>>", fill_dates)
        fill_dates()

        copy_box = ttk.LabelFrame(outer, text=f"Αντιγραφή από {self.active_school_year}", padding=10)
        copy_box.pack(fill="x", pady=12)
        copy_teachers = tk.IntVar(value=1)
        copy_availability = tk.IntVar(value=0)
        copy_areas = tk.IntVar(value=1)
        copy_intervals = tk.IntVar(value=1)
        copy_shifts = tk.IntVar(value=1)
        copy_mode = tk.IntVar(value=1)
        copy_school = tk.IntVar(value=1)
        ttk.Checkbutton(copy_box, text="Εκπαιδευτικοί (ονόματα, συμμετοχή, παρατηρήσεις)", variable=copy_teachers).pack(anchor="w", pady=2)
        ttk.Checkbutton(copy_box, text="Μαζί και διαθεσιμότητες / κύρια & εναλλακτική ημέρα", variable=copy_availability).pack(anchor="w", padx=22, pady=2)
        ttk.Checkbutton(copy_box, text="Τομείς εφημερίας", variable=copy_areas).pack(anchor="w", pady=2)
        ttk.Checkbutton(copy_box, text="Χρονικές ζώνες / διαλείμματα", variable=copy_intervals).pack(anchor="w", pady=2)
        ttk.Checkbutton(copy_box, text="Βάρδιες / ζώνες και αντιστοίχιση τομέων", variable=copy_shifts).pack(anchor="w", pady=2)
        ttk.Checkbutton(copy_box, text="Τρόπος εφημερίας (ανά ημέρα / ανά διάλειμμα / ανά βάρδια)", variable=copy_mode).pack(anchor="w", pady=2)
        ttk.Checkbutton(copy_box, text="Στοιχεία σχολείου και μέγεθος εκτύπωσης", variable=copy_school).pack(anchor="w", pady=2)

        def create():
            try:
                sy = normalize_school_year(year_cb.get())
                start = parse_date(start_e.get()).isoformat()
                end = parse_date(end_e.get()).isoformat()
                if parse_date(start) > parse_date(end):
                    raise ValueError("Η έναρξη δεν μπορεί να είναι μετά τη λήξη.")
                target = year_db_path(sy)
                if target.exists():
                    raise ValueError(f"Το σχολικό έτος {sy} υπάρχει ήδη.")

                source = self.db
                newdb = DB(target, sy)
                newdb.set_setting("start_date", start)
                newdb.set_setting("end_date", end)
                if copy_mode.get():
                    newdb.set_setting("mode", source.get_setting("mode", "daily"))
                if copy_school.get():
                    for key in ("school_name", "school_phone", "school_email", "print_size"):
                        newdb.set_setting(key, source.get_setting(key, "" if key != "print_size" else "normal"))

                if copy_areas.get():
                    newdb.conn.execute("DELETE FROM areas")
                    rows = source.conn.execute("SELECT name,description,active,sort_order FROM areas ORDER BY id").fetchall()
                    newdb.conn.executemany("INSERT INTO areas(name,description,active,sort_order) VALUES(?,?,?,?)", [tuple(r) for r in rows])

                if copy_intervals.get():
                    newdb.conn.execute("DELETE FROM duty_intervals")
                    rows = source.conn.execute("SELECT name,start_time,end_time,active,sort_order FROM duty_intervals ORDER BY id").fetchall()
                    newdb.conn.executemany("INSERT INTO duty_intervals(name,start_time,end_time,active,sort_order) VALUES(?,?,?,?,?)", [tuple(r) for r in rows])

                if copy_shifts.get() and copy_intervals.get() and copy_areas.get():
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
                if copy_teachers.get():
                    newdb.conn.execute("DELETE FROM teachers")
                    rows = source.conn.execute("SELECT * FROM teachers ORDER BY id").fetchall()
                    for r in rows:
                        if copy_availability.get():
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
                        if copy_availability.get():
                            alts=source.teacher_alternate_days(int(r["id"]), int(r["alternate_day"]))
                            newdb.set_teacher_alternate_days(int(cur.lastrowid), alts)

                if copy_teachers.get() and copy_availability.get():
                    interval_name_by_id = {int(r["id"]): r["name"] for r in source.conn.execute("SELECT id,name FROM duty_intervals").fetchall()}
                    new_interval_by_name = {r["name"]: int(r["id"]) for r in newdb.conn.execute("SELECT id,name FROM duty_intervals").fetchall()}
                    av_rows = source.conn.execute("SELECT teacher_id,weekday,interval_id,available FROM teacher_interval_availability").fetchall()
                    for r in av_rows:
                        nt = teacher_map.get(int(r["teacher_id"]))
                        name = interval_name_by_id.get(int(r["interval_id"]))
                        ni = new_interval_by_name.get(name) if name else None
                        if nt and ni:
                            newdb.conn.execute("INSERT OR REPLACE INTO teacher_interval_availability(teacher_id,weekday,interval_id,available) VALUES(?,?,?,?)", (nt, r["weekday"], ni, r["available"]))
                    if copy_shifts.get():
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
                win.destroy()
                self.switch_school_year(sy, show_message=False)
                messagebox.showinfo("Νέο σχολικό έτος", f"Δημιουργήθηκε και άνοιξε το {sy}.")
            except Exception as e:
                try:
                    if 'newdb' in locals():
                        newdb.close()
                except Exception:
                    pass
                messagebox.showerror("Νέο σχολικό έτος", str(e))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", side="bottom", pady=(10, 0))
        ttk.Button(buttons, text="Ακύρωση", command=win.destroy).pack(side="right", padx=5)
        ttk.Button(buttons, text="Δημιουργία", style="Accent.TButton", command=create).pack(side="right")
        self.wait_window(win)

    def delete_school_year(self) -> None:
        sy = self.settings_school_year_cb.get().strip()
        try:
            sy = normalize_school_year(sy)
        except Exception as e:
            messagebox.showerror("Διαγραφή", str(e))
            return
        years = self.available_school_years()
        if sy not in years:
            messagebox.showwarning("Διαγραφή", "Το σχολικό έτος δεν βρέθηκε.")
            return
        if len(years) <= 1:
            messagebox.showwarning("Διαγραφή", "Δεν μπορεί να διαγραφεί το μοναδικό σχολικό έτος. Δημιούργησε πρώτα ένα νέο.")
            return
        if not messagebox.askyesno(
            "Οριστική διαγραφή σχολικού έτους",
            f"Θα διαγραφούν ΟΛΑ τα δεδομένα του {sy}: πρόγραμμα, εκπαιδευτικοί, ειδικές ημέρες και ιστορικό αλλαγών.\n\nΗ ενέργεια δεν αναιρείται. Να συνεχίσω;"
        ):
            return
        path = year_db_path(sy)
        try:
            if sy == self.active_school_year:
                self.auto_backup("delete-year")
            elif path.exists():
                other = sqlite3.connect(path)
                try:
                    self.auto_backup("delete-year", conn=other, sy=sy)
                finally:
                    other.close()
            if sy == self.active_school_year:
                remaining = [y for y in years if y != sy]
                next_year = remaining[-1]
                self.db.close()
                path.unlink(missing_ok=True)
                self.db = DB(year_db_path(next_year), next_year)
                self.active_school_year = next_year
                ACTIVE_YEAR_FILE.write_text(next_year, encoding="utf-8")
                self.sync_school_year_fields()
                self.refresh_all()
            else:
                path.unlink(missing_ok=True)
                self.refresh_school_year_selector()
            messagebox.showinfo("Διαγραφή", f"Το σχολικό έτος {sy} διαγράφηκε.")
        except Exception as e:
            messagebox.showerror("Διαγραφή", f"Δεν ολοκληρώθηκε η διαγραφή:\n{e}")

    def open_path(self, p: Path) -> None:
        p.mkdir(exist_ok=True)
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            webbrowser.open(p.as_uri())

    def selected_id(self, tree: ttk.Treeview) -> Optional[int]:
        sel = tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def yesno(self, v: int) -> str:
        return "Ναι" if v else "Όχι"

    def refresh_all(self):
        try:
            self.storage_status_label.config(text=f"Δεδομένα: {STORAGE_ROOT}")
            self.storage_path_var.set(str(STORAGE_ROOT))
        except Exception:
            pass
        self.sync_school_year_fields()
        self.refresh_home()
        self.refresh_teachers()
        self.refresh_areas()
        self.refresh_exceptions()
        self.refresh_intervals()
        if hasattr(self, "shifts_tree"):
            self.refresh_shifts()
        self.refresh_schedule()
        self.refresh_reports()

    def refresh_home(self):
        teachers = self.db.active_teachers()
        areas = self.db.areas(True)
        intervals = self.db.intervals(True)
        ex_map = self.db.exception_dates()
        start_d = parse_date(self.db.get_setting("start_date"))
        end_d = parse_date(self.db.get_setting("end_date"))
        exc = 0
        cur_d = start_d
        while cur_d <= end_d:
            if cur_d.weekday() < 5 and cur_d.isoformat() in ex_map:
                exc += 1
            cur_d += timedelta(days=1)
        ex_entries = self.db.conn.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0]
        ass = self.db.conn.execute("SELECT COUNT(*), COALESCE(SUM(minutes),0) FROM assignments").fetchone()
        mode = self.db.get_setting("mode", "daily")
        try:
            year_label = self.db.get_setting("school_year")
            school_label = self.db.get_setting("school_name", "").strip() or "Σχολείο"
            self.home_school_var.set(school_label)
            self.home_year_var.set(f"Σχολικό έτος {year_label}")
            self.home_teachers_var.set(str(len(teachers)))
            self.home_assignments_var.set(str(ass[0]))
            self.home_mode_var.set({"daily":"Ανά ημέρα","breaks":"Ανά διάλειμμα","shifts":"Ανά βάρδια"}.get(mode, mode))
            self.home_storage_var.set("Κοινόχρηστα" if STORAGE_ROOT != app_dir() else "Τοπικά")
            self.home_build_var.set(f"v{APP_VERSION} · {APP_ARCH}")
            self.home_path_var.set(str(STORAGE_ROOT))
            self.header_status.config(text=f"{year_label}  ·  { {'daily':'Ημέρα','breaks':'Διάλειμμα','shifts':'Βάρδια'}.get(mode,mode) }  ·  {APP_ARCH}")
            self._update_window_title()
        except Exception:
            pass
        normal_week_slots = len(areas) * 5
        cycle_count = sum(1 for t in teachers if getattr(t, "extra_cycle", 1))
        primary_set = sum(1 for t in teachers if getattr(t, "primary_day", -1) >= 0)
        txt = []
        txt.append(f"Περίοδος: {fmt_date(self.db.get_setting('start_date'))} – {fmt_date(self.db.get_setting('end_date'))}\n")
        txt.append(f"Ενεργοί τομείς: {len(areas)} · ενεργές χρονικές ζώνες: {len(intervals)} · ειδικές εγγραφές: {ex_entries} ({exc} εργάσιμες ημερομηνίες)\n")
        txt.append(f"Κανονική εβδομάδα: {normal_week_slots} θέσεις εφημερίας ({len(areas)} τομείς × 5 ημέρες).\n")
        txt.append(f"Κύρια ημέρα έχει οριστεί σε {primary_set}/{len(teachers)} εκπαιδευτικούς. Στον κύκλο πρόσθετης εφημερίας συμμετέχουν {cycle_count}.\n\n")
        txt.append("Χρήση:\n")
        txt.append("• Στην ημερήσια εφημερία προτιμάται η κύρια ημέρα. Οι εναλλακτικές χρησιμοποιούνται με σειρά προτεραιότητας για αναγκαίες μετακινήσεις ή πρόσθετη εφημερία.\n")
        txt.append("• Στην ανά-διάλειμμα εφημερία χρησιμοποιείται η διαθεσιμότητα ανά ημέρα και χρονική ζώνη και η ετήσια ισοκατανομή λεπτών.\n")
        txt.append("• Οι πραγματοποιημένες/κλειδωμένες εφημερίες και οι αντικαταστάσεις λαμβάνονται υπόψη στις επόμενες παραγωγές.\n")
        self.home_text.delete("1.0", "end")
        self.home_text.insert("end", "".join(txt))
        self._refresh_home_panels()


    def refresh_teachers(self):
        self.teachers_tree.delete(*self.teachers_tree.get_children())
        all_teachers = self.db.teachers()
        for t in all_teachers:
            break_summary = self.db.teacher_interval_availability_summary(t.id)
            self.teachers_tree.insert(
                "", "end", iid=str(t.id),
                values=(
                    t.name,
                    self.yesno(t.participates and t.active),
                    self.availability_summary(t),
                    self.day_name(getattr(t, "primary_day", -1)),
                    " · ".join(self.day_name(wd) for wd in self.db.teacher_alternate_days(t.id, getattr(t, "alternate_day", -1))) or "—",
                    "Ναι" if getattr(t, "extra_cycle", 1) else "Όχι",
                    break_summary,
                    t.notes,
                )
            )
        if hasattr(self, "report_teacher_cb"):
            active_names = [t.name for t in all_teachers if t.active and t.participates]
            values = ["Όλοι οι εκπαιδευτικοί"] + active_names
            self.report_teacher_cb["values"] = values
            if values and self.report_teacher_cb.get() not in values:
                self.report_teacher_cb.set(values[0])


    def refresh_areas(self):
        self.areas_tree.delete(*self.areas_tree.get_children())
        for r in self.db.areas(False):
            self.areas_tree.insert("", "end", iid=str(r["id"]), values=(r["name"], r["description"], self.yesno(r["active"]), r["sort_order"]))

    def refresh_exceptions(self):
        self.exceptions_tree.delete(*self.exceptions_tree.get_children())
        for r in self.db.conn.execute("SELECT * FROM exceptions ORDER BY d").fetchall():
            end_raw = r["end_d"] if "end_d" in r.keys() and r["end_d"] else r["d"]
            self.exceptions_tree.insert("", "end", iid=str(r["id"]), values=(fmt_date(r["d"]), fmt_date(end_raw), r["label"], r["type"]))

    def refresh_intervals(self):
        self.intervals_tree.delete(*self.intervals_tree.get_children())
        for r in self.db.intervals(False):
            mins = minutes_between(r["start_time"], r["end_time"])
            self.intervals_tree.insert("", "end", iid=str(r["id"]), values=(r["name"], r["start_time"], r["end_time"], mins, self.yesno(r["active"]), r["sort_order"]))

    def refresh_reports(self):
        self.report_tree.delete(*self.report_tree.get_children())
        try:
            d1 = parse_date(self.report_from_e.get()).isoformat()
            d2 = parse_date(self.report_to_e.get()).isoformat()
        except Exception:
            d1 = self.db.get_setting("start_date")
            d2 = self.db.get_setting("end_date")
        mode = self.report_mode_for_range(d1, d2)
        rows = list(self.totals_rows(d1, d2))
        if not rows:
            return
        key, label, unit = {"daily": ("duty_days", "Ημέρες εφημερίας", "ημ."), "shifts": ("assignments", "Βάρδιες", "βάρδ.")}.get(mode, ("minutes", "Εφημερίες", "΄"))
        if mode == "breaks":
            label = "Εφημερίες"
            key = "assignments"
        self.report_tree.heading("assignments", text=label)
        vals = [int(r[key] or 0) for r in rows]
        mean = sum(vals) / len(vals)
        tol = 1.0 if mode != "breaks" else max(1.0, mean * 0.05)
        for r in sorted(rows, key=lambda x: (int(x[key] or 0), x["name"])):
            v = int(r[key] or 0)
            diff = v - mean
            if abs(diff) < tol:
                txt, tag = "στον μέσο όρο", ()
            else:
                txt = f"{'+' if diff > 0 else '−'}{abs(diff):.1f}".replace(".", ",") + (" ημ." if mode == "daily" else (" βάρδ." if mode == "shifts" else " εφημ."))
                tag = ("over",) if diff > 0 else ("under",)
            self.report_tree.insert("", "end", values=(r["name"], v, self._hm(int(r["minutes"] or 0)), txt), tags=tag)
        tot_min = sum(int(r["minutes"] or 0) for r in rows)
        self.report_tree.insert("", "end", values=("Μέσος όρος", f"{mean:.1f}".replace(".", ","), self._hm(tot_min / len(rows)), ""), tags=("avg",))

    def teacher_dialog(self, title: str, initial: Optional[Teacher] = None) -> Optional[dict]:
        """Create/edit a teacher in a dialog that remains usable on small or scaled screens.

        The action bar is fixed at the bottom, while only the form body scrolls. This
        prevents the Save button from falling outside the visible window on Windows
        systems that use display scaling (125%/150% etc.).
        """
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.grab_set()
        win.resizable(True, True)

        # Size the window from the available screen instead of assuming a fixed DPI.
        win.update_idletasks()
        sw = max(640, win.winfo_screenwidth())
        sh = max(560, win.winfo_screenheight())
        width = min(760, max(640, sw - 100))
        height = min(760, max(540, sh - 120))
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(620, 500)
        try:
            win.iconbitmap(str(resource_path("app_icon.ico")))
        except Exception:
            pass
        win.lift()

        # Fixed footer: always visible, independently of the form height.
        footer = ttk.Frame(win, padding=(16, 8, 16, 14))
        footer.pack(side="bottom", fill="x")
        ttk.Separator(win, orient="horizontal").pack(side="bottom", fill="x")

        # Scrollable form area for smaller screens / Windows display scaling.
        body = ttk.Frame(win)
        body.pack(side="top", fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        outer = ttk.Frame(canvas, padding=16)
        window_id = canvas.create_window((0, 0), window=outer, anchor="nw")

        def _sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(event):
            canvas.itemconfigure(window_id, width=event.width)

        outer.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_width)

        def _mousewheel(event):
            # Windows sends +/-120 multiples. Keep this local to the dialog.
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta * 3, "units")

        win.bind("<MouseWheel>", _mousewheel)

        ttk.Label(outer, text=title, font=("Segoe UI", 16, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(
            outer,
            text="Συμπλήρωσε τα βασικά στοιχεία. Η αναλυτική διαθεσιμότητα ανά διάλειμμα ρυθμίζεται από το αντίστοιχο κουμπί στην καρτέλα Εκπαιδευτικοί.",
            foreground="#5F6B76",
            wraplength=680,
        ).pack(anchor="w", pady=(2, 12))

        basic = ttk.LabelFrame(outer, text="Βασικά στοιχεία", padding=12)
        basic.pack(fill="x")
        ttk.Label(basic, text="Ονοματεπώνυμο").grid(row=0, column=0, sticky="w")
        name = ttk.Entry(basic)
        name.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        name.insert(0, initial.name if initial else "")
        basic.columnconfigure(0, weight=1)
        participates = tk.IntVar(value=initial.participates if initial else 1)
        active = tk.IntVar(value=initial.active if initial else 1)
        ttk.Checkbutton(basic, text="Συμμετέχει στις εφημερίες", variable=participates).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Checkbutton(basic, text="Ενεργός/ή στο σχολικό έτος", variable=active).grid(row=3, column=0, sticky="w", pady=2)

        avail = ttk.LabelFrame(outer, text="Παρουσία / διαθεσιμότητα ανά ημέρα", padding=12)
        avail.pack(fill="x", pady=12)
        day_vars = []
        vals = [initial.mon, initial.tue, initial.wed, initial.thu, initial.fri] if initial else [1, 1, 1, 1, 1]
        for i, (label, val) in enumerate(zip(DAYS_GR, vals)):
            v = tk.IntVar(value=val)
            day_vars.append(v)
            ttk.Checkbutton(avail, text=label, variable=v).grid(row=0, column=i, sticky="w", padx=(0, 16), pady=3)
        ttk.Label(
            avail,
            text="Για ημερήσια εφημερία επιλέγονται μόνο οι ημέρες στις οποίες ο/η εκπαιδευτικός μπορεί να καλύψει την ημέρα. Για εφημερία ανά διάλειμμα χρησιμοποιείται η αναλυτική διαθεσιμότητα χρονικών ζωνών.",
            foreground="#5F6B76",
            wraplength=680,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))

        plan = ttk.LabelFrame(outer, text="Κύρια και εναλλακτικές ημέρες", padding=12)
        plan.pack(fill="x")
        day_values = ["—"] + DAYS_GR
        ttk.Label(plan, text="Κύρια ημέρα").grid(row=0, column=0, sticky="w")
        primary_cb = ttk.Combobox(plan, values=day_values, state="readonly", width=18)
        primary_idx = getattr(initial, "primary_day", -1) if initial else -1
        primary_cb.set(self.day_name(primary_idx))
        primary_cb.grid(row=1, column=0, sticky="w", pady=(3, 8))
        initial_alts = self.db.teacher_alternate_days(initial.id, getattr(initial, "alternate_day", -1)) if initial else []
        alt_cbs=[]
        for i in range(4):
            col = 1 + (i % 2)
            row = (i // 2) * 2
            ttk.Label(plan, text=f"{i+1}η εναλλακτική").grid(row=row, column=col, sticky="w", padx=(18,0))
            cb=ttk.Combobox(plan, values=day_values, state="readonly", width=18)
            cb.set(self.day_name(initial_alts[i]) if i < len(initial_alts) else "—")
            cb.grid(row=row+1, column=col, sticky="w", padx=(18,0), pady=(3,8))
            alt_cbs.append(cb)
        extra_cycle = tk.IntVar(value=getattr(initial, "extra_cycle", 1) if initial else 1)
        ttk.Checkbutton(plan, text="Συμμετέχει στον κυκλικό επιμερισμό πρόσθετης εφημερίας", variable=extra_cycle).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 2))
        ttk.Label(plan, text="Η κύρια ημέρα έχει απόλυτη προτεραιότητα. Αν χρειαστεί μετακίνηση ή πρόσθετη εφημερία, δοκιμάζονται διαδοχικά η 1η, 2η, 3η και 4η εναλλακτική.", foreground="#5F6B76", wraplength=680).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        notes_box = ttk.LabelFrame(outer, text="Παρατηρήσεις", padding=10)
        notes_box.pack(fill="x", pady=(12, 4))
        notes = ttk.Entry(notes_box)
        notes.pack(fill="x")
        notes.insert(0, initial.notes if initial else "")

        result = {}

        def idx_from_cb(cb):
            val = cb.get().strip()
            return DAYS_GR.index(val) if val in DAYS_GR else -1

        def alternate_values():
            vals=[]
            primary=idx_from_cb(primary_cb)
            for cb in alt_cbs:
                wd=idx_from_cb(cb)
                if wd >= 0 and wd != primary and wd not in vals:
                    vals.append(wd)
            return vals

        def ok():
            if not name.get().strip():
                messagebox.showwarning("Έλεγχος", "Συμπλήρωσε ονοματεπώνυμο.", parent=win)
                name.focus_set()
                return
            pday = idx_from_cb(primary_cb)
            alts = alternate_values()
            if pday >= 0 and not day_vars[pday].get():
                messagebox.showwarning("Κύρια ημέρα", "Η κύρια ημέρα πρέπει να είναι μία από τις διαθέσιμες ημέρες του/της εκπαιδευτικού.", parent=win)
                return
            for wd in alts:
                if not day_vars[wd].get():
                    messagebox.showwarning("Εναλλακτικές ημέρες", f"Η {self.day_name(wd)} έχει δηλωθεί ως εναλλακτική αλλά δεν είναι διαθέσιμη ημέρα.", parent=win)
                    return
            result.update({
                "name": name.get().strip(),
                "participates": participates.get(),
                "active": active.get(),
                "mon": day_vars[0].get(),
                "tue": day_vars[1].get(),
                "wed": day_vars[2].get(),
                "thu": day_vars[3].get(),
                "fri": day_vars[4].get(),
                "primary_day": pday,
                "alternate_day": alts[0] if alts else -1,
                "alternate_days": alts,
                "extra_cycle": extra_cycle.get(),
                "notes": notes.get().strip(),
            })
            win.destroy()

        # The buttons live in the fixed footer and never disappear below the screen.
        ttk.Button(footer, text="Ακύρωση", command=win.destroy).pack(side="right", padx=(6, 0))
        save_btn = ttk.Button(footer, text="Αποθήκευση", style="Accent.TButton", command=ok)
        save_btn.pack(side="right")
        ttk.Label(footer, text="Enter: Αποθήκευση   •   Esc: Ακύρωση", foreground="#6B7280").pack(side="left")

        win.bind("<Escape>", lambda _e: win.destroy())
        win.bind("<Return>", lambda _e: ok())
        name.focus_set()
        self.wait_window(win)
        return result or None


    def add_teacher(self):
        data = self.teacher_dialog("Νέος εκπαιδευτικός")
        if data:
            try:
                payload={k:v for k,v in data.items() if k != "alternate_days"}
                cur=self.db.conn.execute("""INSERT INTO teachers(name,participates,active,mon,tue,wed,thu,fri,primary_day,alternate_day,extra_cycle,notes)
                    VALUES(:name,:participates,:active,:mon,:tue,:wed,:thu,:fri,:primary_day,:alternate_day,:extra_cycle,:notes)""", payload)
                self.db.set_teacher_alternate_days(int(cur.lastrowid), data.get("alternate_days", []))
                self.db.conn.commit()
                self.refresh_all()
                self.set_status(f"Προστέθηκε: {data['name']}")
            except sqlite3.IntegrityError:
                messagebox.showerror("Σφάλμα", "Υπάρχει ήδη εκπαιδευτικός με αυτό το όνομα.")


    def edit_teacher(self):
        sid = self.selected_id(self.teachers_tree)
        if sid is None:
            messagebox.showwarning("Επιλογή", "Επίλεξε πρώτα έναν/μία εκπαιδευτικό.")
            return
        r = self.db.conn.execute("SELECT * FROM teachers WHERE id=?", (sid,)).fetchone()
        data = self.teacher_dialog("Επεξεργασία εκπαιδευτικού", Teacher(**dict(r)))
        if data:
            data["id"] = sid
            payload={k:v for k,v in data.items() if k != "alternate_days"}
            self.db.conn.execute("""UPDATE teachers SET name=:name,participates=:participates,active=:active,
                mon=:mon,tue=:tue,wed=:wed,thu=:thu,fri=:fri,
                primary_day=:primary_day,alternate_day=:alternate_day,extra_cycle=:extra_cycle,notes=:notes WHERE id=:id""", payload)
            self.db.set_teacher_alternate_days(sid, data.get("alternate_days", []))
            self.db.conn.commit()
            self.refresh_all()
            self.set_status(f"Ενημερώθηκε: {data['name']}")


    def edit_teacher_interval_availability(self):
        """Ρυθμίζει διαθεσιμότητα ανά ημέρα και ανά διάλειμμα για τον/την επιλεγμένο/η εκπαιδευτικό."""
        sid = self.selected_id(self.teachers_tree)
        if sid is None:
            messagebox.showwarning("Επιλογή", "Επίλεξε πρώτα εκπαιδευτικό.")
            return
        row = self.db.conn.execute("SELECT * FROM teachers WHERE id=?", (sid,)).fetchone()
        if not row:
            return
        teacher = Teacher(**dict(row))
        intervals = self.db.intervals(True)
        if not intervals:
            messagebox.showwarning("Διαλείμματα", "Δεν υπάρχουν ενεργά διαλείμματα/χρονικές ζώνες.")
            return
        existing = self.db.get_teacher_interval_availability(sid)

        win = tk.Toplevel(self)
        win.title("Διαθεσιμότητα ανά ημέρα και διάλειμμα")
        win.transient(self)
        win.grab_set()
        win.geometry("1080x640")
        win.minsize(900, 520)
        win.resizable(True, True)

        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        ttk.Label(outer, text=f"{teacher.name}", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text=(
                "Επίλεξε σε ποιες ημέρες και σε ποιες χρονικές ζώνες είναι διαθέσιμος/η. "
                "Η ρύθμιση χρησιμοποιείται μόνο όταν ζητείται παραγωγή προγράμματος ανά διάλειμμα. "
                "Στην ημερήσια εφημερία ισχύει η απλή διαθεσιμότητα ημέρας."
            ),
            wraplength=1030,
            foreground="#555555",
        ).grid(row=1, column=0, sticky="ew", pady=(4, 10))

        table_box = ttk.Frame(outer)
        table_box.grid(row=2, column=0, sticky="nsew")
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)

        canvas = tk.Canvas(table_box, borderwidth=0, highlightthickness=0)
        frame = ttk.Frame(canvas)
        vsb = ttk.Scrollbar(table_box, orient="vertical", command=canvas.yview)
        hsb = ttk.Scrollbar(table_box, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")

        def _on_frame_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        frame.bind("<Configure>", _on_frame_configure)

        ttk.Label(frame, text="Ημέρα", font=("Segoe UI", 10, "bold"), width=14).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        for col, interval in enumerate(intervals, start=1):
            ttk.Label(
                frame,
                text=interval["name"],
                font=("Segoe UI", 10, "bold"),
                width=16,
                anchor="center",
                wraplength=120,
            ).grid(row=0, column=col, sticky="nsew", padx=4, pady=6)

        vars_map = {}
        day_base = [teacher.mon, teacher.tue, teacher.wed, teacher.thu, teacher.fri]
        for wd, day_name in enumerate(DAYS_GR):
            ttk.Label(frame, text=day_name, width=14).grid(row=wd+1, column=0, sticky="w", padx=6, pady=5)
            for col, interval in enumerate(intervals, start=1):
                default = 1 if day_base[wd] else 0
                val = existing.get((wd, int(interval["id"])), default)
                v = tk.IntVar(value=val)
                vars_map[(wd, int(interval["id"]))] = v
                cb = ttk.Checkbutton(frame, variable=v)
                cb.grid(row=wd+1, column=col, padx=24, pady=5)
                if not day_base[wd]:
                    v.set(0)

        for col in range(len(intervals) + 1):
            frame.columnconfigure(col, weight=0)

        def select_all():
            for (wd, iid), v in vars_map.items():
                v.set(1 if day_base[wd] else 0)

        def clear_all():
            for v in vars_map.values():
                v.set(0)

        def ok():
            values = {(wd, iid): v.get() for (wd, iid), v in vars_map.items()}
            self.db.set_teacher_interval_availability(sid, values)
            win.destroy()
            self.refresh_all()
            messagebox.showinfo("Αποθήκευση", "Αποθηκεύτηκε η διαθεσιμότητα ανά ημέρα και διάλειμμα.")

        buttons = ttk.Frame(outer)
        buttons.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(2, weight=1)
        ttk.Button(buttons, text="Όλα διαθέσιμα στις ενεργές ημέρες", command=select_all).grid(row=0, column=0, sticky="w", padx=3)
        ttk.Button(buttons, text="Καθαρισμός", command=clear_all).grid(row=0, column=1, sticky="w", padx=3)
        ttk.Button(buttons, text="Ακύρωση", command=win.destroy).grid(row=0, column=3, sticky="e", padx=5)
        ttk.Button(buttons, text="Αποθήκευση", style="Accent.TButton", command=ok).grid(row=0, column=4, sticky="e")
        self.wait_window(win)

    def delete_teacher(self):
        sid = self.selected_id(self.teachers_tree)
        if not sid:
            messagebox.showwarning("Επιλογή", "Επίλεξε πρώτα εκπαιδευτικό.")
            return
        row = self.db.conn.execute("SELECT name FROM teachers WHERE id=?", (sid,)).fetchone()
        if not row:
            return
        name = row["name"]
        assignment_count = self.db.conn.execute("SELECT COUNT(*) FROM assignments WHERE teacher_id=?", (sid,)).fetchone()[0]
        if assignment_count:
            msg = (
                f"Ο/Η {name} έχει {assignment_count} αναθέσεις στο πρόγραμμα.\n\n"
                "Η οριστική διαγραφή θα αφαιρέσει και αυτές τις αναθέσεις, άρα θα δημιουργηθούν κενά στο υπάρχον πρόγραμμα. "
                "Το ιστορικό ονομάτων στις ήδη καταγεγραμμένες αλλαγές παραμένει ως κείμενο.\n\n"
                "Αν θέλεις μόνο να μη συμμετέχει πλέον, είναι ασφαλέστερο να τον/την κάνεις Ανενεργό από την Επεξεργασία.\n\n"
                "Να γίνει ΟΡΙΣΤΙΚΗ διαγραφή;"
            )
        else:
            msg = f"Να διαγραφεί οριστικά ο/η εκπαιδευτικός {name};"
        if not messagebox.askyesno("Οριστική διαγραφή εκπαιδευτικού", msg):
            return
        if assignment_count and not messagebox.askyesno(
            "Δεύτερη επιβεβαίωση",
            "Θα διαγραφούν και οι αναθέσεις του/της από το πρόγραμμα. Θέλεις να συνεχίσεις;"
        ):
            return
        try:
            assignment_ids = [r[0] for r in self.db.conn.execute("SELECT id FROM assignments WHERE teacher_id=?", (sid,)).fetchall()]
            if assignment_ids:
                marks = ",".join("?" for _ in assignment_ids)
                self.db.conn.execute(f"DELETE FROM duty_changes WHERE assignment_id IN ({marks})", assignment_ids)
                self.db.conn.execute("DELETE FROM assignments WHERE teacher_id=?", (sid,))
            self.db.conn.execute("DELETE FROM teacher_interval_availability WHERE teacher_id=?", (sid,))
            self.db.conn.execute("DELETE FROM teachers WHERE id=?", (sid,))
            self.db.conn.commit()
            self.refresh_all()
            messagebox.showinfo("Διαγραφή", f"Ο/Η {name} διαγράφηκε οριστικά.")
        except Exception as e:
            self.db.conn.rollback()
            messagebox.showerror("Σφάλμα", f"Δεν ολοκληρώθηκε η διαγραφή:\n{e}")

    def area_dialog(self, title: str, row=None):
        win = tk.Toplevel(self); win.title(title); win.transient(self); win.grab_set(); win.geometry("560x320")
        f = ttk.Frame(win, padding=15); f.pack(fill="both", expand=True)
        ttk.Label(f, text="Όνομα τομέα").pack(anchor="w")
        name = ttk.Entry(f); name.pack(fill="x", pady=(0,8)); name.insert(0, row["name"] if row else "")
        ttk.Label(f, text="Περιγραφή").pack(anchor="w")
        desc = tk.Text(f, height=5, wrap="word"); desc.pack(fill="both", expand=True); desc.insert("1.0", row["description"] if row else "")
        active = tk.IntVar(value=row["active"] if row else 1)
        sort_order = tk.IntVar(value=row["sort_order"] if row else 0)
        r2 = ttk.Frame(f); r2.pack(fill="x", pady=8)
        ttk.Checkbutton(r2, text="Ενεργός", variable=active).pack(side="left")
        ttk.Label(r2, text="Σειρά").pack(side="left", padx=(20,5))
        ttk.Spinbox(r2, from_=0, to=99, textvariable=sort_order, width=6).pack(side="left")
        result = {}
        def ok():
            if not name.get().strip(): return
            result.update({"name": name.get().strip(), "description": desc.get("1.0","end").strip(), "active": active.get(), "sort_order": sort_order.get()})
            win.destroy()
        b=ttk.Frame(f); b.pack(fill="x"); ttk.Button(b,text="Ακύρωση",command=win.destroy).pack(side="right",padx=5); ttk.Button(b,text="ΟΚ",command=ok).pack(side="right")
        self.wait_window(win)
        return result or None

    def add_area(self):
        data = self.area_dialog("Προσθήκη τομέα")
        if data:
            self.db.conn.execute("INSERT INTO areas(name,description,active,sort_order) VALUES(:name,:description,:active,:sort_order)", data); self.db.conn.commit(); self.refresh_all()

    def edit_area(self):
        sid = self.selected_id(self.areas_tree)
        if sid is None: return
        row = self.db.conn.execute("SELECT * FROM areas WHERE id=?", (sid,)).fetchone()
        data = self.area_dialog("Επεξεργασία τομέα", row)
        if data:
            data["id"] = sid
            old_name = row["name"]
            self.db.conn.execute("UPDATE areas SET name=:name,description=:description,active=:active,sort_order=:sort_order WHERE id=:id", data)
            # Οι παλιές αναθέσεις κρατούν area_name για συμβατότητα, αλλά συνδέονται πλέον και με area_id.
            # Ενημερώνουμε αμέσως το αποθηκευμένο πρόγραμμα ώστε εκτυπώσεις/αναφορές να δείχνουν τη νέα ονομασία.
            self.db.conn.execute("UPDATE assignments SET area_name=?, area_id=? WHERE area_id=? OR area_name=?", (data["name"], sid, sid, old_name))
            self.db.conn.commit(); self.refresh_all()

    def delete_area(self):
        sid = self.selected_id(self.areas_tree)
        if sid and messagebox.askyesno("Επιβεβαίωση", "Να διαγραφεί ο τομέας;"):
            self.db.conn.execute("DELETE FROM areas WHERE id=?", (sid,)); self.db.conn.commit(); self.refresh_all()

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

    def exception_dialog(self, title: str, row=None):
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.grab_set()
        win.geometry("700x700")
        try:
            win.minsize(640, 590)
        except Exception:
            pass

        outer = ttk.Frame(win, padding=15)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Εφαρμογή").pack(anchor="w")
        row_end_raw = ""
        if row is not None and "end_d" in row.keys():
            row_end_raw = row["end_d"] or ""
        row_start_raw = row["d"] if row is not None else ""
        is_range = bool(row and row_end_raw and row_end_raw != row_start_raw)
        scope_var = tk.StringVar(value="range" if is_range else "single")
        scope_line = ttk.Frame(outer)
        scope_line.pack(fill="x", pady=(2, 8))
        ttk.Radiobutton(scope_line, text="Μία ημέρα", variable=scope_var, value="single").pack(side="left")
        ttk.Radiobutton(scope_line, text="Διάστημα από–έως", variable=scope_var, value="range").pack(side="left", padx=(20, 0))

        dates_box = ttk.LabelFrame(outer, text="Ημερομηνία / διάστημα", padding=10)
        dates_box.pack(fill="x")

        ttk.Label(dates_box, text="Από").grid(row=0, column=0, sticky="w")
        de = ttk.Entry(dates_box, width=18)
        de.grid(row=1, column=0, sticky="ew", padx=(0, 5))
        de.insert(0, fmt_date(row_start_raw) if row else "")
        d_btn = self._cal_button(dates_box, de)
        d_btn.grid(row=1, column=1, padx=(0, 18))

        ttk.Label(dates_box, text="Έως").grid(row=0, column=2, sticky="w")
        ee = ttk.Entry(dates_box, width=18)
        ee.grid(row=1, column=2, sticky="ew", padx=(0, 5))
        end_initial = row_end_raw or row_start_raw
        if row:
            ee.insert(0, fmt_date(end_initial))
        e_btn = self._cal_button(dates_box, ee)
        e_btn.grid(row=1, column=3)
        dates_box.columnconfigure(0, weight=1)
        dates_box.columnconfigure(2, weight=1)

        def sync_scope(*_args):
            if scope_var.get() == "single":
                try:
                    ee.configure(state="normal")
                    ee.delete(0, "end")
                    ee.insert(0, de.get())
                    ee.configure(state="disabled")
                    e_btn.configure(state="disabled")
                except Exception:
                    pass
            else:
                try:
                    ee.configure(state="normal")
                    e_btn.configure(state="normal")
                    if not ee.get().strip():
                        ee.insert(0, de.get())
                except Exception:
                    pass

        scope_var.trace_add("write", sync_scope)
        de.bind("<FocusOut>", lambda _e: sync_scope() if scope_var.get() == "single" else None)
        sync_scope()

        ttk.Label(outer, text="Περιγραφή").pack(anchor="w", pady=(10, 0))
        lab = ttk.Entry(outer)
        lab.pack(fill="x")
        lab.insert(0, row["label"] if row else "")

        ttk.Label(outer, text="Τύπος").pack(anchor="w", pady=(8, 0))
        typ = ttk.Combobox(
            outer,
            values=[
                "Χωρίς εφημερία",
                "Αργία",
                "Σχολική γιορτή",
                "Εκδρομή",
                "Ειδική ημέρα",
                "Εξαίρεση",
                "Μόνο προσέλευση-αποχώρηση",
                "Ειδικό πρόγραμμα ζωνών",
            ],
            state="readonly",
        )
        typ.pack(fill="x")
        typ.set(row["type"] if row else "Χωρίς εφημερία")

        cfg = self.db.exception_config(row) if row else {}
        zone_box = ttk.LabelFrame(outer, text="Ειδικό πρόγραμμα ανά χρονική ζώνη (προαιρετικό)", padding=10)
        zone_box.pack(fill="both", expand=True, pady=12)
        ttk.Label(
            zone_box,
            text="Χρησιμοποίησέ το μόνο όταν την ημέρα/το διάστημα γίνεται εφημερία σε συγκεκριμένες ζώνες. Για «Χωρίς εφημερία» δεν χρειάζεται καμία επιλογή εδώ.",
            wraplength=620,
            foreground="#555555",
        ).pack(anchor="w", pady=(0, 8))
        zone_vars = {}
        for intr in self.db.intervals(False):
            key = intr["name"]
            rowf = ttk.Frame(zone_box)
            rowf.pack(fill="x", pady=2)
            default_active = bool(cfg.get(key, {}).get("active", False))
            default_count = int(cfg.get(key, {}).get("count", 1 if "αποχώ" in key.lower() else 2))
            v = tk.IntVar(value=1 if default_active else 0)
            c = tk.IntVar(value=max(1, min(10, default_count)))
            ttk.Checkbutton(rowf, text=key, variable=v).pack(side="left")
            ttk.Label(rowf, text="Τομείς/εκπαιδευτικοί:").pack(side="left", padx=(20, 5))
            ttk.Spinbox(rowf, from_=1, to=10, textvariable=c, width=5).pack(side="left")
            ttk.Label(rowf, text=f"{intr['start_time']}–{intr['end_time']}", foreground="#666666").pack(side="left", padx=12)
            zone_vars[key] = (v, c)

        result = {}

        def ok():
            try:
                start_date = parse_date(de.get())
                if scope_var.get() == "single":
                    end_date = start_date
                else:
                    # Αν το πεδίο ήταν disabled από προηγούμενη επιλογή, το ξαναδιαβάζουμε αφού ενεργοποιηθεί.
                    end_text = ee.get().strip()
                    end_date = parse_date(end_text)
                if end_date < start_date:
                    raise ValueError("Η ημερομηνία «Έως» δεν μπορεί να είναι πριν από την ημερομηνία «Από».")
            except Exception as e:
                messagebox.showerror("Σφάλμα ημερομηνίας", str(e), parent=win)
                return

            label = lab.get().strip()
            if not label:
                messagebox.showerror("Σφάλμα", "Συμπλήρωσε μια σύντομη περιγραφή.", parent=win)
                return

            exclude_id = int(row["id"]) if row is not None else None
            overlap = self.exception_range_overlap(start_date, end_date, exclude_id)
            if overlap is not None:
                ov_end = overlap["end_d"] if "end_d" in overlap.keys() and overlap["end_d"] else overlap["d"]
                messagebox.showerror(
                    "Επικάλυψη διαστήματος",
                    f"Το διάστημα επικαλύπτεται με υπάρχουσα εγγραφή:\n\n{fmt_date(overlap['d'])} – {fmt_date(ov_end)}\n{overlap['label']}\n\nΕπεξεργάσου πρώτα την υπάρχουσα εγγραφή ή επίλεξε διαφορετικό διάστημα.",
                    parent=win,
                )
                return

            selected_type = typ.get().strip() or "Χωρίς εφημερία"
            config = {}
            # Ο ρητός τύπος «Χωρίς εφημερία» υπερισχύει απόλυτα και δεν κρατά ειδικές ζώνες.
            if selected_type != "Χωρίς εφημερία":
                for name, (v, c) in zone_vars.items():
                    if v.get():
                        config[name] = {"active": True, "count": int(c.get())}

            result.update(
                {
                    "d": start_date.isoformat(),
                    "end_d": end_date.isoformat(),
                    "label": label,
                    "type": selected_type,
                    "config": json.dumps(config, ensure_ascii=False),
                }
            )
            win.destroy()

        b = ttk.Frame(outer)
        b.pack(fill="x", pady=(0, 4))
        ttk.Button(b, text="Ακύρωση", command=win.destroy).pack(side="right", padx=5)
        ttk.Button(b, text="Αποθήκευση", style="Accent.TButton", command=ok).pack(side="right")
        win.bind("<Escape>", lambda _e: win.destroy())
        self.wait_window(win)
        return result or None

    def add_exception(self):
        data = self.exception_dialog("Προσθήκη ειδικής ημέρας / διαστήματος")
        if data:
            self.db.conn.execute(
                "INSERT INTO exceptions(d,end_d,label,type,config) VALUES(:d,:end_d,:label,:type,:config)",
                data,
            )
            self.db.conn.commit()
            self.refresh_all()

    def edit_exception(self):
        sid = self.selected_id(self.exceptions_tree)
        if sid is None:
            return
        row = self.db.conn.execute("SELECT * FROM exceptions WHERE id=?", (sid,)).fetchone()
        data = self.exception_dialog("Επεξεργασία ειδικής ημέρας / διαστήματος", row)
        if data:
            data["id"] = sid
            self.db.conn.execute(
                "UPDATE exceptions SET d=:d,end_d=:end_d,label=:label,type=:type,config=:config WHERE id=:id",
                data,
            )
            self.db.conn.commit()
            self.refresh_all()

    def delete_exception(self):
        sid = self.selected_id(self.exceptions_tree)
        if sid and messagebox.askyesno("Επιβεβαίωση", "Να διαγραφεί η επιλεγμένη ειδική ημέρα / διάστημα;"):
            self.db.conn.execute("DELETE FROM exceptions WHERE id=?", (sid,))
            self.db.conn.commit()
            self.refresh_all()

    def reset_default_intervals(self):
        """Επαναφέρει τις προτεινόμενες χρονικές ζώνες που έδωσε ο χρήστης.
        Προσοχή: αλλάζει τον πίνακα διαλειμμάτων, όχι το ήδη παραχθέν πρόγραμμα.
        """
        msg = (
            "Θα αντικατασταθούν τα υπάρχοντα διαλείμματα με την προεπιλογή:\n\n"
            "Προσέλευση 08:00-08:15\n"
            "1ο διάλειμμα 09:40-10:00\n"
            "2ο διάλειμμα 11:30-11:45\n"
            "3ο διάλειμμα 12:25-12:35\n"
            "Αποχώρηση 13:15-13:20\n\n"
            "Να συνεχίσω;"
        )
        if not messagebox.askyesno("Προεπιλογή διαλειμμάτων", msg):
            return
        self.db.conn.execute("DELETE FROM duty_intervals")
        self.db.conn.executemany(
            "INSERT INTO duty_intervals(name,start_time,end_time,active,sort_order) VALUES(?,?,?,?,?)",
            [
                ("Προσέλευση", "08:00", "08:15", 1, 1),
                ("1ο διάλειμμα", "09:40", "10:00", 1, 2),
                ("2ο διάλειμμα", "11:30", "11:45", 1, 3),
                ("3ο διάλειμμα", "12:25", "12:35", 1, 4),
                ("Αποχώρηση", "13:15", "13:20", 1, 5),
            ],
        )
        self.db.conn.commit()
        self.refresh_all()
        messagebox.showinfo("Ολοκληρώθηκε", "Εφαρμόστηκε η προεπιλογή διαλειμμάτων.")

    def interval_dialog(self, title: str, row=None):
        win=tk.Toplevel(self); win.title(title); win.transient(self); win.grab_set(); win.geometry("430x270")
        f=ttk.Frame(win,padding=15); f.pack(fill="both",expand=True)
        ttk.Label(f,text="Όνομα").pack(anchor="w"); name=ttk.Entry(f); name.pack(fill="x"); name.insert(0,row["name"] if row else "")
        r=ttk.Frame(f); r.pack(fill="x",pady=8)
        ttk.Label(r,text="Από").pack(side="left"); st=ttk.Entry(r,width=8); st.pack(side="left",padx=5); st.insert(0,row["start_time"] if row else "")
        ttk.Label(r,text="Έως").pack(side="left",padx=(20,0)); en=ttk.Entry(r,width=8); en.pack(side="left",padx=5); en.insert(0,row["end_time"] if row else "")
        active=tk.IntVar(value=row["active"] if row else 1); order=tk.IntVar(value=row["sort_order"] if row else 0)
        ttk.Checkbutton(f,text="Ενεργό",variable=active).pack(anchor="w")
        ttk.Label(f,text="Σειρά").pack(anchor="w"); ttk.Spinbox(f,from_=0,to=99,textvariable=order,width=8).pack(anchor="w")
        result={}
        def ok():
            try: minutes_between(st.get(),en.get())
            except Exception: messagebox.showerror("Σφάλμα","Οι ώρες πρέπει να είναι π.χ. 09:40 και 09:50",parent=win); return
            result.update({"name":name.get().strip(),"start_time":st.get().strip(),"end_time":en.get().strip(),"active":active.get(),"sort_order":order.get()}); win.destroy()
        b=ttk.Frame(f); b.pack(fill="x",pady=12); ttk.Button(b,text="Ακύρωση",command=win.destroy).pack(side="right",padx=5); ttk.Button(b,text="ΟΚ",command=ok).pack(side="right")
        self.wait_window(win); return result or None

    def add_interval(self):
        data=self.interval_dialog("Προσθήκη διαστήματος")
        if data: self.db.conn.execute("INSERT INTO duty_intervals(name,start_time,end_time,active,sort_order) VALUES(:name,:start_time,:end_time,:active,:sort_order)",data); self.db.conn.commit(); self.refresh_all()

    def edit_interval(self):
        sid=self.selected_id(self.intervals_tree)
        if sid is None: return
        row=self.db.conn.execute("SELECT * FROM duty_intervals WHERE id=?",(sid,)).fetchone(); data=self.interval_dialog("Επεξεργασία διαστήματος",row)
        if data: data["id"]=sid; self.db.conn.execute("UPDATE duty_intervals SET name=:name,start_time=:start_time,end_time=:end_time,active=:active,sort_order=:sort_order WHERE id=:id",data); self.db.conn.commit(); self.refresh_all()

    def delete_interval(self):
        sid=self.selected_id(self.intervals_tree)
        if sid and messagebox.askyesno("Επιβεβαίωση","Να διαγραφεί το διάστημα;"):
            self.db.conn.execute("DELETE FROM duty_intervals WHERE id=?",(sid,)); self.db.conn.commit(); self.refresh_all()

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

    def preferred_week_area(
        self,
        teacher_id: int,
        week_key: str,
        area_names: List[str],
        week_area: Dict[Tuple[int, str], str],
        week_count: Dict[int, int],
    ) -> str:
        if not area_names:
            return ""
        key = (teacher_id, week_key)
        if key in week_area:
            return week_area[key]
        return area_names[week_count.get(teacher_id, 0) % len(area_names)]

    def mark_week_area_used(
        self,
        teacher_id: int,
        week_key: str,
        area_name: str,
        week_area: Dict[Tuple[int, str], str],
        week_count: Dict[int, int],
    ) -> None:
        key = (teacher_id, week_key)
        if key not in week_area:
            week_area[key] = area_name
            week_count[teacher_id] = week_count.get(teacher_id, 0) + 1


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

    def delete_schedule_dialog(self):
        """Χειροκίνητη διαγραφή προγράμματος για διάστημα ή για όλο το σχολικό έτος."""
        win = tk.Toplevel(self)
        win.title("Διαγραφή προγράμματος")
        win.transient(self)
        win.grab_set()
        win.geometry("650x360")
        f = ttk.Frame(win, padding=18)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Διαγραφή προγράμματος", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(f, text="Η ενέργεια διαγράφει τις αναθέσεις και το ιστορικό αλλαγών που συνδέεται με αυτές. Δεν διαγράφει εκπαιδευτικούς, τομείς ή ρυθμίσεις.", wraplength=600, foreground="#8a5a00").pack(anchor="w", pady=(5, 12))
        kind = tk.StringVar(value="range")
        ttk.Radiobutton(f, text="Διαγραφή συγκεκριμένου διαστήματος", variable=kind, value="range").pack(anchor="w", pady=3)
        ttk.Radiobutton(f, text="Διαγραφή ΟΛΟΥ του προγράμματος του σχολικού έτους", variable=kind, value="all").pack(anchor="w", pady=3)
        dates = ttk.Frame(f); dates.pack(fill="x", pady=12)
        from_e = ttk.Entry(dates, width=14); from_e.insert(0, ui_date(self.db.get_setting("start_date"))); from_e.pack(side="left", padx=(0,3))
        self._cal_button(dates, from_e).pack(side="left", padx=(0,10))
        ttk.Label(dates, text="έως").pack(side="left", padx=4)
        to_e = ttk.Entry(dates, width=14); to_e.insert(0, ui_date(self.db.get_setting("end_date"))); to_e.pack(side="left", padx=(3,3))
        self._cal_button(dates, to_e).pack(side="left", padx=(0,10))
        result = {"ok": False}
        def do_delete():
            try:
                if kind.get() == "all":
                    start = parse_date(self.db.get_setting("start_date"))
                    end = parse_date(self.db.get_setting("end_date"))
                    question = f"Να διαγραφεί ΟΛΟ το πρόγραμμα από {fmt_date(start)} έως {fmt_date(end)};"
                else:
                    start = parse_date(from_e.get()); end = parse_date(to_e.get())
                    if end < start:
                        raise ValueError("Η ημερομηνία «Έως» πρέπει να είναι ίδια ή μεταγενέστερη από την «Από».")
                    question = f"Να διαγραφούν ΟΛΕΣ οι αναθέσεις από {fmt_date(start)} έως {fmt_date(end)}, ακόμη και οι κλειδωμένες;"
            except ValueError as e:
                messagebox.showerror("Λάθος ημερομηνία", str(e), parent=win); return
            if not messagebox.askyesno("Επιβεβαίωση διαγραφής", question, parent=win):
                return
            try:
                self.auto_backup("delete-schedule")
                count = self._delete_assignments(start, end, include_locked=True)
                self.db.conn.commit()
            except Exception as e:
                self.db.conn.rollback(); messagebox.showerror("Σφάλμα", str(e), parent=win); return
            result["ok"] = True; result["count"] = count
            win.destroy()
        b = ttk.Frame(f); b.pack(fill="x", pady=(15,0))
        ttk.Button(b, text="Ακύρωση", command=win.destroy).pack(side="right", padx=5)
        ttk.Button(b, text="Διαγραφή", command=do_delete).pack(side="right")
        self.wait_window(win)
        if result.get("ok"):
            self.refresh_all()
            messagebox.showinfo("Διαγραφή προγράμματος", f"Διαγράφηκαν {result.get('count',0)} αναθέσεις.")

    # ---- πρόοδος παραγωγής (χωρίς νήματα: ανανέωση του UI μέσα στον βρόχο, μόνο όταν αργεί) ----
    GEN_PROGRESS_DELAY = 0.8

    def _gen_progress_begin(self) -> None:
        self._gen_prog = {"t0": time.monotonic(), "win": None, "bar": None, "lbl": None, "cancel": False}
        try:
            self.config(cursor="watch")
        except tk.TclError:
            pass

    def _gen_progress_step(self, i: int, n: int, text: str) -> None:
        st = self.__dict__.get("_gen_prog")
        if st is None:
            return
        if st["win"] is None:
            if time.monotonic() - st["t0"] < self.GEN_PROGRESS_DELAY:
                return
            win = tk.Toplevel(self)
            win.title("Παραγωγή προγράμματος")
            win.transient(self)
            win.resizable(False, False)
            win.protocol("WM_DELETE_WINDOW", lambda: st.__setitem__("cancel", True))
            f = ttk.Frame(win, padding=18)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text="Υπολογισμός προγράμματος…", font=("Segoe UI", 12, "bold")).pack(anchor="w")
            st["lbl"] = ttk.Label(f, text="", foreground="#5F6B76")
            st["lbl"].pack(anchor="w", pady=(6, 8))
            st["bar"] = ttk.Progressbar(f, length=380, mode="determinate", maximum=max(1, n))
            st["bar"].pack(fill="x")
            ttk.Button(f, text="Ακύρωση", command=lambda: st.__setitem__("cancel", True)).pack(anchor="e", pady=(12, 0))
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            win.update_idletasks()
            win.geometry(f"+{(sw - win.winfo_reqwidth()) // 2}+{(sh - win.winfo_reqheight()) // 3}")
            try:
                win.grab_set()
            except tk.TclError:
                pass
            st["win"] = win
        st["bar"].config(maximum=max(1, n), value=i)
        st["lbl"].config(text=f"{text}  ({i} από {n})")
        st["win"].update()
        if st["cancel"]:
            raise GenerationCancelled()

    def _gen_progress_end(self) -> None:
        st = self.__dict__.pop("_gen_prog", None)
        try:
            self.config(cursor="")
        except tk.TclError:
            pass
        if st and st.get("win") is not None:
            try:
                st["win"].grab_release()
                st["win"].destroy()
            except tk.TclError:
                pass


    def generate_schedule(self):
        """Παραγωγή/ανακατανομή: αυτόματο backup πριν, και πλήρης ακύρωση (rollback) αν κάτι αποτύχει."""
        try:
            self.auto_backup("generate")
            _snap, _pre = self._undo_snapshot(), self._gen_metrics()
            self._gen_progress_begin()
            try:
                self._generate_schedule_impl()
            finally:
                self._gen_progress_end()
            self._review_generation(_pre, _snap)
        except GenerationCancelled:
            try:
                self.db.conn.rollback()
            except Exception:
                pass
            self.refresh_all()
            self.notify("Η παραγωγή ακυρώθηκε. Το προηγούμενο πρόγραμμα έμεινε όπως ήταν.", kind="info")
        except Exception as e:
            LOG.exception("Αποτυχία παραγωγής προγράμματος")
            try:
                self.db.conn.rollback()
            except Exception:
                pass
            try:
                self.refresh_all()
            except Exception:
                pass
            messagebox.showerror(
                "Σφάλμα παραγωγής",
                f"Η παραγωγή δεν ολοκληρώθηκε:\n{e}\n\nΤο προηγούμενο πρόγραμμα έχει διατηρηθεί όπως ήταν "
                "(και υπάρχει αυτόματο αντίγραφο στο backups\\auto)."
            )

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

        scope = getattr(self, "regenerate_scope_var", tk.StringVar(value="continue")).get()
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

    def _diagnosis_range(self):
        try:
            s_end = parse_date(self.db.get_setting("end_date"))
            s_start = parse_date(self.db.get_setting("start_date"))
            scope = self.regenerate_scope_var.get()
            if scope == "all":
                return s_start, s_end
            a = parse_date(self.regenerate_from_var.get())
            return a, (s_end if scope == "continue" else parse_date(self.regenerate_to_var.get()))
        except Exception:
            return date.today(), date.today()

    def show_conflict_diagnosis(self):
        a, b = self._diagnosis_range()
        mode = self.mode_var.get()
        if mode not in ("daily", "breaks"):
            messagebox.showinfo("Διάγνωση συγκρούσεων", "Η αναλυτική διάγνωση υποστηρίζεται για τους τρόπους «Ανά ημέρα» και «Ανά διάλειμμα».")
            return
        lines = self.diagnose_conflicts(a, b, mode)
        win = tk.Toplevel(self)
        win.title("Διάγνωση συγκρούσεων")
        win.geometry("820x480")
        txt = tk.Text(win, wrap="word", font=("Segoe UI", 10), padx=10, pady=10)
        txt.pack(fill="both", expand=True)
        txt.insert("end", f"Έλεγχος διαστήματος {fmt_date(a)} – {fmt_date(b)}\n\n")
        txt.insert("end", "\n".join(lines) if lines else "Δεν βρέθηκαν συγκρούσεις: όλες οι θέσεις είναι καλυμμένες και υπάρχουν αρκετοί διαθέσιμοι/ες.")
        txt.config(state="disabled")

    # ---- v0.60: νέα διεπαφή (Edge) -------------------------------------------------
    def open_web_ui(self, hidden: bool = False) -> None:
        """Ξεκινά (αν δεν τρέχει ήδη) τον τοπικό server της νέας διεπαφής και την ανοίγει στον Edge."""
        try:
            ui = self.__dict__.get("_webui")
            if ui is None:
                import webui
                ui = webui.WebUI(self, hidden=hidden)
                self._webui = ui
                url = ui.start()
                self._webui_url = url
            else:
                url = self._webui_url
                ui.hidden = hidden or ui.hidden
            ui.open_browser(url)
            if not hidden:
                self.notify("Η νέα διεπαφή άνοιξε στον Edge.", ms=4000)
        except Exception as e:
            LOG.exception("Αποτυχία εκκίνησης νέας διεπαφής")
            self.deiconify()
            messagebox.showerror("Νέα διεπαφή", f"Δεν ήταν δυνατό να ανοίξει η νέα διεπαφή:\n{e}")

    def lock_completed_until(self):
        """Κλειδώνει αναθέσεις έως και μια ημερομηνία, ώστε να θεωρούνται πραγματοποιημένες."""
        default = getattr(self, "regenerate_from_var", tk.StringVar(value=fmt_date(date.today()))).get()
        value = simpledialog.askstring("Κλείδωμα πραγματοποιημένων", "Κλείδωμα εφημεριών έως και ημερομηνία:\nπ.χ. 31/10/2025", initialvalue=default)
        if not value:
            return
        try:
            until = parse_date(value)
        except ValueError as e:
            messagebox.showerror("Λάθος ημερομηνία", str(e))
            return
        cur = self.db.conn.execute("UPDATE assignments SET locked=1 WHERE d <= ?", (until.isoformat(),))
        self.db.conn.commit()
        # Η επόμενη ανακατανομή ξεκινά από την επόμενη ημέρα.
        next_day = until + timedelta(days=1)
        if hasattr(self, "regenerate_from_var"):
            self.regenerate_from_var.set(fmt_date(next_day))
        self.refresh_all()
        self.notify(f"Κλειδώθηκαν {cur.rowcount} αναθέσεις έως και {fmt_date(until)}. Η επόμενη ανακατανομή μπορεί να ξεκινήσει από {fmt_date(next_day)}.")

    def unlock_selected_assignment(self):
        """Ξεκλειδώνει μόνο την επιλεγμένη ανάθεση, ώστε να μπορεί να διορθωθεί ή να ανακατανεμηθεί."""
        sid = self.selected_assignment_id()
        if sid is None:
            messagebox.showwarning("Επιλογή", "Επίλεξε πρώτα μία κλειδωμένη εφημερία από το πρόγραμμα.")
            return

        row = self.db.conn.execute(
            """
            SELECT a.*, t.name AS teacher, COALESCE(ar.name,a.area_name) AS display_area_name
            FROM assignments a
            JOIN teachers t ON t.id=a.teacher_id
            LEFT JOIN areas ar ON ar.id=a.area_id
            WHERE a.id=?
            """,
            (sid,),
        ).fetchone()
        if not row:
            messagebox.showerror("Ξεκλείδωμα", "Η επιλεγμένη εφημερία δεν βρέθηκε.")
            return
        if not int(row["locked"] or 0):
            messagebox.showinfo("Ξεκλείδωμα", "Η επιλεγμένη εφημερία είναι ήδη ξεκλείδωτη.")
            return

        details = (
            f"{fmt_date(row['d'])} - {row['weekday']}\n"
            f"{row['interval_name']} | {row['display_area_name']}\n"
            f"Εκπαιδευτικός: {row['teacher']}"
        )
        if not messagebox.askyesno(
            "Επιβεβαίωση ξεκλειδώματος",
            "Να ξεκλειδωθεί μόνο η επιλεγμένη εφημερία;\n\n"
            + details
            + "\n\nΜετά το ξεκλείδωμα θα μπορεί να αλλάξει χειροκίνητα ή να συμπεριληφθεί σε επόμενη ανακατανομή.",
        ):
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.db.conn.execute("UPDATE assignments SET locked=0 WHERE id=?", (sid,))
        self.db.conn.execute(
            """
            INSERT INTO duty_changes(
                assignment_id,changed_at,change_type,old_teacher_id,new_teacher_id,
                old_teacher_name,new_teacher_name,reason
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                sid,
                now,
                "Χειροκίνητο ξεκλείδωμα εφημερίας",
                row["teacher_id"],
                row["teacher_id"],
                row["teacher"],
                row["teacher"],
                "Ξεκλείδωμα επιλεγμένης εφημερίας για διόρθωση ή ανακατανομή.",
            ),
        )
        self.db.conn.commit()
        self.refresh_all()
        self.notify("Η επιλεγμένη εφημερία ξεκλειδώθηκε. Οι υπόλοιπες κλειδωμένες εφημερίες δεν επηρεάστηκαν.")

    def add_completed_assignment(self):
        """Καταχώρηση εφημερίας που έχει ήδη πραγματοποιηθεί πριν/εκτός της αυτόματης παραγωγής.

        Η εγγραφή αποθηκεύεται κλειδωμένη, μετρά στα ετήσια σύνολα και δεν αλλάζει
        σε κανονική ανακατανομή. Η τρέχουσα διαθεσιμότητα δεν περιορίζει την ιστορική
        καταχώρηση, επειδή σκοπός είναι να αποτυπωθεί τι πραγματικά έγινε.
        """
        teachers = self.db.active_teachers()
        areas = self.db.areas(True)
        intervals = self.db.intervals(True)
        if not teachers:
            messagebox.showerror("Εκπαιδευτικοί", "Δεν υπάρχουν ενεργοί εκπαιδευτικοί που συμμετέχουν σε εφημερίες.")
            return
        if not areas:
            messagebox.showerror("Τομείς", "Δεν υπάρχουν ενεργοί τομείς εφημερίας.")
            return
        if not intervals:
            messagebox.showerror("Χρονικές ζώνες", "Δεν υπάρχουν ενεργές χρονικές ζώνες.")
            return

        win = tk.Toplevel(self)
        win.title("Καταχώρηση πραγματοποιημένης εφημερίας")
        win.transient(self)
        win.grab_set()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        width = min(760, max(650, sw - 120))
        height = min(650, max(560, sh - 160))
        win.geometry(f"{width}x{height}")
        win.minsize(620, 520)

        outer = ttk.Frame(win, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Πραγματοποιημένη εφημερία", font=("Segoe UI", 16, "bold"), foreground="#34495E").pack(anchor="w")
        ttk.Label(
            outer,
            text="Χρησιμοποίησε αυτή τη φόρμα για εφημερίες που έχουν ήδη γίνει. Η καταχώρηση κλειδώνει και συνυπολογίζεται στις επόμενες κατανομές.",
            foreground="#5F6B76",
            wraplength=700,
        ).pack(anchor="w", pady=(3, 12))

        form = ttk.LabelFrame(outer, text="Στοιχεία εφημερίας", padding=12)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        date_var = tk.StringVar(value=date.today().strftime("%d/%m/%Y"))
        mode_setting = self.db.get_setting("mode", "daily")
        mode_var = tk.StringVar(value="Ανά ημέρα" if mode_setting == "daily" else "Ανά διάλειμμα")
        teacher_var = tk.StringVar(value=teachers[0].name)
        area_var = tk.StringVar(value=areas[0]["name"])
        interval_var = tk.StringVar(value=intervals[0]["name"])

        ttk.Label(form, text="Ημερομηνία").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
        date_entry = ttk.Entry(form, textvariable=date_var, width=18)
        date_entry.grid(row=0, column=1, sticky="ew", pady=6)
        self._cal_button(form, date_entry).grid(row=0, column=2, padx=(8, 0), pady=6)

        ttk.Label(form, text="Τρόπος").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        mode_cb = ttk.Combobox(form, textvariable=mode_var, values=["Ανά ημέρα", "Ανά διάλειμμα"], state="readonly")
        mode_cb.grid(row=1, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(form, text="Εκπαιδευτικός").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=6)
        teacher_cb = ttk.Combobox(form, textvariable=teacher_var, values=[t.name for t in teachers], state="readonly")
        teacher_cb.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(form, text="Τομέας").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=6)
        area_cb = ttk.Combobox(form, textvariable=area_var, values=[a["name"] for a in areas], state="readonly")
        area_cb.grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)

        interval_label = ttk.Label(form, text="Χρονική ζώνη")
        interval_label.grid(row=4, column=0, sticky="w", padx=(0, 10), pady=6)
        interval_cb = ttk.Combobox(form, textvariable=interval_var, values=[r["name"] for r in intervals], state="readonly")
        interval_cb.grid(row=4, column=1, columnspan=2, sticky="ew", pady=6)

        hint_var = tk.StringVar()
        ttk.Label(form, textvariable=hint_var, foreground="#5F6B76", wraplength=650).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        note_box = ttk.LabelFrame(outer, text="Προαιρετική παρατήρηση", padding=10)
        note_box.pack(fill="both", expand=True, pady=(12, 0))
        reason = tk.Text(note_box, height=5, wrap="word", font=("Segoe UI", 10))
        reason.pack(fill="both", expand=True)
        reason.insert("1.0", "Κάλυψη πριν την οριστικοποίηση του προγράμματος")

        def refresh_mode_ui(*_args):
            if mode_var.get() == "Ανά διάλειμμα":
                interval_cb.configure(state="readonly")
                hint_var.set("Επίλεξε το συγκεκριμένο διάλειμμα/χρονική ζώνη που πραγματοποιήθηκε η εφημερία.")
            else:
                interval_cb.configure(state="disabled")
                hint_var.set("Στην ημερήσια εφημερία η εφαρμογή υπολογίζει αυτόματα τον χρόνο από τις ενεργές χρονικές ζώνες της ημέρας.")
        mode_cb.bind("<<ComboboxSelected>>", refresh_mode_ui)
        refresh_mode_ui()

        teacher_by_name = {t.name: t for t in teachers}
        area_names = {a["name"] for a in areas}
        interval_by_name = {r["name"]: r for r in intervals}
        result = {"ok": False}

        def save_completed():
            try:
                d_obj = parse_date(date_var.get())
            except Exception as e:
                messagebox.showerror("Ημερομηνία", str(e), parent=win)
                return
            try:
                school_start = parse_date(self.db.get_setting("start_date"))
                school_end = parse_date(self.db.get_setting("end_date"))
            except Exception:
                school_start = school_end = d_obj
            if d_obj < school_start or d_obj > school_end:
                messagebox.showerror(
                    "Εκτός σχολικού έτους",
                    f"Η ημερομηνία πρέπει να βρίσκεται μεταξύ {fmt_date(school_start)} και {fmt_date(school_end)}.",
                    parent=win,
                )
                return
            if d_obj.weekday() >= 5:
                if not messagebox.askyesno("Σαββατοκύριακο", "Η ημερομηνία είναι Σαββατοκύριακο. Να καταχωρηθεί παρ’ όλα αυτά;", parent=win):
                    return

            t = teacher_by_name.get(teacher_var.get())
            area_name = area_var.get().strip()
            if not t or area_name not in area_names:
                messagebox.showerror("Στοιχεία", "Επίλεξε εκπαιδευτικό και τομέα.", parent=win)
                return

            ex = self.db.exception_dates().get(d_obj.isoformat())
            if self.is_no_duty_exception(ex):
                if not messagebox.askyesno(
                    "Ημέρα χωρίς εφημερία",
                    "Η ημερομηνία έχει δηλωθεί ως «Χωρίς εφημερία». Να καταχωρηθεί πραγματοποιημένη εφημερία παρ’ όλα αυτά;",
                    parent=win,
                ):
                    return

            mode = "daily" if mode_var.get() == "Ανά ημέρα" else "breaks"
            if mode == "breaks":
                interval = interval_by_name.get(interval_var.get())
                if not interval:
                    messagebox.showerror("Χρονική ζώνη", "Επίλεξε χρονική ζώνη.", parent=win)
                    return
                interval_name = interval["name"]
                start_time = interval["start_time"]
                end_time = interval["end_time"]
                minutes = minutes_between(start_time, end_time)
                # Ένας τομέας και ένας εκπαιδευτικός μπορούν να χρησιμοποιηθούν μόνο μία φορά στην ίδια ζώνη.
                clash_area = self.db.conn.execute(
                    "SELECT 1 FROM assignments WHERE d=? AND interval_name=? AND start_time=? AND end_time=? AND area_name=? LIMIT 1",
                    (d_obj.isoformat(), interval_name, start_time, end_time, area_name),
                ).fetchone()
                if clash_area:
                    messagebox.showerror("Διπλή ανάθεση", "Υπάρχει ήδη ανάθεση για αυτόν τον τομέα στην ίδια χρονική ζώνη.", parent=win)
                    return
                clash_teacher = self.db.conn.execute(
                    "SELECT 1 FROM assignments WHERE d=? AND interval_name=? AND start_time=? AND end_time=? AND teacher_id=? LIMIT 1",
                    (d_obj.isoformat(), interval_name, start_time, end_time, t.id),
                ).fetchone()
                if clash_teacher:
                    messagebox.showerror("Διπλή ανάθεση", "Ο/Η εκπαιδευτικός έχει ήδη ανάθεση στην ίδια χρονική ζώνη.", parent=win)
                    return
            else:
                day_intervals, _partial = self.intervals_for_day(d_obj, intervals)
                if not day_intervals:
                    messagebox.showerror("Χρονικές ζώνες", "Δεν υπάρχουν ενεργές χρονικές ζώνες για αυτή την ημέρα.", parent=win)
                    return
                interval_name = "Πραγματοποιημένη ημερήσια εφημερία"
                start_time = day_intervals[0]["start_time"]
                end_time = day_intervals[-1]["end_time"]
                minutes = sum(minutes_between(r["start_time"], r["end_time"]) for r in day_intervals)
                clash_area = self.db.conn.execute(
                    "SELECT 1 FROM assignments WHERE d=? AND mode='daily' AND area_name=? LIMIT 1",
                    (d_obj.isoformat(), area_name),
                ).fetchone()
                if clash_area:
                    messagebox.showerror("Διπλή ανάθεση", "Υπάρχει ήδη ημερήσια ανάθεση για αυτόν τον τομέα.", parent=win)
                    return
                clash_teacher = self.db.conn.execute(
                    "SELECT 1 FROM assignments WHERE d=? AND mode='daily' AND teacher_id=? LIMIT 1",
                    (d_obj.isoformat(), t.id),
                ).fetchone()
                if clash_teacher:
                    messagebox.showerror("Διπλή ανάθεση", "Ο/Η εκπαιδευτικός έχει ήδη ημερήσια εφημερία σε αυτή την ημερομηνία.", parent=win)
                    return

            cur = self.db.conn.execute(
                """
                INSERT INTO assignments(d,weekday,interval_name,start_time,end_time,area_name,teacher_id,minutes,mode,locked,entry_type)
                VALUES(?,?,?,?,?,?,?,?,?,1,'manual_completed')
                """,
                (d_obj.isoformat(), DAYS_GR[d_obj.weekday()] if d_obj.weekday() < 5 else d_obj.strftime("%A"), interval_name, start_time, end_time, area_name, t.id, minutes, mode),
            )
            assignment_id = cur.lastrowid
            note = reason.get("1.0", "end").strip()
            self.db.conn.execute(
                """
                INSERT INTO duty_changes(assignment_id,changed_at,change_type,old_teacher_id,new_teacher_id,old_teacher_name,new_teacher_name,reason)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (assignment_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Καταχώρηση πραγματοποιημένης εφημερίας", None, t.id, "", t.name, note),
            )
            self.db.conn.commit()
            result["ok"] = True
            win.destroy()

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Ακύρωση", command=win.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(actions, text="Αποθήκευση ως πραγματοποιημένη", style="Accent.TButton", command=save_completed).pack(side="right")

        win.bind("<Escape>", lambda _e: win.destroy())
        self.wait_window(win)
        if result["ok"]:
            self.refresh_all()
            messagebox.showinfo(
                "Ολοκληρώθηκε",
                "Η εφημερία καταχωρήθηκε ως πραγματοποιημένη και κλειδωμένη. Θα συνυπολογίζεται στα ετήσια σύνολα και στις επόμενες κατανομές.",
            )

    CHANGE_REASONS = ("Ασθένεια", "Άδεια", "Εκπαιδευτική / υπηρεσιακή ανάγκη", "Συνεννόηση μεταξύ συναδέλφων", "Διόρθωση προγράμματος", "Άλλο")

    def replace_selected_assignment(self):
        self.change_teacher_selected("replace")

    def swap_selected_assignment(self):
        self.change_teacher_selected("swap")

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

    def change_teacher_selected(self, initial_mode: str = "replace") -> None:
        """Ενιαίο παράθυρο «Αλλαγή εκπαιδευτικού»: αντικατάσταση από άλλον/η ή εναλλαγή με άλλη εφημερία."""
        sid = self.selected_assignment_id()
        if sid is None:
            messagebox.showwarning("Επιλογή", "Επίλεξε πρώτα μία εφημερία από το πρόγραμμα.")
            return
        c = self.db.conn
        a = c.execute(self.SCHEDULE_SQL + " WHERE a.id=?", (sid,)).fetchone()
        if not a:
            return
        if not self.db.active_teachers():
            messagebox.showerror("Εκπαιδευτικοί", "Δεν υπάρχουν ενεργοί εκπαιδευτικοί.")
            return
        cands = self._change_candidates(a)
        by_iid = {str(t["t"].id): t for t in cands}

        win = self._modal("Αλλαγή εκπαιδευτικού", 840, 700)
        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)
        foot = ttk.Frame(outer)
        foot.pack(side="bottom", fill="x", pady=(10, 0))
        common = ttk.Frame(outer)
        common.pack(side="bottom", fill="x", pady=(8, 0))

        tm ="" if a["mode"] == "daily" else f" · {a['start_time']}–{a['end_time']}"
        ttk.Label(outer, text=f"{self._gr_day(parse_date(a['d']))} {fmt_date(a['d'])}  ·  {a['display_area']}{tm}", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(outer, text=f"Τώρα εφημερεύει: {a['teacher']}", foreground="#8B2E2E", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(2, 8))

        mode_var = tk.StringVar(value=initial_mode if initial_mode in ("replace", "swap") else "replace")
        mrow = ttk.Frame(outer)
        mrow.pack(fill="x")
        ttk.Radiobutton(mrow, text="Αντικατάσταση από άλλον/η εκπαιδευτικό", variable=mode_var, value="replace").pack(side="left", padx=(0, 18))
        ttk.Radiobutton(mrow, text="Εναλλαγή με άλλη εφημερία", variable=mode_var, value="swap").pack(side="left")

        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True, pady=(8, 0))

        # ---- αντικατάσταση ----
        fr_rep = ttk.Frame(holder)
        best = next((x for x in cands if not x["blocked"]), None)
        sug = tk.StringVar(value=(f"Προτείνεται: {best['t'].name} — {best['why']}, με το μικρότερο φορτίο ανάμεσα στους διαθέσιμους/ες." if best else "Δεν υπάρχει διαθέσιμος/η εκπαιδευτικός."))
        ttk.Label(fr_rep, textvariable=sug, foreground="#1F6F43", font=("Segoe UI", 10, "bold"), wraplength=790, justify="left").pack(anchor="w", pady=(0, 6))
        rwrap = ttk.Frame(fr_rep)
        rwrap.pack(fill="both", expand=True)
        rtree = ttk.Treeview(rwrap, columns=("name", "load", "why"), show="headings", selectmode="browse", height=10)
        for col, txt, wd, anc in (("name", "Εκπαιδευτικός", 230, "w"), ("load", "Φορτίο έτους", 110, "center"), ("why", "Κατάσταση", 420, "w")):
            rtree.heading(col, text=txt)
            rtree.column(col, width=wd, anchor=anc, stretch=(col == "why"))
        rsb = ttk.Scrollbar(rwrap, orient="vertical", command=rtree.yview)
        rtree.configure(yscrollcommand=rsb.set)
        rsb.pack(side="right", fill="y")
        rtree.pack(side="left", fill="both", expand=True)
        rtree.tag_configure("blocked", foreground="#9AA5B0")
        rtree.tag_configure("best", background="#E6F4EA")
        for x in cands:
            tags = ("blocked",) if x["blocked"] else (("best",) if x is best else ())
            rtree.insert("", "end", iid=str(x["t"].id), values=(x["t"].name, self._hm(x["load"]), x["why"]), tags=tags)
        if best:
            rtree.selection_set(str(best["t"].id))
            rtree.see(str(best["t"].id))

        # ---- εναλλαγή ----
        fr_swap = ttk.Frame(holder)
        ttk.Label(fr_swap, text="Διάλεξε με ποια εφημερία θα αλλάξει θέση. Οι δύο εκπαιδευτικοί ανταλλάσσουν εφημερίες. Δεν γίνεται αν κάποιος/α θα βρεθεί με δύο εφημερίες την ίδια ώρα.",
                  foreground="#5F6B76", wraplength=790, justify="left").pack(anchor="w", pady=(0, 6))
        q = tk.StringVar()
        srow = ttk.Frame(fr_swap)
        srow.pack(fill="x")
        ttk.Label(srow, text="Αναζήτηση εκπαιδευτικού:").pack(side="left")
        ttk.Entry(srow, textvariable=q, width=24).pack(side="left", padx=6)
        swrap = ttk.Frame(fr_swap)
        swrap.pack(fill="both", expand=True, pady=6)
        stree = ttk.Treeview(swrap, columns=("d", "area", "teacher"), show="headings", selectmode="browse", height=10)
        for col, txt, wd in (("d", "Ημερομηνία", 170), ("area", "Τομέας / ώρα", 250), ("teacher", "Εκπαιδευτικός", 260)):
            stree.heading(col, text=txt)
            stree.column(col, width=wd, anchor="w")
        ssb = ttk.Scrollbar(swrap, orient="vertical", command=stree.yview)
        stree.configure(yscrollcommand=ssb.set)
        ssb.pack(side="right", fill="y")
        stree.pack(side="left", fill="both", expand=True)
        swap_rows = c.execute(self.SCHEDULE_SQL + " WHERE a.locked=0 AND a.id<>? AND a.teacher_id<>? AND a.d>=? ORDER BY a.d, a.start_time, display_area",
                              (sid, a["teacher_id"], min(date.today().isoformat(), a["d"]))).fetchall()

        def fill_swap(*_):
            stree.delete(*stree.get_children())
            key = self._fold(q.get())
            for r in swap_rows:
                if key and key not in self._fold(r["teacher"]):
                    continue
                tt = "" if r["mode"] == "daily" else f" · {r['start_time']}"
                stree.insert("", "end", iid=str(r["id"]), values=(f"{self._gr_day(parse_date(r['d']))} {fmt_date(r['d'])}", f"{r['display_area']}{tt}", r["teacher"]))
        q.trace_add("write", fill_swap)
        fill_swap()

        # ---- κοινά: αιτιολογία, κλείδωμα ----
        crow = ttk.Frame(common)
        crow.pack(fill="x")
        ttk.Label(crow, text="Αιτία").grid(row=0, column=0, sticky="w")
        reason_var = tk.StringVar(value=self.CHANGE_REASONS[0])
        ttk.Combobox(crow, textvariable=reason_var, values=self.CHANGE_REASONS, state="readonly", width=32).grid(row=1, column=0, sticky="w", padx=(0, 12))
        ttk.Label(crow, text="Σημείωση (προαιρετικά)").grid(row=0, column=1, sticky="w")
        note_var = tk.StringVar()
        ttk.Entry(crow, textvariable=note_var, width=42).grid(row=1, column=1, sticky="we")
        crow.columnconfigure(1, weight=1)
        lock_var = tk.BooleanVar(value=True)
        lock_cb = ttk.Checkbutton(common, text="Κλείδωμα ως πραγματοποιημένη (να μη μετακινηθεί σε επόμενη ανακατανομή)", variable=lock_var)
        lock_cb.pack(anchor="w", pady=(8, 0))

        result = {"ok": False, "msg": ""}
        ok_btn = ttk.Button(foot, text="Αντικατάσταση", style="Accent.TButton")

        def show_mode(*_):
            fr_rep.pack_forget()
            fr_swap.pack_forget()
            if mode_var.get() == "swap":
                fr_swap.pack(fill="both", expand=True)
                ok_btn.config(text="Εναλλαγή")
                lock_cb.state(["disabled"])
            else:
                fr_rep.pack(fill="both", expand=True)
                ok_btn.config(text="Αντικατάσταση")
                lock_cb.state(["!disabled"])
        mode_var.trace_add("write", show_mode)

        def reason_text(extra: str = "") -> str:
            parts = [reason_var.get()]
            if note_var.get().strip():
                parts.append(note_var.get().strip())
            if extra:
                parts.append(extra)
            return " | ".join(parts)

        def do_replace():
            sel = rtree.selection()
            if not sel:
                messagebox.showwarning("Επιλογή", "Επίλεξε τον/την εκπαιδευτικό που θα αναλάβει.", parent=win)
                return
            x = by_iid[sel[0]]
            if x["blocked"]:
                messagebox.showerror("Δεν είναι δυνατό", f"{x['t'].name}: {x['why']}. Διάλεξε κάποιον/α άλλον/η.", parent=win)
                return
            extra = "Εθελοντική εξυπηρέτηση (εκτός δηλωμένης διαθεσιμότητας)." if x["rank"] == 3 else ""
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            locked_value = 1 if lock_var.get() else a["locked"]
            c.execute("UPDATE assignments SET teacher_id=?, locked=? WHERE id=?", (x["t"].id, locked_value, sid))
            c.execute("""INSERT INTO duty_changes(assignment_id,changed_at,change_type,old_teacher_id,new_teacher_id,old_teacher_name,new_teacher_name,reason)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (sid, now, "Αντικατάσταση λόγω απουσίας/διόρθωση", a["teacher_id"], x["t"].id, a["teacher"], x["t"].name, reason_text(extra)))
            if a["mode"] == "shifts" and x["rank"] == 0:
                c.execute("""UPDATE shift_reserves SET activated=1,replacement_assignment_id=?
                             WHERE d=? AND teacher_id=? AND shift_id=(SELECT id FROM shifts WHERE name=? LIMIT 1)""",
                          (sid, a["d"], x["t"].id, a["interval_name"]))
            c.commit()
            result["msg"] = f"Η αλλαγή καταγράφηκε: {a['teacher']} → {x['t'].name}. (Ctrl+Z για αναίρεση)"
            result["ok"] = True
            win.destroy()

        def do_swap():
            if a["locked"]:
                messagebox.showinfo("Εναλλαγή", "Η εφημερία είναι κλειδωμένη/πραγματοποιημένη. Ξεκλείδωσέ την πρώτα αν θέλεις να την αλλάξεις.", parent=win)
                return
            sel = stree.selection()
            if not sel:
                messagebox.showwarning("Επιλογή", "Διάλεξε εφημερία για εναλλαγή.", parent=win)
                return
            err = self._swap_assignments(sid, int(sel[0]), reason_text())
            if err:
                messagebox.showerror("Εναλλαγή", err, parent=win)
                return
            result["msg"] = "Η εναλλαγή καταγράφηκε. (Ctrl+Z για αναίρεση)"
            result["ok"] = True
            win.destroy()

        ok_btn.config(command=lambda: do_swap() if mode_var.get() == "swap" else do_replace())
        ttk.Button(foot, text="Ακύρωση", command=win.destroy).pack(side="right", padx=(8, 0))
        ok_btn.pack(side="right")
        rtree.bind("<Double-1>", lambda _e: do_replace())
        stree.bind("<Double-1>", lambda _e: do_swap())
        win.bind("<Escape>", lambda _e: win.destroy())
        show_mode()
        self.wait_window(win)
        if result["ok"]:
            self.refresh_all()
            self.notify(result["msg"])

    def set_regenerate_from_selected(self):
        """Ανακατανομή από την επιλεγμένη ημέρα: τρέχει απευθείας (backup, σύνοψη πριν/μετά, επαναφορά, Ctrl+Z)."""
        sid = self.selected_assignment_id()
        if sid is None:
            messagebox.showwarning("Επιλογή", "Επίλεξε πρώτα μία εφημερία από το πρόγραμμα.")
            return
        row = self.db.conn.execute("SELECT d FROM assignments WHERE id=?", (sid,)).fetchone()
        if not row:
            return
        d = parse_date(row["d"])
        last = self.db.conn.execute("SELECT MAX(d) FROM assignments").fetchone()[0]
        end = max(d, parse_date(last)) if last else d
        if not messagebox.askyesno(
                "Ανακατανομή από αυτή την ημέρα",
                f"Θα ανακατανεμηθούν οι μη κλειδωμένες εφημερίες από {fmt_date(d)} έως {fmt_date(end)} (όσο φτάνει το υπάρχον πρόγραμμα).\n\n"
                "• Οι κλειδωμένες/πραγματοποιημένες εφημερίες δεν αλλάζουν.\n"
                "• Θα παρθεί αυτόματο αντίγραφο ασφαλείας και θα δεις σύνοψη πριν κρατηθεί το νέο πρόγραμμα (Όχι = επαναφορά).\n"
                "• Για επέκταση του προγράμματος μέχρι το τέλος του έτους χρησιμοποίησε την «Παραγωγή προγράμματος».\n\nΣυνέχεια;"):
            return
        self._regenerate_for_absence("range_keep", d, end)

    def _make_cal_icon(self) -> tk.PhotoImage:
        """Μικρό εικονίδιο ημερολογίου (ζωγραφισμένο στον κώδικα, χωρίς εξωτερικά αρχεία)."""
        img = tk.PhotoImage(width=18, height=18)
        navy, grey, white = "#2C3E50", "#5B6B7A", "#FFFFFF"
        img.put(navy, to=(1, 3, 17, 7))          # κεφαλίδα
        img.put(white, to=(1, 7, 17, 17))        # σώμα
        img.put(navy, to=(1, 7, 2, 17))          # αριστερό περίγραμμα
        img.put(navy, to=(16, 7, 17, 17))        # δεξί περίγραμμα
        img.put(navy, to=(1, 16, 17, 17))        # κάτω περίγραμμα
        img.put(navy, to=(4, 1, 6, 5))           # κρίκοι
        img.put(navy, to=(12, 1, 14, 5))
        for r in range(3):
            for c in range(4):
                img.put(grey, to=(3 + c * 3, 9 + r * 3, 5 + c * 3, 11 + r * 3))
        return img

    def _cal_button(self, parent, entry) -> ttk.Button:
        """Κουμπί-εικονίδιο που ανοίγει το ημερολόγιο για το πεδίο ημερομηνίας."""
        if getattr(self, "_cal_icon", None) is None:
            self._cal_icon = self._make_cal_icon()
            try:
                self.style.configure("Cal.TButton", padding=(7, 5))
            except Exception:
                pass
        b = ttk.Button(parent, image=self._cal_icon, style="Cal.TButton", command=lambda: self.pick_date_into(entry), takefocus=0)
        _Tip(b, "Επιλογή ημερομηνίας")
        return b

    def pick_date_into(self, entry: ttk.Entry) -> None:
        try:
            initial = parse_date(entry.get())
        except Exception:
            initial = date.today()
        selected = self.calendar_dialog(initial, "Επιλογή ημερομηνίας")
        if selected:
            entry.delete(0, "end")
            entry.insert(0, fmt_date(selected))

    def calendar_dialog(self, initial: date, title: str = "Ημερολόγιο") -> Optional[date]:
        win = tk.Toplevel(self); win.title(title); win.transient(self); win.grab_set(); win.geometry("330x330")
        current = {"year": initial.year, "month": initial.month, "selected": None}
        frame = ttk.Frame(win, padding=10); frame.pack(fill="both", expand=True)
        head = ttk.Frame(frame); head.pack(fill="x")
        title_lbl = ttk.Label(head, text="", font=("Segoe UI", 11, "bold")); title_lbl.pack(side="left", expand=True)
        grid = ttk.Frame(frame); grid.pack(fill="both", expand=True, pady=8)
        def draw():
            for w in grid.winfo_children(): w.destroy()
            y, m = current["year"], current["month"]
            title_lbl.config(text=f"{MONTHS_GR_ALL[m - 1]} {y}")
            for c, name in enumerate(["Δε","Τρ","Τε","Πε","Πα","Σα","Κυ"]):
                ttk.Label(grid, text=name, anchor="center", font=("Segoe UI", 9, "bold")).grid(row=0, column=c, sticky="nsew", padx=1, pady=1)
            cal = calendar.Calendar(firstweekday=0)
            for r, week in enumerate(cal.monthdatescalendar(y, m), start=1):
                for c, dd in enumerate(week):
                    txt = str(dd.day) if dd.month == m else ""
                    state = "normal" if dd.month == m else "disabled"
                    btn = ttk.Button(grid, text=txt, width=4, state=state, command=lambda x=dd: select(x))
                    btn.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
            for c in range(7): grid.columnconfigure(c, weight=1)
        def prev_month():
            m = current["month"] - 1; y = current["year"]
            if m == 0: m = 12; y -= 1
            current.update(year=y, month=m); draw()
        def next_month():
            m = current["month"] + 1; y = current["year"]
            if m == 13: m = 1; y += 1
            current.update(year=y, month=m); draw()
        def select(d):
            current["selected"] = d; win.destroy()
        ttk.Button(head, text="‹", width=3, command=prev_month).pack(side="left")
        ttk.Button(head, text="›", width=3, command=next_month).pack(side="right")
        b = ttk.Frame(frame); b.pack(fill="x")
        ttk.Button(b, text="Σήμερα", command=lambda: select(date.today())).pack(side="left")
        ttk.Button(b, text="Άκυρο", command=win.destroy).pack(side="right")
        draw()
        self.wait_window(win)
        return current["selected"]

    def set_report_month(self):
        month_names = ["Σεπτέμβριος","Οκτώβριος","Νοέμβριος","Δεκέμβριος","Ιανουάριος","Φεβρουάριος","Μάρτιος","Απρίλιος","Μάιος","Ιούνιος"]
        month_numbers = [9,10,11,12,1,2,3,4,5,6]
        name = self.report_month_cb.get()
        if name not in month_names:
            messagebox.showwarning("Μήνας", "Επίλεξε μήνα.")
            return
        sy = self.db.get_setting("school_year", "2026-2027")
        start_year = int(sy.split("-")[0])
        m = month_numbers[month_names.index(name)]
        y = start_year if m >= 9 else start_year + 1
        first = date(y, m, 1)
        last = date(y, m, calendar.monthrange(y, m)[1])
        start_limit = parse_date(self.db.get_setting("start_date"))
        end_limit = parse_date(self.db.get_setting("end_date"))
        if first < start_limit: first = start_limit
        if last > end_limit: last = end_limit
        self.report_from_e.delete(0, "end"); self.report_from_e.insert(0, fmt_date(first))
        self.report_to_e.delete(0, "end"); self.report_to_e.insert(0, fmt_date(last))
        self.refresh_reports()

    # ------------------------------------------------------------------
    #  Εισαγωγή ωρολογίου (aSc XML / xlsx)
    # ------------------------------------------------------------------
    def export_timetable_template(self) -> None:
        path = filedialog.asksaveasfilename(title="Αποθήκευση πρότυπου ωρολογίου", defaultextension=".xlsx",
                                            initialfile="Prototypo_Orologiou.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            write_xlsx(path, [
                ("Ωρολόγιο", ["Εκπαιδευτικός", "Ημέρα", "Ώρα"],
                 [("Παπαδοπούλου Μαρία", "Δευτέρα", 1), ("Παπαδοπούλου Μαρία", "Δευτέρα", 2), ("Παπαδοπούλου Μαρία", "Τετάρτη", 3),
                  ("Γεωργίου Νίκος", "Τρίτη", 1), ("Γεωργίου Νίκος", "Τρίτη", 2)], [28, 14, 8]),
                ("Ώρες", ["Ώρα", "Από", "Έως"],
                 [(1, "08:15", "09:00"), (2, "09:00", "09:45"), (3, "10:00", "10:45"), (4, "10:45", "11:30"), (5, "11:45", "12:30"), (6, "12:30", "13:15")], [8, 10, 10]),
            ])
        except Exception as exc:
            messagebox.showerror("Πρότυπο ωρολογίου", f"Η αποθήκευση απέτυχε:\n{exc}")
            return
        messagebox.showinfo("Πρότυπο ωρολογίου", "Αποθηκεύτηκε. Μία γραμμή ανά ώρα διδασκαλίας: Εκπαιδευτικός, Ημέρα, Ώρα.\nΤο φύλλο «Ώρες» (ώρες ρολογιού) είναι προαιρετικό· χρειάζεται μόνο για την αυτόματη διαθεσιμότητα ανά διάλειμμα.")

    def import_timetable(self) -> None:
        path = filedialog.askopenfilename(title="Εισαγωγή ωρολογίου προγράμματος",
                                          filetypes=[("Ωρολόγιο (aSc XML ή Excel)", "*.xml *.xlsx *.roz"), ("aSc XML", "*.xml"), ("Excel", "*.xlsx"), ("Όλα", "*.*")])
        if not path:
            return
        ext = Path(path).suffix.lower()
        if ext == ".roz":
            messagebox.showinfo("Αρχείο .roz", "Το .roz είναι το εσωτερικό αρχείο του aSc Timetables και δεν διαβάζεται απευθείας.\n\n"
                                "Ανοίξτε το στο aSc και κάντε: Αρχείο → Εξαγωγή → XML (ή «Εξαγωγή σε XML»). Μετά εισάγετε εδώ το .xml.")
            return
        try:
            raw = Path(path).read_bytes()
            tt = parse_xlsx_timetable(raw) if ext in (".xlsx", ".xlsm") else parse_asc_timetable(raw)
        except Exception as exc:
            messagebox.showerror("Εισαγωγή ωρολογίου", str(exc))
            return
        self._timetable_preview(tt, Path(path).name)

    def _timetable_preview(self, tt: dict, filename: str) -> None:
        info = analyze_timetable(tt)
        teachers = self.db.teachers()
        rows = []
        used = set()
        for name in sorted(info, key=_plain):
            t = match_teacher_name(name, teachers)
            if t is not None and t.id in used:
                t = None
            if t is not None:
                used.add(t.id)
            rows.append((name, t, info[name]))
        have_times = bool(tt.get("periods")) and any(a and b for a, b in tt["periods"].values())
        win = self._modal("Εισαγωγή ωρολογίου προγράμματος", 1040, 660)
        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Εισαγωγή ωρολογίου προγράμματος", font=("Segoe UI", 15, "bold"), foreground="#34495E").pack(anchor="w")
        found = sum(1 for _n, t, _i in rows if t)
        ttk.Label(outer, text=f"Αρχείο: {filename} · {len(rows)} εκπαιδευτικοί στο ωρολόγιο · {found} αντιστοιχίστηκαν με εκπαιδευτικούς της εφαρμογής."
                              + (f" Αγνοήθηκαν {tt['bad_rows']} μη έγκυρες γραμμές." if tt.get("bad_rows") else ""),
                  foreground="#5F6B76", wraplength=980).pack(anchor="w", pady=(2, 8))
        foot = ttk.Frame(outer)
        foot.pack(side="bottom", fill="x", pady=(10, 0))
        opts = ttk.LabelFrame(outer, text="Τι θα ενημερωθεί", padding=8)
        opts.pack(side="bottom", fill="x", pady=(8, 0))
        v_days = tk.BooleanVar(value=True)
        v_prim = tk.BooleanVar(value=True)
        v_brk = tk.BooleanVar(value=have_times)
        v_new = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Ημέρες παρουσίας (διαθέσιμες ημέρες = ημέρες που διδάσκει)", variable=v_days).pack(anchor="w")
        ttk.Checkbutton(opts, text="Κύρια και εναλλακτικές ημέρες (πρώτα οι ημέρες με τις λιγότερες ώρες διδασκαλίας)", variable=v_prim).pack(anchor="w")
        cb = ttk.Checkbutton(opts, text="Διαθεσιμότητα ανά διάλειμμα (διαθέσιμος όταν βρίσκεται στο σχολείο εκείνη την ώρα)", variable=v_brk)
        cb.pack(anchor="w")
        if not have_times:
            cb.state(["disabled"])
            ttk.Label(opts, text="   Δεν υπάρχουν ώρες ρολογιού στο αρχείο (στο xlsx προσθέστε το φύλλο «Ώρες»).", foreground="#7A858C").pack(anchor="w")
        ttk.Checkbutton(opts, text="Δημιουργία εκπαιδευτικών που δεν υπάρχουν στην εφαρμογή", variable=v_new).pack(anchor="w")
        cols = ("name", "match", "days", "hours", "primary", "alt")
        tree = ttk.Treeview(outer, columns=cols, show="headings", height=14)
        for c, h, w in zip(cols, ("Όνομα στο ωρολόγιο", "Εκπαιδευτικός εφαρμογής", "Ημέρες παρουσίας", "Ώρες/εβδ.", "Κύρια", "Εναλλακτικές"), (230, 230, 170, 80, 90, 190)):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w" if c in ("name", "match", "days", "alt") else "center")
        tree.tag_configure("miss", foreground="#B3261E")
        dn = ["Δε", "Τρ", "Τε", "Πε", "Πα"]
        for name, t, i in rows:
            tree.insert("", "end", tags=("miss",) if t is None else (), values=(
                name, t.name if t else "— δεν βρέθηκε —", " ".join(dn[d] for d in i["days"]), i["total"],
                dn[i["primary"]] if i["primary"] >= 0 else "", " ".join(dn[d] for d in i["alternates"])))
        tree.pack(fill="both", expand=True)

        def apply():
            opt = {"days": v_days.get(), "primary": v_prim.get(), "breaks": v_brk.get() and have_times, "create": v_new.get()}
            if not any(opt[k] for k in ("days", "primary", "breaks")):
                messagebox.showwarning("Εισαγωγή ωρολογίου", "Επιλέξτε τουλάχιστον ένα από τα στοιχεία προς ενημέρωση.", parent=win)
                return
            n_applied = sum(1 for _n, t, _i in rows if t is not None or opt["create"])
            if not messagebox.askyesno("Εισαγωγή ωρολογίου", f"Θα ενημερωθεί η διαθεσιμότητα {n_applied} εκπαιδευτικών (υπάρχουσες τιμές θα αντικατασταθούν).\n"
                                       "Πριν από την αλλαγή θα παρθεί αυτόματο αντίγραφο ασφαλείας.\n\nΝα συνεχιστεί;", parent=win):
                return
            win.destroy()
            self._apply_timetable(rows, tt, opt)
        ttk.Button(foot, text="Εφαρμογή", style="Accent.TButton", command=apply).pack(side="right")
        ttk.Button(foot, text="Άκυρο", command=win.destroy).pack(side="right", padx=8)
        self._timetable_win = win

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

    def import_teachers_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return

        # Συμβατότητα με CSV παλαιότερων εκδόσεων:
        # - οι εξαγωγές της εφαρμογής χρησιμοποιούν ελληνικό Excel-friendly ';'
        # - δεχόμαστε επίσης ',', tab και '|', UTF-8 ή Windows Greek (cp1253).
        raw_text = None
        used_encoding = None
        for enc in ("utf-8-sig", "utf-8", "cp1253", "windows-1253"):
            try:
                raw_text = Path(path).read_text(encoding=enc)
                used_encoding = enc
                break
            except UnicodeDecodeError:
                continue
            except OSError as e:
                messagebox.showerror("Εισαγωγή CSV", f"Δεν ήταν δυνατή η ανάγνωση του αρχείου:\n{e}")
                return
        if raw_text is None:
            messagebox.showerror("Εισαγωγή CSV", "Το αρχείο δεν αναγνωρίστηκε ως UTF-8 ή ελληνικό Windows CSV.")
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


    def export_exceptions_csv(self):
        path = EXPORT_DIR / f"special_days_{self.db.get_setting('school_year')}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        rows = self.db.conn.execute("SELECT d,end_d,label,type,config FROM exceptions ORDER BY d,end_d").fetchall()
        out=[]
        for r in rows:
            out.append((r["d"], r["end_d"] or r["d"], r["label"], r["type"], r["config"] or "{}"))
        self.write_csv(path, ["Από_ISO","Έως_ISO","Περιγραφή","Τύπος","config_json"], out)
        messagebox.showinfo("Εξαγωγή ειδικών ημερών", f"Αποθηκεύτηκε:\n{path}")

    def import_exceptions_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path: return
        try:
            text = None
            for enc in ("utf-8-sig","utf-8","cp1253","windows-1253"):
                try:
                    text = Path(path).read_text(encoding=enc); break
                except UnicodeDecodeError: continue
            if text is None: raise ValueError("Δεν αναγνωρίστηκε η κωδικοποίηση του CSV.")
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

    def import_schedule_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path: return
        replace = messagebox.askyesnocancel(
            "Εισαγωγή προγράμματος",
            "Ναι = αντικατάσταση όλου του υπάρχοντος προγράμματος του ενεργού σχολικού έτους.\n"
            "Όχι = συγχώνευση με το υπάρχον πρόγραμμα (παραλείπονται διπλότυπα).\n"
            "Άκυρο = ακύρωση."
        )
        if replace is None: return
        try:
            raw=None
            for enc in ("utf-8-sig","utf-8","cp1253","windows-1253"):
                try: raw=Path(path).read_text(encoding=enc); break
                except UnicodeDecodeError: continue
            if raw is None: raise ValueError("Δεν αναγνωρίστηκε η κωδικοποίηση του CSV.")
            first=raw.splitlines()[0] if raw.splitlines() else ""
            delim=";" if first.count(";") >= first.count(",") else ","
            reader=csv.DictReader(io.StringIO(raw), delimiter=delim)
            rows=list(reader)
            if replace:
                if not messagebox.askyesno("Επιβεβαίωση", "Θα διαγραφεί το υπάρχον πρόγραμμα του ενεργού σχολικού έτους πριν την εισαγωγή. Συνέχεια;"):
                    return
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

    def export_full_backup(self):
        try:
            sy=self.db.get_setting("school_year", self.active_school_year)
            default=BACKUP_DIR / f"Efimeries_Backup_{sy}_{datetime.now().strftime('%Y%m%d_%H%M')}.efbackup"
            path=filedialog.asksaveasfilename(defaultextension=".efbackup", initialfile=default.name,
                                              filetypes=[("Efimeries Backup","*.efbackup"),("ZIP","*.zip")])
            if not path: return
            self._write_backup_zip(path, sy)
            messagebox.showinfo("Πλήρες Backup", f"Δημιουργήθηκε:\n{path}")
        except Exception as e:
            messagebox.showerror("Πλήρες Backup", str(e))

    def import_full_backup(self):
        path=filedialog.askopenfilename(filetypes=[("Efimeries Backup","*.efbackup *.zip"),("All files","*.*")])
        if not path: return
        try:
            with zipfile.ZipFile(path,"r") as z:
                manifest=json.loads(z.read("manifest.json").decode("utf-8"))
                if manifest.get("format")!="efimeries-portable-backup":
                    raise ValueError("Το αρχείο δεν είναι έγκυρο backup της εφαρμογής.")
                sy=normalize_school_year(manifest.get("school_year",""))
                db_names=[n for n in z.namelist() if n.lower().endswith(".db")]
                if not db_names: raise ValueError("Δεν βρέθηκε βάση δεδομένων μέσα στο backup.")
                target=year_db_path(sy)
                exists=target.exists()
                if exists and not messagebox.askyesno("Επαναφορά Backup", f"Το σχολικό έτος {sy} υπάρχει ήδη.\n\nΝα αντικατασταθεί ΟΛΟ με το backup;"):
                    return
                data=z.read(db_names[0])
            problem=validate_backup_db(data)
            if problem:
                raise ValueError(problem)
            if version_tuple(manifest.get("app_version","0")) > version_tuple(APP_VERSION):
                if not messagebox.askyesno("Επαναφορά Backup", f"Το backup δημιουργήθηκε από νεότερη έκδοση ({manifest.get('app_version')}) από αυτή που τρέχει ({APP_VERSION}). Μπορεί να μην λειτουργεί σωστά.\n\nΝα συνεχιστεί;"):
                    return
            self.auto_backup("before-restore")
            if sy==self.active_school_year:
                self.db.close()
                tmp=target.with_suffix(".restore.tmp")
                tmp.write_bytes(data); os.replace(tmp,target)
                self.db=DB(target,sy)
                self.sync_school_year_fields(); self.refresh_all()
            else:
                target.parent.mkdir(exist_ok=True)
                tmp=target.with_suffix(".restore.tmp")
                tmp.write_bytes(data); os.replace(tmp,target)
                # Άνοιγμα της εισαγόμενης χρονιάς για να εφαρμοστούν τυχόν migrations.
                chk=DB(target,sy); chk.close()
                if messagebox.askyesno("Επαναφορά Backup", f"Το {sy} επαναφέρθηκε. Να ανοίξει τώρα;"):
                    self.switch_school_year(sy,show_message=False)
                else:
                    self.refresh_school_year_selector()
            messagebox.showinfo("Επαναφορά Backup", f"Η επαναφορά του σχολικού έτους {sy} ολοκληρώθηκε.")
        except Exception as e:
            # Αν είχε κλείσει η τρέχουσα βάση και κάτι απέτυχε, επιχειρούμε επανασύνδεση.
            try:
                self.db.conn.execute("SELECT 1")
            except Exception:
                try: self.db=DB(year_db_path(self.active_school_year),self.active_school_year)
                except Exception: pass
            messagebox.showerror("Επαναφορά Backup", str(e))

    def export_teachers_csv(self):
        path = EXPORT_DIR / f"teachers_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        rows = self.db.teachers()
        out = []
        for t in rows:
            out.append((
                t.name, self.yesno(t.participates), self.yesno(t.active),
                self.yesno(t.mon), self.yesno(t.tue), self.yesno(t.wed), self.yesno(t.thu), self.yesno(t.fri),
                self.day_name(t.primary_day), self.day_name(t.alternate_day),
                " | ".join(self.day_name(wd) for wd in self.db.teacher_alternate_days(t.id, t.alternate_day)),
                self.yesno(t.extra_cycle), t.notes,
            ))
        self.write_csv(path, ["Ονοματεπώνυμο","Συμμετέχει","Ενεργός","Δευτέρα","Τρίτη","Τετάρτη","Πέμπτη","Παρασκευή","Κύρια ημέρα","Εναλλακτική ημέρα","Εναλλακτικές ημέρες","Πρόσθετος κύκλος","Παρατηρήσεις"], out)
        messagebox.showinfo("Εξαγωγή", f"Αποθηκεύτηκε:\n{path}")


    def export_schedule_csv(self):
        path = EXPORT_DIR / f"program_{self.db.get_setting('school_year')}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        rows = self.db.conn.execute("SELECT a.d,a.weekday,a.interval_name,a.start_time,a.end_time,a.area_name,t.name,a.minutes,a.mode,a.locked,a.entry_type FROM assignments a JOIN teachers t ON t.id=a.teacher_id ORDER BY a.d,a.start_time,a.area_name").fetchall()
        out = []
        for r in rows:
            status = "Πραγματοποιημένο" if r[10] == "manual_completed" else ("Κλειδωμένο" if r[9] else "Μελλοντικό")
            out.append((r[0], fmt_date(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], int(r[9]), r[10], status))
        self.write_csv(path, ["date_iso","Ημερομηνία","Ημέρα","Χρόνος","Από","Έως","Τομέας","Εκπαιδευτικός","Λεπτά","Τρόπος","locked","entry_type","Κατάσταση"], out)
        messagebox.showinfo("Εξαγωγή", f"Αποθηκεύτηκε:\n{path}")

    def export_report_csv(self):
        d1 = parse_date(self.report_from_e.get()).isoformat(); d2 = parse_date(self.report_to_e.get()).isoformat()
        path = EXPORT_DIR / f"report_{self.db.get_setting('school_year')}_{d1}_to_{d2}.csv"
        rows = self.db.conn.execute("""
            SELECT t.name, COUNT(a.id) AS assignments, COALESCE(SUM(a.minutes),0) AS minutes
            FROM teachers t LEFT JOIN assignments a ON a.teacher_id=t.id AND a.d BETWEEN ? AND ?
            WHERE t.active=1 AND t.participates=1 GROUP BY t.id,t.name ORDER BY minutes,assignments,t.name
        """, (d1,d2)).fetchall()
        self.write_csv(path, ["Εκπαιδευτικός","Αναθέσεις","Λεπτά"], rows)
        messagebox.showinfo("Εξαγωγή", f"Αποθηκεύτηκε:\n{path}")


    def week_bounds_for(self, d: date) -> Tuple[date, date]:
        start = d - timedelta(days=d.weekday())
        return start, start + timedelta(days=4)

    def ask_week_date(self) -> Optional[date]:
        try:
            default = parse_date(self.db.get_setting("start_date"))
        except Exception:
            default = date.today()
        return self.calendar_dialog(default, "Επιλογή εβδομάδας εκτύπωσης")

    def weekly_rows(self, d: date):
        w1, w2 = self.week_bounds_for(d)
        rows = [dict(r) for r in self.db.conn.execute(
            """
            SELECT a.d,a.weekday,a.interval_name,a.start_time,a.end_time,COALESCE(ar.name,a.area_name) AS area_name,t.name AS teacher,a.minutes,a.mode
            FROM assignments a JOIN teachers t ON t.id=a.teacher_id LEFT JOIN areas ar ON ar.id=a.area_id
            WHERE a.d BETWEEN ? AND ?
            ORDER BY a.d,a.start_time,a.area_name
            """,
            (w1.isoformat(), w2.isoformat()),
        ).fetchall()]
        reserves=self.db.conn.execute("""SELECT sr.d,sh.name AS interval_name,t.name AS teacher FROM shift_reserves sr JOIN shifts sh ON sh.id=sr.shift_id JOIN teachers t ON t.id=sr.teacher_id WHERE sr.d BETWEEN ? AND ? ORDER BY sr.d,sh.sort_order""",(w1.isoformat(),w2.isoformat())).fetchall()
        for rr in reserves:
            rows.append({"d":rr["d"],"weekday":DAYS_GR[parse_date(rr["d"]).weekday()],"interval_name":rr["interval_name"],"start_time":"","end_time":"","area_name":"Αναπληρωματικός","teacher":rr["teacher"],"minutes":0,"mode":"shifts"})
        rows.sort(key=lambda r:(r["d"],r["interval_name"],r["area_name"]))
        return w1, w2, rows

    def export_weekly_program_csv(self):
        d = self.ask_week_date()
        if not d:
            return
        w1, w2, rows = self.weekly_rows(d)
        path = EXPORT_DIR / f"program_week_{self.db.get_setting('school_year')}_{w1.isoformat()}_to_{w2.isoformat()}.csv"
        out = [(fmt_date(r["d"]), r["weekday"], r["interval_name"], r["start_time"], r["end_time"], r["area_name"], r["teacher"], r["minutes"], {"daily":"Ημέρα","breaks":"Διάλειμμα","shifts":"Βάρδια"}.get(r["mode"],r["mode"])) for r in rows]
        self.write_csv(path, ["Ημερομηνία","Ημέρα","Χρόνος","Από","Έως","Τομέας","Εκπαιδευτικός","Λεπτά","Τρόπος"], out)
        messagebox.showinfo("Εξαγωγή", f"Αποθηκεύτηκε:\n{path}")

    # ---- εκτύπωση: βοηθητικά
    AREA_PALETTE = ("#D4A017", "#2F6F73", "#8E5EA2", "#C05621", "#2B6CB0", "#4A9D6B", "#B83280", "#718096", "#A0522D", "#2C7A7B")

    def _area_color(self, area_name: str) -> str:
        try:
            order = [r["name"] for r in self.db.areas(False)]
            if area_name in order:
                return self.AREA_PALETTE[order.index(area_name) % len(self.AREA_PALETTE)]
        except Exception:
            pass
        return self.AREA_PALETTE[0]

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

    def week_grid_html(self, w1: date, rows) -> str:
        """Δημιουργεί HTML εβδομαδιαίου πλέγματος Δευτέρα-Παρασκευή."""
        by_day = {}
        for r in rows:
            by_day.setdefault(r["d"], []).append(r)

        body = []
        body.append("<table class='week-grid'>")
        body.append("<thead><tr>")
        for i in range(5):
            day = w1 + timedelta(days=i)
            body.append(f"<th>{DAYS_GR[i]}<br><span>{fmt_date(day)}</span></th>")
        body.append("</tr></thead><tbody><tr>")
        for i in range(5):
            day = w1 + timedelta(days=i)
            day_key = day.isoformat()
            day_rows = by_day.get(day_key, [])
            note_css, note_txt = self._print_day_note(day) if not day_rows else ("", "")
            body.append("<td class='day-cell off-cell'>" if note_css == "off-day" else "<td class='day-cell'>")
            if not day_rows:
                body.append(f"<div class='{note_css}'>{html.escape(note_txt)}</div>")
            else:
                has_interval_mode = any(r["mode"] != "daily" for r in day_rows)
                if has_interval_mode:
                    grouped = []
                    seen_intervals = set()
                    for r in day_rows:
                        key = (r["interval_name"], r["start_time"], r["end_time"])
                        if key not in seen_intervals:
                            seen_intervals.add(key)
                            grouped.append((key, []))
                        grouped[-1][1].append(r)
                    for (interval_name, start_time, end_time), interval_rows in grouped:
                        is_departure = "αποχώ" in str(interval_name).lower() or "αποχω" in str(interval_name).lower()
                        rows_to_show = interval_rows[:1] if is_departure else interval_rows
                        body.append("<div class='interval-block'>" f"<div class='interval-heading'>{html.escape(interval_name)}</div>")
                        for r in rows_to_show:
                            body.append(
                                "<div class='duty-line duty-line-interval'>"
                                f"<span class='area-label'>{html.escape(r['area_name'])}</span>"
                                f"<span class='teacher-name'>{html.escape(r['teacher'])}</span>"
                                "</div>"
                            )
                        body.append("</div>")
                else:
                    seen_pairs = set()
                    compact_rows = []
                    for r in day_rows:
                        pair = (r["area_name"], r["teacher"])
                        if pair in seen_pairs:
                            continue
                        seen_pairs.add(pair)
                        compact_rows.append(pair)
                    for area_name, teacher in compact_rows:
                        body.append(
                            "<div class='duty-line'>"
                            f"<span class='area-label' style='border-left-color:{self._area_color(area_name)}'>{html.escape(area_name)}</span>"
                            f"<span class='teacher-name'>{html.escape(teacher)}</span>"
                            "</div>"
                        )
            body.append("</td>")
        body.append("</tr></tbody></table>")
        return "".join(body)

    _MONTHS_GR = ["Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος", "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος"]

    def _print_range(self, kind: str, day: Optional[date], month: str, d1: Optional[date], d2: Optional[date]):
        """Επιστρέφει (από, έως) για την επιλογή εκτύπωσης ή None αν δεν είναι έγκυρη."""
        if kind == "week":
            return self.week_bounds_for(day) if day else None
        if kind == "month":
            if month not in self._MONTHS_GR:
                return None
            m = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6][self._MONTHS_GR.index(month)]
            y0 = int(self.db.get_setting("school_year", "2026-2027").split("-")[0])
            y = y0 if m >= 9 else y0 + 1
            a, b = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
            return max(a, parse_date(self.db.get_setting("start_date"))), min(b, parse_date(self.db.get_setting("end_date")))
        return (d1, d2) if d1 and d2 else None

    def print_dialog(self, initial: str = "week", day: Optional[date] = None):
        """Ενιαία εκτύπωση: Εβδομάδα / Μήνας / Περίοδος / Ανά εκπαιδευτικό / Σύνολα, με σύνοψη πριν ανοίξει ο browser."""
        win = self._modal("Εκτύπωση", 640, 600)
        kind = tk.StringVar(value=initial)
        try:
            school_start = parse_date(self.db.get_setting("start_date"))
            school_end = parse_date(self.db.get_setting("end_date"))
        except Exception:
            school_start = school_end = date.today()
        base_day = day or min(max(date.today(), school_start), school_end)
        v_day = tk.StringVar(value=ui_date(base_day.isoformat()))
        _m_first = base_day.replace(day=1)
        _m_last = base_day.replace(day=calendar.monthrange(base_day.year, base_day.month)[1])
        v_from = tk.StringVar(value=ui_date(max(_m_first, school_start).isoformat()))
        v_to = tk.StringVar(value=ui_date(min(_m_last, school_end).isoformat()))
        _mname = MONTHS_GR_ALL[base_day.month - 1]
        v_month = tk.StringVar(value=_mname if _mname in self._MONTHS_GR else self._MONTHS_GR[0])
        names = ["Όλοι οι εκπαιδευτικοί"] + [t.name for t in self.db.teachers() if t.active and t.participates]
        v_teacher = tk.StringVar(value=names[0])
        outer = ttk.Frame(win, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Τι θέλεις να εκτυπώσεις;", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Θα ανοίξει σελίδα στον browser. Από εκεί: Εκτύπωση ή «Αποθήκευση ως PDF».", foreground="#5F6B76").pack(anchor="w", pady=(2, 10))
        radios = ttk.Frame(outer)
        radios.pack(fill="x")
        fields = ttk.LabelFrame(outer, text="Επιλογές", padding=10)
        fields.pack(fill="x", pady=(10, 8))
        rows = {}

        def line(key, label, builder):
            fr = ttk.Frame(fields)
            ttk.Label(fr, text=label, width=22).pack(side="left")
            builder(fr)
            rows[key] = fr

        def b_day(fr):
            e = ttk.Entry(fr, textvariable=v_day, width=14); e.pack(side="left")
            self._cal_button(fr, e).pack(side="left", padx=4)
            ttk.Label(fr, text="(εκτυπώνεται όλη η εβδομάδα)", foreground="#7A858C").pack(side="left", padx=6)
        def b_month(fr):
            ttk.Combobox(fr, textvariable=v_month, values=self._MONTHS_GR, state="readonly", width=16).pack(side="left")
        def b_from(fr):
            e = ttk.Entry(fr, textvariable=v_from, width=14); e.pack(side="left")
            self._cal_button(fr, e).pack(side="left", padx=4)
        def b_to(fr):
            e = ttk.Entry(fr, textvariable=v_to, width=14); e.pack(side="left")
            self._cal_button(fr, e).pack(side="left", padx=4)
        def b_teacher(fr):
            ttk.Combobox(fr, textvariable=v_teacher, values=names, state="readonly", width=34).pack(side="left")
        line("day", "Μια ημέρα της εβδομάδας", b_day)
        line("month", "Μήνας", b_month)
        def b_quick(fr):
            def setr(a, b):
                v_from.set(ui_date(max(a, school_start).isoformat()))
                v_to.set(ui_date(min(b, school_end).isoformat()))
            def month_of(off):
                y, m = base_day.year, base_day.month + off
                y += (m - 1) // 12
                m = (m - 1) % 12 + 1
                return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
            ttk.Button(fr, text="Αυτός ο μήνας", command=lambda: setr(*month_of(0))).pack(side="left", padx=(0, 6))
            ttk.Button(fr, text="Επόμενος μήνας", command=lambda: setr(*month_of(1))).pack(side="left", padx=(0, 6))
            ttk.Button(fr, text="Από σήμερα έως το τέλος", command=lambda: setr(base_day, school_end)).pack(side="left", padx=(0, 6))
            ttk.Button(fr, text="Όλο το έτος", command=lambda: setr(school_start, school_end)).pack(side="left")
        line("quick", "Γρήγορη επιλογή", b_quick)
        line("from", "Από", b_from)
        line("to", "Έως", b_to)
        line("teacher", "Εκπαιδευτικός", b_teacher)
        summary = ttk.Label(outer, text="", wraplength=590, justify="left", foreground="#34495E")
        summary.pack(anchor="w", pady=(4, 0))

        def current():
            k = kind.get()
            def pd(v):
                try:
                    return parse_date(v.get())
                except Exception:
                    return None
            return k, self._print_range(k, pd(v_day), v_month.get(), pd(v_from), pd(v_to))

        def refresh(*_a):
            k = kind.get()
            show = {"week": ["day"], "month": ["month"], "period": ["quick", "from", "to"], "teacher": ["teacher", "quick", "from", "to"], "totals": ["quick", "from", "to"]}[k]
            for key, fr in rows.items():
                fr.pack_forget()
            for key in ("day", "month", "teacher", "quick", "from", "to"):
                if key in show:
                    rows[key].pack(fill="x", pady=2)
            _k, rng = current()
            if not rng or rng[1] < rng[0]:
                summary.config(text="Συμπλήρωσε έγκυρες ημερομηνίες.")
                return
            a, b = rng
            q = "SELECT COUNT(*), COUNT(DISTINCT d), COUNT(DISTINCT teacher_id) FROM assignments WHERE d BETWEEN ? AND ?"
            args = [a.isoformat(), b.isoformat()]
            who = ""
            if k == "teacher" and v_teacher.get() != names[0]:
                q += " AND teacher_id IN (SELECT id FROM teachers WHERE name=?)"
                args.append(v_teacher.get())
                who = f" για τον/την {v_teacher.get()}"
            n, nd, nt = self.db.conn.execute(q, args).fetchone()
            if n == 0:
                summary.config(text=f"Περίοδος {fmt_date(a)} – {fmt_date(b)}{who}: δεν υπάρχουν εφημερίες. Δεν θα εκτυπωθεί τίποτα.")
            else:
                summary.config(text=f"Θα εκτυπωθεί η περίοδος {fmt_date(a)} – {fmt_date(b)}{who}: {n} εφημερίες σε {nd} ημέρες, {nt} εκπαιδευτικοί.")
        self._print_dialog_refresh = refresh
        for text, val in (("Εβδομάδα", "week"), ("Μήνας", "month"), ("Περίοδος", "period"), ("Ανά εκπαιδευτικό", "teacher"), ("Σύνολα", "totals")):
            ttk.Radiobutton(radios, text=text, variable=kind, value=val).pack(side="left", padx=(0, 18))
        for v in (kind, v_day, v_month, v_from, v_to, v_teacher):
            v.trace_add("write", refresh)

        def go():
            k, rng = current()
            if not rng or rng[1] < rng[0]:
                messagebox.showwarning("Εκτύπωση", "Έλεγξε τις ημερομηνίες.", parent=win)
                return
            a, b = rng
            win.destroy()
            if k == "week":
                self.ask_week_date = lambda: a
                try:
                    self.print_weekly_program()
                finally:
                    del self.ask_week_date
                return
            if k == "month":
                self.report_month_cb.set(v_month.get())
                self.print_month_program()
                return
            for e, val in ((self.report_from_e, a), (self.report_to_e, b)):
                e.delete(0, "end"); e.insert(0, ui_date(val.isoformat()))
            if k == "period":
                self.print_range_program()
            elif k == "teacher":
                self.report_teacher_cb.set(v_teacher.get())
                self.print_teacher_program()
            else:
                self.print_totals_report()
        foot = ttk.Frame(outer)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(foot, text="Άνοιγμα για εκτύπωση", style="Accent.TButton", command=go).pack(side="right")
        ttk.Button(foot, text="Άκυρο", command=win.destroy).pack(side="right", padx=8)
        self._print_dialog_win = win
        refresh()
        return win

    def _export_menubutton(self, parent) -> ttk.Menubutton:
        mb = ttk.Menubutton(parent, text="Εξαγωγή ▾")
        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Excel (.xlsx) – πρόγραμμα, σύνολα, απουσίες", command=self.export_xlsx)
        m.add_command(label="Όλο το πρόγραμμα σε CSV", command=self.export_schedule_csv)
        m.add_command(label="Εβδομάδα σε CSV", command=self.export_weekly_program_csv)
        m.add_command(label="Αναφορά συνόλων σε CSV", command=self.export_report_csv)
        m.add_separator()
        m.add_command(label="Ημερολόγιο εκπαιδευτικού (.ics)…", command=self.export_teacher_ics)
        mb["menu"] = m
        return mb

    def print_weekly_program(self):
        d = self.ask_week_date()
        if not d:
            return
        w1, w2, rows = self.weekly_rows(d)
        if not rows:
            messagebox.showwarning("Εκτύπωση", "Δεν υπάρχουν αναθέσεις για αυτή την εβδομάδα.")
            return

        # Η ημερομηνία που δίνεται χρησιμοποιείται μόνο για να εντοπιστεί η εβδομάδα.
        # Η εκτύπωση εμφανίζει πάντα όλη την εβδομάδα: Δευτέρα έως Παρασκευή.
        title = f"Πρόγραμμα εφημερίας εβδομάδας {fmt_date(w1)} – {fmt_date(w2)}"
        body = self.week_grid_html(w1, rows)

        path = EXPORT_DIR / f"print_week_{self.db.get_setting('school_year')}_{w1.isoformat()}_to_{w2.isoformat()}.html"
        self.write_print_html(path, title, body, landscape=True)
        webbrowser.open(path.as_uri())
        messagebox.showinfo("Εκτύπωση", f"Δημιουργήθηκε αρχείο εκτύπωσης:\n{path}\n\nΆνοιξε στον browser. Από εκεί επιλέγεις Εκτύπωση ή Αποθήκευση ως PDF.")

    def program_rows_range(self, start_date: date, end_date: date):
        rows=[dict(r) for r in self.db.conn.execute(
            """SELECT a.d,a.weekday,a.interval_name,a.start_time,a.end_time,COALESCE(ar.name,a.area_name) AS area_name,t.name AS teacher,a.minutes,a.mode
               FROM assignments a JOIN teachers t ON t.id=a.teacher_id LEFT JOIN areas ar ON ar.id=a.area_id
               WHERE a.d BETWEEN ? AND ? ORDER BY a.d,a.start_time,a.area_name""",
            (start_date.isoformat(),end_date.isoformat())).fetchall()]
        for rr in self.db.conn.execute("""SELECT sr.d,sh.name AS interval_name,t.name AS teacher FROM shift_reserves sr JOIN shifts sh ON sh.id=sr.shift_id JOIN teachers t ON t.id=sr.teacher_id WHERE sr.d BETWEEN ? AND ? ORDER BY sr.d,sh.sort_order""",(start_date.isoformat(),end_date.isoformat())).fetchall():
            rows.append({"d":rr["d"],"weekday":DAYS_GR[parse_date(rr["d"]).weekday()],"interval_name":rr["interval_name"],"start_time":"","end_time":"","area_name":"Αναπληρωματικός","teacher":rr["teacher"],"minutes":0,"mode":"shifts"})
        rows.sort(key=lambda r:(r["d"],r["interval_name"],r["area_name"]))
        return rows

    def print_month_program(self):
        month_names = ["Σεπτέμβριος","Οκτώβριος","Νοέμβριος","Δεκέμβριος","Ιανουάριος","Φεβρουάριος","Μάρτιος","Απρίλιος","Μάιος","Ιούνιος"]
        month_numbers = [9,10,11,12,1,2,3,4,5,6]
        name = self.report_month_cb.get()
        if name not in month_names:
            messagebox.showwarning("Μήνας", "Επίλεξε πρώτα μήνα από το πεδίο «ή μήνας».")
            return
        sy = self.db.get_setting("school_year", "2026-2027")
        start_year = int(sy.split("-")[0])
        m = month_numbers[month_names.index(name)]
        y = start_year if m >= 9 else start_year + 1
        start_date = date(y, m, 1)
        end_date = date(y, m, calendar.monthrange(y, m)[1])
        start_limit = parse_date(self.db.get_setting("start_date"))
        end_limit = parse_date(self.db.get_setting("end_date"))
        start_date = max(start_date, start_limit)
        end_date = min(end_date, end_limit)
        if end_date < start_date:
            messagebox.showwarning("Εκτύπωση", "Ο επιλεγμένος μήνας είναι εκτός του ενεργού σχολικού έτους.")
            return
        rows = self.program_rows_range(start_date, end_date)
        if not rows:
            messagebox.showwarning("Εκτύπωση", "Δεν υπάρχουν αναθέσεις για τον επιλεγμένο μήνα.")
            return
        body_parts = []
        cur = start_date - timedelta(days=start_date.weekday())
        while cur <= end_date:
            w1 = cur; w2 = cur + timedelta(days=4)
            week_rows = [r for r in rows if w1.isoformat() <= r["d"] <= w2.isoformat()]
            if week_rows:
                body_parts.append("<section class='week-print-block'>")
                body_parts.append(f"<h2>Εβδομάδα {fmt_date(w1)} – {fmt_date(w2)}</h2>")
                body_parts.append(self.week_grid_html(w1, week_rows))
                body_parts.append("</section>")
            cur += timedelta(days=7)
        title = f"Πρόγραμμα εφημερίας – {name} {y}"
        path = EXPORT_DIR / f"print_month_{self.db.get_setting('school_year')}_{y}_{m:02d}.html"
        body_parts.append(self.done_totals_html(start_date.isoformat(), end_date.isoformat()))
        self.write_print_html(path, title, "".join(body_parts), landscape=True)
        webbrowser.open(path.as_uri())
        messagebox.showinfo("Εκτύπωση", f"Δημιουργήθηκε αρχείο εκτύπωσης:\n{path}")

    def _teacher_print_section(self, teacher_name: str, start_date: date, end_date: date) -> tuple[str, str]:
        rows = self.db.conn.execute(
            """
            SELECT a.d,a.weekday,a.interval_name,a.start_time,a.end_time,COALESCE(ar.name,a.area_name) AS area_name,a.minutes,a.mode
            FROM assignments a JOIN teachers t ON t.id=a.teacher_id LEFT JOIN areas ar ON ar.id=a.area_id
            WHERE t.name=? AND a.d BETWEEN ? AND ?
            ORDER BY a.d,a.start_time,a.area_name
            """, (teacher_name, start_date.isoformat(), end_date.isoformat())
        ).fetchall()
        if not rows:
            return "", ""
        mode = "shifts" if any(r["mode"] == "shifts" for r in rows) else ("breaks" if any(r["mode"] == "breaks" for r in rows) else "daily")
        trs = []
        if mode == "daily":
            seen = set()
            for r in rows:
                key=(r["d"],r["area_name"])
                if key in seen:
                    continue
                seen.add(key)
                trs.append(f"<tr><td>{fmt_date(r['d'])}</td><td>{html.escape(r['weekday'])}</td><td>{html.escape(r['area_name'])}</td></tr>")
            body = (
                f"<div class='summary-note'>Σύνολο ημερών εφημερίας: <strong>{len(set(r['d'] for r in rows))}</strong></div>"
                "<table><thead><tr><th>Ημερομηνία</th><th>Ημέρα</th><th>Τομέας</th></tr></thead><tbody>"
                + "".join(trs) + "</tbody></table>"
            )
        elif mode == "shifts":
            for r in rows:
                trs.append(f"<tr><td>{fmt_date(r['d'])}</td><td>{html.escape(r['weekday'])}</td><td>{html.escape(r['interval_name'])}</td><td>{html.escape(r['area_name'])}</td></tr>")
            body=(f"<div class='summary-note'>Σύνολο πραγματικών αναθέσεων βάρδιας: <strong>{len(rows)}</strong></div>"+"<table><thead><tr><th>Ημερομηνία</th><th>Ημέρα</th><th>Βάρδια</th><th>Τομέας</th></tr></thead><tbody>"+"".join(trs)+"</tbody></table>")
        else:
            total_minutes = 0
            for r in rows:
                total_minutes += int(r["minutes"] or 0)
                trs.append(
                    f"<tr><td>{fmt_date(r['d'])}</td><td>{html.escape(r['weekday'])}</td>"
                    f"<td>{html.escape(r['interval_name'])}</td><td>{html.escape(r['start_time'])}–{html.escape(r['end_time'])}</td>"
                    f"<td>{html.escape(r['area_name'])}</td><td class='num'>{int(r['minutes'] or 0)}</td></tr>"
                )
            body = (
                f"<div class='summary-note'>Συνολικός χρόνος εφημερίας: <strong>{total_minutes}΄</strong></div>"
                "<table><thead><tr><th>Ημερομηνία</th><th>Ημέρα</th><th>Χρονική ζώνη</th><th>Ώρα</th><th>Τομέας</th><th>Λεπτά</th></tr></thead><tbody>"
                + "".join(trs) + "</tbody></table>"
            )
        return body, mode

    def print_teacher_program(self):
        teacher_name = self.report_teacher_cb.get().strip() if hasattr(self, "report_teacher_cb") else ""
        if not teacher_name:
            messagebox.showwarning("Εκπαιδευτικός", "Επίλεξε εκπαιδευτικό ή «Όλοι οι εκπαιδευτικοί».")
            return
        try:
            start_date = parse_date(self.report_from_e.get())
            end_date = parse_date(self.report_to_e.get())
            if end_date < start_date:
                raise ValueError("Η ημερομηνία «Έως» πρέπει να είναι ίδια ή μεταγενέστερη από την «Από».")
        except Exception as e:
            messagebox.showerror("Σφάλμα", str(e)); return

        if teacher_name == "Όλοι οι εκπαιδευτικοί":
            names = [t.name for t in self.db.teachers() if t.active and t.participates]
            sections = []
            any_breaks = False
            for name in names:
                body, mode = self._teacher_print_section(name, start_date, end_date)
                if not body:
                    continue
                any_breaks = any_breaks or mode == "breaks"
                sections.append(
                    "<section class='teacher-print-block'>"
                    f"<h2>{html.escape(name)}</h2>" + body + "</section>"
                )
            if not sections:
                messagebox.showwarning("Εκτύπωση", "Δεν υπάρχουν εφημερίες για τους εκπαιδευτικούς στο επιλεγμένο διάστημα.")
                return
            title = f"Εφημερίες ανά εκπαιδευτικό ({fmt_date(start_date)} – {fmt_date(end_date)})"
            path = EXPORT_DIR / f"print_all_teachers_{start_date.isoformat()}_to_{end_date.isoformat()}.html"
            self.write_print_html(path, title, "".join(sections), landscape=any_breaks)
        else:
            body, mode = self._teacher_print_section(teacher_name, start_date, end_date)
            if not body:
                messagebox.showwarning("Εκτύπωση", "Δεν υπάρχουν εφημερίες για τον εκπαιδευτικό στο επιλεγμένο διάστημα.")
                return
            title = f"Εφημερίες εκπαιδευτικού – {teacher_name} ({fmt_date(start_date)} – {fmt_date(end_date)})"
            safe_name = "".join(ch if ch.isalnum() else "_" for ch in teacher_name)[:50]
            path = EXPORT_DIR / f"print_teacher_{safe_name}_{start_date.isoformat()}_to_{end_date.isoformat()}.html"
            self.write_print_html(path, title, body, landscape=(mode in ("breaks","shifts")))
        webbrowser.open(path.as_uri())
        messagebox.showinfo("Εκτύπωση", f"Δημιουργήθηκε αρχείο εκτύπωσης:\n{path}")

    def print_range_program(self):
        try:
            start_date = parse_date(self.report_from_e.get())
            end_date = parse_date(self.report_to_e.get())
        except Exception as e:
            messagebox.showerror("Σφάλμα", str(e)); return
        d1 = start_date.isoformat()
        d2 = end_date.isoformat()
        rows = self.program_rows_range(start_date, end_date)
        if not rows:
            messagebox.showwarning("Εκτύπωση", "Δεν υπάρχουν αναθέσεις για αυτό το διάστημα."); return
        title = f"Πρόγραμμα εφημερίας {fmt_date(d1)} – {fmt_date(d2)}"

        body_parts = []
        # Εμφάνιση διαστήματος με την ίδια λογική της εβδομαδιαίας εκτύπωσης,
        # ώστε να είναι άμεσα κατανοητό από τον Σύλλογο.
        first_monday = start_date - timedelta(days=start_date.weekday())
        cur = first_monday
        while cur <= end_date:
            w1 = cur
            w2 = cur + timedelta(days=4)
            week_rows = [r for r in rows if w1.isoformat() <= r["d"] <= w2.isoformat()]
            if week_rows:
                body_parts.append("<section class='week-print-block'>")
                body_parts.append(f"<h2>Εβδομάδα {fmt_date(w1)} – {fmt_date(w2)}</h2>")
                body_parts.append(self.week_grid_html(w1, week_rows))
                body_parts.append("</section>")
            cur += timedelta(days=7)

        path = EXPORT_DIR / f"print_range_{self.db.get_setting('school_year')}_{d1}_to_{d2}.html"
        body_parts.append(self.done_totals_html(start_date.isoformat(), end_date.isoformat()))
        self.write_print_html(path, title, "".join(body_parts), landscape=True)
        webbrowser.open(path.as_uri())
        messagebox.showinfo("Εκτύπωση", f"Δημιουργήθηκε αρχείο εκτύπωσης:\n{path}")

    def done_totals_html(self, d1: str, d2: str) -> str:
        """Πίνακας στο τέλος των εκτυπώσεων: πλήθος πραγματοποιημένων εφημεριών ανά εκπαιδευτικό (μόνο το σύνολο)."""
        today = date.today().isoformat()
        mode = self.report_mode_for_range(d1, d2)
        expr = "COUNT(DISTINCT a.d)" if mode == "daily" else "COUNT(a.id)"
        rows = self.db.conn.execute(
            f"""SELECT t.name, {expr} AS n FROM teachers t
                LEFT JOIN assignments a ON a.teacher_id=t.id AND a.d BETWEEN ? AND ? AND (a.locked=1 OR a.d<=?)
                WHERE t.active=1 AND t.participates=1 GROUP BY t.id, t.name ORDER BY t.name""",
            (d1, d2, today)).fetchall()
        if not rows:
            return ""
        unit = "Ημέρες εφημερίας" if mode == "daily" else "Εφημερίες"
        trs = "".join(f"<tr><td>{html.escape(r['name'])}</td><td style='text-align:center'>{r['n']}</td></tr>" for r in rows)
        return ("<section style='page-break-inside:avoid;margin-top:14px'>"
                f"<h2>Σύνολο πραγματοποιημένων εφημεριών ({fmt_date(d1)} – {fmt_date(d2)})</h2>"
                f"<table style='border-collapse:collapse;min-width:50%'><tr><th style='text-align:left'>Εκπαιδευτικός</th><th>{unit}</th></tr>{trs}</table>"
                "<p style='font-size:11px;color:#555'>Πραγματοποιημένες: οι κλειδωμένες ή όσες έχουν ήδη περάσει (έως σήμερα).</p></section>")

    def totals_rows(self, d1: str, d2: str):
        return self.db.conn.execute(
            """
            SELECT t.name, COUNT(a.id) AS assignments, COALESCE(SUM(a.minutes),0) AS minutes,
                   COUNT(DISTINCT a.d) AS duty_days
            FROM teachers t
            LEFT JOIN assignments a ON a.teacher_id=t.id AND a.d BETWEEN ? AND ?
            WHERE t.active=1 AND t.participates=1
            GROUP BY t.id,t.name
            ORDER BY t.name
            """,
            (d1, d2),
        ).fetchall()

    def report_mode_for_range(self, d1: str, d2: str) -> str:
        row = self.db.conn.execute(
            "SELECT mode, COUNT(*) AS c FROM assignments WHERE d BETWEEN ? AND ? GROUP BY mode ORDER BY c DESC LIMIT 1",
            (d1, d2),
        ).fetchone()
        if row and row["mode"]:
            return row["mode"]
        return self.db.get_setting("mode", "daily")

    def print_totals_report(self):
        try:
            d1 = parse_date(self.report_from_e.get()).isoformat()
            d2 = parse_date(self.report_to_e.get()).isoformat()
        except Exception as e:
            messagebox.showerror("Σφάλμα", str(e))
            return
        rows = self.totals_rows(d1, d2)
        mode = self.report_mode_for_range(d1, d2)
        title = f"Σύνολα εφημεριών ανά εκπαιδευτικό ({fmt_date(d1)} – {fmt_date(d2)})"
        trs = []
        if mode == "daily":
            total_days = 0
            for idx, r in enumerate(sorted(rows, key=lambda x: (int(x["duty_days"] or 0), x["name"])), start=1):
                total_days += int(r["duty_days"] or 0)
                trs.append(
                    "<tr>"
                    f"<td class='num'>{idx}</td>"
                    f"<td>{html.escape(r['name'])}</td>"
                    f"<td class='num'>{r['duty_days']}</td>"
                    "</tr>"
                )
            body = (
                "<table><thead><tr>"
                "<th>Α/Α</th><th>Εκπαιδευτικός</th><th>Σύνολο ημερών</th>"
                "</tr></thead><tbody>"
                + "".join(trs)
                + f"</tbody><tfoot><tr><td colspan='2'>Σύνολο</td><td class='num'>{total_days}</td></tr></tfoot></table>"
            )
        elif mode == "shifts":
            total_assignments=0
            for idx,r in enumerate(sorted(rows,key=lambda x:(int(x["assignments"] or 0),x["name"])),start=1):
                n=int(r["assignments"] or 0); total_assignments+=n
                trs.append("<tr>"+f"<td class='num'>{idx}</td>"+f"<td>{html.escape(r['name'])}</td>"+f"<td class='num'>{n}</td>"+"</tr>")
            body=("<table><thead><tr><th>Α/Α</th><th>Εκπαιδευτικός</th><th>Σύνολο βαρδιών</th></tr></thead><tbody>"+"".join(trs)+f"</tbody><tfoot><tr><td colspan='2'>Σύνολο</td><td class='num'>{total_assignments}</td></tr></tfoot></table>")
        else:
            total_minutes = 0
            values = [int(r["minutes"] or 0) for r in rows]
            spread = (max(values) - min(values)) if values else 0
            for idx, r in enumerate(sorted(rows, key=lambda x: (int(x["minutes"] or 0), x["name"])), start=1):
                mins = int(r["minutes"] or 0)
                total_minutes += mins
                trs.append(
                    "<tr>"
                    f"<td class='num'>{idx}</td>"
                    f"<td>{html.escape(r['name'])}</td>"
                    f"<td class='num'>{mins}</td>"
                    "</tr>"
                )
            body = (
                f"<div class='summary-note'>Απόκλιση λεπτών μεταξύ μικρότερης και μεγαλύτερης επιβάρυνσης: <strong>{spread}΄</strong></div>"
                "<table><thead><tr>"
                "<th>Α/Α</th><th>Εκπαιδευτικός</th><th>Σύνολο λεπτών</th>"
                "</tr></thead><tbody>"
                + "".join(trs)
                + f"</tbody><tfoot><tr><td colspan='2'>Σύνολο</td><td class='num'>{total_minutes}</td></tr></tfoot></table>"
            )
        path = EXPORT_DIR / f"print_totals_{self.db.get_setting('school_year')}_{d1}_to_{d2}.html"
        self.write_print_html(path, title, body, landscape=False)
        webbrowser.open(path.as_uri())
        messagebox.showinfo("Εκτύπωση", f"Δημιουργήθηκε αρχείο εκτύπωσης:\n{path}\n\nΆνοιξε στον browser. Από εκεί επιλέγεις Εκτύπωση ή Αποθήκευση ως PDF.")

    def write_print_html(self, path: Path, title: str, body: str, landscape: bool = False) -> None:
        EXPORT_DIR.mkdir(exist_ok=True)
        orientation = "landscape" if landscape else "portrait"
        school_year = html.escape(self.db.get_setting("school_year"))
        school_name = html.escape(self.db.get_setting("school_name", "").strip() or "Σχολείο")
        school_phone = html.escape(self.db.get_setting("school_phone", "").strip())
        school_email = html.escape(self.db.get_setting("school_email", "").strip())
        contact_bits = [x for x in (school_phone, school_email) if x]
        contact_line = " · ".join(contact_bits)
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        size = self.db.get_setting("print_size", "normal")
        profiles = {
            "compact": {"body":"9.3px","school":"14px","h1":"16px","h2":"12px","cell":"2px 3px","teacher":"9.5px","interval":"9.2px"},
            "normal": {"body":"10.5px","school":"16px","h1":"19px","h2":"13.5px","cell":"3px 4px","teacher":"10.8px","interval":"10.3px"},
            "large": {"body":"11.6px","school":"17px","h1":"20px","h2":"14.5px","cell":"4px 5px","teacher":"11.6px","interval":"11px"},
        }
        pf = profiles.get(size, profiles["normal"])
        content = f"""<!doctype html>
<html lang="el">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
@page {{ size: A4 {orientation}; margin: 7mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: Arial, 'Segoe UI', sans-serif; color: #222; font-size: {pf['body']}; line-height: 1.22; }}
.header {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #2F6F73; padding-bottom: 7px; margin-bottom: 10px; page-break-after: avoid; break-after: avoid; }}
.school {{ font-weight: 700; color: #2F6F73; font-size: {pf['school']}; }}
.school-contact {{ color:#59666d; font-size: 10px; margin-top:2px; }}
h1 {{ font-size: {pf['h1']}; margin: 2px 0 3px 0; color: #34495E; }}
h2 {{ font-size: {pf['h2']}; margin: 8px 0 4px 0; color: #2F6F73; page-break-after: avoid; break-after: avoid; }}
table {{ width: 100%; border-collapse: collapse; margin-bottom: 7px; page-break-inside: avoid; break-inside: avoid; }}
th, td {{ border: 1px solid #aeb7ba; padding: {pf['cell']}; vertical-align: top; }}
th {{ background: #EEF4F4; color: #263b3d; font-weight: 700; }}
th span {{ font-weight: 400; font-size: 10.5px; }}
.week-print-block {{ page-break-inside: avoid; break-inside: avoid; margin-bottom: 8px; }}
.week-grid {{ table-layout: fixed; margin-bottom: 5px; page-break-inside: avoid; break-inside: avoid; }}
.week-grid th {{ text-align: center; }}
.day-cell {{ width: 20%; }}
.duty-line {{ display: block; border: 1px solid #DDE4E4; border-radius: 3px; margin: 0 0 4px 0; overflow: hidden; background: #fff; }}
.duty-line-interval {{ display: flex; align-items: stretch; }}
.duty-line-interval {{ font-size: {pf['interval']}; }}
.area-label {{ display: block; background: #F7F7F5; border-left: 4px solid #D4A017; padding: 2px 5px; font-weight: 600; font-size: 0.88em; color: #5F6B76; }}
.duty-line-interval .area-label {{ flex: 0 0 36%; font-size: 1em; font-weight: 700; color: #34495E; }}
.duty-line-interval .area-label {{ border-left: 1px solid #E5E5E5; }}
.interval-block {{ margin: 0 0 4px 0; padding: 2px; border: 1px solid #e1e6e6; border-radius: 3px; background: #fbfbfb; }}
.interval-heading {{ font-weight: 800; margin-bottom: 3px; color: #2F6F73; font-size: {pf['interval']}; border-bottom: 1px solid #e5eaea; padding: 1px 2px 2px 2px; }}
.teacher-name {{ display: block; flex: 1; padding: 3px 5px 4px 9px; font-weight: 700; font-size: {pf['teacher']}; }}
.empty-day {{ color: #777; font-style: italic; text-align: center; padding: 16px 4px; }}
.off-cell {{ background: #F1F3F4; }}
.off-day {{ color: #8A949B; text-align: center; padding: 16px 4px; font-size: 0.92em; }}
tfoot td {{ font-weight: 700; background: #F7F7F5; }}
.num {{ text-align: center; white-space: nowrap; }}
.meta {{ color: #555; font-size: 10px; text-align: right; }}
.summary-note {{ margin: 0 0 8px 0; padding: 6px 8px; border-left: 4px solid #D4A017; background: #F7F7F5; }}
.print-note {{ margin-top: 12px; color: #666; font-size: 10px; }}
@media print {{
  .no-print {{ display: none; }}
  .header {{ page-break-after: avoid; break-after: avoid; }}
  .week-print-block {{ page-break-inside: avoid; break-inside: avoid; }}
  h2 + table {{ page-break-before: avoid; break-before: avoid; }}
}}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="school">{school_name}</div>
    {f'<div class="school-contact">{contact_line}</div>' if contact_line else ''}
    <h1>{html.escape(title)}</h1>
    <div>Σχολικό έτος: {school_year}</div>
  </div>
  <div class="meta">Δημιουργία: {now}</div>
</div>
{body}
<div class="print-note no-print">Για εκτύπωση: Ctrl+P ή Αρχείο → Εκτύπωση. Μπορεί να αποθηκευτεί και ως PDF.</div>
<script>window.addEventListener('load', function(){{ setTimeout(function(){{ window.print(); }}, 500); }});</script>
</body>
</html>"""
        path.write_text(content, encoding="utf-8")

    def export_xlsx(self):
        """Εξαγωγή σε Excel: Πρόγραμμα, Σύνολα ανά εκπαιδευτικό και Απουσίες (ένα αρχείο, τρία φύλλα)."""
        year = self.db.get_setting("school_year")
        conn = self.db.conn
        rows = conn.execute(
            "SELECT a.d,a.weekday,a.interval_name,a.start_time,a.end_time,COALESCE(ar.name,a.area_name),t.name,a.minutes,a.mode,a.locked,a.entry_type "
            "FROM assignments a JOIN teachers t ON t.id=a.teacher_id LEFT JOIN areas ar ON ar.id=a.area_id ORDER BY a.d,a.start_time,a.area_name").fetchall()
        if not rows:
            messagebox.showinfo("Εξαγωγή σε Excel", "Δεν υπάρχει πρόγραμμα για εξαγωγή. Δημιουργήστε πρώτα πρόγραμμα.")
            return
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
        path = EXPORT_DIR / f"efimeries_{year}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        try:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            write_xlsx(path, [
                ("Πρόγραμμα", ["Ημερομηνία", "Ημέρα", "Χρόνος", "Από", "Έως", "Τομέας", "Εκπαιδευτικός", "Λεπτά", "Τρόπος", "Κατάσταση"],
                 prog, [13, 12, 18, 8, 8, 18, 30, 8, 12, 18]),
                ("Σύνολα", ["Εκπαιδευτικός", "Αναθέσεις", "Ημέρες εφημερίας", "Λεπτά", "Ώρες", "Απόκλιση ημερών από μέσο όρο"],
                 tot_rows, [32, 11, 17, 9, 9, 28]),
                ("Απουσίες", ["Εκπαιδευτικός", "Από", "Έως", "Ημέρες", "Αιτιολογία"], ab_rows, [32, 13, 13, 9, 34]),
            ])
        except Exception as exc:
            LOG.exception("Αποτυχία εξαγωγής xlsx")
            messagebox.showerror("Εξαγωγή σε Excel", f"Η εξαγωγή απέτυχε:\n{exc}")
            return
        if messagebox.askyesno("Εξαγωγή σε Excel", f"Αποθηκεύτηκε:\n{path}\n\nΝα ανοίξει ο φάκελος εξαγωγών;"):
            self.open_path(EXPORT_DIR)

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
    ("lock_selected_assignment", "κλείδωμα εφημερίας"),
    ("unlock_selected_assignment", "ξεκλείδωμα εφημερίας"),
    ("add_completed_assignment", "προσθήκη πραγματοποιημένης εφημερίας"),
    ("change_teacher_selected", "αλλαγή εκπαιδευτικού"),
    ("lock_completed_until", "κλείδωμα πραγματοποιημένων"),
    ("absence_dialog", "απουσία εκπαιδευτικού"),
    ("_lock_past_assignments", "κλείδωμα παρελθοντικών εφημεριών"),
    ("generate_schedule", "παραγωγή προγράμματος"),
    ("import_schedule_csv", "εισαγωγή προγράμματος από CSV"),
    ("delete_schedule_dialog", "διαγραφή προγράμματος"),
):
    setattr(EfimeriesApp, _name, _undoable(_label)(getattr(EfimeriesApp, _name)))


def main():
    setup_logging()
    sys.excepthook = lambda t, v, tb: LOG.error("Μη χειριζόμενο σφάλμα:\n%s", "".join(traceback.format_exception(t, v, tb)))
    app = EfimeriesApp()
    try:
        if "--web" in sys.argv or app.db.get_setting("ui_mode", "classic") == "web":
            app.withdraw()
            app.open_web_ui(hidden=True)
    except Exception:
        LOG.exception("Εκκίνηση με τη νέα διεπαφή")
        app.deiconify()
    app.mainloop()


if __name__ == "__main__":
    main()
