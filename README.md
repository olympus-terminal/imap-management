# IMAP Management Tools

Command-line tools for managing large IMAP mailboxes, particularly Gmail.

## gmail_cleanup.py

Bulk email deletion tool with rate limiting and automatic retry logic. Designed to handle massive mailboxes (tested with 1M+ emails).

### Features

- Gmail native search syntax support (same as web UI)
- Rate limiting to avoid IMAP throttling
- Exponential backoff with auto-reconnect on errors
- Loop mode for continuous batch deletion
- Dry-run preview mode

### Requirements

- Python 3.10+
- IMAP enabled in Gmail settings
- 2FA enabled on your Google account
- App Password (not your regular password)

### Setup

1. Enable IMAP in Gmail: Settings > See all settings > Forwarding and POP/IMAP > Enable IMAP
2. Generate an App Password: Google Account > Security > 2-Step Verification > App passwords

### Usage

```bash
# Analyze your inbox
python gmail_cleanup.py -e your@gmail.com --analyze

# Preview old emails from a sender
python gmail_cleanup.py -e your@gmail.com --gmail "from:github.com older_than:1y" --preview

# Delete with rate limiting (recommended for large batches)
python gmail_cleanup.py -e your@gmail.com --password-file app_password.txt \
  --gmail "from:notifications@github.com older_than:3y" \
  --batch-size 500 --delay 1.0 --loop --delete

# Empty trash
python gmail_cleanup.py -e your@gmail.com --empty-trash
```

### Options

| Option | Description |
|--------|-------------|
| `--gmail`, `-g` | Gmail search query (same syntax as web UI) |
| `--batch-size` | Emails per batch (default: 500) |
| `--delay` | Seconds between batches (default: 1.0) |
| `--loop` | Keep deleting until no matches remain |
| `--preview` | Show matching emails without deleting |
| `--delete` | Actually delete the emails |
| `--folder`, `-f` | Target folder (inbox, sent, all, spam, trash) |
| `--analyze` | Show inbox statistics and cleanup suggestions |

### Gmail Search Examples

- `older_than:1y` - Emails older than 1 year
- `from:github.com` - From GitHub
- `larger:5M` - Larger than 5 MB
- `has:attachment older_than:2y` - Old emails with attachments
- `category:promotions older_than:6m` - Old promotional emails

## License

MIT
