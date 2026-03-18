#!/usr/bin/env python3
"""
Gmail Cleanup Script - IMAP-based email management
Requires: IMAP enabled in Gmail, 2FA enabled, App Password generated

Usage:
    python gmail_cleanup.py --help
    python gmail_cleanup.py --preview  # See what would be deleted (dry run)
    python gmail_cleanup.py --delete   # Actually delete emails
"""

import imaplib
# Override the default 1MB line limit for huge mailboxes
imaplib._MAXLINE = 1000000000  # 1GB
import email
from email.header import decode_header
import argparse
import getpass
from datetime import datetime, timedelta
from collections import defaultdict
import re
import sys
import time

class GmailCleaner:
    def __init__(self, email_address: str, app_password: str):
        self.email_address = email_address
        self.app_password = app_password
        self._connect()
        self.current_folder = 'INBOX'
        print(f"Connected to {email_address}")

    def _connect(self):
        """Establish IMAP connection."""
        self.mail = imaplib.IMAP4_SSL('imap.gmail.com')
        self.mail.login(self.email_address, self.app_password)

    def reconnect(self):
        """Reconnect to Gmail after connection loss."""
        print("Reconnecting to Gmail...")
        try:
            self.mail.logout()
        except:
            pass
        time.sleep(2)
        self._connect()
        if self.current_folder:
            self.select_folder(self.current_folder)
        print("Reconnected.")

    def list_folders(self) -> list[str]:
        """List all available folders/labels."""
        status, folders = self.mail.list()
        folder_names = []
        for folder in folders:
            # Parse folder name from IMAP response
            match = re.search(r'"([^"]+)"$|(\S+)$', folder.decode())
            if match:
                folder_names.append(match.group(1) or match.group(2))
        return folder_names

    def select_folder(self, folder: str = 'INBOX') -> int:
        """Select a folder and return message count."""
        # Gmail special folders
        folder_map = {
            'inbox': 'INBOX',
            'sent': '[Gmail]/Sent Mail',
            'drafts': '[Gmail]/Drafts',
            'spam': '[Gmail]/Spam',
            'trash': '[Gmail]/Trash',
            'all': '[Gmail]/All Mail',
            'starred': '[Gmail]/Starred',
            'important': '[Gmail]/Important',
            'promotions': '[Gmail]/Promotions',
            'social': '[Gmail]/Social',
            'updates': '[Gmail]/Updates',
            'forums': '[Gmail]/Forums',
        }
        folder = folder_map.get(folder.lower(), folder)
        self.current_folder = folder
        try:
            status, data = self.mail.select(f'"{folder}"')
            if status != 'OK':
                raise Exception(f"Failed to select folder: {folder}")
            return int(data[0])
        except imaplib.IMAP4.error as e:
            if '1000000 bytes' in str(e):
                # Folder metadata too large - this is a HUGE mailbox
                # We can still work with it, we just don't know the count
                print(f"Extremely large folder detected - proceeding without count...")
                # The folder IS selected even though it threw an error
                # We just couldn't receive the full response
                return -1  # Unknown count
            raise

    def build_search_query(
        self,
        before_days: int = None,
        after_days: int = None,
        before_date: str = None,
        after_date: str = None,
        from_addr: str = None,
        to_addr: str = None,
        subject: str = None,
        has_attachment: bool = None,
        larger_than_kb: int = None,
        smaller_than_kb: int = None,
        unread: bool = None,
        read: bool = None,
        flagged: bool = None,
        label: str = None,
    ) -> str:
        """Build IMAP search query from parameters."""
        criteria = []

        # Date filters
        if before_days:
            date = (datetime.now() - timedelta(days=before_days)).strftime('%d-%b-%Y')
            criteria.append(f'BEFORE {date}')
        if after_days:
            date = (datetime.now() - timedelta(days=after_days)).strftime('%d-%b-%Y')
            criteria.append(f'SINCE {date}')
        if before_date:
            criteria.append(f'BEFORE {before_date}')
        if after_date:
            criteria.append(f'SINCE {after_date}')

        # Address filters
        if from_addr:
            criteria.append(f'FROM "{from_addr}"')
        if to_addr:
            criteria.append(f'TO "{to_addr}"')

        # Subject filter
        if subject:
            criteria.append(f'SUBJECT "{subject}"')

        # Status filters
        if unread:
            criteria.append('UNSEEN')
        if read:
            criteria.append('SEEN')
        if flagged:
            criteria.append('FLAGGED')

        # Size filters (Gmail extension via X-GM-RAW)
        # Note: Standard IMAP LARGER/SMALLER work too
        if larger_than_kb:
            criteria.append(f'LARGER {larger_than_kb * 1024}')
        if smaller_than_kb:
            criteria.append(f'SMALLER {smaller_than_kb * 1024}')

        # Gmail label (via X-GM-LABELS)
        if label:
            criteria.append(f'X-GM-LABELS "{label}"')

        return ' '.join(criteria) if criteria else 'ALL'

    def search_emails(self, query: str = 'ALL', limit: int = None, gmail_raw: str = None) -> list[bytes]:
        """Search for emails matching query."""
        try:
            if gmail_raw:
                # Use Gmail's native search (X-GM-RAW) - much faster for large mailboxes
                status, data = self.mail.uid('SEARCH', 'X-GM-RAW', f'"{gmail_raw}"')
            else:
                status, data = self.mail.uid('SEARCH', query)

            if status != 'OK':
                return []

            message_ids = data[0].split()
            if limit:
                message_ids = message_ids[:limit]
            return message_ids
        except imaplib.IMAP4.error as e:
            error_str = str(e)
            if '1000000 bytes' in error_str:
                print(f"Result set too large, fetching in limited batches...")
                # Try with UID ranges - get recent UIDs first
                return self._search_with_uid_limit(query, gmail_raw, limit or 500)
            raise

    def _search_with_uid_limit(self, query: str, gmail_raw: str, limit: int) -> list[bytes]:
        """Scan mailbox and filter client-side when server search fails."""
        print("  Server search failed. Scanning and filtering client-side...")
        print("  (This is slower but works with any mailbox size)")

        # Parse the gmail query for client-side filtering
        from_filter = None
        if gmail_raw:
            # Extract from: filter
            match = re.search(r'from:(\S+)', gmail_raw)
            if match:
                from_filter = match.group(1).lower()

        found = []
        batch_size = 100
        start_seq = 1
        scanned = 0

        # Get an estimate of mailbox size by fetching a high sequence number
        # We'll work from oldest to newest since old emails are targets
        try:
            # Try to get message 1 to verify mailbox isn't empty
            status, data = self.mail.fetch('1', '(UID)')
            if status != 'OK':
                print("  Mailbox appears empty")
                return []
        except Exception as e:
            print(f"  Cannot access mailbox: {e}")
            print("  Trying to reselect folder...")
            try:
                self.mail.select('INBOX')
                status, data = self.mail.fetch('1', '(UID)')
                if status != 'OK':
                    print("  Mailbox appears empty after reselect")
                    return []
            except Exception as e2:
                print(f"  Still cannot access: {e2}")
                return []

        print(f"  Scanning for: from={from_filter or 'any'}")
        print(f"  Will stop after finding {limit} matches...")

        seq = 1
        while len(found) < limit:
            # Fetch headers for a batch
            end_seq = seq + batch_size - 1
            range_str = f"{seq}:{end_seq}"

            try:
                status, data = self.mail.fetch(
                    range_str,
                    '(UID BODY.PEEK[HEADER.FIELDS (FROM DATE)])'
                )

                if status != 'OK' or not data:
                    break

                # Process results
                batch_found = 0
                for item in data:
                    if not isinstance(item, tuple):
                        continue

                    # Extract UID
                    uid_match = re.search(rb'UID (\d+)', item[0])
                    if not uid_match:
                        continue
                    uid = uid_match.group(1)

                    # Check from header
                    header = item[1].decode('utf-8', errors='replace').lower() if len(item) > 1 else ''

                    if from_filter:
                        if from_filter in header:
                            found.append(uid)
                            batch_found += 1
                    else:
                        found.append(uid)
                        batch_found += 1

                    if len(found) >= limit:
                        break

                scanned += batch_size
                if batch_found > 0:
                    print(f"  Scanned {scanned} messages, found {len(found)} matches so far...")

                seq = end_seq + 1

                # Check if we've hit the end (no data returned means no more messages)
                if len([x for x in data if isinstance(x, tuple)]) < batch_size:
                    print(f"  Reached end of mailbox at sequence {seq}")
                    break

            except imaplib.IMAP4.error as e:
                if 'fetch' in str(e).lower():
                    # Probably hit end of mailbox
                    break
                raise

        print(f"  Scan complete. Found {len(found)} matching emails.")
        return found[:limit]

    def search_gmail(self, gmail_query: str, limit: int = None) -> list[bytes]:
        """Search using Gmail's native search syntax (same as web UI)."""
        return self.search_emails(None, limit, gmail_raw=gmail_query)

    def get_email_summary(self, msg_id: bytes) -> dict:
        """Fetch email headers for preview."""
        status, data = self.mail.uid('FETCH', msg_id, '(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])')
        if status != 'OK':
            return None

        size = 0
        headers = b''
        for item in data:
            if isinstance(item, tuple):
                if b'RFC822.SIZE' in item[0]:
                    size_match = re.search(rb'RFC822\.SIZE (\d+)', item[0])
                    if size_match:
                        size = int(size_match.group(1))
                headers = item[1]

        msg = email.message_from_bytes(headers)

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

        return {
            'id': msg_id,
            'from': decode_field(msg.get('From', '')),
            'to': decode_field(msg.get('To', '')),
            'subject': decode_field(msg.get('Subject', '')),
            'date': msg.get('Date', ''),
            'size_kb': size / 1024,
        }

    def preview_emails(self, query: str, limit: int = 50) -> list[dict]:
        """Preview emails that match the query."""
        msg_ids = self.search_emails(query, limit)
        print(f"\nFound {len(msg_ids)} emails matching query")

        summaries = []
        for i, msg_id in enumerate(msg_ids):
            summary = self.get_email_summary(msg_id)
            if summary:
                summaries.append(summary)
                print(f"\n[{i+1}] {summary['date']}")
                print(f"    From: {summary['from'][:60]}")
                print(f"    Subject: {summary['subject'][:60]}")
                print(f"    Size: {summary['size_kb']:.1f} KB")

        return summaries

    def delete_emails(self, query: str = None, gmail_query: str = None, batch_size: int = 500, dry_run: bool = True, delay: float = 1.0) -> int:
        """Delete emails matching query. Uses IMAP MOVE to Trash for Gmail."""
        if gmail_query:
            msg_ids = self.search_gmail(gmail_query)
        else:
            msg_ids = self.search_emails(query)

        total = len(msg_ids)

        if total == 0:
            print("No emails match the query.")
            return 0

        print(f"\n{'[DRY RUN] ' if dry_run else ''}Found {total} emails to delete")

        if dry_run:
            print("Run with --delete to actually delete these emails.")
            return total

        deleted = 0
        max_retries = 5

        for i in range(0, total, batch_size):
            batch = msg_ids[i:i + batch_size]
            msg_set = b','.join(batch)

            # Retry loop with exponential backoff
            for attempt in range(max_retries):
                try:
                    # Use IMAP MOVE to Trash - this actually removes from All Mail
                    # (STORE \Deleted + EXPUNGE silently does nothing in Gmail All Mail)
                    self.mail.uid('MOVE', msg_set.decode(), '"[Gmail]/Trash"')
                    deleted += len(batch)
                    print(f"Deleted {deleted}/{total} emails (moved to trash)", flush=True)
                    break  # Success, exit retry loop
                except imaplib.IMAP4.error as e:
                    error_str = str(e)
                    if 'System Error' in error_str or 'EOF' in error_str:
                        wait_time = (2 ** attempt) * 2  # 2, 4, 8, 16, 32 seconds
                        print(f"Rate limited or connection error. Waiting {wait_time}s before retry {attempt+1}/{max_retries}...", flush=True)
                        time.sleep(wait_time)
                        try:
                            self.reconnect()
                        except Exception as reconn_err:
                            print(f"Reconnect failed: {reconn_err}", flush=True)
                            if attempt == max_retries - 1:
                                raise
                    else:
                        raise  # Unknown error, don't retry
            else:
                # All retries exhausted
                print(f"Failed after {max_retries} retries. Deleted {deleted} emails before failure.", flush=True)
                return deleted

            # Rate limiting: pause between batches
            if i + batch_size < total:
                time.sleep(delay)

        print(f"\nDeleted {deleted} emails (all moved to trash).", flush=True)
        return deleted

    def analyze_inbox(self, limit: int = 1000) -> dict:
        """Analyze inbox to find cleanup opportunities."""
        print("\nAnalyzing inbox for cleanup opportunities...")

        msg_ids = self.search_emails('ALL', limit)
        print(f"Scanning {len(msg_ids)} emails...")

        senders = defaultdict(lambda: {'count': 0, 'size': 0})
        total_size = 0
        old_count = 0
        large_count = 0

        one_year_ago = (datetime.now() - timedelta(days=365)).strftime('%d-%b-%Y')

        for msg_id in msg_ids:
            summary = self.get_email_summary(msg_id)
            if summary:
                # Extract sender domain
                from_addr = summary['from']
                match = re.search(r'<([^>]+)>|(\S+@\S+)', from_addr)
                if match:
                    addr = match.group(1) or match.group(2)
                    domain = addr.split('@')[-1] if '@' in addr else addr
                    senders[domain]['count'] += 1
                    senders[domain]['size'] += summary['size_kb']

                total_size += summary['size_kb']
                if summary['size_kb'] > 1024:  # > 1MB
                    large_count += 1

        # Check old emails count
        old_msg_ids = self.search_emails(f'BEFORE {one_year_ago}')
        old_count = len(old_msg_ids)

        print("\n" + "=" * 60)
        print("INBOX ANALYSIS REPORT")
        print("=" * 60)

        print(f"\nTotal emails scanned: {len(msg_ids)}")
        print(f"Total size: {total_size / 1024:.1f} MB")
        print(f"Emails older than 1 year: {old_count}")
        print(f"Large emails (>1MB): {large_count}")

        print("\n--- Top Senders by Count ---")
        sorted_by_count = sorted(senders.items(), key=lambda x: x[1]['count'], reverse=True)[:15]
        for domain, stats in sorted_by_count:
            print(f"  {stats['count']:5d} emails | {stats['size']/1024:6.1f} MB | {domain}")

        print("\n--- Top Senders by Size ---")
        sorted_by_size = sorted(senders.items(), key=lambda x: x[1]['size'], reverse=True)[:10]
        for domain, stats in sorted_by_size:
            print(f"  {stats['count']:5d} emails | {stats['size']/1024:6.1f} MB | {domain}")

        print("\n--- Suggested Cleanup Commands ---")
        if old_count > 100:
            print(f"  # Delete emails older than 1 year ({old_count} emails):")
            print(f"  python gmail_cleanup.py --before-days 365 --delete")

        if large_count > 10:
            print(f"  # Delete large emails >5MB:")
            print(f"  python gmail_cleanup.py --larger-than 5120 --preview")

        if sorted_by_count[0][1]['count'] > 50:
            top_sender = sorted_by_count[0][0]
            print(f"  # Delete all from {top_sender}:")
            print(f"  python gmail_cleanup.py --from '{top_sender}' --preview")

        return {
            'total': len(msg_ids),
            'total_size_mb': total_size / 1024,
            'old_count': old_count,
            'large_count': large_count,
            'top_senders': dict(sorted_by_count),
        }

    def empty_trash(self):
        """Permanently delete all emails in Trash."""
        self.select_folder('trash')
        msg_ids = self.search_emails('ALL')
        if not msg_ids:
            print("Trash is already empty.")
            return 0

        print(f"Permanently deleting {len(msg_ids)} emails from Trash...")
        msg_set = b','.join(msg_ids)
        self.mail.store(msg_set.decode(), '+FLAGS', '\\Deleted')
        self.mail.expunge()
        print("Trash emptied.")
        return len(msg_ids)

    def empty_spam(self):
        """Delete all spam."""
        self.select_folder('spam')
        msg_ids = self.search_emails('ALL')
        if not msg_ids:
            print("Spam folder is already empty.")
            return 0

        print(f"Deleting {len(msg_ids)} spam emails...")
        msg_set = b','.join(msg_ids)
        self.mail.store(msg_set.decode(), '+FLAGS', '\\Deleted')
        self.mail.expunge()
        print("Spam folder emptied.")
        return len(msg_ids)

    def close(self):
        """Close IMAP connection."""
        try:
            self.mail.close()
            self.mail.logout()
        except:
            pass

