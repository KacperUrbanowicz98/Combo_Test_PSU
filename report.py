"""Raporty z weryfikacji: CSV (Excel PL, separator ';') + podsumowanie TXT."""
import csv
import datetime as dt
import os

from config import APP_NAME, APP_VERSION
from verifier import result_rows

COLUMNS = [
    ("case_id", "ID"), ("group", "Grupa"), ("title", "Test"), ("step_no", "Krok"), ("sent", "Wysłano"),
    ("query", "Zapytanie"), ("expect", "Oczekiwano"), ("received", "Odebrano"), ("status", "Status"),
    ("ms", "Czas [ms]"), ("detail", "Szczegóły"), ("page", "Str. manuala"), ("kind", "Rodzaj"),
]


def save_reports(results, folder, meta):
    """meta: dict(idn, port, transport, operator, summary). Zwraca (csv_path, txt_path)."""
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(folder, f"OEL85_verify_{stamp}")
    csv_path, txt_path = base + ".csv", base + ".txt"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f, delimiter=";")
        wr.writerow([h for _, h in COLUMNS])
        for row in result_rows(results):
            wr.writerow([str(row.get(k, "")).replace(".", ",") if k == "ms" else row.get(k, "")
                         for k, _ in COLUMNS])

    s = meta.get("summary", {})
    counts = s.get("counts", {})
    lines = [
        "OEL85 Command Verifier - raport",
        "=" * 60,
        f"Aplikacja:   {APP_NAME} {APP_VERSION}",
        f"Data:        {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Urządzenie:  {meta.get('idn', '')}",
        f"Połączenie:  {meta.get('transport', '')} {meta.get('port', '')}",
        f"Czas testu:  {s.get('duration_s', '')} s",
        f"Przerwano:   {s.get('aborted') or 'nie'}",
        "",
        "Podsumowanie (testy):",
    ]
    for k in sorted(counts):
        lines.append(f"  {k:10s} {counts[k]}")
    lines += ["", "Testy niezaliczone / bez odpowiedzi:"]
    bad = [r for r in results if r.status not in ("PASS", "INFO ✓", "INFO ✗", "SKIP", "ZNANY FW")]
    if not bad:
        lines.append("  brak")
    for r in bad:
        lines.append(f"  [{r.status}] {r.case_id} - {r.title} (str. {r.page})")
        for st in r.steps:
            if st.status != "PASS":
                lines.append(f"      krok {st.step_no}: TX '{st.sent}' Q '{st.query}' -> '{st.received}' "
                             f"(oczek. {st.expect}) {st.detail}")
    lines += ["", "Znane niezgodności firmware (potwierdzone wcześniej, nie liczone jako nowe błędy):"]
    known = [r for r in results if r.status == "ZNANY FW"]
    if not known:
        lines.append("  brak")
    for r in known:
        lines.append(f"  {r.case_id} - {r.note}")
    lines += ["", "Sondy (niejasności manuala / komendy nieudokumentowane):"]
    for r in results:
        if r.kind == "probe":
            rx = "; ".join(st.received or "-" for st in r.steps)
            lines.append(f"  [{r.status}] {r.case_id} - {r.title} -> {rx}")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return csv_path, txt_path
