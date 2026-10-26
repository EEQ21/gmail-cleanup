#!/usr/bin/env python3
"""Gmail cleanup tool: permanently delete mail from a sender, clear drafts and spam."""
import os
import time
from collections import Counter
from email.utils import parseaddr
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://mail.google.com/"]  # full scope is required for permanent delete
DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(DIR, "token.json")
CREDS = os.path.join(DIR, "credentials.json")


def connect():
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES) if os.path.exists(TOKEN) else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES).run_local_server(port=0)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def list_ids(svc, q=None, labels=None):
    ids, token = [], None
    while True:
        r = svc.users().messages().list(userId="me", q=q, labelIds=labels, pageToken=token,
                                        includeSpamTrash=True, maxResults=500).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        token = r.get("nextPageToken")
        if not token:
            return ids


def msg_date(svc, mid):
    m = svc.users().messages().get(userId="me", id=mid, format="minimal").execute()
    return datetime.fromtimestamp(int(m["internalDate"]) / 1000).strftime("%Y-%m-%d %H:%M")


def batch_delete(svc, ids):
    for i in range(0, len(ids), 1000):
        svc.users().messages().batchDelete(userId="me", body={"ids": ids[i:i + 1000]}).execute()


def confirm(msg):
    return input(f"{msg} Type 'yes' to confirm: ").strip().lower() == "yes"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def delete_sender(svc):
    addr = input("Sender email address: ").strip()
    if addr:
        delete_from(svc, addr)


def delete_from(svc, addr):
    ids = list_ids(svc, q=f"from:{addr}")
    if not ids:
        print("No emails found from that address.")
        return
    print(f"\n  Emails from {addr}: {len(ids)}")
    print(f"  First received:  {msg_date(svc, ids[-1])}")
    print(f"  Most recent:     {msg_date(svc, ids[0])}\n")
    if confirm(f"PERMANENTLY delete these {len(ids)} emails? This cannot be undone."):
        batch_delete(svc, ids)
        print(f"Deleted {len(ids)} emails.")


def clear_spam(svc):
    ids = list_ids(svc, labels=["SPAM"])
    if not ids:
        print("Spam is already empty.")
    elif confirm(f"Permanently delete {len(ids)} spam emails?"):
        batch_delete(svc, ids)
        print(f"Deleted {len(ids)} spam emails.")


def clear_category(svc, label, name):
    ids = list_ids(svc, labels=[label])
    if not ids:
        print(f"{name} is already empty.")
    elif confirm(f"Permanently delete {len(ids)} emails in {name}?"):
        batch_delete(svc, ids)
        print(f"Deleted {len(ids)} emails from {name}.")


def clear_drafts(svc):
    drafts, token = [], None
    while True:
        r = svc.users().drafts().list(userId="me", pageToken=token, maxResults=500).execute()
        drafts += r.get("drafts", [])
        token = r.get("nextPageToken")
        if not token:
            break
    if not drafts:
        print("No drafts.")
    elif confirm(f"Permanently delete {len(drafts)} drafts?"):
        for d in drafts:
            svc.users().drafts().delete(userId="me", id=d["id"]).execute()
        print(f"Deleted {len(drafts)} drafts.")


def scan_senders(svc):
    print("Scanning mailbox (this can take a while for large mailboxes)...")
    ids = list_ids(svc, q="-in:spam -in:trash -in:drafts -in:sent")
    senders, done, delay = Counter(), 0, 0.5

    queue = list(ids)
    while queue:
        chunk, queue = queue[:25], queue[25:]
        failed = []

        def cb(rid, resp, err):
            if err:
                failed.append(rid)
                return
            hdr = next((h["value"] for h in resp["payload"].get("headers", []) if h["name"].lower() == "from"), "")
            senders[parseaddr(hdr)[1].lower() or "(unknown)"] += 1

        batch = svc.new_batch_http_request(callback=cb)
        for mid in chunk:
            batch.add(svc.users().messages().get(userId="me", id=mid, format="metadata",
                                                 metadataHeaders=["From"]), request_id=mid)
        batch.execute()
        done += len(chunk) - len(failed)
        queue = failed + queue  # retry rate-limited ones right away, after a pause
        delay = min(delay * 2, 10) if failed else 0.5
        print(f"\r  {done}/{len(ids)}   ", end="", flush=True)
        time.sleep(delay)
    print()
    return senders


def list_senders(svc):
    senders = scan_senders(svc).most_common()
    if not senders:
        print("No emails found.")
        return
    clear()
    print(f"{'#':>4}  {'Emails':>6}  Sender")
    for n, (addr, cnt) in enumerate(senders, 1):
        print(f"{n:>4}  {cnt:>6}  {addr}")
    pick = input("\nNumbers to delete (e.g. 1,3,5-8, 'all', or Enter to cancel): ").strip().lower()
    if not pick:
        return
    chosen = set()
    if pick == "all":
        chosen = set(range(len(senders)))
    else:
        for part in pick.replace(" ", "").split(","):
            try:
                a, _, b = part.partition("-")
                chosen.update(range(int(a) - 1, int(b or a)))
            except ValueError:
                print(f"Ignoring invalid entry: {part}")
    addrs = [senders[i][0] for i in sorted(chosen) if 0 <= i < len(senders) and senders[i][0] != "(unknown)"]
    if not addrs:
        return
    total = sum(dict(senders)[a] for a in addrs)
    print("\nSelected:\n  " + "\n  ".join(addrs))
    if not confirm(f"PERMANENTLY delete all emails from these {len(addrs)} senders (~{total} emails)?"):
        return
    for a in addrs:
        ids = list_ids(svc, q=f"from:{a}")
        batch_delete(svc, ids)
        print(f"Deleted {len(ids)} from {a}")


def main():
    svc = connect()
    me = svc.users().getProfile(userId="me").execute()["emailAddress"]
    actions = {"1": delete_sender, "2": list_senders, "3": clear_drafts, "4": clear_spam,
               "5": lambda svc: clear_category(svc, "CATEGORY_SOCIAL", "Social"),
               "6": lambda svc: clear_category(svc, "CATEGORY_PROMOTIONS", "Promotions (Reclame)"),
               "7": lambda svc: clear_category(svc, "CATEGORY_UPDATES", "Updates")}
    while True:
        clear()
        print(f"Gmail Cleanup - {me}\n")
        print("1) Delete all emails from a sender\n2) List all senders (and delete)\n"
              "3) Clear drafts (concepts)\n4) Clear spam\n"
              "5) Clear Social\n6) Clear Promotions (Reclame)\n7) Clear Updates\nq) Quit")
        c = input("> ").strip().lower()
        if c == "q":
            break
        if c in actions:
            clear()
            actions[c](svc)
            input("\nPress Enter to return to the menu...")


if __name__ == "__main__":
    main()
