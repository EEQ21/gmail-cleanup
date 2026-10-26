# 📬 Gmail Cleanup

A simple terminal tool to clean up your Gmail fast. Find out who fills your mailbox, see how many emails each sender sent, and permanently delete them in bulk. It can also empty your drafts and spam with one command.

## ✨ Features

- **Delete everything from a sender**: enter an email address and see how many emails they sent, when the first one arrived and when the most recent one arrived. Then permanently delete them all.
- **Sender overview**: scan your mailbox and list every sender, sorted by how many emails they sent. Pick senders to delete by number (`1,3,5-8`) or delete them `all`.
- **Clear drafts** instantly.
- **Clear spam** instantly.
- **Safe by default**: every delete shows what will be removed and asks you to type `yes`.
- Runs locally. Your mail goes only between your computer and Google.

> ⚠️ **Deletes are permanent.** Emails skip the Trash and cannot be recovered.

## 🚀 Setup

### 1. Clone and install

```bash
git clone https://github.com/EEQ21/gmail-cleanup.git
cd gmail-cleanup
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create your Google credentials

Each user needs their own (free) Google Cloud credentials:

1. Go to [Google Cloud Console](https://console.cloud.google.com) and create a project.
2. Enable the [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
3. Go to **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - Click **Get started**, fill in an app name and your email, and choose **External** as the audience.
   - Under **Audience → Test users**, add your own Gmail address.
4. Go to **Clients → Create client**:
   - Application type: **Desktop app**
   - Click **Download JSON** and save it in this folder as `credentials.json`.

### 3. Run

```bash
python cleanup.py
```

On the first run a browser opens so you can sign in. If Google warns that the app is unverified, click **Advanced → Go to (app name)**. That's expected because it's your own app. Your login is saved to `token.json` for next time.

## 🖥️ Usage

```
Gmail Cleanup - you@gmail.com

1) Delete all emails from a sender
2) List all senders (and delete)
3) Clear drafts (concepts)
4) Clear spam
q) Quit
```

Example sender overview:

```
   #  Emails  Sender
   1     842  news@shop.com
   2     310  noreply@social.com
   3      97  updates@service.com

Numbers to delete (e.g. 1,3,5-8, 'all', or Enter to cancel):
```

## 💡 Notes

- Gmail groups emails into conversations, so the tool counts more emails than the Gmail web interface shows.
- The sender scan skips sent mail, spam, trash and drafts.
- Scanning a large mailbox takes a few minutes because Gmail limits how fast emails can be read.
- The tool needs full Gmail access (`https://mail.google.com/`) because Google requires it for permanent deletion.

## 🔒 Security

`credentials.json` and `token.json` are in `.gitignore`. **Never commit them.** `token.json` gives full access to your mailbox.

## 📄 License

MIT
