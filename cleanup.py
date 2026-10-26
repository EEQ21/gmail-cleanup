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
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.prompt import Prompt
from rich.table import Table

SCOPES = ["https://mail.google.com/"]  # full scope is required for permanent delete
DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(DIR, "token.json")
CREDS = os.path.join(DIR, "credentials.json")
console = Console()


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
    console.print(f"\n[bold red]⚠  {msg}[/]")
    return Prompt.ask("[bold]Type [red]yes[/] to confirm[/]", default="no").strip().lower() == "yes"


def clear():
    console.clear()


def ok(msg):
    console.print(f"\n[bold green]✔[/] {msg}")


def info(msg):
    console.print(f"[dim]{msg}[/]")


def fetch_ids(msg, **kw):
    with console.status(f"[cyan]{msg}"):
        return list_ids(**kw)


def delete_sender(svc):
    addr = Prompt.ask("[bold cyan]Sender email address[/]").strip()
    if addr:
        delete_from(svc, addr)


def delete_from(svc, addr):
    ids = fetch_ids(f"Searching emails from {addr}...", svc=svc, q=f"from:{addr}")
    if not ids:
        info("No emails found from that address.")
        return
    stats = Table.grid(padding=(0, 2))
    stats.add_row("[dim]Emails[/]", f"[bold yellow]{len(ids)}[/]")
    stats.add_row("[dim]First received[/]", msg_date(svc, ids[-1]))
    stats.add_row("[dim]Most recent[/]", msg_date(svc, ids[0]))
    console.print(Panel(stats, title=f"[bold]{addr}[/]", border_style="cyan", expand=False))
    if confirm(f"PERMANENTLY delete these {len(ids)} emails? This cannot be undone."):
        with console.status("[red]Deleting..."):
            batch_delete(svc, ids)
        ok(f"Deleted {len(ids)} emails.")


def clear_spam(svc):
    clear_category(svc, "SPAM", "Spam")


def clear_category(svc, label, name):
    ids = fetch_ids(f"Counting emails in {name}...", svc=svc, labels=[label])
    if not ids:
        info(f"{name} is already empty.")
    elif confirm(f"Permanently delete {len(ids)} emails in {name}?"):
        with console.status("[red]Deleting..."):
            batch_delete(svc, ids)
        ok(f"Deleted {len(ids)} emails from {name}.")


def clear_drafts(svc):
    drafts, token = [], None
    while True:
        with console.status("[cyan]Loading drafts..."):
            r = svc.users().drafts().list(userId="me", pageToken=token, maxResults=500).execute()
        drafts += r.get("drafts", [])
        token = r.get("nextPageToken")
        if not token:
            break
    if not drafts:
        info("No drafts.")
    elif confirm(f"Permanently delete {len(drafts)} drafts?"):
        with progress() as bar:
            task = bar.add_task("Deleting drafts", total=len(drafts))
            for d in drafts:
                svc.users().drafts().delete(userId="me", id=d["id"]).execute()
                bar.advance(task)
        ok(f"Deleted {len(drafts)} drafts.")


def progress():
    return Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), BarColumn(),
                    MofNCompleteColumn(), TimeRemainingColumn(), console=console)


def scan_senders(svc):
    ids = fetch_ids("Listing emails...", svc=svc, q="-in:spam -in:trash -in:drafts -in:sent")
    senders, delay = Counter(), 0.5

    queue = list(ids)
    with progress() as bar:
        task = bar.add_task("Scanning mailbox", total=len(ids))
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
            bar.advance(task, len(chunk) - len(failed))
            queue = failed + queue  # retry rate-limited ones right away, after a pause
            delay = min(delay * 2, 10) if failed else 0.5
            time.sleep(delay)
    return senders


def list_senders(svc):
    senders = scan_senders(svc).most_common()
    if not senders:
        info("No emails found.")
        return
    clear()
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold magenta", row_styles=["", "dim"],
                  title=f"[bold]{len(senders)} senders · {sum(c for _, c in senders)} emails[/]")
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Emails", justify="right", style="bold yellow")
    table.add_column("Sender")
    for n, (addr, cnt) in enumerate(senders, 1):
        table.add_row(str(n), str(cnt), addr)
    console.print(table)
    pick = Prompt.ask("[bold cyan]Numbers to delete[/] [dim](e.g. 1,3,5-8, 'all', or Enter to cancel)[/]",
                      default="", show_default=False).strip().lower()
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
                console.print(f"[yellow]Ignoring invalid entry: {part}[/]")
    addrs = [senders[i][0] for i in sorted(chosen) if 0 <= i < len(senders) and senders[i][0] != "(unknown)"]
    if not addrs:
        return
    total = sum(dict(senders)[a] for a in addrs)
    console.print(Panel("\n".join(addrs), title="[bold]Selected senders[/]", border_style="red", expand=False))
    if not confirm(f"PERMANENTLY delete all emails from these {len(addrs)} senders (~{total} emails)?"):
        return
    deleted = 0
    with progress() as bar:
        task = bar.add_task("Deleting", total=len(addrs))
        for a in addrs:
            ids = list_ids(svc, q=f"from:{a}")
            batch_delete(svc, ids)
            deleted += len(ids)
            bar.console.print(f"  [green]✔[/] {len(ids):>5}  [dim]{a}[/]")
            bar.advance(task)
    ok(f"Deleted {deleted} emails from {len(addrs)} senders.")


def main():
    with console.status("[cyan]Connecting to Gmail..."):
        svc = connect()
        me = svc.users().getProfile(userId="me").execute()["emailAddress"]
    actions = {"1": delete_sender, "2": list_senders, "3": clear_drafts, "4": clear_spam,
               "5": lambda svc: clear_category(svc, "CATEGORY_SOCIAL", "Social"),
               "6": lambda svc: clear_category(svc, "CATEGORY_PROMOTIONS", "Promotions (Reclame)"),
               "7": lambda svc: clear_category(svc, "CATEGORY_UPDATES", "Updates")}
    items = [("1", "✉", "Delete all emails from a sender"), ("2", "☰", "List all senders (and delete)"),
             ("3", "✎", "Clear drafts (concepts)"), ("4", "⚠", "Clear spam"), ("5", "☺", "Clear Social"),
             ("6", "★", "Clear Promotions (Reclame)"), ("7", "↻", "Clear Updates"), ("q", "⏻", "Quit")]
    menu = Table.grid(padding=(0, 2))
    for key, icon, label in items:
        menu.add_row(f"[bold cyan]{key}[/]", icon, label)
    while True:
        clear()
        console.print(Panel(menu, title="[bold magenta]📬 Gmail Cleanup[/]", subtitle=f"[dim]{me}[/]",
                            border_style="magenta", padding=(1, 3), expand=False))
        c = Prompt.ask("[bold cyan]›[/]").strip().lower()
        if c == "q":
            break
        if c in actions:
            clear()
            actions[c](svc)
            Prompt.ask("\n[dim]Press Enter to return to the menu[/]", default="", show_default=False)


if __name__ == "__main__":
    main()
