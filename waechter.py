#!/usr/bin/env python3
"""Prüft, ob der Sessel REMMELS (JYSK Art. 3650325) online bestellbar ist.

Sobald er verfügbar wird:
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
import time
import urllib.request
from pathlib import Path

ARTICLE_ID = "3650325"
PRODUCT_URL = "https://jysk.de/wohnzimmer/sessel/sessel-remmels-zebra-stoff-chrom"
ATP_URL = (
    "https://jysk.de/websapapi/api/v2/atp/onlineWithStoreStatus/1023"
    f"?articleIds={ARTICLE_ID}&atpType=ONLINE"
)
HERE = Path(__file__).resolve().parent
IN_CLOUD = os.environ.get("GITHUB_ACTIONS") == "true"
# Push-Thema: in der Cloud aus dem GitHub-Secret, lokal aus ntfy_topic.txt (nicht im Repo)
_topic_file = HERE / "ntfy_topic.txt"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or (_topic_file.read_text().strip() if _topic_file.exists() else "")
STATE_FILE = HERE / "status.json"
LOG_FILE = HERE / "waechter.log"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


def check():
    req = urllib.request.Request(ATP_URL, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)[ARTICLE_ID]
    online = data["onlineAtp"]["dcAtp"]["quantity"]
    upcoming = data["onlineAtp"].get("nextAtp") or []
    stores = data["store"]["totalNumberStoresWithATP"]
    return online, upcoming, stores


def notify_mac(title, text):
    if IN_CLOUD:
        return
    script = f'display notification {json.dumps(text, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)} sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False)


def notify_phone(title, text, priority="urgent"):
    if not NTFY_TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=text.encode(),
        headers={"Title": title, "Priority": priority, "Click": PRODUCT_URL, "Tags": "couch_and_lamp"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"ntfy-Fehler: {e}")


def main():
    test = "--test" in sys.argv or os.environ.get("WAECHTER_TEST") == "1"
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"available": False}

    try:
        online, upcoming, stores = (5, [], 0) if test else check()
    except Exception as e:
        log(f"Fehler beim Abruf: {e}")
        sys.exit(1)

    available = online > 0
    log(f"online={online} demnächst={upcoming} filialen={stores}")

    if available and not state.get("available"):
        title = "REMMELS-Sessel ist da!" + (" (TEST)" if test else "")
        text = f"{online} Stück online bei JYSK verfügbar. Jetzt in den Warenkorb!"
        notify_mac(title, text)
        notify_phone(title, text)
        if not IN_CLOUD:
            subprocess.run(["open", PRODUCT_URL], check=False)
        log("BENACHRICHTIGT")
    elif upcoming and not state.get("upcoming"):
        # JYSK kennt einen Liefertermin -> Vorwarnung
        text = f"JYSK erwartet neue Ware: {json.dumps(upcoming, ensure_ascii=False)[:200]}"
        notify_mac("REMMELS: Nachschub angekündigt", text)
        notify_phone("REMMELS: Nachschub angekündigt", text, priority="default")
        log("VORWARNUNG")

    if not test:
        STATE_FILE.write_text(json.dumps({"available": available, "upcoming": bool(upcoming)}))


if __name__ == "__main__":
    main()
