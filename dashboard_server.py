# dashboard_server.py
"""
Dashboard wyników stanowisk Combo Test PSU - strona WWW w sieci firmowej.

    python dashboard_server.py                      # katalog i port z config.json (ENG -> Logi)
    python dashboard_server.py --dir "\\\\serwer\\udział\\Combo Stats" --port 8080

Strona: http://<nazwa-komputera>:<port>/   (odświeża się sama co 30 s)

Tylko ODCZYT plików statystyk (stats_store) - serwer niczego nie zapisuje i nie ma
żadnych akcji zmieniających dane. Można go uruchomić na dowolnym komputerze, który
widzi katalog statystyk (np. serwer plików albo PC przy linii).

API (JSON):
  /api/report?day=RRRR-MM-DD&shift=current|all|1|2|3
  /api/days
  /health
"""
import argparse
import json
import socket
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import stats_store
from dashboard_page import PAGE

STATS_DIR = ""
INCLUDE_SIM = False
STALE_MIN = 30          # [min] stanowisko bez testów dłużej = "brak testów"


def build_payload(day: str, shift: str) -> dict:
    now = datetime.now()
    cur = stats_store.shift_info(now)
    if shift == "current" or not day:
        day = cur["day"].strftime("%Y-%m-%d")
    recs = stats_store.read_day(STATS_DIR, day, include_sim=INCLUDE_SIM)
    if shift == "current":
        sel = [r for r in recs if r.get("shift") == cur["num"]]
        shift_num = cur["num"]
    elif shift in ("1", "2", "3"):
        shift_num = int(shift)
        sel = [r for r in recs if r.get("shift") == shift_num]
    else:
        shift_num = None
        sel = recs
    rep = stats_store.build_report(sel)
    last_seen = {}
    for r in recs:
        last_seen[r.get("station", "?")] = r.get("ts", "")
    stations = []
    for name, s in rep["stations"].items():
        ts = last_seen.get(name, "")
        try:
            ago = int((now - datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")).total_seconds() // 60)
        except ValueError:
            ago = None
        stations.append({"name": name, "last": ts[11:16], "ago_min": ago, **s})
    return {
        "day": day,
        "shift": shift,
        "shift_num": shift_num,
        "shift_label": (f"Zmiana {stats_store.shift_name(shift_num)} "
                        f"({stats_store.shift_hours(shift_num)})" if shift_num else "Cały dzień (06:00-06:00)"),
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "current": {"day": cur["day"].strftime("%Y-%m-%d"), "num": cur["num"], "name": cur["name"],
                    "hours": stats_store.shift_hours(cur["num"])},
        "is_live": day == cur["day"].strftime("%Y-%m-%d") and shift_num in (None, cur["num"]),
        "stale_min": STALE_MIN,
        "total": rep["total"],
        "hours": rep["hours"],
        "fail_codes": rep["fail_codes"],
        "profiles": [{"name": k, **v} for k, v in rep["profiles"].items()],
        "stations": stations,
        "shifts": [{"name": n, "num": num, "hours": stats_store.shift_hours(num),
                    **stats_store.summarize([r for r in recs if r.get("shift") == num])}
                   for num, n, _, _ in stats_store.SHIFTS],
        "day_total": stats_store.summarize(recs),
        "last": rep["last"],
        "records": len(sel),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ComboDashboard/1.0"

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif u.path == "/api/report":
                day = (q.get("day") or [""])[0]
                shift = (q.get("shift") or ["current"])[0]
                if day and not stats_store._DAY_RE.match(day):
                    return self._json({"error": "zły format dnia"}, 400)
                if shift not in ("current", "all", "1", "2", "3"):
                    return self._json({"error": "zła zmiana"}, 400)
                self._json(build_payload(day, shift))
            elif u.path == "/api/days":
                self._json({"days": stats_store.list_days(STATS_DIR)})
            elif u.path == "/health":
                self._json({"ok": True, "now": datetime.now().isoformat(timespec="seconds")})
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
        except Exception as e:                       # serwer ma działać dalej
            print(f"[DASH] Błąd: {e}")
            self._json({"error": str(e)}, 500)

    def log_message(self, fmt, *args):
        pass                                          # bez logu każdego odświeżenia


def main():
    global STATS_DIR, INCLUDE_SIM
    ap = argparse.ArgumentParser(description="Combo Test PSU - dashboard")
    ap.add_argument("--dir", help="katalog statystyk (domyślnie z config.json)")
    ap.add_argument("--port", type=int, help="port (domyślnie z config.json, 8080)")
    ap.add_argument("--host", default="0.0.0.0", help="adres nasłuchu (domyślnie wszystkie)")
    ap.add_argument("--sim", action="store_true", help="pokaż wpisy z trybu symulacji (testy)")
    args = ap.parse_args()
    INCLUDE_SIM = args.sim
    port = args.port
    if not args.dir or not port:
        from config import Config
        cfg = Config()
        STATS_DIR = args.dir or cfg.resolve_stats_dir()
        port = port or int(getattr(cfg, "DASHBOARD_PORT", 8080) or 8080)
    else:
        STATS_DIR = args.dir
    srv = ThreadingHTTPServer((args.host, port), Handler)
    srv.daemon_threads = True
    host = socket.gethostname()
    print("=" * 60)
    print("  Combo Test PSU - dashboard")
    print(f"  Katalog statystyk: {STATS_DIR}")
    print(f"  Adres:             http://{host}:{port}/")
    print("  Zatrzymanie: Ctrl+C")
    print("=" * 60)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
