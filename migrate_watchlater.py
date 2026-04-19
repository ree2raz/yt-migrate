#!/usr/bin/env python3
"""Migrate Watch Later from Takeout CSV to destination account."""
import csv
import pickle
import time
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request

TOKENS_DIR = Path(__file__).parent / "tokens"
WATCH_LATER_CSV = Path(__file__).parent / "takeout_extracted/Takeout/YouTube and YouTube Music/playlists/Watch later-videos.csv"
DEST_CHANNEL_ID = "UC_DEST_CHANNEL_ID_HERE"


def load_creds(key):
    token_file = TOKENS_DIR / f"{key}_token.pkl"
    with open(token_file, "rb") as f:
        creds = pickle.load(f)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request)
            with open(token_file, "wb") as f:
                pickle.dump(creds, f)
    return creds


def main():
    video_ids = []
    with open(WATCH_LATER_CSV, "r") as f:
        for row in csv.DictReader(f):
            vid = row.get("Video ID", "").strip()
            if vid:
                video_ids.append(vid)

    print(f"Watch Later: {len(video_ids)} videos")

    creds = load_creds("dest")
    yt = build("youtube", "v3", credentials=creds)

    r = yt.channels().list(part="id,snippet", mine=True).execute()
    ch = r["items"][0]
    print(f"Logged in as: {ch['snippet']['title']} ({ch['id']})")
    if ch["id"] != DEST_CHANNEL_ID:
        print(f"✗ Wrong account! Expected {DEST_CHANNEL_ID}")
        return

    # Create playlist
    pl = yt.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": "Watch Later", "description": "Migrated from previous account"},
            "status": {"privacyStatus": "private"},
        },
    ).execute()
    pl_id = pl["id"]
    print(f"Created playlist: {pl_id}")

    ok = 0
    fail = 0
    for i, vid in enumerate(video_ids, 1):
        try:
            yt.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": pl_id,
                        "resourceId": {"kind": "youtube#video", "videoId": vid},
                    }
                },
            ).execute()
            ok += 1
        except HttpError as e:
            if e.resp.status == 404:
                print(f"  ⚠ Deleted/private: {vid}")
            else:
                print(f"  ✗ Failed: {vid} — {e.reason}")
            fail += 1

        if i % 10 == 0 or i == len(video_ids):
            print(f"  Progress: {i}/{len(video_ids)}")
        time.sleep(0.5)

    print(f"\nDone! Added: {ok}, Failed: {fail}")


if __name__ == "__main__":
    main()
