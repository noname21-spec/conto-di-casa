#!/usr/bin/env python3
"""Scarica i prezzi dei carburanti del MIMIT (open data, ore 8) e crea:
- data/carburanti/index.json  medie self per provincia e nazionali
- data/carburanti/<SIGLA>.json  distributori della provincia con i prezzi
Gira ogni mattina su GitHub Actions."""
import csv, io, json, os, statistics, sys, urllib.request
from datetime import datetime, timedelta

BASE = "https://www.mimit.gov.it/images/exportCSV/"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "carburanti")
UA = {"User-Agent": "conto-di-casa-bot/1.0 (+https://github.com/noname21-spec/conto-di-casa)"}

HOSTS = ["https://www.mimit.gov.it/images/exportCSV/", "https://mimit.gov.it/images/exportCSV/"]
BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "Accept": "text/csv,text/plain,*/*", "Accept-Language": "it-IT,it;q=0.9"}

def scarica(nome):
    import subprocess, time
    ultimo = None
    for tentativo in range(4):
        for base in HOSTS:
            try:
                out = subprocess.run(["curl", "-sSfL", "--retry", "2", "--retry-delay", "10", "--connect-timeout", "60",
                                      "--max-time", "420", "-H", "User-Agent: " + BROWSER["User-Agent"],
                                      "-H", "Accept-Language: it-IT,it;q=0.9", base + nome],
                                     capture_output=True, timeout=480)
                if out.returncode == 0 and len(out.stdout) > 10000:
                    raw = out.stdout
                    break
                ultimo = out.stderr.decode(errors="replace").strip() or f"codice {out.returncode}"
            except Exception as e:
                ultimo = str(e)
        else:
            print(f"Tentativo {tentativo + 1} non riuscito per {nome}: {ultimo}", file=sys.stderr)
            time.sleep(30)
            continue
        break
    else:
        raise RuntimeError(f"download di {nome} non riuscito: {ultimo}")
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")

def leggi(testo):
    righe = testo.splitlines()
    estrazione = righe[0].replace("Estrazione del", "").strip()
    header = righe[1]
    sep = "|" if header.count("|") >= header.count(";") else ";"
    reader = csv.reader(io.StringIO("\n".join(righe[1:])), delimiter=sep, quotechar='"')
    cols = [c.strip() for c in next(reader)]
    out = []
    for row in reader:
        if len(row) < len(cols):
            continue
        out.append({c: (row[i].strip() if i < len(row) else "") for i, c in enumerate(cols)})
    return estrazione, out

def num(x):
    try:
        return float(str(x).replace(",", "."))
    except ValueError:
        return None

def tipo(desc):
    d = desc.strip().lower()
    if d == "benzina": return "benzina"
    if d == "gasolio": return "diesel"
    if d == "gpl": return "gpl"
    if d == "metano": return "metano"
    return None

LIMITI = {"benzina": (1.2, 3.5), "diesel": (1.2, 3.5), "gpl": (0.4, 1.6), "metano": (0.8, 3.5)}

def main():
    est_a, anag = leggi(scarica("anagrafica_impianti_attivi.csv"))
    est_p, prezzi = leggi(scarica("prezzo_alle_8.csv"))
    try:
        oggi = datetime.strptime(est_p, "%Y-%m-%d")
    except ValueError:
        oggi = datetime.utcnow()
    limite = oggi - timedelta(days=14)

    impianti = {}
    for r in anag:
        pid = r.get("idImpianto") or r.get("idimpianto")
        if not pid:
            continue
        impianti[pid] = {
            "n": (r.get("Nome Impianto") or "").title()[:60],
            "b": (r.get("Bandiera") or "").strip()[:30],
            "a": (r.get("Indirizzo") or "").title()[:80],
            "c": (r.get("Comune") or "").upper(),
            "pr": (r.get("Provincia") or "").upper(),
            "lat": num(r.get("Latitudine")),
            "lon": num(r.get("Longitudine")),
            "p": {},
        }

    for r in prezzi:
        pid = r.get("idImpianto") or r.get("idimpianto")
        imp = impianti.get(pid)
        t = tipo(r.get("descCarburante", ""))
        p = num(r.get("prezzo"))
        if not imp or not t or p is None:
            continue
        lo, hi = LIMITI[t]
        if not (lo <= p <= hi):
            continue
        self_ = str(r.get("isSelf", "")).strip() == "1"
        if t in ("benzina", "diesel") and not self_:
            continue
        try:
            dt = datetime.strptime(r.get("dtComu", "").strip(), "%d/%m/%Y %H:%M:%S")
            if dt < limite:
                continue
        except ValueError:
            pass
        old = imp["p"].get(t)
        imp["p"][t] = round(p if old is None else min(old, p), 3)

    per_prov = {}
    for imp in impianti.values():
        if not imp["p"] or not imp["pr"]:
            continue
        per_prov.setdefault(imp["pr"], []).append(imp)

    def media(vals):
        vals = sorted(vals)
        if not vals:
            return None
        k = int(len(vals) * 0.05)
        core = vals[k:len(vals) - k] or vals
        return round(statistics.mean(core), 3)

    os.makedirs(OUT, exist_ok=True)
    idx = {"aggiornato": est_p, "fonte": "MIMIT, Osservatorio prezzi carburanti (prezzi alle 8)", "nazionale": {}, "province": {}}
    tutti = {t: [] for t in LIMITI}
    for sig, lst in sorted(per_prov.items()):
        info = {"n": len(lst)}
        for t in LIMITI:
            vals = [i["p"][t] for i in lst if t in i["p"]]
            tutti[t] += vals
            m = media(vals)
            if m is not None:
                info[t] = m
                info[t + "_min"] = min(vals)
        idx["province"][sig] = info
        stazioni = [{k: v for k, v in i.items() if k != "pr" and v not in (None, "")} for i in lst]
        stazioni.sort(key=lambda s: (s["c"], s["n"]))
        with open(os.path.join(OUT, f"{sig}.json"), "w", encoding="utf-8") as f:
            json.dump({"aggiornato": est_p, "provincia": sig, "stazioni": stazioni}, f, ensure_ascii=False, separators=(",", ":"))
    for t, vals in tutti.items():
        m = media(vals)
        if m is not None:
            idx["nazionale"][t] = m
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Estrazione {est_p}: {len(per_prov)} province, {sum(len(v) for v in per_prov.values())} impianti")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Aggiornamento non riuscito:", e, file=sys.stderr)
        sys.exit(1)
