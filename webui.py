# -*- coding: utf-8 -*-
"""Νέα διεπαφή «Εφημερίες Εκπαιδευτικών» (v0.60): τοπικός web server + σελίδα στον Edge.

Η διεπαφή δουλεύει πάνω στην ΙΔΙΑ βάση και τον ΙΔΙΟ αλγόριθμο με την κλασική (Tkinter) εφαρμογή.
Ο server ακούει μόνο στο 127.0.0.1 (δεν είναι ορατός από το δίκτυο) και ζητά μυστικό διακριτικό (token).
Όλες οι κλήσεις εκτελούνται στο κύριο νήμα του Tk μέσω ουράς, ώστε να μη χρειάζεται κλείδωμα.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

try:
    from webui_assets import INDEX_HTML
except Exception:  # ανάπτυξη: διαβάζει το webui/index.html δίπλα στο αρχείο
    _p = Path(__file__).resolve().parent / "webui" / "index.html"
    INDEX_HTML = _p.read_text(encoding="utf-8") if _p.exists() else "<h1>Λείπει το webui/index.html</h1>"

DAYS_GR = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή"]


class ApiError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


class _Captured:
    """Αντικαθιστά προσωρινά τα messagebox ώστε να μη μπλοκάρει η κλήση σε κρυφό παράθυρο."""

    def __init__(self):
        self.messages: List[str] = []

    def __enter__(self):
        from tkinter import messagebox
        self._mb = messagebox
        self._saved = {k: getattr(messagebox, k) for k in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel", "askquestion")}

        def note(title="", message="", *a, **k):
            self.messages.append(f"{title}: {message}".strip(": "))
            return None

        def yes(title="", message="", *a, **k):
            return True
        for k in ("showinfo", "showwarning", "showerror"):
            setattr(messagebox, k, note)
        for k in ("askyesno", "askokcancel"):
            setattr(messagebox, k, yes)
        messagebox.askquestion = lambda *a, **k: "yes"
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            setattr(self._mb, k, v)
        return False


def find_edge() -> Optional[str]:
    cands = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cands.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for c in cands:
        if c.exists():
            return str(c)
    return shutil.which("msedge")


class WebUI:
    def __init__(self, app, hidden: bool = False):
        self.app = app
        self.mod = sys.modules[type(app).__module__]
        self.hidden = hidden            # True: το παράθυρο Tk είναι κρυφό και η εφαρμογή κλείνει όταν κλείσει ο Edge
        self.token = secrets.token_urlsafe(24)
        self.q: "queue.Queue" = queue.Queue()
        self.last_ping = 0.0
        self.bye_at = 0.0
        self.update_rel: Optional[dict] = None
        self.dl: Dict[str, Any] = {"state": "idle", "done": 0, "total": 0}
        self.direct: Dict[str, Callable] = {"/api/update/check": self.api_update_check, "/api/update/install": self.api_update_install, "/api/update/status": self.api_update_status}
        self.started = time.time()
        self.port = 0
        self._server: Optional[ThreadingHTTPServer] = None
        self._quit = False
        # Χωρίς παράθυρα προόδου Tk στην παραγωγή από τη νέα διεπαφή
        for n in ("_gen_progress_begin", "_gen_progress_end"):
            setattr(app, n, lambda *a, **k: None)
        app._gen_progress_step = lambda *a, **k: None

    # ------------------------------------------------------------------ server
    def start(self) -> str:
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").split(":")[0]
                return host in ("127.0.0.1", "localhost")

            def _send(self, code: int, body: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj: Any):
                self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

            def _token_ok(self, qs: Dict[str, List[str]]) -> bool:
                t = self.headers.get("X-Token") or (qs.get("t") or [""])[0]
                return secrets.compare_digest(t, outer.token)

            def _handle(self, method: str):
                if not self._host_ok():
                    return self._json(403, {"error": "forbidden"})
                u = urlparse(self.path)
                qs = parse_qs(u.query)
                if method == "GET" and u.path == "/":
                    if not self._token_ok(qs):
                        return self._send(403, "Άκυρος σύνδεσμος. Άνοιξε τη νέα διεπαφή από την εφαρμογή.".encode("utf-8"), "text/plain; charset=utf-8")
                    html = INDEX_HTML.replace("__TOKEN__", outer.token)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                if not self._token_ok(qs):
                    return self._json(403, {"error": "forbidden"})
                body: Dict[str, Any] = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    is_raw = u.path.startswith(("/api/backup/", "/api/timetable/")) and (self.headers.get("Content-Type") or "").startswith("application/octet-stream")
                    if n > (80_000_000 if is_raw else 2_000_000):
                        return self._json(413, {"error": "Το αρχείο είναι πολύ μεγάλο"})
                    raw = self.rfile.read(n) if n else b""
                    if is_raw:
                        body = {"_raw": raw}
                    else:
                        try:
                            body = json.loads(raw.decode("utf-8")) if raw else {}
                        except Exception:
                            return self._json(400, {"error": "bad json"})
                params = {k: v[0] for k, v in qs.items()}
                if u.path == "/api/ping":
                    outer.last_ping = time.time()
                    return self._json(200, {"ok": True})
                if u.path == "/api/bye":
                    outer.bye_at = time.time()
                    return self._json(200, {"ok": True})
                try:
                    if u.path in outer.direct:   # δικτυακές κλήσεις: δεν χρειάζονται το νήμα του Tk
                        return self._json(200, outer.direct[u.path](params, body))
                    res = outer.run_on_tk(u.path, method, params, body)
                except ApiError as e:
                    return self._json(e.status, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    return self._json(500, {"error": f"Σφάλμα: {e}"})
                if isinstance(res, tuple) and res and res[0] == "file":
                    from urllib.parse import quote
                    self.send_response(200)
                    self.send_header("Content-Type", res[3])
                    self.send_header("Content-Length", str(len(res[2])))
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(res[1]))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(res[2])
                    return
                if isinstance(res, tuple) and res and res[0] == "html":
                    return self._send(200, res[1].encode("utf-8"), "text/html; charset=utf-8")
                return self._json(200, res)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.app.after(25, self._pump)
        self.app.after(2000, self._watchdog)
        _t = threading.Timer(6.0, self.auto_check)
        _t.daemon = True
        _t.start()
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def stop(self):
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass

    # ------------------------------------------------------------- γέφυρα Tk
    def run_on_tk(self, path: str, method: str, params: dict, body: dict, timeout: float = 600.0):
        ev = threading.Event()
        box: Dict[str, Any] = {}
        self.q.put((path, method, params, body, ev, box))
        if not ev.wait(timeout):
            raise ApiError("Λήξη χρόνου αναμονής", 504)
        if "err" in box:
            raise box["err"]
        return box.get("res")

    def _pump(self):
        try:
            while True:
                path, method, params, body, ev, box = self.q.get_nowait()
                try:
                    box["res"] = self.dispatch(path, method, params, body)
                except ApiError as e:
                    box["err"] = e
                except Exception as e:  # noqa: BLE001
                    try:
                        self.app.db.conn.rollback()
                    except Exception:
                        pass
                    box["err"] = ApiError(f"{e}", 500)
                    sys.stderr.write(traceback.format_exc())
                ev.set()
        except queue.Empty:
            pass
        if not self._quit:
            self.app.after(25, self._pump)

    def _watchdog(self):
        if self._quit:
            return
        now = time.time()
        if self.hidden:
            if self.last_ping == 0 and now - self.started > 45:
                # ο Edge δεν άνοιξε: δείξε την κλασική εφαρμογή
                self.show_classic()
            elif self.last_ping and (now - self.last_ping > 180 or (self.bye_at > self.last_ping and now - self.bye_at > 8)):
                # 180΄΄: ο Edge περιορίζει τα χρονόμετρα σε κρυμμένο παράθυρο· το «αντίο» (pagehide) κλείνει πιο γρήγορα
                self._quit = True
                self.app._on_close()
                return
        self.app.after(2000, self._watchdog)

    # ----------------------------------------------------------------- browser
    def open_browser(self, url: str) -> None:
        if os.environ.get("EFIMERIES_WEB_NO_BROWSER"):
            return
        edge = find_edge()
        try:
            if edge:
                subprocess.Popen([edge, f"--app={url}", "--start-maximized"], close_fds=True)
                return
        except Exception:
            pass
        webbrowser.open(url)

    def show_classic(self, tab: str = "") -> None:
        app = self.app
        self.hidden = False
        try:
            app.deiconify()
            app.lift()
            app.focus_force()
        except Exception:
            pass
        m = {"home": "tab_home", "settings": "tab_settings", "teachers": "tab_teachers", "generate": "tab_generate",
             "schedule": "tab_schedule", "reports": "tab_reports", "areas": "tab_areas", "calendar": "tab_calendar",
             "intervals": "tab_intervals", "shifts": "tab_shifts", "organisation": "tab_organisation"}
        name = m.get(tab)
        try:
            if name in ("tab_areas", "tab_calendar", "tab_intervals", "tab_shifts"):
                app.nb.select(app.tab_organisation)
                app.org_nb.select(getattr(app, name))
            elif name:
                app.nb.select(getattr(app, name))
        except Exception:
            pass

    # ---------------------------------------------------------------- API
    def dispatch(self, path: str, method: str, p: dict, b: dict):
        h = getattr(self, "api_" + path.strip("/").replace("api/", "", 1).replace("/", "_"), None)
        if h is None:
            raise ApiError("Άγνωστη λειτουργία", 404)
        return h(p, b)

    @property
    def db(self):
        return self.app.db

    def _d(self, s: str) -> date:
        try:
            return date.fromisoformat(s)
        except Exception:
            raise ApiError("Λάθος ημερομηνία")

    def _row_dict(self, r) -> dict:
        return {"id": r["id"], "d": r["d"], "weekday": r["weekday"], "interval": r["interval_name"], "start": r["start_time"], "end": r["end_time"],
                "area": r["display_area"], "teacher": r["teacher"], "teacher_id": r["teacher_id"], "minutes": r["minutes"],
                "locked": int(r["locked"] or 0), "entry_type": r["entry_type"], "replaced": bool(r["replaced"]), "old_teacher": r["old_teacher"] or "", "mode": r["mode"]}

    def api_state(self, p, b):
        app, db = self.app, self.db
        c = db.conn
        today = date.today()
        teachers = db.active_teachers()
        n, mins = c.execute("SELECT COUNT(*), COALESCE(SUM(minutes),0) FROM assignments").fetchone()
        sums = {r[0]: int(r[1]) for r in c.execute("SELECT teacher_id, COALESCE(SUM(minutes),0) FROM assignments GROUP BY teacher_id")}
        vals = [sums.get(t.id, 0) for t in teachers] or [0]
        sql = app.SCHEDULE_SQL + " WHERE a.d BETWEEN ? AND ? ORDER BY a.d, a.start_time, display_area"
        rows = [self._row_dict(r) for r in c.execute(sql, (today.isoformat(), (today + timedelta(days=21)).isoformat())).fetchall()]
        attention = []
        if not teachers:
            attention.append({"kind": "warn", "text": "Δεν υπάρχουν εκπαιδευτικοί που συμμετέχουν στις εφημερίες.", "go": "teachers"})
        elif n == 0:
            attention.append({"kind": "warn", "text": "Δεν υπάρχει ακόμη πρόγραμμα εφημεριών. Δημιούργησέ το από την Παραγωγή.", "go": "generate"})
        try:
            for ab in app._absence_rows(only_current=True):
                left = c.execute("SELECT COUNT(*) FROM assignments WHERE teacher_id=? AND d BETWEEN ? AND ? AND d>=? AND locked=0",
                                 (ab["teacher_id"], ab["d_from"], ab["d_to"], today.isoformat())).fetchone()[0]
                if left:
                    attention.append({"kind": "warn", "text": f"{ab['teacher']} απουσιάζει ({ab['d_from']} έως {ab['d_to']}) και έχει ακόμη {left} εφημερίες στο διάστημα.", "go": "absences"})
        except Exception:
            pass
        return {
            "school": db.get_setting("school_name", "").strip() or "Σχολείο",
            "year": db.get_setting("school_year"),
            "mode": db.get_setting("mode", "daily"),
            "version": self.mod.APP_VERSION, "arch": self.mod.APP_ARCH,
            "start": db.get_setting("start_date"), "end": db.get_setting("end_date"),
            "teachers": len(teachers), "assignments": n, "spread": max(vals) - min(vals),
            "today": today.isoformat(), "upcoming": rows, "attention": attention,
            "data_dir": str(self.mod.STORAGE_ROOT),
            "web_start": db.get_setting("ui_mode", "classic") == "web",
            "theme": db.get_setting("ui_theme", "auto"),
            "update": self.update_info(),
            "auto_update": self.app._update_state().get("auto", True) is not False,
            "can_undo": bool(app.__dict__.get("_undo_stack")),
        }

    def api_schedule(self, p, b):
        a, z = self._d(p.get("from", "")), self._d(p.get("to", ""))
        sql = self.app.SCHEDULE_SQL + " WHERE a.d BETWEEN ? AND ? ORDER BY a.d, a.start_time, display_area"
        return {"rows": [self._row_dict(r) for r in self.db.conn.execute(sql, (a.isoformat(), z.isoformat())).fetchall()]}

    def api_candidates(self, p, b):
        app = self.app
        a = app.db.conn.execute(app.SCHEDULE_SQL + " WHERE a.id=?", (int(p.get("id", 0)),)).fetchone()
        if not a:
            raise ApiError("Η εφημερία δεν βρέθηκε", 404)
        out = []
        for x in app._change_candidates(a):
            out.append({"id": x["t"].id, "name": x["t"].name, "rank": x["rank"], "why": x["why"], "blocked": bool(x["blocked"]), "load": x["load"]})
        return {"assignment": self._row_dict(a), "candidates": out}

    def api_replace(self, p, b):
        app, c = self.app, self.db.conn
        sid, new_id = int(b.get("id", 0)), int(b.get("new_id", 0))
        a = c.execute(app.SCHEDULE_SQL + " WHERE a.id=?", (sid,)).fetchone()
        if not a:
            raise ApiError("Η εφημερία δεν βρέθηκε", 404)
        cand = {x["t"].id: x for x in app._change_candidates(a)}
        x = cand.get(new_id)
        if not x:
            raise ApiError("Ο/Η εκπαιδευτικός δεν είναι διαθέσιμος/η για αντικατάσταση")
        if x["blocked"]:
            raise ApiError(f"{x['t'].name}: {x['why']}")
        snap = app._undo_snapshot()
        extra = "Εθελοντική εξυπηρέτηση (εκτός δηλωμένης διαθεσιμότητας)." if x["rank"] == 3 else ""
        reason = " | ".join(s for s in (str(b.get("reason") or "").strip(), str(b.get("note") or "").strip(), extra) if s)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        locked_value = 1 if b.get("lock") else a["locked"]
        c.execute("UPDATE assignments SET teacher_id=?, locked=? WHERE id=?", (new_id, locked_value, sid))
        c.execute("""INSERT INTO duty_changes(assignment_id,changed_at,change_type,old_teacher_id,new_teacher_id,old_teacher_name,new_teacher_name,reason)
                     VALUES(?,?,?,?,?,?,?,?)""", (sid, now, "Αντικατάσταση λόγω απουσίας/διόρθωση", a["teacher_id"], new_id, a["teacher"], x["t"].name, reason))
        if a["mode"] == "shifts" and x["rank"] == 0:
            c.execute("""UPDATE shift_reserves SET activated=1,replacement_assignment_id=?
                         WHERE d=? AND teacher_id=? AND shift_id=(SELECT id FROM shifts WHERE name=? LIMIT 1)""", (sid, a["d"], new_id, a["interval_name"]))
        c.commit()
        app._undo_push("αλλαγή εκπαιδευτικού (νέα διεπαφή)", snap)
        return {"ok": True, "msg": f"{a['teacher']} → {x['t'].name}"}

    def api_lock(self, p, b):
        app, c = self.app, self.db.conn
        snap = app._undo_snapshot()
        c.execute("UPDATE assignments SET locked=? WHERE id=?", (1 if b.get("locked") else 0, int(b.get("id", 0))))
        c.commit()
        app._undo_push("κλείδωμα/ξεκλείδωμα (νέα διεπαφή)", snap)
        return {"ok": True}

    def api_lock_until(self, p, b):
        app, c = self.app, self.db.conn
        until = self._d(b.get("date", ""))
        snap = app._undo_snapshot()
        cur = c.execute("UPDATE assignments SET locked=1 WHERE d<=?", (until.isoformat(),))
        c.commit()
        app._undo_push("κλείδωμα πραγματοποιημένων (νέα διεπαφή)", snap)
        return {"ok": True, "count": cur.rowcount}

    def api_undo(self, p, b):
        app = self.app
        stack = app.__dict__.get("_undo_stack") or []
        if not stack:
            return {"ok": False, "msg": "Δεν υπάρχει αλλαγή για αναίρεση."}
        label = stack[-1][0]
        with _Captured():
            app.undo_last()
        app.refresh_all()
        return {"ok": True, "msg": f"Αναιρέθηκε: {label}"}

    # ----------------------------------------------------------- εκπαιδευτικοί
    def _teacher_dict(self, t) -> dict:
        alts = self.db.teacher_alternate_days(t.id, getattr(t, "alternate_day", -1))
        return {"id": t.id, "name": t.name, "participates": int(t.participates), "active": int(t.active),
                "days": [int(t.mon), int(t.tue), int(t.wed), int(t.thu), int(t.fri)], "primary": int(t.primary_day), "alternates": alts,
                "extra_cycle": int(t.extra_cycle), "notes": t.notes or ""}

    def api_teachers(self, p, b):
        c = self.db.conn
        loads = {r[0]: (int(r[1]), int(r[2])) for r in c.execute("SELECT teacher_id, COALESCE(SUM(minutes),0), COUNT(*) FROM assignments GROUP BY teacher_id")}
        out = []
        for t in self.db.teachers():
            d = self._teacher_dict(t)
            d["minutes"], d["count"] = loads.get(t.id, (0, 0))
            out.append(d)
        return {"teachers": out}

    def api_teacher_save(self, p, b):
        app, c = self.app, self.db.conn
        tid = int(b.get("id", 0))
        if not c.execute("SELECT 1 FROM teachers WHERE id=?", (tid,)).fetchone():
            raise ApiError("Ο/Η εκπαιδευτικός δεν βρέθηκε", 404)
        days = [1 if x else 0 for x in (b.get("days") or [1, 1, 1, 1, 1])][:5]
        days += [1] * (5 - len(days))
        primary = int(b.get("primary", -1))
        if primary != -1 and not (0 <= primary <= 4 and days[primary]):
            primary = -1
        alts = [w for w in (b.get("alternates") or []) if 0 <= int(w) <= 4 and days[int(w)] and int(w) != primary]
        name = str(b.get("name") or "").strip()
        if not name:
            raise ApiError("Το όνομα δεν μπορεί να είναι κενό")
        try:
            c.execute("UPDATE teachers SET name=?, participates=?, active=?, mon=?, tue=?, wed=?, thu=?, fri=?, primary_day=?, extra_cycle=?, notes=? WHERE id=?",
                      (name, 1 if b.get("participates", 1) else 0, 1 if b.get("active", 1) else 0, *days, primary, 1 if b.get("extra_cycle", 1) else 0, str(b.get("notes") or ""), tid))
            app._set_alternates_nocommit(tid, alts)
            c.commit()
        except Exception as e:  # noqa: BLE001
            c.rollback()
            if "UNIQUE" in str(e):
                raise ApiError("Υπάρχει ήδη εκπαιδευτικός με αυτό το όνομα")
            raise
        app._absent_cache = None
        app.refresh_all()
        t = next(t for t in self.db.teachers() if t.id == tid)
        return {"ok": True, "teacher": self._teacher_dict(t)}

    def api_teacher_add(self, p, b):
        name = str(b.get("name") or "").strip()
        if not name:
            raise ApiError("Γράψε το όνομα")
        c = self.db.conn
        try:
            cur = c.execute("INSERT INTO teachers(name) VALUES(?)", (name,))
            c.commit()
        except Exception:
            c.rollback()
            raise ApiError("Υπάρχει ήδη εκπαιδευτικός με αυτό το όνομα")
        self.app.refresh_all()
        return {"ok": True, "id": cur.lastrowid}

    # ------------------------------------------------------------------ απουσίες
    def api_absences(self, p, b):
        rows = self.app._absence_rows()
        return {"absences": [{"id": r["id"], "teacher_id": r["teacher_id"], "teacher": r["teacher"], "from": r["d_from"], "to": r["d_to"], "reason": r["reason"] or ""} for r in rows]}

    def api_absence_add(self, p, b):
        app, c = self.app, self.db.conn
        tid = int(b.get("teacher_id", 0))
        d1, d2 = self._d(b.get("from", "")), self._d(b.get("to", "") or b.get("from", ""))
        reason = str(b.get("reason") or "").strip()
        if not c.execute("SELECT 1 FROM teachers WHERE id=?", (tid,)).fetchone():
            raise ApiError("Διάλεξε εκπαιδευτικό")
        snap = app._undo_snapshot()
        app._add_absence(tid, d1, d2, reason)
        done, missing, locked_n = 0, [], 0
        if b.get("replace", True):
            if d2 < d1:
                d1, d2 = d2, d1
            plan, locked_n = app._plan_absence(tid, d1, d2)
            done = app._apply_absence_plan(tid, plan, reason or "Απουσία", False)
            missing = [f"{it['d']} {it['area']}" for it in plan if not it["new_id"]]
        app._undo_push("απουσία εκπαιδευτικού (νέα διεπαφή)", snap)
        app.refresh_all()
        return {"ok": True, "replaced": done, "missing": missing, "locked": locked_n}

    def api_absence_delete(self, p, b):
        app = self.app
        snap = app._undo_snapshot()
        app._delete_absence(int(b.get("id", 0)))
        app._undo_push("διαγραφή απουσίας (νέα διεπαφή)", snap)
        app.refresh_all()
        return {"ok": True}

    # ------------------------------------------------------------------ παραγωγή
    def api_generate_settings(self, p, b):
        app, db = self.app, self.db
        return {"mode": db.get_setting("mode", "daily"), "stay": app.stay_enabled(),
                "weights": {k: app.rule_weight(k) for k in app.RULE_DEFAULTS},
                "start": db.get_setting("start_date"), "end": db.get_setting("end_date"), "today": date.today().isoformat()}

    def api_generate(self, p, b):
        app, db = self.app, self.db
        mode = b.get("mode") or "daily"
        scope = b.get("scope") or "continue"
        if mode not in ("daily", "breaks", "shifts") or scope not in ("continue", "range", "all"):
            raise ApiError("Λάθος επιλογές")
        return self._generate(mode, scope, b)

    def _generate(self, mode: str, scope: str, b: dict):
        app, db = self.app, self.db
        ui = self.mod.ui_date
        app.mode_var.set(mode)
        app.regenerate_scope_var.set(scope)
        if scope != "all":
            app.regenerate_from_var.set(ui(self._d(b.get("from", "")).isoformat()))
        if scope == "range":
            app.regenerate_to_var.set(ui(self._d(b.get("to", "")).isoformat()))
        db.set_setting("stay_in_place", "1" if b.get("stay", True) else "0")
        for k, v in (b.get("weights") or {}).items():
            if k in app.RULE_DEFAULTS:
                db.set_setting(k, str(max(0, min(5, int(v)))))
        app.generate_text.delete("1.0", "end")
        snap, pre = app._undo_snapshot(), app._gen_metrics()
        with _Captured() as cap:
            app.auto_backup("generate")
            try:
                app._generate_schedule_impl()
            except Exception:
                db.conn.rollback()
                raise
        if app._undo_snapshot() == snap:
            return {"ok": True, "changed": False, "text": app.generate_text.get("1.0", "end").strip(), "messages": cap.messages}
        post = app._gen_metrics()
        app._undo_push("παραγωγή προγράμματος (νέα διεπαφή)", snap)
        app.refresh_all()
        text = app.generate_text.get("1.0", "end").strip()
        gaps = 0
        if "Προσοχή — χρειάζεται έλεγχος:" in text:
            gaps = sum(1 for ln in text.split("Προσοχή — χρειάζεται έλεγχος:", 1)[1].splitlines() if ln.startswith("- "))
        return {"ok": True, "changed": True, "text": text, "messages": cap.messages, "n": post["n"], "n_before": pre["n"],
                "different": len(post["keys"] - pre["keys"]), "spread": post["spread"], "spread_before": pre["spread"], "gaps": gaps}

    def api_diagnose(self, p, b):
        mode = p.get("mode") or self.db.get_setting("mode", "daily")
        a, z = self._d(p.get("from", "")), self._d(p.get("to", ""))
        if mode not in ("daily", "breaks"):
            return {"lines": [], "supported": False}
        return {"lines": self.app.diagnose_conflicts(a, z, mode), "supported": True}

    # -------------------------------------------------------------- εκτύπωση
    def _duty_rows(self, a: date, z: date, tid: Optional[int] = None):
        sql = self.app.SCHEDULE_SQL + " WHERE a.d BETWEEN ? AND ?" + (" AND a.teacher_id=?" if tid else "") + " ORDER BY a.d, a.start_time, display_area"
        args = (a.isoformat(), z.isoformat()) + ((tid,) if tid else ())
        return self.db.conn.execute(sql, args).fetchall()

    def _grid_weeks(self, a: date, z: date) -> list:
        """Εβδομαδιαίο πλέγμα: κάθε εβδομάδα έχει ημερομηνίες/ημέρες και μία γραμμή ανά τομέα (ή ανά ζώνη+τομέα στους τρόπους διαλειμμάτων/βαρδιών)."""
        m = self.mod
        rows = self._duty_rows(a, z)
        areas = [r["name"] for r in self.db.areas(True)]
        aidx = {n: i for i, n in enumerate(areas)}
        interval_mode = any(r["mode"] != "daily" for r in rows)
        labels: Dict[str, tuple] = {}
        cells: Dict[tuple, list] = {}
        for r in rows:
            area = r["display_area"]
            if interval_mode:
                lab = f"{r['interval_name']} · {area}"
                key = (r["start_time"] or "", aidx.get(area, 99))
            else:
                lab = area
                key = (aidx.get(area, 99), 0)
            labels.setdefault(lab, key)
            lst = cells.setdefault((lab, r["d"]), [])
            if r["teacher"] not in lst:
                lst.append(r["teacher"])
        if not interval_mode:
            for n in areas:
                labels.setdefault(n, (aidx[n], 0))
        order = sorted(labels, key=lambda k: (labels[k], k))
        weeks = []
        mon = a - timedelta(days=a.weekday())
        while mon <= z:
            days = [mon + timedelta(days=i) for i in range(5)]
            if any(a <= d <= z for d in days):
                wk = {"dates": [m.fmt_date(d.isoformat()) for d in days], "days": list(m.DAYS_GR), "rows": []}
                notes = []
                for d in days:
                    txt = ""
                    if a <= d <= z and not any((lab, d.isoformat()) in cells for lab in order):
                        try:
                            css, t = self.app._print_day_note(d)
                            txt = t if css == "off-day" else ""
                        except Exception:
                            txt = ""
                    notes.append(txt)
                for k, lab in enumerate(order):
                    vals, flags = [], []
                    for i, d in enumerate(days):
                        if notes[i]:
                            vals.append(notes[i] if k == 0 else "")
                            flags.append(True)
                        else:
                            vals.append(" / ".join(cells.get((lab, d.isoformat()), [])))
                            flags.append(False)
                    wk["rows"].append((lab, vals, flags))
                weeks.append(wk)
            mon += timedelta(days=7)
        return weeks

    def _who_ids(self, who: str) -> List[int]:
        if who.startswith("teacher:"):
            return [int(who.split(":", 1)[1])]
        if who == "each":
            return [t.id for t in self.db.teachers() if t.active]
        return []

    def api_print(self, p, b):
        a, z = self._d(p.get("from", "")), self._d(p.get("to", ""))
        who = p.get("who", "all")
        esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        fmt = lambda s: f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
        school = esc(self.db.get_setting("school_name", "").strip() or "Σχολείο")
        period = f"{fmt(a.isoformat())} – {fmt(z.isoformat())}"
        names = {t.id: t.name for t in self.db.teachers()}
        out = [f"<!doctype html><meta charset='utf-8'><title>Εφημερίες {period}</title>",
               "<style>body{font:13px 'Segoe UI',Arial,sans-serif;margin:24px;color:#111}h1{font-size:18px;margin:0}h2{font-size:15px;margin:2px 0}p{margin:2px 0 14px;color:#444}"
               "table{border-collapse:collapse;width:100%}th,td{border:1px solid #999;padding:5px 8px;text-align:left}th{background:#eee}"
               ".sheet{page-break-after:always;margin-bottom:30px}.sheet:last-of-type{page-break-after:auto}.tot{margin-top:10px;font-weight:600}"
               "@media print{.np{display:none}body{margin:8mm}.sheet{margin-bottom:0}}</style>",
               "<button class='np' onclick='print()' style='float:right;padding:6px 14px'>Εκτύπωση</button>"]

        def table(rows, show_teacher: bool) -> str:
            h = "<table><tr><th>Ημερομηνία</th><th>Ημέρα</th><th>Ζώνη</th><th>Τομέας</th>" + ("<th>Εκπαιδευτικός</th>" if show_teacher else "") + "<th></th></tr>"
            last = None
            for r in rows:
                h += (f"<tr><td>{fmt(r['d']) if r['d'] != last else ''}</td><td>{esc(r['weekday']) if r['d'] != last else ''}</td>"
                      f"<td>{esc(r['interval_name'])} {esc(r['start_time'])}–{esc(r['end_time'])}</td><td>{esc(r['display_area'])}</td>"
                      + (f"<td>{esc(r['teacher'])}</td>" if show_teacher else "") + f"<td>{'✔' if int(r['locked'] or 0) else ''}</td></tr>")
                last = r["d"]
            return h + "</table>"
        if who == "all" and p.get("fmt", "grid") == "grid":
            weeks = self._grid_weeks(a, z)
            out[1] = out[1].replace("</style>", ".wk{page-break-inside:avoid;margin-bottom:14px}.wk table{table-layout:fixed}.wk th{text-align:center;background:#d9d9d9}.wk td{text-align:center;height:24px}"
                                    ".wk td.l{text-align:left;font-weight:700;width:13%}.wk td.n{background:#fff2cc;font-style:italic;color:#7a5a00;font-size:11px}.wk th.e{background:#fff}"
                                    "@media print{@page{size:A4 landscape;margin:8mm}.wk{margin-bottom:10px}}</style>")
            out.append(f"<h1>{school}</h1><p style='font-size:15px;color:#111'><b>Πρόγραμμα Εφημεριών από {fmt(a.isoformat())} έως {fmt(z.isoformat())}</b></p>")
            for wk in weeks:
                t = "<div class='wk'><table><tr><th class='e'></th>" + "".join(f"<th>{esc(d)}</th>" for d in wk["dates"]) + "</tr>"
                t += "<tr><th class='e'></th>" + "".join(f"<th>{esc(d)}</th>" for d in wk["days"]) + "</tr>"
                for lab, vals, flags in wk["rows"]:
                    t += f"<tr><td class='l'>{esc(lab)}</td>" + "".join(f"<td{' class=n' if f else ''}>{esc(v)}</td>" for v, f in zip(vals, flags)) + "</tr>"
                out.append(t + "</table></div>")
        elif who == "all":
            rows = self._duty_rows(a, z)
            out.append(f"<h1>{school}</h1><p>Εφημερίες {period}</p>" + table(rows, True))
            tot: Dict[str, int] = {}
            for r in rows:
                if int(r["locked"] or 0):
                    tot[r["teacher"]] = tot.get(r["teacher"], 0) + 1
            if tot:
                out.append("<h3>Πραγματοποιηθείσες εφημερίες (σύνολο ανά εκπαιδευτικό)</h3><table><tr><th>Εκπαιδευτικός</th><th>Σύνολο</th></tr>"
                           + "".join(f"<tr><td>{esc(k)}</td><td>{tot[k]}</td></tr>" for k in sorted(tot)) + "</table>")
        else:
            ids = self._who_ids(who)
            sheets = 0
            for tid in ids:
                rows = self._duty_rows(a, z, tid)
                if not rows and who == "each":
                    continue
                done = sum(1 for r in rows if int(r["locked"] or 0))
                days = len({r["d"] for r in rows})
                out.append(f"<div class='sheet'><h1>{school}</h1><h2>{esc(names.get(tid, ''))}</h2><p>Πρόγραμμα εφημεριών {period}</p>"
                           + (table(rows, False) if rows else "<p>Δεν υπάρχουν εφημερίες στο διάστημα.</p>")
                           + f"<div class='tot'>Σύνολο: {len(rows)} εφημερίες σε {days} ημέρες · πραγματοποιημένες: {done}</div><p style='font-size:11px;margin-top:6px'>✔ = πραγματοποιημένη</p></div>")
                sheets += 1
            if not sheets:
                out.append("<p>Δεν βρέθηκαν εφημερίες στο διάστημα.</p>")
        return ("html", "".join(out))

    # ------------------------------------------------------------ ημερολόγιο (.ics)
    _TZ = ("BEGIN:VTIMEZONE\r\nTZID:Europe/Athens\r\nBEGIN:DAYLIGHT\r\nTZOFFSETFROM:+0200\r\nTZOFFSETTO:+0300\r\nTZNAME:EEST\r\nDTSTART:19700329T030000\r\n"
           "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\nEND:DAYLIGHT\r\nBEGIN:STANDARD\r\nTZOFFSETFROM:+0300\r\nTZOFFSETTO:+0200\r\nTZNAME:EET\r\nDTSTART:19701025T040000\r\n"
           "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n")

    @staticmethod
    def _ics_escape(t: str) -> str:
        return str(t).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    @staticmethod
    def _ics_fold(line: str) -> str:
        """Στίχοι ≤75 byte (UTF-8), συνέχεια με κενό στην αρχή."""
        raw = line.encode("utf-8")
        if len(raw) <= 75:
            return line
        parts, cur, size = [], "", 0
        for ch in line:
            n = len(ch.encode("utf-8"))
            if size + n > (75 if not parts else 74):
                parts.append(cur)
                cur, size = "", 0
            cur += ch
            size += n
        parts.append(cur)
        return "\r\n ".join(parts)

    def _ics(self, rows, cal_name: str, remind: int) -> bytes:
        esc, now = self._ics_escape, datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Efimeries Ekpaideftikon//EL", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
                 f"X-WR-CALNAME:{esc(cal_name)}", "X-WR-TIMEZONE:Europe/Athens"]
        body = []
        for r in rows:
            d = r["d"].replace("-", "")
            st, en = r["start_time"].replace(":", "") + "00", r["end_time"].replace(":", "") + "00"
            zone = "" if r["mode"] == "daily" else f" · {r['interval_name']}"
            summary = f"Εφημερία {r['display_area']}{zone}"
            desc = f"{r['teacher']} · {r['interval_name']} {r['start_time']}–{r['end_time']} · {r['display_area']}"
            ev = ["BEGIN:VEVENT", f"UID:efimeria-{r['id']}-{r['teacher_id']}@efimeries.local", f"DTSTAMP:{now}",
                  f"DTSTART;TZID=Europe/Athens:{d}T{st}", f"DTEND;TZID=Europe/Athens:{d}T{en}",
                  f"SUMMARY:{esc(summary)}", f"DESCRIPTION:{esc(desc)}", "TRANSP:OPAQUE"]
            if remind > 0:
                ev += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(summary)}", f"TRIGGER:-PT{int(remind)}M", "END:VALARM"]
            ev.append("END:VEVENT")
            body += ev
        head = "\r\n".join(self._ics_fold(x) for x in lines) + "\r\n" + self._TZ
        return (head + "\r\n".join(self._ics_fold(x) for x in body) + ("\r\n" if body else "") + "END:VCALENDAR\r\n").encode("utf-8")

    def api_ics(self, p, b):
        import io
        import zipfile
        a, z = self._d(p.get("from", "")), self._d(p.get("to", ""))
        who = p.get("who", "all")
        remind = max(0, min(10080, int(p.get("remind") or 0)))
        school = self.db.get_setting("school_name", "").strip() or "Σχολείο"
        names = {t.id: t.name for t in self.db.teachers()}
        safe = lambda s: "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in s).strip() or "efimeries"
        period = f"{a.isoformat()}_{z.isoformat()}"
        if who == "all":
            rows = self._duty_rows(a, z)
            return ("file", f"Efimeries_{period}.ics", self._ics(rows, f"Εφημερίες {school}", remind), "text/calendar; charset=utf-8")
        if who.startswith("teacher:"):
            tid = int(who.split(":", 1)[1])
            rows = self._duty_rows(a, z, tid)
            return ("file", f"Efimeries_{safe(names.get(tid, 'teacher'))}_{period}.ics", self._ics(rows, f"Εφημερίες {names.get(tid, '')}", remind), "text/calendar; charset=utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            n = 0
            for tid in self._who_ids("each"):
                rows = self._duty_rows(a, z, tid)
                if rows:
                    zf.writestr(f"{safe(names.get(tid, str(tid)))}.ics", self._ics(rows, f"Εφημερίες {names.get(tid, '')}", remind))
                    n += 1
            if not n:
                raise ApiError("Δεν υπάρχουν εφημερίες στο διάστημα")
        return ("file", f"Efimeries_ana_ekpaideutiko_{period}.zip", buf.getvalue(), "application/zip")

    # ------------------------------------------------------------ ενημερώσεις
    def page_open(self) -> bool:
        """True όταν η νέα διεπαφή είναι αυτή που βλέπει ο χρήστης (οι ειδοποιήσεις ενημέρωσης πάνε εκεί)."""
        return self.hidden or (self.last_ping and time.time() - self.last_ping < 60)

    def _newer(self, rel: dict) -> bool:
        return self.app._version_tuple(rel["tag"]) > self.app._version_tuple(self.mod.APP_VERSION)

    def update_info(self) -> Optional[dict]:
        rel = self.update_rel
        if not rel or not self._newer(rel) or self.app._update_state().get("skip") == rel["tag"]:
            return None
        return {"tag": rel["tag"], "notes": (rel.get("body") or "")[:2500], "url": rel["url"], "can_install": bool(self.app._setup_asset(rel))}

    def api_update_check(self, p, b):
        app = self.app
        try:
            rel = app._fetch_latest_release()
        except Exception:
            return {"status": "error"}
        app._save_update_state(last_check=time.time())
        if self._newer(rel):
            self.update_rel = rel
            return {"status": "newer", "tag": rel["tag"]}
        self.update_rel = None
        return {"status": "current", "version": self.mod.APP_VERSION}

    def auto_check(self):
        if os.environ.get("EFIMERIES_NO_UPDATE_CHECK") or self._quit:
            return
        st = self.app._update_state()
        if st.get("auto", True) is False or time.time() - float(st.get("last_check", 0) or 0) < 24 * 3600:
            return
        try:
            self.api_update_check({}, {})
        except Exception:
            pass

    def api_update_install(self, p, b):
        rel = self.update_rel
        if not rel:
            raise ApiError("Δεν υπάρχει διαθέσιμη ενημέρωση")
        asset = self.app._setup_asset(rel)
        if not asset:
            return {"portable": True, "url": rel["url"]}
        if self.dl.get("state") == "downloading":
            return {"ok": True}
        dest = Path(tempfile.gettempdir()) / asset["name"]
        self.dl = {"state": "downloading", "done": 0, "total": 0, "path": str(dest)}

        def work():
            try:
                self.app._download_file(asset["url"], dest, lambda d, t: self.dl.update(done=d, total=t))
                if asset.get("size") and dest.stat().st_size != int(asset["size"]):
                    raise OSError("το μέγεθος του αρχείου δεν ταιριάζει")
                self.dl["state"] = "ready"
            except Exception as e:  # noqa: BLE001
                self.dl.update(state="error", msg=str(e))
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def api_update_status(self, p, b):
        return {k: v for k, v in self.dl.items() if k != "path"}

    def api_update_launch(self, p, b):
        if self.dl.get("state") != "ready":
            raise ApiError("Η λήψη δεν έχει ολοκληρωθεί")
        try:
            subprocess.Popen([self.dl["path"], "/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS", "/LAUNCH=1"], close_fds=True)
        except Exception as e:  # noqa: BLE001
            raise ApiError(f"Δεν ήταν δυνατή η εκκίνηση της εγκατάστασης: {e}")
        self._quit = True
        self.app.after(400, self.app._on_close)
        return {"ok": True}

    def api_update_skip(self, p, b):
        if self.update_rel:
            self.app._save_update_state(skip=self.update_rel["tag"])
        return {"ok": True}

    def api_update_auto(self, p, b):
        self.app._save_update_state(auto=bool(b.get("auto")))
        return {"ok": True}

    # ------------------------------------------------------- ρυθμίσεις (v0.62)
    def api_settings(self, p, b):
        db = self.db
        return {"school_name": db.get_setting("school_name", ""), "school_phone": db.get_setting("school_phone", ""),
                "school_email": db.get_setting("school_email", ""), "start": db.get_setting("start_date"), "end": db.get_setting("end_date"),
                "school_year": db.get_setting("school_year")}

    def api_settings_save(self, p, b):
        start, end = self._d(str(b.get("start", ""))), self._d(str(b.get("end", "")))
        if start > end:
            raise ApiError("Η έναρξη δεν μπορεί να είναι μετά τη λήξη.")
        db = self.db
        db.set_setting("school_name", str(b.get("school_name") or "").strip())
        db.set_setting("school_phone", str(b.get("school_phone") or "").strip())
        db.set_setting("school_email", str(b.get("school_email") or "").strip())
        db.set_setting("start_date", start.isoformat())
        db.set_setting("end_date", end.isoformat())
        try:
            self.app.sync_school_year_fields()
        except Exception:
            pass
        self.app.refresh_all()
        return {"ok": True}

    def _time_ok(self, s: str) -> str:
        s = str(s or "").strip()
        try:
            self.mod.minutes_between(s, "23:59")
        except Exception:
            raise ApiError("Οι ώρες πρέπει να είναι της μορφής 09:40")
        return s

    def api_areas(self, p, b):
        c = self.db.conn
        used = {r[0]: r[1] for r in c.execute("SELECT area_name, COUNT(*) FROM assignments GROUP BY area_name")}
        return {"areas": [{"id": r["id"], "name": r["name"], "description": r["description"] or "", "active": int(r["active"]), "sort_order": int(r["sort_order"]),
                           "used": used.get(r["name"], 0)} for r in self.db.areas()]}

    def api_area_save(self, p, b):
        c = self.db.conn
        name = str(b.get("name") or "").strip()
        if not name:
            raise ApiError("Γράψε το όνομα του τομέα")
        data = {"name": name, "description": str(b.get("description") or "").strip(), "active": 1 if b.get("active", 1) else 0, "sort_order": int(b.get("sort_order") or 0)}
        try:
            if b.get("id"):
                sid = int(b["id"])
                row = c.execute("SELECT name FROM areas WHERE id=?", (sid,)).fetchone()
                if not row:
                    raise ApiError("Ο τομέας δεν βρέθηκε", 404)
                c.execute("UPDATE areas SET name=:name,description=:description,active=:active,sort_order=:sort_order WHERE id=:id", {**data, "id": sid})
                c.execute("UPDATE assignments SET area_name=?, area_id=? WHERE area_id=? OR area_name=?", (name, sid, sid, row["name"]))
            else:
                c.execute("INSERT INTO areas(name,description,active,sort_order) VALUES(:name,:description,:active,:sort_order)", data)
            c.commit()
        except ApiError:
            c.rollback()
            raise
        except Exception as e:  # noqa: BLE001
            c.rollback()
            raise ApiError("Υπάρχει ήδη τομέας με αυτό το όνομα" if "UNIQUE" in str(e) else str(e))
        self.app.refresh_all()
        return {"ok": True}

    def api_area_delete(self, p, b):
        c = self.db.conn
        c.execute("DELETE FROM areas WHERE id=?", (int(b.get("id", 0)),))
        c.commit()
        self.app.refresh_all()
        return {"ok": True}

    def api_intervals(self, p, b):
        return {"intervals": [{"id": r["id"], "name": r["name"], "start": r["start_time"], "end": r["end_time"], "active": int(r["active"]), "sort_order": int(r["sort_order"])} for r in self.db.intervals()]}

    def api_interval_save(self, p, b):
        c = self.db.conn
        name = str(b.get("name") or "").strip()
        if not name:
            raise ApiError("Γράψε το όνομα της ζώνης")
        st, en = self._time_ok(b.get("start")), self._time_ok(b.get("end"))
        try:
            if self.mod.minutes_between(st, en) <= 0:
                raise ValueError
        except Exception:
            raise ApiError("Η ώρα λήξης πρέπει να είναι μετά την ώρα έναρξης")
        data = {"name": name, "start_time": st, "end_time": en, "active": 1 if b.get("active", 1) else 0, "sort_order": int(b.get("sort_order") or 0)}
        if b.get("id"):
            c.execute("UPDATE duty_intervals SET name=:name,start_time=:start_time,end_time=:end_time,active=:active,sort_order=:sort_order WHERE id=:id", {**data, "id": int(b["id"])})
        else:
            c.execute("INSERT INTO duty_intervals(name,start_time,end_time,active,sort_order) VALUES(:name,:start_time,:end_time,:active,:sort_order)", data)
        c.commit()
        self.app.refresh_all()
        return {"ok": True}

    def api_interval_delete(self, p, b):
        c = self.db.conn
        c.execute("DELETE FROM duty_intervals WHERE id=?", (int(b.get("id", 0)),))
        c.commit()
        self.app.refresh_all()
        return {"ok": True}

    def api_intervals_reset(self, p, b):
        c = self.db.conn
        c.execute("DELETE FROM duty_intervals")
        c.executemany("INSERT INTO duty_intervals(name,start_time,end_time,active,sort_order) VALUES(?,?,?,?,?)",
                      [("Προσέλευση", "08:00", "08:15", 1, 1), ("1ο διάλειμμα", "09:40", "10:00", 1, 2), ("2ο διάλειμμα", "11:30", "11:45", 1, 3),
                       ("3ο διάλειμμα", "12:25", "12:35", 1, 4), ("Αποχώρηση", "13:15", "13:20", 1, 5)])
        c.commit()
        self.app.refresh_all()
        return {"ok": True}

    EXC_TYPES = ["Χωρίς εφημερία", "Αργία", "Σχολική γιορτή", "Εκδρομή", "Ειδική ημέρα", "Εξαίρεση", "Μόνο προσέλευση-αποχώρηση", "Ειδικό πρόγραμμα ζωνών"]

    def api_exceptions(self, p, b):
        out = []
        for r in self.db.conn.execute("SELECT * FROM exceptions ORDER BY d").fetchall():
            out.append({"id": r["id"], "from": r["d"], "to": r["end_d"] or r["d"], "label": r["label"], "type": r["type"], "config": self.db.exception_config(r)})
        return {"exceptions": out, "types": self.EXC_TYPES}

    def api_exception_save(self, p, b):
        app, c = self.app, self.db.conn
        d1 = self._d(str(b.get("from", "")))
        d2 = self._d(str(b.get("to") or b.get("from", "")))
        if d2 < d1:
            raise ApiError("Η ημερομηνία «Έως» δεν μπορεί να είναι πριν από την «Από».")
        label = str(b.get("label") or "").strip()
        if not label:
            raise ApiError("Συμπλήρωσε μια σύντομη περιγραφή.")
        typ = str(b.get("type") or "Χωρίς εφημερία")
        if typ not in self.EXC_TYPES:
            raise ApiError("Άκυρος τύπος")
        eid = int(b["id"]) if b.get("id") else None
        ov = app.exception_range_overlap(d1, d2, eid)
        if ov is not None:
            ov_end = ov["end_d"] or ov["d"]
            raise ApiError(f"Το διάστημα επικαλύπτεται με υπάρχουσα εγγραφή: {ov['d'][8:10]}/{ov['d'][5:7]}/{ov['d'][:4]} – {ov_end[8:10]}/{ov_end[5:7]}/{ov_end[:4]} ({ov['label']}). Επεξεργάσου πρώτα εκείνη.")
        cfg: Dict[str, Any] = {}
        if typ != "Χωρίς εφημερία":
            valid = {r["name"] for r in self.db.intervals()}
            for name, v in (b.get("config") or {}).items():
                if name in valid and v and v.get("active"):
                    cfg[name] = {"active": True, "count": max(1, min(10, int(v.get("count") or 1)))}
        data = {"d": d1.isoformat(), "end_d": d2.isoformat(), "label": label, "type": typ, "config": json.dumps(cfg, ensure_ascii=False)}
        try:
            if eid:
                c.execute("UPDATE exceptions SET d=:d,end_d=:end_d,label=:label,type=:type,config=:config WHERE id=:id", {**data, "id": eid})
            else:
                c.execute("INSERT INTO exceptions(d,end_d,label,type,config) VALUES(:d,:end_d,:label,:type,:config)", data)
            c.commit()
        except Exception as e:  # noqa: BLE001
            c.rollback()
            raise ApiError("Υπάρχει ήδη εγγραφή που ξεκινά την ίδια ημερομηνία" if "UNIQUE" in str(e) else str(e))
        app.refresh_all()
        return {"ok": True}

    def api_exception_delete(self, p, b):
        c = self.db.conn
        c.execute("DELETE FROM exceptions WHERE id=?", (int(b.get("id", 0)),))
        c.commit()
        self.app.refresh_all()
        return {"ok": True}

    # ------------------------------------------------- φάκελος δεδομένων (v0.63)
    def api_storage(self, p, b):
        m = self.mod
        root = Path(m.STORAGE_ROOT)
        try:
            dbp = str(m.year_db_path(self.app.active_school_year))
        except Exception:
            dbp = ""
        installed = (m.app_dir() / "installed.flag").exists()
        c = self.db.conn
        return {"root": str(root), "db": dbp, "installed": installed, "local": root == m.app_dir(),
                "teachers": c.execute("SELECT COUNT(*) FROM teachers").fetchone()[0], "assignments": c.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]}

    def api_storage_browse(self, p, b):
        from tkinter import filedialog
        sel = filedialog.askdirectory(title="Επιλογή φακέλου κοινόχρηστων δεδομένων", initialdir=str(self.mod.STORAGE_ROOT))
        return {"path": sel or ""}

    def api_storage_set(self, p, b):
        path = str(b.get("path") or "").strip().strip('"')
        if not path:
            raise ApiError("Γράψε ή επίλεξε φάκελο")
        before = str(self.mod.STORAGE_ROOT)
        with _Captured() as cap:
            self.app.change_storage_root(selected=path, copy_data=bool(b.get("copy")))
        self.app.refresh_all()
        return {"ok": True, "root": str(self.mod.STORAGE_ROOT), "changed": str(self.mod.STORAGE_ROOT) != before, "messages": cap.messages}

    # ------------------------------------------------- αρχεία και backup (v0.65)
    def api_xlsx(self, p, b):
        m = self.mod
        year = self.db.get_setting("school_year")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.xlsx"
            if not self.app.build_xlsx(path):
                raise ApiError("Δεν υπάρχει πρόγραμμα για εξαγωγή. Δημιούργησε πρώτα πρόγραμμα.")
            data = path.read_bytes()
        return ("file", f"efimeries_{year}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx", data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def api_xlsx_grid(self, p, b):
        m = self.mod
        a, z = self._d(p.get("from", "")), self._d(p.get("to", ""))
        weeks = self._grid_weeks(a, z)
        if not any(v for wk in weeks for _l, vals, fl in wk["rows"] for v, f in zip(vals, fl) if v and not f):
            raise ApiError("Δεν υπάρχουν εφημερίες στο διάστημα.")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "g.xlsx"
            m.write_xlsx_grid(path, f"Πρόγραμμα Εφημεριών από: {m.fmt_date(a.isoformat())} έως {m.fmt_date(z.isoformat())}", weeks)
            data = path.read_bytes()
        return ("file", f"Programma_Efimerion_{a.isoformat()}_{z.isoformat()}.xlsx", data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def _csv_bytes(self, headers, rows) -> bytes:
        import csv
        import io
        buf = io.StringIO(newline="")
        w = csv.writer(buf, delimiter=";")
        w.writerow(headers)
        for r in rows:
            w.writerow(list(r))
        return buf.getvalue().encode("utf-8-sig")

    def api_csv(self, p, b):
        m = self.mod
        kind = p.get("kind", "schedule")
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        if kind == "teachers":
            app = self.app
            out = []
            for t in self.db.teachers():
                out.append((t.name, app.yesno(t.participates), app.yesno(t.active), app.yesno(t.mon), app.yesno(t.tue), app.yesno(t.wed), app.yesno(t.thu), app.yesno(t.fri),
                            app.day_name(t.primary_day), app.day_name(t.alternate_day),
                            " | ".join(app.day_name(wd) for wd in self.db.teacher_alternate_days(t.id, t.alternate_day)), app.yesno(t.extra_cycle), t.notes))
            data = self._csv_bytes(["Ονοματεπώνυμο", "Συμμετέχει", "Ενεργός", "Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Κύρια ημέρα", "Εναλλακτική ημέρα", "Εναλλακτικές ημέρες", "Πρόσθετος κύκλος", "Παρατηρήσεις"], out)
            return ("file", f"teachers_{stamp}.csv", data, "text/csv; charset=utf-8")
        rows = self.db.conn.execute("SELECT a.d,a.weekday,a.interval_name,a.start_time,a.end_time,a.area_name,t.name,a.minutes,a.mode,a.locked,a.entry_type FROM assignments a JOIN teachers t ON t.id=a.teacher_id ORDER BY a.d,a.start_time,a.area_name").fetchall()
        out = []
        for r in rows:
            status = "Πραγματοποιημένο" if r[10] == "manual_completed" else ("Κλειδωμένο" if r[9] else "Μελλοντικό")
            out.append((r[0], m.fmt_date(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], int(r[9]), r[10], status))
        data = self._csv_bytes(["date_iso", "Ημερομηνία", "Ημέρα", "Χρόνος", "Από", "Έως", "Τομέας", "Εκπαιδευτικός", "Λεπτά", "Τρόπος", "locked", "entry_type", "Κατάσταση"], out)
        return ("file", f"program_{self.db.get_setting('school_year')}_{stamp}.csv", data, "text/csv; charset=utf-8")

    def api_backup(self, p, b):
        sy = self.db.get_setting("school_year", self.app.active_school_year)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "b.efbackup"
            self.app._write_backup_zip(path, sy)
            data = path.read_bytes()
        return ("file", f"Efimeries_Backup_{sy}_{datetime.now().strftime('%Y%m%d_%H%M')}.efbackup", data, "application/octet-stream")

    def _backup_info(self, data: bytes) -> dict:
        import io
        import zipfile
        m = self.mod
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as z:
                manifest = json.loads(z.read("manifest.json").decode("utf-8"))
                if manifest.get("format") != "efimeries-portable-backup":
                    raise ValueError
                dbs = [n for n in z.namelist() if n.lower().endswith(".db")]
                if not dbs:
                    raise ValueError
                blob = z.read(dbs[0])
        except Exception:
            raise ApiError("Το αρχείο δεν είναι έγκυρο backup της εφαρμογής.")
        sy = m.normalize_school_year(manifest.get("school_year", ""))
        problem = m.validate_backup_db(blob)
        if problem:
            raise ApiError(problem)
        return {"school_year": sy, "created_at": manifest.get("created_at", ""), "app_version": manifest.get("app_version", ""),
                "newer": m.version_tuple(manifest.get("app_version", "0")) > m.version_tuple(m.APP_VERSION),
                "exists": m.year_db_path(sy).exists(), "active": sy == self.app.active_school_year, "_blob": blob}

    def _do_restore(self, data: bytes, confirm: bool) -> dict:
        m, app = self.mod, self.app
        info = self._backup_info(data)
        blob = info.pop("_blob")
        if not confirm:
            return {"info": info, "restored": False}
        target = m.year_db_path(info["school_year"])
        app.auto_backup("before-restore")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".restore.tmp")
        if info["active"]:
            app.db.close()
            tmp.write_bytes(blob)
            os.replace(tmp, target)
            app.db = m.DB(target, info["school_year"])
            app.sync_school_year_fields()
            app.refresh_all()
        else:
            tmp.write_bytes(blob)
            os.replace(tmp, target)
            chk = m.DB(target, info["school_year"])
            chk.close()
            app.switch_school_year(info["school_year"], show_message=False)
        return {"info": info, "restored": True}

    def api_backup_inspect(self, p, b):
        return self._do_restore(b.get("_raw") or b"", False)

    def api_backup_restore(self, p, b):
        return self._do_restore(b.get("_raw") or b"", True)

    def _auto_dir(self) -> Path:
        return Path(self.mod.BACKUP_DIR) / "auto"

    def api_backup_list(self, p, b):
        folder = self._auto_dir()
        items = []
        if folder.exists():
            for f in sorted(folder.glob("Efimeries_Auto_*.efbackup"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
                parts = f.stem.split("_")
                items.append({"name": f.name, "year": parts[2] if len(parts) > 2 else "", "reason": parts[3] if len(parts) > 3 else "",
                              "when": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"), "size": f.stat().st_size})
        return {"items": items, "folder": str(self._auto_dir()), "year": self.app.active_school_year}

    def api_backup_restore_auto(self, p, b):
        name = os.path.basename(str(b.get("name", "")))
        f = self._auto_dir() / name
        if not name.startswith("Efimeries_Auto_") or not f.is_file():
            raise ApiError("Δεν βρέθηκε το αντίγραφο")
        return self._do_restore(f.read_bytes(), True)

    # ------------------------------------------------------- σχολικά έτη (v0.66)
    def api_years(self, p, b):
        app, m = self.app, self.mod
        years = app.available_school_years()
        info = []
        for y in years:
            n = None
            if y == app.active_school_year:
                n = self.db.conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
            info.append({"year": y, "active": y == app.active_school_year, "assignments": n})
        opts = [y for y in app.school_year_options() if y not in years]
        defaults = {y: list(app.dates_from_school_year(y)) for y in opts}
        suggested = ""
        try:
            cur = int(app.active_school_year.split("-")[0])
            suggested = f"{cur + 1}-{cur + 2}" if f"{cur + 1}-{cur + 2}" in opts else (opts[0] if opts else "")
        except Exception:
            pass
        return {"years": info, "active": app.active_school_year, "options": opts, "defaults": defaults, "suggested": suggested}

    def api_year_switch(self, p, b):
        app, m = self.app, self.mod
        sy = m.normalize_school_year(str(b.get("year", "")))
        try:
            app.switch_school_year(sy, show_message=False)
        except FileNotFoundError as e:
            raise ApiError(str(e), 404)
        return {"ok": True, "year": sy}

    def api_year_create(self, p, b):
        app, m = self.app, self.mod
        c = b.get("copy") or {}
        opts = {k: 1 if c.get(k) else 0 for k in ("teachers", "availability", "areas", "intervals", "shifts", "mode", "school")}
        try:
            sy = app.create_school_year(str(b.get("year", "")), str(m.ui_date(b.get("start", ""))), str(m.ui_date(b.get("end", ""))), opts)
        except ValueError as e:
            raise ApiError(str(e))
        app.switch_school_year(sy, show_message=False)
        return {"ok": True, "year": sy}

    def api_year_delete(self, p, b):
        app, m = self.app, self.mod
        sy = m.normalize_school_year(str(b.get("year", "")))
        years = app.available_school_years()
        if sy not in years:
            raise ApiError("Το σχολικό έτος δεν βρέθηκε", 404)
        if len(years) <= 1:
            raise ApiError("Δεν μπορεί να διαγραφεί το μοναδικό σχολικό έτος. Δημιούργησε πρώτα ένα νέο.")
        path = m.year_db_path(sy)
        if sy == app.active_school_year:
            app.auto_backup("delete-year")
            nxt = [y for y in years if y != sy][-1]
            app.db.close()
            path.unlink(missing_ok=True)
            app.db = m.DB(m.year_db_path(nxt), nxt)
            app.active_school_year = nxt
            m.ACTIVE_YEAR_FILE.write_text(nxt, encoding="utf-8")
            app.sync_school_year_fields()
            app.refresh_all()
        else:
            if path.exists():
                import sqlite3
                other = sqlite3.connect(path)
                try:
                    app.auto_backup("delete-year", conn=other, sy=sy)
                finally:
                    other.close()
            path.unlink(missing_ok=True)
            app.refresh_school_year_selector()
        return {"ok": True, "active": app.active_school_year}

    # ------------------------------------------------------------ ωρολόγιο (v0.66)
    def api_timetable_template(self, p, b):
        m = self.mod
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.xlsx"
            m.write_xlsx(path, [
                ("Ωρολόγιο", ["Εκπαιδευτικός", "Ημέρα", "Ώρα"],
                 [("Παπαδοπούλου Μαρία", "Δευτέρα", 1), ("Παπαδοπούλου Μαρία", "Δευτέρα", 2), ("Παπαδοπούλου Μαρία", "Τετάρτη", 3),
                  ("Γεωργίου Νίκος", "Τρίτη", 1), ("Γεωργίου Νίκος", "Τρίτη", 2)], [28, 14, 8]),
                ("Ώρες", ["Ώρα", "Από", "Έως"],
                 [(1, "08:15", "09:00"), (2, "09:00", "09:45"), (3, "10:00", "10:45"), (4, "10:45", "11:30"), (5, "11:45", "12:30"), (6, "12:30", "13:15")], [8, 10, 10]),
            ])
            data = path.read_bytes()
        return ("file", "Prototypo_Orologiou.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def api_timetable_preview(self, p, b):
        m = self.mod
        raw = b.get("_raw") or b""
        name = os.path.basename(str(p.get("name", "")))
        ext = Path(name).suffix.lower()
        if ext == ".roz":
            raise ApiError("Το .roz είναι το εσωτερικό αρχείο του aSc Timetables και δεν διαβάζεται απευθείας. Ανοίξτε το στο aSc και κάντε Αρχείο → Εξαγωγή → XML, μετά εισάγετε εδώ το .xml.")
        try:
            tt = m.parse_xlsx_timetable(raw) if ext in (".xlsx", ".xlsm") else m.parse_asc_timetable(raw)
        except Exception as exc:  # noqa: BLE001
            raise ApiError(str(exc))
        info = m.analyze_timetable(tt)
        teachers = self.db.teachers()
        used = set()
        rows = []
        for nm in sorted(info, key=m._plain):
            t = m.match_teacher_name(nm, teachers)
            if t is not None and t.id in used:
                t = None
            if t is not None:
                used.add(t.id)
            i = info[nm]
            rows.append({"name": nm, "teacher_id": t.id if t else None, "days": list(i["days"]), "total": i["total"],
                         "primary": i["primary"], "alternates": list(i["alternates"])})
        have_times = bool(tt.get("periods")) and any(a and z for a, z in tt["periods"].values())
        self._tt = {"tt": tt, "info": info}
        return {"file": name, "rows": rows, "have_times": have_times, "bad_rows": int(tt.get("bad_rows") or 0),
                "teachers": [{"id": t.id, "name": t.name} for t in teachers if t.active]}

    def api_timetable_apply(self, p, b):
        st = getattr(self, "_tt", None)
        if not st:
            raise ApiError("Η προεπισκόπηση έληξε. Επίλεξε ξανά το αρχείο.")
        tt, info = st["tt"], st["info"]
        by_id = {t.id: t for t in self.db.teachers()}
        matches = b.get("matches") or {}
        rows = []
        used = set()
        for nm in sorted(info, key=self.mod._plain):
            tid = matches.get(nm)
            t = by_id.get(int(tid)) if tid not in (None, "", 0) else None
            if t is not None:
                if t.id in used:
                    raise ApiError(f"Ο/Η «{t.name}» έχει επιλεγεί για δύο ονόματα του ωρολογίου.")
                used.add(t.id)
            rows.append((nm, t, info[nm]))
        have_times = bool(tt.get("periods")) and any(a and z for a, z in tt["periods"].values())
        opt = {"days": bool(b.get("days", True)), "primary": bool(b.get("primary", True)), "breaks": bool(b.get("breaks")) and have_times, "create": bool(b.get("create"))}
        if not (opt["days"] or opt["primary"] or opt["breaks"]):
            raise ApiError("Επίλεξε τουλάχιστον ένα στοιχείο προς ενημέρωση.")
        with _Captured() as cap:
            res = self.app._apply_timetable(rows, tt, opt)
        if not res:
            raise ApiError("Η εισαγωγή απέτυχε και δεν έγινε καμία αλλαγή. " + " ".join(cap.messages))
        self.app._absent_cache = None
        self._tt = None
        return {"ok": True, **res}

    # --------------------------------------------------------------- βάρδιες (v0.66)
    def _shift_dict(self, sh) -> dict:
        c = self.db.conn
        general = [r[0] for r in c.execute("SELECT interval_id FROM shift_interval_map WHERE shift_id=? AND weekday=-1", (sh["id"],))]
        over = {str(wd): [r[0] for r in c.execute("SELECT interval_id FROM shift_interval_map WHERE shift_id=? AND weekday=?", (sh["id"], wd))] for wd in range(5)}
        areas = [r[0] for r in c.execute("SELECT area_id FROM shift_area_map WHERE shift_id=?", (sh["id"],))]
        return {"id": sh["id"], "name": sh["name"], "active": int(sh["active"]), "has_reserve": int(sh["has_reserve"]), "sort_order": int(sh["sort_order"]),
                "general": general, "overrides": over, "areas": areas}

    def api_shifts(self, p, b):
        db = self.db
        return {"shifts": [self._shift_dict(sh) for sh in db.shifts(False)],
                "intervals": [{"id": r["id"], "name": r["name"], "start": r["start_time"], "end": r["end_time"]} for r in db.intervals(False)],
                "areas": [{"id": r["id"], "name": r["name"]} for r in db.areas(False)],
                "mode": db.get_setting("mode", "daily")}

    def api_shift_save(self, p, b):
        c = self.db.conn
        name = str(b.get("name") or "").strip()
        general = [int(x) for x in b.get("general") or []]
        areas = [int(x) for x in b.get("areas") or []]
        if not name:
            raise ApiError("Δώσε όνομα βάρδιας.")
        if not general:
            raise ApiError("Επίλεξε τουλάχιστον μία γενική χρονική ζώνη.")
        if not areas:
            raise ApiError("Επίλεξε τουλάχιστον έναν τομέα.")
        sid = int(b.get("id") or 0)
        vals = (name, 1 if b.get("active", 1) else 0, max(0, min(99, int(b.get("sort_order") or 0))), 1 if b.get("has_reserve", 1) else 0)
        try:
            if sid:
                if not c.execute("SELECT 1 FROM shifts WHERE id=?", (sid,)).fetchone():
                    raise ApiError("Η βάρδια δεν βρέθηκε", 404)
                c.execute("UPDATE shifts SET name=?,active=?,sort_order=?,has_reserve=? WHERE id=?", (*vals, sid))
            else:
                sid = c.execute("INSERT INTO shifts(name,active,sort_order,has_reserve) VALUES(?,?,?,?)", vals).lastrowid
            self.app._save_shift_maps(sid, {"general": general, "areas": areas,
                                             "overrides": {int(k): [int(x) for x in v] for k, v in (b.get("overrides") or {}).items() if 0 <= int(k) <= 4}})
            c.commit()
        except ApiError:
            c.rollback()
            raise
        except Exception as e:  # noqa: BLE001
            c.rollback()
            if "UNIQUE" in str(e):
                raise ApiError("Υπάρχει ήδη βάρδια με αυτό το όνομα")
            raise
        self.app.refresh_all()
        return {"ok": True, "id": sid}

    def api_shift_delete(self, p, b):
        c = self.db.conn
        c.execute("DELETE FROM shifts WHERE id=?", (int(b.get("id", 0)),))
        c.commit()
        self.app.refresh_all()
        return {"ok": True}

    def api_teacher_extra(self, p, b):
        db = self.db
        tid = int(p.get("id", 0))
        sav = db.get_teacher_shift_availability(tid)
        iav = db.get_teacher_interval_availability(tid)
        allowed = sorted(db.teacher_shift_allowed_areas(tid))
        return {"shifts": [{"id": s["id"], "name": s["name"]} for s in db.shifts(True)],
                "shift_avail": {f"{wd}-{sid}": v for (wd, sid), v in sav.items()},
                "intervals": [{"id": r["id"], "name": r["name"], "start": r["start_time"], "end": r["end_time"]} for r in db.intervals(True)],
                "interval_avail": {f"{wd}-{iid}": v for (wd, iid), v in iav.items()},
                "areas": [{"id": r["id"], "name": r["name"]} for r in db.areas(True)], "allowed": allowed}

    def api_teacher_extra_save(self, p, b):
        db = self.db
        tid = int(b.get("id", 0))
        if not db.conn.execute("SELECT 1 FROM teachers WHERE id=?", (tid,)).fetchone():
            raise ApiError("Ο/Η εκπαιδευτικός δεν βρέθηκε", 404)
        def pairs(d):
            out = {}
            for k, v in (d or {}).items():
                a, z = k.split("-")
                out[(int(a), int(z))] = 1 if v else 0
            return out
        db.set_teacher_shift_availability(tid, pairs(b.get("shift_avail")))
        db.set_teacher_interval_availability(tid, pairs(b.get("interval_avail")))
        areas = [r["id"] for r in db.areas(True)]
        chosen = [int(x) for x in b.get("allowed") or []] or areas
        db.set_teacher_shift_allowed_areas(tid, chosen, areas)
        self.app._absent_cache = None
        self.app.refresh_all()
        return {"ok": True}

    # ---------------------------------------------------------------- λοιπά
    def api_classic(self, p, b):
        self.show_classic(str(b.get("tab") or ""))
        return {"ok": True}

    def api_setting(self, p, b):
        key = b.get("key")
        if key not in ("ui_mode", "ui_theme"):
            raise ApiError("Μη επιτρεπτή ρύθμιση")
        self.db.set_setting(key, str(b.get("value") or ""))
        return {"ok": True}

    def api_quit(self, p, b):
        self._quit = True
        self.app.after(150, self.app._on_close)
        return {"ok": True}