def main():
    parser = argparse.ArgumentParser(
        description='Gmail Cleanup Tool - Delete emails via IMAP',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze your inbox for cleanup opportunities
  python gmail_cleanup.py --analyze

  # Preview emails older than 2 years
  python gmail_cleanup.py --before-days 730 --preview

  # Delete emails from a specific sender
  python gmail_cleanup.py --from "newsletter@spam.com" --delete

  # Delete large emails (>10MB)
  python gmail_cleanup.py --larger-than 10240 --preview

  # Delete old promotional emails
  python gmail_cleanup.py --before-days 365 --folder promotions --delete

  # Empty spam and trash
  python gmail_cleanup.py --empty-spam
  python gmail_cleanup.py --empty-trash
        """
    )

    # Authentication
    parser.add_argument('--email', '-e', help='Gmail address')
    parser.add_argument('--password', '-p', help='App password (will prompt if not provided)')
    parser.add_argument('--password-file', help='Read app password from file')

    # Actions
    parser.add_argument('--preview', action='store_true', help='Preview emails matching criteria')
    parser.add_argument('--delete', action='store_true', help='Delete emails matching criteria')
    parser.add_argument('--analyze', action='store_true', help='Analyze inbox for cleanup opportunities')
    parser.add_argument('--list-folders', action='store_true', help='List all folders/labels')
    parser.add_argument('--empty-trash', action='store_true', help='Permanently empty trash')
    parser.add_argument('--empty-spam', action='store_true', help='Empty spam folder')

    # Folder selection
    parser.add_argument('--folder', '-f', default='inbox',
                        help='Folder to operate on (inbox, sent, all, spam, trash, or label name)')

    # Search filters
    parser.add_argument('--before-days', type=int, help='Emails older than N days')
    parser.add_argument('--after-days', type=int, help='Emails newer than N days')
    parser.add_argument('--before-date', help='Emails before date (DD-Mon-YYYY, e.g., 01-Jan-2023)')
    parser.add_argument('--after-date', help='Emails after date (DD-Mon-YYYY)')
    parser.add_argument('--from', dest='from_addr', help='From address (partial match)')
    parser.add_argument('--to', dest='to_addr', help='To address (partial match)')
    parser.add_argument('--subject', help='Subject contains')
    parser.add_argument('--larger-than', type=int, help='Larger than N KB')
    parser.add_argument('--smaller-than', type=int, help='Smaller than N KB')
    parser.add_argument('--unread', action='store_true', help='Only unread emails')
    parser.add_argument('--read', action='store_true', help='Only read emails')
    parser.add_argument('--limit', type=int, default=100, help='Limit results (default: 100)')
    parser.add_argument('--gmail', '-g', dest='gmail_query',
                        help='Use Gmail native search syntax (same as web UI). Examples: "from:github.com", "older_than:1y", "larger:5M"')
    parser.add_argument('--loop', action='store_true',
                        help='Keep deleting in batches until no more matches (for huge result sets)')
    parser.add_argument('--batch-size', type=int, default=500,
                        help='Number of emails to delete per batch (default: 500)')
    parser.add_argument('--delay', type=float, default=1.0,
                        help='Seconds to wait between batches to avoid rate limits (default: 1.0)')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Skip confirmation prompt')

    args = parser.parse_args()

    # Check for action
    if not any([args.preview, args.delete, args.analyze, args.list_folders,
                args.empty_trash, args.empty_spam]):
        parser.print_help()
        print("\nError: Please specify an action (--preview, --delete, --analyze, etc.)")
        sys.exit(1)

    # Get credentials
    email_addr = args.email or input("Gmail address: ")
    if args.password_file:
        with open(args.password_file) as f:
            app_password = f.read().strip()
    else:
        app_password = args.password or getpass.getpass("App password: ")

    try:
        cleaner = GmailCleaner(email_addr, app_password)

        if args.list_folders:
            folders = cleaner.list_folders()
            print("\nAvailable folders/labels:")
            for f in folders:
                print(f"  {f}")

        elif args.analyze:
            cleaner.select_folder(args.folder)
            cleaner.analyze_inbox(args.limit)

        elif args.empty_trash:
            cleaner.empty_trash()

        elif args.empty_spam:
            cleaner.empty_spam()

        elif args.preview or args.delete:
            cleaner.select_folder(args.folder)

            # Check if using Gmail native search
            gmail_query = args.gmail_query
            query = None

            if not gmail_query:
                query = cleaner.build_search_query(
                    before_days=args.before_days,
                    after_days=args.after_days,
                    before_date=args.before_date,
                    after_date=args.after_date,
                    from_addr=args.from_addr,
                    to_addr=args.to_addr,
                    subject=args.subject,
                    larger_than_kb=args.larger_than,
                    smaller_than_kb=args.smaller_than,
                    unread=args.unread,
                    read=args.read,
                )
                print(f"Search query: {query}")
            else:
                print(f"Gmail search: {gmail_query}")

            if args.preview:
                if gmail_query:
                    msg_ids = cleaner.search_gmail(gmail_query, args.limit)
                    print(f"\nFound {len(msg_ids)} emails matching query")
                    for i, msg_id in enumerate(msg_ids[:args.limit]):
                        summary = cleaner.get_email_summary(msg_id)
                        if summary:
                            print(f"\n[{i+1}] {summary['date']}")
                            print(f"    From: {summary['from'][:60]}")
                            print(f"    Subject: {summary['subject'][:60]}")
                            print(f"    Size: {summary['size_kb']:.1f} KB")
                else:
                    cleaner.preview_emails(query, args.limit)
            elif args.delete:
                # Safety confirmation
                if gmail_query:
                    msg_ids = cleaner.search_gmail(gmail_query, limit=args.limit)
                else:
                    msg_ids = cleaner.search_emails(query, limit=args.limit)

                if len(msg_ids) == 0:
                    print("No emails match the query.")
                else:
                    print(f"\nFound {len(msg_ids)} emails to delete.")
                    if args.loop:
                        print("Loop mode: will keep deleting batches until done.")
                    if args.yes:
                        confirm = 'yes'
                    else:
                        confirm = input("Type 'yes' to confirm: ")
                    if confirm.lower() == 'yes':
                        total_deleted = 0
                        round_num = 1

                        while True:
                            deleted = cleaner.delete_emails(query=query, gmail_query=gmail_query, batch_size=args.batch_size, dry_run=False, delay=args.delay)
                            total_deleted += deleted

                            if not args.loop or deleted == 0:
                                break

                            print(f"\n--- Round {round_num} complete. Total deleted so far: {total_deleted} ---")
                            round_num += 1

                            # Brief pause between rounds to avoid rate limits
                            time.sleep(args.delay * 2)

                            # Re-search for more
                            if gmail_query:
                                msg_ids = cleaner.search_gmail(gmail_query, limit=args.limit)
                            else:
                                msg_ids = cleaner.search_emails(query, limit=args.limit)

                            if len(msg_ids) == 0:
                                print("No more emails match the query.")
                                break

                            print(f"Found {len(msg_ids)} more emails...")

                        print(f"\n=== DONE. Total deleted: {total_deleted} ===")
                    else:
                        print("Cancelled.")

        cleaner.close()

    except imaplib.IMAP4.error as e:
        print(f"IMAP Error: {e}")
        print("\nTroubleshooting:")
        print("1. Ensure IMAP is enabled in Gmail settings")
        print("2. Use an App Password, not your regular password")
        print("3. Check that 2FA is enabled on your account")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)

if __name__ == '__main__':
    main()
