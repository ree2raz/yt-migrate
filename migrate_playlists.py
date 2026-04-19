#!/usr/bin/env python3
"""
Migrate saved playlists + Watch Later from source to destination.
Uses API for playlists and Takeout CSV for Watch Later.
"""
import csv
import pickle
import sys
import time
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

CLIENT_SECRETS_FILE = Path(__file__).parent / "client_secrets.json"
TOKENS_DIR = Path(__file__).parent / "tokens"
TAKEOUT_DIR = Path(__file__).parent / "takeout_extracted"
WATCH_LATER_CSV = TAKEOUT_DIR / "Takeout/YouTube and YouTube Music/playlists/Watch later-videos.csv"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube",
]

DEST_CHANNEL_ID = "UC_DEST_CHANNEL_ID_HERE"

SAVED_PLAYLISTS = [
    # Add your saved playlists here as (playlist_id, display_name) tuples
    # Example: ("PLXXXXXXXXXXXXXXXXXX", "Playlist Name")
]

INSERT_DELAY = 0.5


def get_credentials(token_key: str, expected_channel_id: str = None):
    TOKENS_DIR.mkdir(exist_ok=True)
    token_file = TOKENS_DIR / f"{token_key}_token.pkl"

    creds = None
    if token_file.exists():
        with open(token_file, "rb") as f:
            creds = pickle.load(f)

    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request)
            except Exception:
                creds = None
                token_file.unlink(missing_ok=True)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
        with open(token_file, "wb") as f:
            pickle.dump(creds, f)

    if expected_channel_id:
        youtube = build("youtube", "v3", credentials=creds)
        r = youtube.channels().list(part="id,snippet", mine=True).execute()
        items = r.get("items", [])
        if items and items[0]["id"] != expected_channel_id:
            print(f"  ✗ WRONG ACCOUNT! Got {items[0]['id']}, expected {expected_channel_id}")
            token_file.unlink(missing_ok=True)
            sys.exit(1)
        print(f"  ✓ Verified: {items[0]['snippet']['title']} ({items[0]['id']})")

    return creds


def fetch_playlist_items(youtube, playlist_id: str) -> list[dict]:
    items = []
    page_token = None
    while True:
        try:
            r = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            ).execute()
        except HttpError as e:
            print(f"    ✗ Error {e.resp.status}: {e.reason}")
            return items
        items.extend(r.get("items", []))
        page_token = r.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.1)
    return items


def create_playlist(youtube, title: str, description: str = "", privacy: str = "private") -> str | None:
    body = {
        "snippet": {"title": title, "description": description},
        "status": {"privacyStatus": privacy},
    }
    try:
        r = youtube.playlists().insert(part="snippet,status", body=body).execute()
        print(f"    Created: {r['id']}")
        return r["id"]
    except HttpError as e:
        print(f"    ✗ Failed: {e.reason}")
        return None


def add_video_to_playlist(youtube, playlist_id: str, video_id: str, video_title: str = "") -> bool:
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    try:
        youtube.playlistItems().insert(part="snippet", body=body).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            print(f"        ⚠ Deleted/private: {video_id}")
        else:
            print(f"        ✗ Failed: {video_id} — {e.reason}")
        return False


def read_watch_later_csv() -> list[str]:
    """Read video IDs from Google Takeout Watch Later CSV."""
    video_ids = []
    if not WATCH_LATER_CSV.exists():
        print(f"  ⚠ Watch Later CSV not found at {WATCH_LATER_CSV}")
        return video_ids

    with open(WATCH_LATER_CSV, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            vid = row.get("Video ID", "").strip()
            if vid:
                video_ids.append(vid)

    return video_ids


def main():
    print("=" * 60)
    print("  Saved Playlists & Watch Later Migration")
    print("=" * 60)

    # ── Authenticate source ──────────────────────────────────────────
    print("\n── Step 1: Authenticate SOURCE account ──")
    source_creds = get_credentials("source")
    source_yt = build("youtube", "v3", credentials=source_creds)
    print("  ✓ Source authenticated")

    # ── Fetch saved playlists from API ───────────────────────────────
    print("\n── Fetching saved playlists from API ──")
    saved_data = {}
    for pid, name in SAVED_PLAYLISTS:
        print(f"  {name}")
        items = fetch_playlist_items(source_yt, pid)
        saved_data[pid] = {"title": name, "items": items}
        print(f"    → {len(items)} videos")

    # ── Read Watch Later from Takeout CSV ────────────────────────────
    print("\n── Reading Watch Later from Takeout CSV ──")
    wl_video_ids = read_watch_later_csv()
    print(f"  → {len(wl_video_ids)} videos")
    if wl_video_ids:
        saved_data["WL"] = {
            "title": "Watch Later",
            "items": [
                {
                    "snippet": {
                        "resourceId": {"videoId": vid},
                        "title": vid,
                    }
                }
                for vid in wl_video_ids
            ],
            "from_csv": True,
        }

    # ── Summary ──────────────────────────────────────────────────────
    total_videos = sum(len(d["items"]) for d in saved_data.values())
    print(f"\n── Summary ──")
    print(f"  Playlists to create: {len(saved_data)}")
    print(f"  Total videos: {total_videos}")
    for d in saved_data.values():
        src = " (from Takeout CSV)" if d.get("from_csv") else ""
        print(f"    {d['title']}: {len(d['items'])} videos{src}")

    cost = len(saved_data) * 50 + total_videos * 50
    print(f"\n  Estimated quota: {cost} units")

    confirm = input("\nProceed? (y/N): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Aborted.")
        return

    # ── Authenticate destination ─────────────────────────────────────
    print("\n── Step 2: Authenticate DESTINATION account ──")
    print("  ⚠ Use INCOGNITO window if browser auto-logs into wrong account")
    dest_creds = get_credentials("dest", expected_channel_id=DEST_CHANNEL_ID)
    dest_yt = build("youtube", "v3", credentials=dest_creds)
    print("  ✓ Destination authenticated")

    # ── Migrate ──────────────────────────────────────────────────────
    stats = {"playlists": 0, "videos_ok": 0, "videos_fail": 0}

    for data in saved_data.values():
        title = data["title"]
        items = data["items"]
        print(f"\n── Migrating: {title} ({len(items)} videos) ──")

        new_pid = create_playlist(dest_yt, title, privacy="private")
        if not new_pid:
            continue
        stats["playlists"] += 1

        for i, item in enumerate(items, 1):
            vid = item["snippet"]["resourceId"]["videoId"]
            vtitle = item["snippet"].get("title", vid)

            if add_video_to_playlist(dest_yt, new_pid, vid, vtitle):
                stats["videos_ok"] += 1
            else:
                stats["videos_fail"] += 1

            if i % 10 == 0 or i == len(items):
                print(f"    {i}/{len(items)}")

            time.sleep(INSERT_DELAY)

    print(f"\n{'=' * 60}")
    print(f"  DONE!")
    print(f"  Playlists: {stats['playlists']}")
    print(f"  Videos added: {stats['videos_ok']}")
    print(f"  Videos failed: {stats['videos_fail']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
