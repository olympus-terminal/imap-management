#!/usr/bin/env python3
"""Bulk delete all GitHub notification emails from Gmail using IMAP MOVE to Trash."""

import os
import imaplib
imaplib._MAXLINE = 1000000000
import time
import sys

EMAIL = os.environ.get("GMAIL_ADDRESS") or input("Gmail address: ")
PASSWORD_FILE = os.environ.get("GMAIL_APP_PASSWORD_FILE", "app_password.txt")
with open(PASSWORD_FILE) as f:
    PASSWORD = f.read().strip()

BATCH_SIZE = 500
DELAY_BETWEEN_BATCHES = 0.3
MAX_RETRIES = 5

TARGETS = [
    ("from:notifications@github.com", "GitHub notifications"),
    ("from:github-actions", "GitHub Actions"),
    ("from:dependabot", "Dependabot"),
]

def connect():
    print("Connecting to Gmail...", flush=True)
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(EMAIL, PASSWORD)
    print("Connected.", flush=True)
    return mail

def reconnect_and_select(mail):
    print("Reconnecting...", flush=True)
    try:
        mail.logout()
    except:
        pass
    time.sleep(3)
    mail = connect()
    mail.select('"[Gmail]/All Mail"')
    return mail

def move_batch(mail, msg_ids):
    """MOVE a batch of messages to Trash. Returns (count_moved, mail)."""
    msg_set = b','.join(msg_ids)
    for attempt in range(MAX_RETRIES):
        try:
            status, _ = mail.uid('MOVE', msg_set.decode(), '"[Gmail]/Trash"')
            if status == 'OK':
                return len(msg_ids), mail
            else:
                print(f"  MOVE returned {status}, retrying...", flush=True)
        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError, BrokenPipeError) as e:
            wait = (2 ** attempt) * 2
            print(f"  Error: {e}. Retry {attempt+1}/{MAX_RETRIES} in {wait}s...", flush=True)
            time.sleep(wait)
            try:
                mail = reconnect_and_select(mail)
            except Exception as e2:
                print(f"  Reconnect failed: {e2}", flush=True)
                if attempt == MAX_RETRIES - 1:
                    return 0, mail
    return 0, mail

def purge_target(mail, gmail_query, label):
    """Search and move all matching emails to Trash."""
    print(f"\n{'='*60}", flush=True)
    print(f"PURGING: {label}", flush=True)
    print(f"Query: {gmail_query}", flush=True)
    print(f"{'='*60}", flush=True)

    total_moved = 0
    round_num = 0

    while True:
        round_num += 1

        # Search for matching emails
        for attempt in range(MAX_RETRIES):
            try:
                status, data = mail.uid('SEARCH', 'X-GM-RAW', f'"{gmail_query}"')
                break
            except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError) as e:
                wait = (2 ** attempt) * 2
                print(f"  Search error: {e}. Retry in {wait}s...", flush=True)
                time.sleep(wait)
                mail = reconnect_and_select(mail)
        else:
            print(f"  Search failed after {MAX_RETRIES} retries. Stopping.", flush=True)
            break

        if status != 'OK' or not data[0]:
            print(f"  No more emails found. Total moved to trash: {total_moved}", flush=True)
            break

        msg_ids = data[0].split()
        remaining = len(msg_ids)
        print(f"  Round {round_num}: {remaining} remaining", flush=True)

        # Move in batches
        round_moved = 0
        for i in range(0, min(remaining, 5000), BATCH_SIZE):
            batch = msg_ids[i:i + BATCH_SIZE]
            moved, mail = move_batch(mail, batch)
            round_moved += moved
            total_moved += moved
            print(f"  Moved {total_moved} to trash ({remaining - round_moved} est. remaining)", flush=True)

            if moved == 0:
                print(f"  Batch failed. Stopping this round.", flush=True)
                break

            if i + BATCH_SIZE < remaining:
                time.sleep(DELAY_BETWEEN_BATCHES)

        if round_moved == 0:
            print(f"  No progress this round. Stopping.", flush=True)
            break

        # Need to re-select folder after MOVE invalidates state
        try:
            mail.select('"[Gmail]/All Mail"')
        except:
            mail = reconnect_and_select(mail)

        time.sleep(0.5)

    return total_moved, mail

def main():
    mail = connect()

    try:
        mail.select('"[Gmail]/All Mail"')
    except imaplib.IMAP4.error as e:
        print(f"Warning selecting All Mail: {e}", flush=True)
        print("Proceeding anyway...", flush=True)

    grand_total = 0
    start_time = time.time()

    for query, label in TARGETS:
        moved, mail = purge_target(mail, query, label)
        grand_total += moved

    elapsed = time.time() - start_time
    hours = elapsed / 3600
    print(f"\n{'='*60}", flush=True)
    print(f"PURGE COMPLETE", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"Total emails moved to trash: {grand_total}", flush=True)
    print(f"Time elapsed: {hours:.1f} hours ({elapsed:.0f}s)", flush=True)
    print(f"Rate: {grand_total/max(elapsed,1):.0f} emails/sec", flush=True)
    print(f"\nNote: Run './gmail empty-trash' to permanently delete.", flush=True)

    try:
        mail.logout()
    except:
        pass

if __name__ == '__main__':
    main()
