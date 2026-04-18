#!/usr/bin/env python3
"""
YouTube Migration Tool
Migrates playlists and subscriptions between two YouTube accounts.

Usage:
  python migrate.py

Prerequisites:
  1. Google Cloud project with YouTube Data API v3 enabled
  2. OAuth 2.0 Client ID (Desktop App type) credentials downloaded as client_secrets.json
  3. Google Auth Platform consent screen configured
  4. Both YouTube accounts added as Test Users (if app is in Testing mode)

Quota costs (default 10,000 units/day):
  - subscriptions.list: 1 unit per call
  - subscriptions.insert: 50 units per subscription
  - playlists.list: 1 unit per call
  - playlists.insert: 50 units per playlist
  - playlistItems.list: 1 unit per call
  - playlistItems.insert: 50 units per video
"""

import os
import sys
import json
import time
import pickle
import argparse
from pathlib import Path
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

# ── Configuration ──────────────────────────────────────────────────────────────

CLIENT_SECRETS_FILE = Path(__file__).parent / "client_secrets.json"
TOKENS_DIR = Path(__file__).parent / "tokens"

# Scopes needed: read source, write to destination
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube",
]

# Source and destination channel IDs
SOURCE_CHANNEL_ID = "UC_SOURCE_CHANNEL_ID_HERE"
DEST_CHANNEL_ID = "UC_DEST_CHANNEL_ID_HERE"

# Special playlists that CAN'T be written to via API
SYSTEM_PLAYLISTS = {"likes", "watch-history", "watch-later", "uploads"}

# Quota costs per operation
QUOTA_COSTS = {
    "subscriptions.list": 1,
    "subscriptions.insert": 50,
    "playlists.list": 1,
    "playlists.insert": 50,
    "playlistItems.list": 1,
    "playlistItems.insert": 50,
    "channels.list": 1,
}

# API rate limiting: sleep between insert calls to avoid rate errors
INSERT_DELAY_SECONDS = 0.5
LIST_DELAY_SECONDS = 0.1

# ── Authentication ─────────────────────────────────────────────────────────────

def get_credentials(token_key: str, expected_channel_id: str = None):
    """
    Get authenticated credentials for a YouTube account.
    Uses pickle to persist tokens between runs.
    
    Args:
        token_key: Identifier for this token file (e.g., 'source', 'dest')
        expected_channel_id: If provided, verify the authenticated account matches this channel
    """
    TOKENS_DIR.mkdir(exist_ok=True)
    token_file = TOKENS_DIR / f"{token_key}_token.pkl"
    
    creds = None
    if token_file.exists():
        with open(token_file, "rb") as f:
            creds = pickle.load(f)
    
    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print(f"  Refreshed token for {token_key}")
            except (RefreshError, Exception) as e:
                print(f"  Token refresh failed for {token_key}: {e}")
                creds = None
                # Delete stale token file
                token_file.unlink(missing_ok=True)
    
    if creds and creds.valid:
        # Verify this is the correct account
        if expected_channel_id:
            try:
                youtube = build("youtube", "v3", credentials=creds)
                response = youtube.channels().list(part="id,snippet", mine=True).execute()
                items = response.get("items", [])
                if items:
                    actual_channel_id = items[0]["id"]
                    channel_title = items[0]["snippet"]["title"]
                    email_hint = channel_title
                    print(f"  Cached token belongs to: {channel_title} ({actual_channel_id})")
                    
                    if actual_channel_id != expected_channel_id:
                        print(f"  ✗ WRONG ACCOUNT! Expected: {expected_channel_id}")
                        print(f"  Deleting stale token and re-authenticating...")
                        creds = None
                        token_file.unlink(missing_ok=True)
                    else:
                        print(f"  ✓ Correct account verified")
                else:
                    print(f"  ⚠ Could not verify channel, re-authenticating...")
                    creds = None
                    token_file.unlink(missing_ok=True)
            except Exception as e:
                print(f"  ⚠ Token verification failed: {e}")
                creds = None
                token_file.unlink(missing_ok=True)
    
    if not creds or not creds.valid:
        if not CLIENT_SECRETS_FILE.exists():
            print(f"\nERROR: {CLIENT_SECRETS_FILE} not found!")
            print("Download OAuth 2.0 client secrets from Google Cloud Console:")
            print("https://console.cloud.google.com/apis/credentials")
            sys.exit(1)
        
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_SECRETS_FILE), SCOPES
        )
        creds = flow.run_local_server(port=0, prompt="consent")
        print(f"  ✓ Authenticated for {token_key}")
        
        # Save token
        with open(token_file, "wb") as f:
            pickle.dump(creds, f)
        
        # Verify the newly authenticated account
        if expected_channel_id:
            youtube = build("youtube", "v3", credentials=creds)
            response = youtube.channels().list(part="id,snippet", mine=True).execute()
            items = response.get("items", [])
            if items:
                actual_channel_id = items[0]["id"]
                channel_title = items[0]["snippet"]["title"]
                print(f"  Logged in as: {channel_title} ({actual_channel_id})")
                
                if actual_channel_id != expected_channel_id:
                    print(f"\n  ✗✗✗ WRONG ACCOUNT! ✗✗✗")
                    print(f"  Expected channel ID: {expected_channel_id}")
                    print(f"  Got channel ID:      {actual_channel_id}")
                    print(f"  Please log out of Google in your browser and try again.")
                    print(f"  Or use an incognito/private window.")
                    # Delete wrong token
                    token_file.unlink(missing_ok=True)
                    sys.exit(1)
                else:
                    print(f"  ✓ Channel ID matches expected: {expected_channel_id}")
    else:
        # Save refreshed token
        with open(token_file, "wb") as f:
            pickle.dump(creds, f)
    
    return creds


