#!/usr/bin/env python3
"""Test which IMAP deletion method actually works on Gmail All Mail."""

import os
import imaplib
imaplib._MAXLINE = 1000000000
import time

EMAIL = os.environ.get("GMAIL_ADDRESS") or input("Gmail address: ")
PASSWORD_FILE = os.environ.get("GMAIL_APP_PASSWORD_FILE", "app_password.txt")
with open(PASSWORD_FILE) as f:
    PASSWORD = f.read().strip()

mail = imaplib.IMAP4_SSL('imap.gmail.com')
mail.login(EMAIL, PASSWORD)
print("Connected.\n")

# Check IMAP capabilities
status, caps = mail.capability()
print(f"Server capabilities: {caps[0].decode()}\n")

mail.select('"[Gmail]/All Mail"')

# Find a small batch of GitHub notifications to test with
status, data = mail.uid('SEARCH', 'X-GM-RAW', '"from:notifications@github.com"')
all_ids = data[0].split()
print(f"Total GitHub notifications before test: {len(all_ids)}")

# Pick 5 test emails
test_ids = all_ids[:5]
test_set = b','.join(test_ids)
print(f"Test UIDs: {[uid.decode() for uid in test_ids]}")

# Method 1: MOVE to Trash (RFC 6851)
print("\n--- Testing MOVE to [Gmail]/Trash ---")
try:
    result = mail.uid('MOVE', test_set.decode(), '"[Gmail]/Trash"')
    print(f"MOVE result: {result}")
except Exception as e:
    print(f"MOVE failed: {e}")
    # Fallback: COPY to Trash + delete from All Mail
    print("\n--- Testing COPY to [Gmail]/Trash + STORE \\Deleted + EXPUNGE ---")
    try:
        result = mail.uid('COPY', test_set.decode(), '"[Gmail]/Trash"')
        print(f"COPY result: {result}")
        result = mail.uid('STORE', test_set.decode(), '+FLAGS', '\\Deleted')
        print(f"STORE result: {result}")
        result = mail.expunge()
        print(f"EXPUNGE result: {result}")
    except Exception as e2:
        print(f"COPY+DELETE also failed: {e2}")

# Verify - re-search
time.sleep(2)
mail.select('"[Gmail]/All Mail"')  # re-select to refresh
status, data = mail.uid('SEARCH', 'X-GM-RAW', '"from:notifications@github.com"')
after_ids = data[0].split()
print(f"\nTotal GitHub notifications after test: {len(after_ids)}")
diff = len(all_ids) - len(after_ids)
print(f"Difference: {diff} emails {'REMOVED' if diff > 0 else 'unchanged (deletion failed)'}")

# Also check if they landed in Trash
mail.select('"[Gmail]/Trash"')
status, data = mail.uid('SEARCH', 'X-GM-RAW', '"from:notifications@github.com"')
if status == 'OK' and data[0]:
    trash_ids = data[0].split()
    print(f"GitHub notifications in Trash: {len(trash_ids)}")
else:
    print("No GitHub notifications found in Trash")

mail.logout()
print("\nDone.")
