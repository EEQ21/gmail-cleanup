#!/usr/bin/env python3
"""Gmail Cleanup TUI: bulk-delete mail by sender, see sender stats, clear drafts, spam and tabs."""
import os
import time
from collections import Counter
from datetime import datetime
from email.utils import parseaddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from rich.table import Table
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (Button, ContentSwitcher, DataTable, Footer, Header, Input, Label,
                             ListItem, ListView, ProgressBar, RichLog, Static)

SCOPES = ["https://mail.google.com/"]  # full scope is required for permanent delete
DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(DIR, "token.json")
CREDS = os.path.join(DIR, "credentials.json")

CATEGORIES = {"spam": ("SPAM", "Spam"), "social": ("CATEGORY_SOCIAL", "Social"),
              "promo": ("CATEGORY_PROMOTIONS", "Promotions (Reclame)"),
              "updates": ("CATEGORY_UPDATES", "Updates")}
MENU = [("sender", "[cyan]●[/] Delete by sender"), ("senders", "[cyan]●[/] All senders"),
        ("drafts", "[red]●[/] Clear drafts"), ("spam", "[red]●[/] Clear spam"),
        ("social", "[red]●[/] Clear Social"), ("promo", "[red]●[/] Clear Promotions"),
        ("updates", "[red]●[/] Clear Updates")]
COUNTED = {"drafts": "DRAFT", **{k: label for k, (label, _) in CATEGORIES.items()}}
WELCOME = """[b magenta]Gmail Cleanup[/]

Pick an action on the left with [b cyan]↑ ↓[/] and [b cyan]Enter[/].

[b]Delete by sender[/]  type an address, see stats, delete everything from it
[b]All senders[/]       scan your mailbox and see who sends you the most
[b]Clear ...[/]         empty drafts, spam or a Gmail tab in one go

[b red]Deletes are permanent[/] — emails skip the Trash.
[dim]Ctrl+Q quits.[/]"""


# ---------- Gmail ----------

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


def list_drafts(svc):
    drafts, token = [], None
    while True:
        r = svc.users().drafts().list(userId="me", pageToken=token, maxResults=500).execute()
        drafts += r.get("drafts", [])
        token = r.get("nextPageToken")
        if not token:
            return drafts


def scan_senders(svc, on_progress):
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
        on_progress(done, len(ids))
        queue = failed + queue  # retry rate-limited ones right away, after a pause
        delay = min(delay * 2, 10) if failed else 0.5
        time.sleep(delay)
    return senders


# ---------- TUI ----------

class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    def compose(self):
        with Vertical(id="dialog"):
            yield Label("[b red]Permanent delete[/]")
            yield Label(self.msg, id="question")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="no")
                yield Button("Delete", variant="error", id="yes")

    def on_mount(self):
        self.query_one("#no").focus()

    def on_button_pressed(self, e):
        self.dismiss(e.button.id == "yes")

    def action_cancel(self):
        self.dismiss(False)


class SenderTable(DataTable):
    BINDINGS = [Binding("space", "toggle", "Mark"), Binding("a", "mark_all", "Mark all"),
                Binding("n", "unmark_all", "Unmark all")]
    marked: set

    def set_mark(self, key, on):
        (self.marked.add if on else self.marked.discard)(key)
        self.update_cell(key, "mark", "[b red]✔[/]" if on else "")

    def action_toggle(self):
        if self.row_count:
            key = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
            self.set_mark(key, key not in self.marked)
            self.action_cursor_down()

    def action_mark_all(self):
        for row in list(self.rows):
            self.set_mark(row.value, True)

    def action_unmark_all(self):
        for row in list(self.rows):
            self.set_mark(row.value, False)


