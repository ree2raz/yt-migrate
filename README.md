# YouTube Migration Tool

Migrate your YouTube **subscriptions** and **playlists** from one account to another using the YouTube Data API v3.

Supports both YouTube accounts (personal and brand accounts).

## Prerequisites

- Python 3.10+
- A Google account
- 5 minutes for one-time Google Cloud setup

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/<your-username>/yt-migrate.git
cd yt-migrate
python3 -m venv .venv
source .venv/bin/activate
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

### 2. Create Google Cloud credentials

You need an OAuth 2.0 client ID so this script can authenticate with YouTube on your behalf.

#### 2a. Create a Google Cloud project

1. Open [Google Cloud Console](https://console.cloud.google.com/)
2. Click the **project dropdown** at the top of the page
3. Click **New Project**
4. Name it anything (e.g., `yt-migrate`)
5. Click **Create**

#### 2b. Enable the YouTube Data API

1. Go to [APIs & Services > Library](https://console.cloud.google.com/apis/library)
2. Search for **YouTube Data API v3**
3. Click on it and press **Enable**

#### 2c. Set up the OAuth consent screen

1. Go to [APIs & Services > OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
2. Select **External** and click **Create**
3. Fill in the required fields:
   - **App name**: `yt-migrate` (or anything you like)
   - **User support email**: your email address
   - **Developer contact email**: your email address
4. Click **Save and Continue**
5. On the **Scopes** page, click **Add or Remove Scopes**
6. Add these three scopes (paste each one into the filter and check the box):

   ```
   https://www.googleapis.com/auth/youtube.readonly
   https://www.googleapis.com/auth/youtube.force-ssl
   https://www.googleapis.com/auth/youtube
   ```

7. Click **Update**, then **Save and Continue**
8. On the **Test users** page, click **Add Users**
9. Add **both** email addresses:
   - The email of your **source** YouTube account (where you're migrating FROM)
   - The email of your **destination** YouTube account (where you're migrating TO)
   > **Important:** Without adding both emails here, authentication will fail with "Access Denied"
10. Click **Save and Continue**, then **Back to Dashboard**

#### 2d. Create the OAuth client ID

1. Go to [APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials** → **OAuth client ID**
3. **Application type**: select **Desktop app**
4. **Name**: `yt-migrate`
5. Click **Create**
6. A dialog shows "OAuth client created" — click **Download JSON**
7. Rename the downloaded file to `client_secrets.json`
8. Place it in the root of this project directory:

   ```
   yt-migrate/
   ├── client_secrets.json   <-- put it here
   ├── migrate.py
   └── ...
   ```

> **Troubleshooting:** If you see "Google hasn't verified this app" during login, click **Advanced** → **Go to yt-migrate (unsafe)**. This is normal — you're using your own app, not a public one.

### 3. Edit channel IDs

Open `migrate.py` and update these two lines with your channel IDs:

```python
SOURCE_CHANNEL_ID = "UC_SOURCE_CHANNEL_ID_HERE"       # account you're migrating FROM
DEST_CHANNEL_ID   = "UC_DEST_CHANNEL_ID_HERE"         # account you're migrating TO
```

To find your channel ID:
1. Go to [YouTube Studio](https://studio.youtube.com/) while logged into that account
2. Click **Settings** → **Channel** → **Basic info**
3. Your channel ID starts with `UC`

## Usage

### Dry run (no changes made)

```bash
source .venv/bin/activate
python migrate.py --dry-run
```

This authenticates your source account, fetches all subscriptions and playlists, and shows what would be migrated. No writes are made.

### Full migration

```bash
python migrate.py
```

The script will:
1. Ask you to authenticate your **source** account in the browser
2. Fetch all subscriptions and playlists
3. Ask you to authenticate your **destination** account in the browser
4. Confirm before making any changes
5. Migrate everything

### Options

```bash
python migrate.py --subscriptions-only    # only migrate subscriptions
python migrate.py --playlists-only        # only migrate playlists
python migrate.py -y                      # skip confirmation prompt
python migrate.py --clear-tokens          # force re-authentication for both accounts
python migrate.py --dry-run               # preview without making changes
```

## Authentication flow

The script opens your browser **twice**:

1. **First popup** → log into your **source** YouTube account
2. **Second popup** → log into your **destination** YouTube account

If your browser automatically logs into the wrong account:
- Use an **incognito/private window** for the second auth
- Or run `python migrate.py --clear-tokens` to start fresh

The script verifies each authenticated account matches the expected channel ID. If you accidentally authenticate with the wrong account, it will warn you and exit.

## Quota limits

YouTube API gives you **10,000 free units per day**. Costs per operation:

| Operation | Cost per call |
|---|---|
| `subscriptions.list` | 1 unit |
| `subscriptions.insert` | 50 units |
| `playlists.list` | 1 unit |
| `playlists.insert` | 50 units |
| `playlistItems.list` | 1 unit |
| `playlistItems.insert` | 50 units |

**Example:** 100 subscriptions + 10 playlists with 200 videos = 100×50 + 10×50 + 200×50 = **15,500 units** (~2 days)

If you hit the daily quota, re-run the next day. Already-created items will be skipped automatically.

## What gets migrated

| Data | Migrated? | Notes |
|---|---|---|
| Subscriptions | ✅ Yes | All channel subscriptions |
| Playlists | ✅ Yes | Your playlists, recreated on destination |
| Playlist videos | ✅ Yes | All videos added to new playlists |
| Liked videos | ❌ No | API does not allow writing to Likes |
| Watch Later | ❌ No | System-managed playlist |
| Watch History | ❌ No | Not accessible via API |
| History / Home feed | ❌ No | Not accessible via API |

## Troubleshooting

**"Access Denied" during OAuth:**
→ You didn't add your email as a Test User. Go to [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent), add both account emails under Test Users.

**"Google hasn't verified this app":**
→ Click **Advanced** → **Go to yt-migrate (unsafe)**. This is expected — you're running your own app.

**"quotaExceeded" error:**
→ Daily API limit reached. Wait until midnight Pacific Time, re-run the script.

**"subscriptionForbidden" error:**
→ You're trying to subscribe to your own channel. The script skips this automatically.

**Browser logs into wrong account during auth:**
→ Use an incognito/private browser window, or log out of Google first, or run `python migrate.py --clear-tokens`.

**Token errors or stale sessions:**
→ Run `python migrate.py --clear-tokens` to delete cached auth tokens and re-authenticate from scratch.

## File structure

```
yt-migrate/
├── migrate.py              # Main script
├── client_secrets.json     # Your OAuth credentials (you create this)
├── tokens/                 # Auto-created, stores auth tokens
│   ├── source_token.pkl
│   └── dest_token.pkl
├── .venv/                  # Python virtual environment
├── .gitignore
└── README.md
```

`client_secrets.json` and `tokens/` are in `.gitignore` and will never be committed.

## License

MIT