def build_youtube_client(creds):
    """Build YouTube API client from credentials."""
    return build("youtube", "v3", credentials=creds)


# ── Quota Tracking ─────────────────────────────────────────────────────────────

class QuotaTracker:
    """Track API quota usage."""
    
    def __init__(self):
        self.usage = {op: 0 for op in QUOTA_COSTS}
    
    def add(self, operation: str, count: int = 1):
        if operation in QUOTA_COSTS:
            self.usage[operation] += count
    
    def total(self) -> int:
        return sum(self.usage[op] * QUOTA_COSTS[op] for op in self.usage)
    
    def print_summary(self):
        print("\n── Quota Usage Summary ──")
        for op, count in sorted(self.usage.items()):
            if count > 0:
                cost = count * QUOTA_COSTS[op]
                print(f"  {op:<25} {count:>5} calls × {QUOTA_COSTS[op]:>3} units = {cost:>6} units")
        print(f"  {'TOTAL':.<30} {self.total():>15} units")
        remaining = 10000 - self.total()
        pct = (self.total() / 10000) * 100
        print(f"  Daily quota: {pct:.1f}% used, {remaining} units remaining")


quota = QuotaTracker()


# ── Error Handling ──────────────────────────────────────────────────────────────

def api_call(func, *args, **kwargs):
    """Execute API call with retry on quota errors."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs).execute()
        except HttpError as e:
            if e.resp.status == 403 and "quotaExceeded" in str(e):
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 60  # 1min, 2min, 3min
                    print(f"  ⚠ Quota exceeded. Waiting {wait}s before retry...")
                    time.sleep(wait)
                    continue
                else:
                    print("  ✗ Quota exceeded after all retries. Try again tomorrow.")
                    raise
            elif e.resp.status == 409:  # Conflict (already exists)
                print(f"  ⚠ Already exists")
                return None
            elif e.resp.status == 400 and "subscriptionForbidden" in str(e):
                print(f"  ⚠ Cannot subscribe to own channel")
                return None
            elif e.resp.status == 400 and "invalidChannelId" in str(e):
                print(f"  ⚠ Invalid channel ID")
                return None
            else:
                print(f"  ✗ API error {e.resp.status}: {e.reason}")
                raise


# ── Fetch from Source ──────────────────────────────────────────────────────────

def fetch_subscriptions(youtube, channel_id: str) -> list[dict]:
    """Fetch all subscriptions for a channel (requires auth to read 'mine')."""
    print(f"\n── Fetching subscriptions ──")
    subs = []
    page_token = None
    
    while True:
        response = api_call(
            youtube.subscriptions().list,
            part="snippet",
            mine=True,
            maxResults=50,
            pageToken=page_token,
        )
        quota.add("subscriptions.list")
        
        if response is None:
            break
        
        items = response.get("items", [])
        subs.extend(items)
        print(f"  Fetched {len(items)} subscriptions (total: {len(subs)})")
        
        page_token = response.get("nextPageToken")
        if not page_token:
            break
        
        time.sleep(LIST_DELAY_SECONDS)
    
    print(f"  Total: {len(subs)} subscriptions")
    return subs


def fetch_playlists(youtube, channel_id: str) -> list[dict]:
    """Fetch all user-created playlists (excluding system playlists)."""
    print(f"\n── Fetching playlists ──")
    playlists = []
    page_token = None
    
    while True:
        response = api_call(
            youtube.playlists().list,
            part="snippet,status,contentDetails",
            mine=True,
            maxResults=50,
            pageToken=page_token,
        )
        quota.add("playlists.list")
        
        if response is None:
            break
        
        items = response.get("items", [])
        playlists.extend(items)
        print(f"  Fetched {len(items)} playlists (total: {len(playlists)})")
        
        page_token = response.get("nextPageToken")
        if not page_token:
            break
        
        time.sleep(LIST_DELAY_SECONDS)
    
    print(f"  Total: {len(playlists)} playlists")
    return playlists


def fetch_playlist_items(youtube, playlist_id: str) -> list[dict]:
    """Fetch all videos in a playlist."""
    items = []
    page_token = None
    
    while True:
        response = api_call(
            youtube.playlistItems().list,
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        )
        quota.add("playlistItems.list")
        
        if response is None:
            break
        
        batch = response.get("items", [])
        items.extend(batch)
        
        page_token = response.get("nextPageToken")
        if not page_token:
            break
        
        time.sleep(LIST_DELAY_SECONDS)
    
    return items


# ── Write to Destination ───────────────────────────────────────────────────────

def migrate_subscriptions(youtube, subscriptions: list[dict]) -> dict:
    """Subscribe to channels on destination account."""
    print(f"\n── Migrating {len(subscriptions)} subscriptions ──")
    
    stats = {"created": 0, "skipped": 0, "failed": 0}
    
    for i, sub in enumerate(subscriptions, 1):
        channel_id = sub["snippet"]["resourceId"]["channelId"]
        title = sub["snippet"]["title"]
        
        body = {
            "snippet": {
                "resourceId": {
                    "kind": "youtube#channel",
                    "channelId": channel_id,
                }
            }
        }
        
        try:
            result = api_call(
                youtube.subscriptions().insert,
                part="snippet",
                body=body,
            )
            quota.add("subscriptions.insert")
            
            if result:
                stats["created"] += 1
                print(f"  [{i}/{len(subscriptions)}] ✓ {title}")
            else:
                stats["skipped"] += 1
        
        except Exception as e:
            stats["failed"] += 1
            print(f"  [{i}/{len(subscriptions)}] ✗ Failed: {title} — {e}")
        
        time.sleep(INSERT_DELAY_SECONDS)
    
    print(f"\n  Results: {stats['created']} created, {stats['skipped']} skipped, {stats['failed']} failed")
    return stats


def migrate_playlists(youtube, playlists: list[dict]) -> dict:
    """Recreate playlists on destination account with all their videos."""
    print(f"\n── Migrating {len(playlists)} playlists ──")
    
    stats = {"created": 0, "skipped": 0, "failed": 0, "videos": 0}
    
    for i, playlist in enumerate(playlists, 1):
        title = playlist["snippet"]["title"]
        desc = playlist["snippet"].get("description", "")
        privacy = playlist["status"].get("privacyStatus", "private")
        
        # Skip system playlists
        if title.lower().replace(" ", "-") in SYSTEM_PLAYLISTS:
            print(f"  [{i}/{len(playlists)}] ⚠ Skipping system playlist: {title}")
            stats["skipped"] += 1
            continue
        
        print(f"\n  [{i}/{len(playlists)}] Processing: {title} (privacy: {privacy})")
        
        # Create playlist on destination
        body = {
            "snippet": {
                "title": title,
                "description": desc,
            },
            "status": {
                "privacyStatus": privacy,
            },
        }
        
        try:
            new_playlist = api_call(
                youtube.playlists().insert,
                part="snippet,status",
                body=body,
            )
            quota.add("playlists.insert")
            
            if not new_playlist:
                stats["skipped"] += 1
                continue
            
            new_playlist_id = new_playlist["id"]
            stats["created"] += 1
            print(f"    Created playlist ID: {new_playlist_id}")
            
        except Exception as e:
            stats["failed"] += 1
            print(f"    ✗ Failed to create playlist: {e}")
            continue
        
        # Fetch items from source playlist
        source_items = fetch_playlist_items(youtube, playlist["id"])
        print(f"    {len(source_items)} videos in source playlist")
        
        # Add items to destination playlist
        item_stats = {"added": 0, "skipped": 0, "failed": 0}
        for j, item in enumerate(source_items, 1):
            video_id = item["snippet"]["resourceId"]["videoId"]
            video_title = item["snippet"].get("title", video_id)
            
            item_body = {
                "snippet": {
                    "playlistId": new_playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                },
            }
            
            try:
                api_call(
                    youtube.playlistItems().insert,
                    part="snippet",
                    body=item_body,
                )
                quota.add("playlistItems.insert")
                item_stats["added"] += 1
                stats["videos"] += 1
                
                if j % 10 == 0 or j == len(source_items):
                    print(f"    Progress: {j}/{len(source_items)} videos")
            
            except HttpError as e:
                if e.resp.status == 404:
                    print(f"    ⚠ Video unavailable (deleted/private): {video_title}")
                else:
                    print(f"    ✗ Failed: {video_title} — {e.reason}")
                item_stats["failed"] += 1
            
            except Exception as e:
                print(f"    ✗ Failed: {video_title} — {e}")
                item_stats["failed"] += 1
            
            time.sleep(INSERT_DELAY_SECONDS)
        
        print(f"    Playlist done: {item_stats['added']} added, {item_stats['skipped']} skipped, {item_stats['failed']} failed")
    
    print(f"\n  Results: {stats['created']} playlists created, {stats['videos']} total videos added")
    return stats


# ── Dry Run Mode ───────────────────────────────────────────────────────────────

def dry_run(youtube):
    """Show what would be migrated without making changes."""
    print("\n" + "=" * 60)
    print("  DRY RUN — No changes will be made")
    print("=" * 60)
    
    subs = fetch_subscriptions(youtube, SOURCE_CHANNEL_ID)
    playlists = fetch_playlists(youtube, SOURCE_CHANNEL_ID)
    
    total_items = 0
    print(f"\n── Playlist Details ──")
    for pl in playlists:
        items = fetch_playlist_items(youtube, pl["id"])
        total_items += len(items)
        print(f"  {pl['snippet']['title']}: {len(items)} videos (privacy: {pl['status'].get('privacyStatus', '?')})")
    
    # Estimate quota
    estimate_subs = len(subs) * QUOTA_COSTS["subscriptions.insert"]
    estimate_playlists = len(playlists) * QUOTA_COSTS["playlists.insert"]
    estimate_items = total_items * QUOTA_COSTS["playlistItems.insert"]
    estimate_read = (len(subs) + len(playlists)) * QUOTA_COSTS["subscriptions.list"]
    estimate_total = estimate_subs + estimate_playlists + estimate_items + estimate_read
    
    print(f"\n── Migration Summary ──")
    print(f"  Subscriptions: {len(subs)}")
    print(f"  Playlists:     {len(playlists)}")
    print(f"  Total videos:  {total_items}")
    print(f"\n── Estimated Quota Cost ──")
    print(f"  Subscriptions: {len(subs)} × {QUOTA_COSTS['subscriptions.insert']} = {estimate_subs} units")
    print(f"  Playlists:     {len(playlists)} × {QUOTA_COSTS['playlists.insert']} = {estimate_playlists} units")
    print(f"  Videos:        {total_items} × {QUOTA_COSTS['playlistItems.insert']} = {estimate_items} units")
    print(f"  TOTAL:         {estimate_total} units (daily limit: 10,000)")
    
    if estimate_total > 10000:
        days = (estimate_total // 10000) + 1
        print(f"  ⚠ Exceeds daily quota! Will take ~{days} day(s)")
    
    return subs, playlists


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Migrate YouTube playlists & subscriptions")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be migrated")
    parser.add_argument("--subscriptions-only", action="store_true", help="Migrate only subscriptions")
    parser.add_argument("--playlists-only", action="store_true", help="Migrate only playlists")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--clear-tokens", action="store_true", help="Delete all saved tokens and re-authenticate")
    args = parser.parse_args()
    
    print("=" * 60)
    print("  YouTube Migration Tool")
    print("=" * 60)
    print(f"  Source:      {SOURCE_CHANNEL_ID} (bytebyte1001@gmail.com)")
    print(f"  Destination: {DEST_CHANNEL_ID} (rituraj171926@gmail.com)")
    print(f"  Timestamp:   {datetime.now().isoformat()}")
    
    # Clear tokens if requested
    if args.clear_tokens:
        TOKENS_DIR.mkdir(exist_ok=True)
        for f in TOKENS_DIR.glob("*_token.pkl"):
            f.unlink()
            print(f"  Deleted: {f}")
        print("  All tokens cleared.")
    
    if not CLIENT_SECRETS_FILE.exists():
        print(f"\n✗ Missing {CLIENT_SECRETS_FILE}")
        print("  1. Go to https://console.cloud.google.com/apis/credentials")
        print("  2. Create OAuth 2.0 Client ID (type: Desktop App)")
        print("  3. Download JSON and save as client_secrets.json here")
        sys.exit(1)
    
    # Step 1: Authenticate source account (read access)
    print("\n── Step 1: Authenticate SOURCE account ──")
    print(f"  Log into: bytebyte1001@gmail.com")
    print(f"  Expected channel ID: {SOURCE_CHANNEL_ID}")
    source_creds = get_credentials("source", expected_channel_id=SOURCE_CHANNEL_ID)
    source_youtube = build_youtube_client(source_creds)
    print("  ✓ Source authenticated")
    
    # Dry run — only needs source
    if args.dry_run:
        dry_run(source_youtube)
        quota.print_summary()
        return
    
    # Step 2: Fetch data from source
    subs = None
    playlists = None
    
    if not args.playlists_only:
        subs = fetch_subscriptions(source_youtube, SOURCE_CHANNEL_ID)
    
    if not args.subscriptions_only:
        playlists = fetch_playlists(source_youtube, SOURCE_CHANNEL_ID)
    
    # Step 3: Authenticate destination account (write access)
    print("\n── Step 2: Authenticate DESTINATION account ──")
    print(f"  Log into: rituraj171926@gmail.com")
    print(f"  Expected channel ID: {DEST_CHANNEL_ID}")
    print(f"  ⚠ Make sure you are logged into the CORRECT Google account in your browser!")
    print(f"  ⚠ If stuck on the wrong account, use an incognito/private window.")
    dest_creds = get_credentials("dest", expected_channel_id=DEST_CHANNEL_ID)
    dest_youtube = build_youtube_client(dest_creds)
    print("  ✓ Destination authenticated")
    
    # Step 4: Confirm
    if not args.yes:
        print(f"\n── Ready to migrate ──")
        if subs:
            print(f"  {len(subs)} subscriptions")
        if playlists:
            print(f"  {len(playlists)} playlists (with all videos)")
        print(f"\n  Estimated quota cost: ~{quota.total()} units so far (reading)")
        confirm = input("\nProceed? (y/N): ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return
    
    # Step 5: Migrate
    sub_stats = None
    playlist_stats = None
    
    if subs:
        sub_stats = migrate_subscriptions(dest_youtube, subs)
    
    if playlists:
        playlist_stats = migrate_playlists(dest_youtube, playlists)
    
    # Step 6: Summary
    quota.print_summary()
    
    print("\n── Migration Complete ──")
    if sub_stats:
        print(f"  Subscriptions: {sub_stats['created']} created, {sub_stats['skipped']} skipped, {sub_stats['failed']} failed")
    if playlist_stats:
        print(f"  Playlists: {playlist_stats['created']} created, {playlist_stats['videos']} videos, {playlist_stats['failed']} failed")


if __name__ == "__main__":
    main()