class CleanupApp(App):
    TITLE = "Gmail Cleanup"
    CSS = """
    #menu { width: 30; border: round $accent; }
    #main { padding: 0 1; }
    #views { height: 1fr; }
    #home { padding: 1 2; }
    #sender, #senders { padding: 1 0 0 0; }
    #stats { height: auto; margin: 1 0; }
    .bar { height: auto; }
    .bar Button { margin-right: 2; }
    SenderTable { height: 1fr; border: round $primary; }
    #progress { height: 1; margin: 1 0 0 0; }
    #log { height: 9; border: round $primary; }
    Confirm { align: center middle; }
    #dialog { width: 64; height: auto; border: thick $error; background: $surface; padding: 1 2; }
    #question { margin: 1 0; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    """

    def __init__(self, svc, me):
        super().__init__()
        self.svc, self.me, self.busy, self.sender_ids, self.counts = svc, me, False, [], {}

    def compose(self):
        yield Header()
        with Horizontal():
            yield ListView(*[ListItem(Label(t + (" [dim](…)[/]" if k in COUNTED else ""), id=f"l-{k}"),
                                      id=f"m-{k}") for k, t in MENU], id="menu")
            with Vertical(id="main"):
                with ContentSwitcher(initial="home", id="views"):
                    yield Static(WELCOME, id="home")
                    with Vertical(id="sender"):
                        yield Input(placeholder="sender@example.com  (Enter to look up)", id="addr")
                        yield Static(id="stats")
                        yield Button("Delete all from this sender", variant="error", id="del-sender", disabled=True)
                    with Vertical(id="senders"):
                        with Horizontal(classes="bar"):
                            yield Button("Scan mailbox", variant="primary", id="scan")
                            yield Button("Delete marked", variant="error", id="del-marked")
                        yield SenderTable(id="table", cursor_type="row", zebra_stripes=True)
                yield ProgressBar(id="progress", show_eta=True)
                yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self):
        self.sub_title = self.me
        self.query_one("#log").border_title = "Activity"
        self.query_one("#menu").border_title = "Actions"
        t = self.query_one(SenderTable)
        t.marked = set()
        t.add_column("✓", key="mark", width=2)
        t.add_column("#", key="n")
        t.add_column("Emails", key="count")
        t.add_column("Sender", key="addr")
        self.query_one("#menu").focus()
        self.refresh_counts()

    def refresh_counts(self):
        def run():
            for key, label in COUNTED.items():
                try:
                    n = self.svc.users().labels().get(userId="me", id=label).execute()["messagesTotal"]
                except Exception:
                    n = "?"
                text = dict(MENU)[key] + f" [dim]({n})[/]"
                self.call_from_thread(self.query_one(f"#l-{key}", Label).update, text)
        self.run_worker(run, thread=True, group="counts")

    # helpers usable from worker threads
    def say(self, msg):
        self.call_from_thread(self.query_one(RichLog).write, msg)

    def progress(self, done, total):
        self.call_from_thread(self.query_one(ProgressBar).update, total=total, progress=done)

    def ask(self, msg, on_yes):
        self.call_from_thread(self.push_screen, Confirm(msg), lambda ok: ok and on_yes())

    def start(self, fn, *args):
        if self.busy:
            self.notify("Still working on the previous task…", severity="warning")
            return
        self.busy = True

        def run():
            try:
                fn(*args)
            except Exception as e:
                self.say(f"[b red]Error:[/] {e}")
            finally:
                self.busy = False
                self.call_from_thread(self.refresh_counts)
        self.run_worker(run, thread=True)

    # events
    def on_list_view_selected(self, e):
        key = e.item.id[2:]
        if key in ("sender", "senders"):
            self.query_one("#views").current = key
            self.query_one("#addr" if key == "sender" else SenderTable).focus()
        elif key == "drafts":
            self.start(self.drafts_count)
        else:
            self.start(self.category_count, *CATEGORIES[key])

    def on_input_submitted(self, e):
        if e.value.strip():
            self.start(self.sender_lookup, e.value.strip())

    def on_button_pressed(self, e):
        if e.button.id == "del-sender":
            addr = self.query_one("#addr").value.strip()
            self.push_screen(Confirm(f"Delete all [b]{len(self.sender_ids)}[/] emails from [b]{addr}[/]?"),
                             lambda ok: ok and self.start(self.delete_senders, [addr]))
        elif e.button.id == "scan":
            self.start(self.scan)
        elif e.button.id == "del-marked":
            marked = sorted(self.query_one(SenderTable).marked)
            if not marked:
                self.notify("Mark senders with Space first (A marks all).", severity="warning")
                return
            total = sum(self.counts[a] for a in marked)
            self.push_screen(Confirm(f"Delete all emails from [b]{len(marked)}[/] senders (~[b]{total}[/] emails)?"),
                             lambda ok: ok and self.start(self.delete_senders, marked))

    # workers (run in threads)
    def sender_lookup(self, addr):
        self.say(f"Looking up [cyan]{addr}[/]…")
        self.sender_ids = ids = list_ids(self.svc, q=f"from:{addr}")
        btn = self.query_one("#del-sender")
        if not ids:
            self.call_from_thread(self.query_one("#stats").update, "[dim]No emails found from that address.[/]")
            self.call_from_thread(setattr, btn, "disabled", True)
            return
        grid = Table.grid(padding=(0, 3))
        grid.add_row("[dim]Sender[/]", f"[b]{addr}[/]")
        grid.add_row("[dim]Emails[/]", f"[b yellow]{len(ids)}[/]")
        grid.add_row("[dim]First received[/]", msg_date(self.svc, ids[-1]))
        grid.add_row("[dim]Most recent[/]", msg_date(self.svc, ids[0]))
        self.call_from_thread(self.query_one("#stats").update, grid)
        self.call_from_thread(setattr, btn, "disabled", False)

    def scan(self):
        self.say("Scanning mailbox… [dim](large mailboxes take a few minutes)[/]")
        senders = scan_senders(self.svc, self.progress).most_common()
        self.counts = dict(senders)
        self.call_from_thread(self.fill_table, senders)
        self.say(f"[green]✔[/] Found [b]{len(senders)}[/] senders, [b]{sum(self.counts.values())}[/] emails.")

    def fill_table(self, senders):
        t = self.query_one(SenderTable)
        t.clear()
        t.marked.clear()
        for n, (addr, cnt) in enumerate(senders, 1):
            t.add_row("", f"[cyan]{n}[/]", f"[b yellow]{cnt}[/]", addr, key=addr)
        t.focus()

    def delete_senders(self, addrs):
        deleted = 0
        for i, a in enumerate(addrs, 1):
            ids = list_ids(self.svc, q=f"from:{a}")
            batch_delete(self.svc, ids)
            deleted += len(ids)
            self.say(f"[green]✔[/] {len(ids):>5}  [dim]{a}[/]")
            self.progress(i, len(addrs))
            if a in self.counts:
                self.call_from_thread(self.remove_row, a)
        self.say(f"[b green]Deleted {deleted} emails from {len(addrs)} sender(s).[/]")
        self.call_from_thread(setattr, self.query_one("#del-sender"), "disabled", True)

    def remove_row(self, addr):
        t = self.query_one(SenderTable)
        t.marked.discard(addr)
        if addr in t.rows:
            t.remove_row(addr)

    def category_count(self, label, name):
        self.say(f"Counting emails in [cyan]{name}[/]…")
        ids = list_ids(self.svc, labels=[label])
        if not ids:
            self.say(f"[dim]{name} is already empty.[/]")
            return
        self.ask(f"Delete [b]{len(ids)}[/] emails in [b]{name}[/]?",
                 lambda: self.start(self.category_delete, ids, name))

    def category_delete(self, ids, name):
        batch_delete(self.svc, ids)
        self.progress(1, 1)
        self.say(f"[b green]✔ Deleted {len(ids)} emails from {name}.[/]")

    def drafts_count(self):
        self.say("Loading drafts…")
        drafts = list_drafts(self.svc)
        if not drafts:
            self.say("[dim]No drafts.[/]")
            return
        self.ask(f"Delete [b]{len(drafts)}[/] drafts?", lambda: self.start(self.drafts_delete, drafts))

    def drafts_delete(self, drafts):
        for i, d in enumerate(drafts, 1):
            self.svc.users().drafts().delete(userId="me", id=d["id"]).execute()
            self.progress(i, len(drafts))
        self.say(f"[b green]✔ Deleted {len(drafts)} drafts.[/]")


def main():
    print("Connecting to Gmail…")
    svc = connect()
    me = svc.users().getProfile(userId="me").execute()["emailAddress"]
    CleanupApp(svc, me).run()


if __name__ == "__main__":
    main()
