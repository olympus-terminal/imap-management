#!/usr/bin/env python3
"""Audit GitHub notification emails - check category tabs and recent arrivals."""

import os
import imaplib
imaplib._MAXLINE = 1000000000
import email
from email.header import decode_header
from collections import defaultdict
import re
import sys
import time

EMAIL = os.environ.get("GMAIL_ADDRESS") or input("Gmail address: ")
PASSWORD_FILE = os.environ.get("GMAIL_APP_PASSWORD_FILE", "app_password.txt")
with open(PASSWORD_FILE) as f:
    PASSWORD = f.read().strip()

def decode_field(field):
    if not field:
        return ''
    decoded = decode_header(field)
    parts = []
    for content, charset in decoded:
        if isinstance(content, bytes):
            try:
                parts.append(content.decode(charset or 'utf-8', errors='replace'))
            except (LookupError, UnicodeDecodeError):
                parts.append(content.decode('utf-8', errors='replace'))
        else:
            parts.append(content)
    return ' '.join(parts)

print("Connecting to Gmail...")
mail = imaplib.IMAP4_SSL('imap.gmail.com')
mail.login(EMAIL, PASSWORD)
print("Connected.")

# Check Gmail category tabs for GitHub emails
categories = [
    ('Updates', '[Gmail]/Updates'),
    ('Forums', '[Gmail]/Forums'),
    ('Social', '[Gmail]/Social'),
    ('Promotions', '[Gmail]/Promotions'),
]

for label, folder in categories:
    try:
        status, data = mail.select(f'"{folder}"')
        if status != 'OK':
            print(f"  Could not select {folder}")
            continue
        count = int(data[0])
        print(f"\n{'='*70}")
        print(f"{label} tab: {count} total emails")

        # Search for GitHub
        status, data = mail.uid('SEARCH', 'X-GM-RAW', '"from:notifications@github.com"')
        if status == 'OK' and data[0]:
            gh_ids = data[0].split()
            print(f"  GitHub notifications: {len(gh_ids)}")

            # Check recent ones (last 30 days)
            status2, data2 = mail.uid('SEARCH', 'X-GM-RAW',
                '"from:notifications@github.com newer_than:30d"')
            if status2 == 'OK' and data2[0]:
                recent = data2[0].split()
                print(f"  GitHub notifications (last 30 days): {len(recent)}")

        # Also check github-actions
        status, data = mail.uid('SEARCH', 'X-GM-RAW', '"from:github.com"')
        if status == 'OK' and data[0]:
            gh_ids = data[0].split()
            print(f"  All from github.com: {len(gh_ids)}")

    except Exception as e:
        print(f"  Error with {folder}: {e}")

# Now do the detailed repo analysis on All Mail, recent emails
print(f"\n{'='*70}")
print("DETAILED ANALYSIS: Recent GitHub notifications (last 60 days)")
print(f"{'='*70}")

mail.select('"[Gmail]/All Mail"')

status, data = mail.uid('SEARCH', 'X-GM-RAW',
    '"from:notifications@github.com newer_than:60d"')

if status != 'OK' or not data[0]:
    print("No recent GitHub notifications found.")
    mail.logout()
    sys.exit(0)

msg_ids = data[0].split()
total = len(msg_ids)
print(f"Found {total} GitHub notifications in last 60 days")

# Analyze by repo (List-ID header)
repos = defaultdict(int)
reasons = defaultdict(int)
repo_samples = defaultdict(list)

batch_size = 200
for i in range(0, total, batch_size):
    batch = msg_ids[i:i+batch_size]
    msg_set = b','.join(batch)

    try:
        status, data = mail.uid('FETCH', msg_set,
            '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT LIST-ID X-GITHUB-REASON X-GITHUB-SENDER)])')
    except imaplib.IMAP4.error as e:
        print(f"  Error fetching batch at {i}: {e}")
        time.sleep(2)
        continue

    for item in data:
        if not isinstance(item, tuple):
            continue
        try:
            header_text = item[1].decode('utf-8', errors='replace')
            msg = email.message_from_string(header_text)

            subject = decode_field(msg.get('Subject', ''))
            list_id = decode_field(msg.get('List-ID', ''))
            reason = decode_field(msg.get('X-GitHub-Reason', '')).strip()
            gh_sender = decode_field(msg.get('X-GitHub-Sender', '')).strip()

            # Extract repo from List-ID
            repo = 'unknown'
            if list_id:
                # Format: "owner/repo <repo.owner.github.com>"
                m = re.match(r'([^<]+)', list_id)
                if m:
                    repo = m.group(1).strip().strip('"')

            repos[repo] += 1
            if reason:
                reasons[reason] += 1

            if len(repo_samples[repo]) < 3:
                repo_samples[repo].append(f"{subject[:70]}  [by:{gh_sender}]")

        except Exception:
            continue

    progress = min(i + batch_size, total)
    if progress % 500 == 0 or progress == total:
        print(f"  Processed {progress}/{total}...", flush=True)

    if i + batch_size < total:
        time.sleep(0.2)

print(f"\n--- Top Repos by Notification Count (last 60 days) ---")
sorted_repos = sorted(repos.items(), key=lambda x: x[1], reverse=True)
for repo, count in sorted_repos[:30]:
    pct = count / total * 100
    print(f"  {count:6d} ({pct:5.1f}%) | {repo}")
    for sample in repo_samples.get(repo, []):
        print(f"         | -> {sample}")

print(f"\n--- Notification Reasons ---")
sorted_reasons = sorted(reasons.items(), key=lambda x: x[1], reverse=True)
for reason, count in sorted_reasons:
    pct = count / total * 100
    print(f"  {count:6d} ({pct:5.1f}%) | {reason}")

print(f"\n--- Summary ---")
print(f"Total repos sending notifications: {len(repos)}")
print(f"Total recent notifications (60d): {total}")
print(f"Estimated monthly rate: ~{total/2:.0f}/month")

mail.logout()
print("\nDone.")
