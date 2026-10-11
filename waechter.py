#!/usr/bin/env python3
"""Prüft, ob der Sessel REMMELS Zebra Stoff/Chrom bei JYSK online bestellbar ist,
getrennt für jysk.de, jysk.at, jysk.nl und jysk.ch (jeweils eigener Bestand).

Sobald er in einem Land verfügbar wird:
  1. macOS-Mitteilung mit Ton
  2. Push aufs Handy über ntfy.sh (App "ntfy", Thema siehe NTFY_TOPIC)
  3. Produktseite öffnet sich im Standardbrowser -> nur noch "In den Warenkorb"

Läuft lokal per launchd (de.luca.jysk-waechter.plist) und in der Cloud
per GitHub Actions (.github/workflows/waechter.yml), jeweils alle 5 Minuten.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

SHOPS = {
    "DE": {
        "name": "jysk.de",
        "article": "3650325",
        "url": "https://jysk.de/wohnzimmer/sessel/sessel-remmels-zebra-stoff-chrom",
        "atp": "https://jysk.de/websapapi/api/v2/atp/onlineWithStoreStatus/1023"
               "?articleIds=3650325&atpType=ONLINE",
    },
    "AT": {
        "name": "jysk.at",
        "article": "3610119",
        "url": "https://jysk.at/wohnzimmer/sessel/sessel-remmels-zebra-stoff-chrom",
        "atp": "https://jysk.at/api/atp?productIds=3610119&storeId=",
    },
    "NL": {
        "name": "jysk.nl",
        "article": "3650325",
        "url": "https://jysk.nl/woonkamer/fauteuils/fauteuil-remmels-zebra-stof-chroom",
        "atp": "https://jysk.nl/websapapi/api/v2/atp/onlineWithStoreStatus/1009"
               "?articleIds=3650325&atpType=ONLINE",
    },
    "CH": {
        "name": "jysk.ch",
        "article": "3650325",
        "url": "https://jysk.ch/de/wohnzimmer/sessel/sessel-remmels-zebra-stoff-chrom",
        "atp": "https://jysk.ch/websapapi/api/v2/atp/onlineWithStoreStatus/1025"
               "?articleIds=3650325&atpType=ONLINE",
    },
}

HERE = Path(__file__).resolve().parent
IN_CLOUD = os.environ.get("GITHUB_ACTIONS") == "true"
# Push-Thema: in der Cloud aus dem GitHub-Secret, lokal aus ntfy_topic.txt (nicht im Repo)
_topic_file = HERE / "ntfy_topic.txt"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or (_topic_file.read_text().strip() if _topic_file.exists() else "")
# Cloud und Mac führen getrennte Dateien, damit sie sich beim Git-Sync nicht stören
_suffix = "" if IN_CLOUD else "-lokal"
STATE_FILE = HERE / f"status{_suffix}.json"
LOG_FILE = HERE / "waechter.log"
# Eine Zeile pro Datenaktualisierung bei JYSK (Cloud-Version liegt im Repo)
UPDATES_FILE = HERE / f"aktualisierungen{_suffix}.csv"
UPDATES_HEADER = "land;jysk_stand;bemerkt_um;online;demnaechst;filialen\n"
BERLIN = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"


def log(msg):
    line = f"{datetime.now(BERLIN):%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


def check(shop):
    req = urllib.request.Request(shop["atp"], headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.load(r)
    # jysk.at verpackt die Antwort zusätzlich in {"success": ..., "data": {...}}
    data = payload.get("data", payload)[shop["article"]]
    online = data["onlineAtp"]["dcAtp"]["quantity"]
    upcoming = [
        {"datum": n.get("availabilityDate"), "menge": n.get("quantity")}
        for n in data["onlineAtp"].get("nextAtp") or []
    ]
    stores = data["store"]["totalNumberStoresWithATP"]
    stamp = data["onlineAtp"]["dcAtp"].get("timestamp", "")
    return online, upcoming, stores, stamp


def berlin(stamp):
    """JYSK-Zeitstempel (UTC) in deutsche Ortszeit umrechnen."""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(BERLIN).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stamp or "?"


def record_update(land, stamp, online, upcoming, stores):
    if UPDATES_FILE.exists() and not UPDATES_FILE.read_text().startswith("land;"):
        # alte Datei (nur Deutschland) um die Spalte "land" ergänzen
        old = UPDATES_FILE.read_text().splitlines()[1:]
        UPDATES_FILE.write_text(UPDATES_HEADER + "".join(f"DE;{l}\n" for l in old))
    if not UPDATES_FILE.exists():
        UPDATES_FILE.write_text(UPDATES_HEADER)
    with UPDATES_FILE.open("a") as f:
        f.write(f"{land};{berlin(stamp)};{datetime.now(BERLIN):%Y-%m-%d %H:%M:%S};{online};"
                f"{json.dumps(upcoming, ensure_ascii=False)};{stores}\n")


def notify_mac(title, text):
    if IN_CLOUD:
        return
    script = f'display notification {json.dumps(text, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)} sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False)


def notify_phone(title, text, url, priority=5):
    """priority: 5 = dringend, 3 = normal (ntfy-Skala)."""
    if not NTFY_TOPIC:
        return
    msg = {"topic": NTFY_TOPIC, "title": title, "message": text, "priority": priority,
           "click": url, "tags": ["couch_and_lamp"]}
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(msg).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"ntfy-Fehler: {e}")


def load_state():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if "available" in state:  # altes Format (nur Deutschland)
        state = {"DE": state}
    return state


def watch(land, shop, prev, test):
    online, upcoming, stores, stamp = (5, [], 0, "") if test else check(shop)
    available = online > 0
    log(f"[{land}] online={online} demnächst={upcoming} filialen={stores} jysk_stand={berlin(stamp)}")

    if stamp and stamp != prev.get("stamp"):
        log(f"[{land}] DATEN AKTUALISIERT: JYSK-Stand jetzt {berlin(stamp)} (vorher {berlin(prev.get('stamp', ''))})")
        record_update(land, stamp, online, upcoming, stores)

    shop_name = shop["name"]
    if available and not prev.get("available"):
        title = f"REMMELS-Sessel ist da! ({shop_name})" + (" TEST" if test else "")
        text = f"{online} Stück online bei {shop_name} verfügbar. Jetzt in den Warenkorb!"
        notify_mac(title, text)
        notify_phone(title, text, shop["url"])
        if not IN_CLOUD:
            subprocess.run(["open", shop["url"]], check=False)
        log(f"[{land}] BENACHRICHTIGT")
    elif upcoming and upcoming != prev.get("upcoming"):
        # JYSK kennt (neuen) Liefertermin -> Vorwarnung
        termine = ", ".join(f"{u['menge']} Stk. ab {u['datum']}" for u in upcoming)
        title = f"REMMELS: Nachschub angekündigt ({shop_name})"
        notify_mac(title, termine)
        notify_phone(title, termine, shop["url"], priority=3)
        log(f"[{land}] VORWARNUNG: {termine}")

    return {"available": available, "upcoming": upcoming, "stamp": stamp}


def main():
    test = "--test" in sys.argv or os.environ.get("WAECHTER_TEST") == "1"
    state = load_state()
    failed = False

    for land, shop in SHOPS.items():
        try:
            new = watch(land, shop, state.get(land, {}), test)
        except Exception as e:
            log(f"[{land}] Fehler beim Abruf: {e}")
            failed = True
            continue
        if not test:
            state[land] = new

    if not test:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
