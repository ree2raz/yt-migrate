# YouTube Migration Tool

Migrate your YouTube **subscriptions**, **playlists**, and **Watch Later** from one account to another using the YouTube Data API v3.

## What can be migrated

| Data | Method | Notes |
|---|---|---|
| Subscriptions | YouTube Data API | Skips own channel |
| Your playlists (created) | YouTube Data API | Creates new playlists on destination |
| Saved/bookmarked playlists | YouTube Data API + Takeout CSV | Recreated as private playlists |
| Watch Later | Google Takeout CSV | Migrated as a private playlist |
| Liked videos | ❌ Not supported | API prohibits writing to Likes |
| Watch History | ❌ Not supported | Not accessible via API |

## Prerequisites

- Python 3.10+
- A Google account with YouTube access
- ~10 minutes for one-time Google Cloud setup

## Quick start

```bash
git clone https://github.com/ree2raz/migrate-my-youtube.git
cd migrate-my-youtube
python3 -m venv .venv
source .venv/bin/activate
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

## Google Cloud setup (one-time)

### Step 1: Create a Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click the **project dropdown** at the top
3. Click **New Project**, name it anything (e.g., `yt-migrate`), click **Create**

### Step 2: Enable YouTube Data API v3

1. Go to [APIs & Services > Library](https://console.cloud.google.com/apis/library)
2. Search for **YouTube Data API v3**
3. Click on it, press **Enable**

### Step 3: Configure OAuth consent screen

1. Go to [APIs & Services > OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
2. Choose **External**, click **Create**
3. Fill required fields (app name, your email). Click **Save and Continue**
4. On the **Scopes** page, click **Add or Remove Scopes** and add these three:

   ```
   https://www.googleapis.com/auth/youtube.readonly
   https://www.googleapis.com/auth/youtube.force-ssl
   https://www.googleapis.com/auth/youtube
   ```

5. Click **Update** → **Save and Continue**
6. On the **Test users** page, click **Add Users**
7. Add **both** email addresses:
   - The email of your **source** YouTube account (migrating FROM)
   - The email of your **destination** YouTube account (migrating TO)
8. Click **Save and Continue** → **Back to Dashboard**

> **Important:** Without adding both emails as Test Users, authentication will fail with "Access Denied".

### Step 4: Create OAuth 2.0 client ID

1. Go to [APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials** → **OAuth client ID**
3. **Application type**: select **Desktop app**
4. **Name**: `yt-migrate`
5. Click **Create**
6. Click **Download JSON**
7. Rename the downloaded file to `client_secrets.json`
8. Place it in the root of this project:

   ```
   yt-migrate/
   ├── client_secrets.json   <-- put it here
   ├── migrate.py
   ├── migrate_playlists.py
   └── migrate_watchlater.py
   ```

> If you see "Google hasn't verified this app" during login, click **Advanced** → **Go to yt-migrate (unsafe)**. This is expected — you're running your own app.

## Configure your channel IDs

Edit these files and replace the placeholder channel IDs with yours:

**migrate.py** (subscriptions + owned playlists):
```python
SOURCE_CHANNEL_ID = "UC_YOUR_SOURCE_CHANNEL_ID"
DEST_CHANNEL_ID   = "UC_YOUR_DEST_CHANNEL_ID"
```

**migrate_playlists.py** (saved/bookmarked playlists):
```python
DEST_CHANNEL_ID = "UC_YOUR_DEST_CHANNEL_ID"

