import os, json, re, logging, time
from urllib.parse import urljoin
import argparse, requests
from bs4 import BeautifulSoup

BASE_URL = "https://unjobs.org/duty_stations/bangladesh"
PAGES_TO_CHECK = 2
SEEN_FILE = "seen_jobs.json"
UA = "unjobs-watcher/1.0"
TIMEOUT = 15

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)

def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def parse_jobs(html, base=BASE_URL):
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen_links = [], set()
    for a in soup.find_all("a", href=re.compile(r"/jobs/")):
        title = a.get_text(strip=True)
        href = a.get("href") or ""
        link = urljoin(base, href)
        if not title or link in seen_links:
            continue
        seen_links.add(link)

        # org (best-effort: nearby text that isn't "Updated:")
        org = ""
        node = a
        for _ in range(6):
            node = node.next_sibling or (getattr(a, "parent", None) and a.parent.next_sibling)
            if not node:
                break
            txt = node.get_text(strip=True) if hasattr(node, "get_text") else (node.strip() if isinstance(node, str) else "")
            if txt and not txt.startswith("Updated:"):
                org = txt
                break

        # updated timestamp “Updated: 2025-09-24T09:59:00Z” seen on UNjobs
        upd_node = a.find_next(string=re.compile(r"Updated:\s*\d{4}-\d{2}-\d{2}T"))
        updated = re.search(r"Updated:\s*(\S+)", upd_node).group(1) if upd_node else ""

        job_id = f"{link}::{updated}"
        jobs.append({"id": job_id, "title": title, "org": org, "link": link, "updated": updated})
    return jobs

def send_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=10)
        r.raise_for_status()
        return True
    except Exception:
        logging.exception("Telegram failed")
        return False

def send_email(subject, body):
    host, user, pw, to = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"), os.getenv("ALERT_EMAIL")
    port = int(os.getenv("SMTP_PORT") or 587)
    if not (host and user and pw and to):
        return False
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception:
        logging.exception("Email failed")
        return False

def alert(new_jobs):
    if not new_jobs:
        return
    lines = []
    for j in new_jobs:
        lines += [f"<b>{j['title']}</b>", (j["org"] or ""), j["link"], (f"Updated: {j['updated']}" if j["updated"] else ""), ""]
    msg = "\n".join([x for x in lines if x != ""])
    ok = send_telegram(msg)
    if not ok:
        send_email("New UNjobs - Bangladesh", msg)

def check_once(seen):
    found = []
    for p in range(1, PAGES_TO_CHECK + 1):
        url = BASE_URL if p == 1 else f"{BASE_URL}/{p}"
        try:
            html = fetch(url)
            found += parse_jobs(html)
            time.sleep(1)
        except Exception:
            logging.exception("Fetch/parse error")
    # unique & new
    uniq, seen_ids = set(), []
    for j in found:
        if j["id"] in uniq:
            continue
        uniq.add(j["id"])
        seen_ids.append(j)
    new = [j for j in seen_ids if j["id"] not in seen]
    if new:
        logging.info("New jobs: %d", len(new))
        alert(new)
        seen.update([j["id"] for j in new])
        save_seen(seen)
    else:
        logging.info("No new jobs.")
    return seen

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run a single check (for cron/Actions).")
    args = ap.parse_args()
    seen = load_seen()
    if args.once:
        check_once(seen)
    else:
        # local loop mode (not used by Actions, but handy elsewhere)
        while True:
            seen = check_once(seen)
            time.sleep(30 * 60)