SAVED_PLAYLISTS = [
    ("PL_PLAYLIST_ID_1", "Display name for playlist 1"),
    ("PL_PLAYLIST_ID_2", "Display name for playlist 2"),
]
```

**migrate_watchlater.py** (Watch Later):
```python
DEST_CHANNEL_ID = "UC_YOUR_DEST_CHANNEL_ID"
```

To find your channel ID: [YouTube Studio](https://studio.youtube.com/) → Settings → Channel → Basic info.

## Usage

### Migrate subscriptions and owned playlists

```bash
source .venv/bin/activate
python migrate.py --dry-run   # preview first
python migrate.py             # run for real
```

Options: `--subscriptions-only`, `--playlists-only`, `--clear-tokens`, `-y` (skip confirmation).

### Migrate saved/bookmarked playlists

These are playlists you've bookmarked/saved from other channels (not ones you created).

**Step 1:** Collect playlist IDs. Open YouTube, go to each saved playlist, and copy the ID from the URL:
```
https://www.youtube.com/playlist?list=PLXXXXXXXXXXXXXXXXXX
                                       ^^^^^^^^^^^^^^^^^^^^ this part
```

**Step 2:** Add them to `migrate_playlists.py` in the `SAVED_PLAYLISTS` list:
```python
SAVED_PLAYLISTS = [
    ("PLAqhIrjkxbuWI23v9cThsA9GvCAUhRvKZ", "3Blue1Brown - Linear Algebra"),
    ("PLoROMvodv4rOCXd21gf0CF4xr35yINeOy", "Stanford CS224N"),
]
```

**Step 3:** Run:
```bash
python migrate_playlists.py
```

### Migrate Watch Later

The YouTube API cannot read Watch Later. Use Google Takeout instead.

**Step 1:** Export from Google Takeout:
1. Go to [takeout.google.com](https://takeout.google.com)
2. Deselect all, then select only **YouTube and YouTube Music**
3. Click **All YouTube data included** → deselect everything except **playlists**
4. Export and download

**Step 2:** Place the Takeout zip in this directory and extract:
```bash
# The script expects this structure:
# takeout_extracted/Takeout/YouTube and YouTube Music/playlists/Watch later-videos.csv

mkdir -p takeout_extracted
cd takeout_extracted
unzip ../takeout.zip "Takeout/YouTube and YouTube Music/playlists/*"
cd ..
```

**Step 3:** Run:
```bash
python migrate_watchlater.py
```

## Authentication

The scripts open your browser for Google OAuth. You'll authenticate **twice**:

1. **Source account** — log into the account you're migrating FROM
2. **Destination account** — log into the account you're migrating TO

**If your browser auto-logs into the wrong account:** use an incognito/private window for the second auth, or run `python migrate.py --clear-tokens` to start fresh.

Each script verifies the authenticated account matches the expected channel ID before making any changes.

## Quota limits

YouTube API gives **10,000 free units per day**:

| Operation | Cost |
|---|---|
| `subscriptions.list` | 1 unit |
| `subscriptions.insert` | 50 units |
| `playlists.list` | 1 unit |
| `playlists.insert` | 50 units |
| `playlistItems.list` | 1 unit |
| `playlistItems.insert` | 50 units |

If you hit the daily quota, re-run the next day. Already-created items will be skipped.

## File structure

```
yt-migrate/
├── migrate.py              # Subscriptions + owned playlists
├── migrate_playlists.py    # Saved/bookmarked playlists
├── migrate_watchlater.py   # Watch Later (from Takeout CSV)
├── client_secrets.json     # Your OAuth credentials (you create this)
├── takeout_extracted/      # Unzipped Google Takeout data
├── tokens/                 # Auto-created, stores auth tokens
├── .gitignore
└── README.md
```

`client_secrets.json`, `tokens/`, and `takeout_extracted/` are gitignored and will never be committed.

## Troubleshooting

**"Access Denied" during OAuth:**
→ Add both accounts as Test Users in the [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent).

**"Google hasn't verified this app":**
→ Click **Advanced** → **Go to yt-migrate (unsafe)**. Normal for personal apps.

**"quotaExceeded" error:**
→ Wait until midnight Pacific Time, re-run.

**Wrong account authenticated:**
→ Use incognito/private window, or `python migrate.py --clear-tokens`.

**Watch Later shows 0 videos:**
→ The API cannot read Watch Later. Use the Takeout CSV method instead.

## License

MIT
