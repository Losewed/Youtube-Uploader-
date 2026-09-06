# Youtube-Shorts-Uploader
Uploading videos with titles, tags, and descriptions via Google Drive and local AI any AI

ENG/RU


For the API (uploading to YouTube, downloading from Google Drive), we use console.cloud.google.com

In Google Cloud -> create new project -> we enable the Drive API and the YouTube API v3 -> seach OAuth consest screen -> Client -> Deskctop app -> Audience (External) -> Download Json

Download Unsloth https://unsloth.ai/ 

Download an AI model that can watch videos—in my example, it's Qwen3-VL-8B-Instruct

In unsloth, create an API key and paste it into the `local_api_key` line in `config.json`

The program retrieves videos from a folder on Google Drive, displays thumbnails from a local
template, extracts the title, description, and tags from it, converts the video to the required
format, and uploads it to YouTube with a scheduled publication.

It runs automatically according to a schedule. All you need to do is place the file in the folder.

```
Google Drive: videos/finished videos
        │  download
        ▼
   ffmpeg: 12 frames from the video
        │
        ▼
   Qwen3-VL in Unsloth  ──►  title, description, tags (JSON according to the schema)
        │
        ▼
   ffmpeg: 1920×1080 → 1080×1920 (for Shorts)
        │
        ▼
   YouTube: private + upload time
        │
        ▼
   file on Drive → to trash
```

---

## How to Use

Drop your videos into the **`videos/finished videos`** folder on Google Drive. Every
half hour from 10:00 AM to 12:00 AM, the program checks the folder, retrieves new videos, and uploads them.
Videos are published one at a time per day at 3:30 AM.

Everything else covers how to set this up and make changes.

### Dashboard

Double-click **`Dashboard.bat`**. The window shows a countdown to
the next upload, the time of the next folder check, the model’s status, and
the queue. On the left is a list of videos with thumbnails; on the right is what the model has written.

The buttons at the bottom perform the same actions as the commands: check the folder now, generate
a draft without uploading, wake up or shut down the model, and open the log

In the header: **“Settings”** (format, model, folder), a **RU/EN** language switch,
and an update option. Within the settings, there is a **“Restart Setup”** button, which
opens the first-run wizard.

### First-Run Wizard

It opens automatically if the settings are empty. Five steps: check ffmpeg, sign in to
Google, select a folder, select a model, and set a schedule. It won’t break anything—you can just
click “Next” all the way through; the current values are pre-filled.

---

## Commands

Run in PowerShell, opened in the program folder.

| Command | What it does |
|---|---|
| `uploader.py check` | Checks the entire pipeline: model, FFmpeg, Drive, channel |
| `uploader.py plan` | Shows what has already been uploaded and is awaiting publication, and what is in the queue |
| `uploader.py list` | Contents of the folder on Drive |
| `uploader.py run` | Retrieve and upload new videos |
| `uploader.py run --dry-run` | Display metadata without uploading anything |
| `uploader.py run --limit 1` | One video per run |
| `uploader.py models` | Which models are on the server, their context, and status |
| `uploader.py setmodel NAME` | Change the model (`--text` — text-based, `--force` — without validation) |
| `uploader.py setmode shorts\|video\|auto` | Publication format |
| `uploader.py setfolder NAME\|ID` | Change folder (`--processed` — for processed videos) |
| `uploader.py folders [part of name]` | Find a folder and its ID |
| `uploader.py loadmodel` / `unloadmodel` | Load a model into memory / free up video memory |

The team needs to set up the Python environment:

```
.\.venv\Scripts\python.exe uploader.py check
```

---

## Settings (`config.json`)

You can configure this through the settings window, using the commands above, or manually. After editing the file manually,
verify that the file is intact:

```
.\.venv\Scripts\python.exe -c “import json; json.load(open(‘config.json’, encoding=‘utf-8’)); print(‘JSON is intact’)”
```

### `drive` — where files come from

| Key | Value | What it does |
|---|---|---|
| `folder_id` | Folder ID | Where to get videos from |
| `processed_folder_id` | Folder ID | Where to move files when `after_upload: move` |
| `after_upload` | `trash` / `move` / `delete` | What to do with the file after upload |
| `recurse` | `true` / `false` | Whether to enter nested folders |
| `min_age_minutes` | `2` | Do not touch files that were just modified—they may still be syncing |

`trash` — moves to the Drive trash; can be restored within 30 days. `delete` — permanently deleted, bypasses
the trash. `move` — remains in `processed_folder_id` permanently.

### `youtube` — what happens on the channel

| Key | Value | What it does |
|---|---|---|
| `mode` | `shorts` / `video` / `auto` | Post format; see below |
| `privacy_status` | `private` | Must be `private` for scheduled posts |
| `category_id` | `20` | Category. 20 — Games, 22 — People and Blogs, 24 — Entertainment |
| `auto_category` | `false` | `true` — the model selects the category. For a single-topic channel, `false` is better |
| `default_language` | `en` | Video language for YouTube |
| `made_for_kids` | `false` | Declaration of children’s content |
| `playlist_id` | `“”` | If specified, the video will be added to the playlist |
| `force_vertical` | `true` | Force a horizontal frame to be vertical (only in Shorts mode) |
| `vertical_mode` | `crop` / `blur` / `stretch` | How to adjust the aspect ratio |
| `footer_shorts` | `#shorts #minecraft` | Caption at the end of the description for Shorts |
| `footer_video` | `#minecraft` | Same for a regular video |

**Posting modes.** `shorts` — everything is posted as Shorts; horizontal footage
is cropped. `video` — the footage remains unchanged; the description is slightly more detailed.
`auto` — determined by the source: vertical clips up to 3 minutes as Shorts; everything else as a regular
video.

**Cropping methods.** `crop` — enlarge to fill the frame and crop the sides,
aspect ratio preserved. `blur` — fit to width, fill the top and bottom margins
with a blurred frame. `stretch` — stretch, aspect ratio distorted.

YouTube classifies a video as a Shorts video only based on the vertical aspect ratio and a duration of up to
three minutes. The `#shorts` hashtag does not affect this.

### `schedule` — when it goes live

| Key | Value | What it does |
|---|---|---|
| `timezone` | `Europe/Moscow` | Time zone for publication times |
| `publish_times` | `[“03:30”]` | Time slots per day. Twice a day — `[“03:30”, “15:00”]` |
| `min_lead_hours` | `3` | Do not schedule a publication earlier than this many hours after upload |
| `max_per_run` | `3` | Number of videos to upload per run |
| `max_per_day` | `5` | Daily upload limit |

Slots do not overlap: the last scheduled time is stored in `state.json`, and
the next video is assigned the next available slot. Three videos scheduled for the same slot
will be published on three different days.

`max_per_day` prevents the quota from being exhausted. YouTube’s quota is 10,000 units per
day; each upload costs 1,600, or six units. If checks were performed every half hour without
this limit, the quota would be exhausted in two runs.

### `metadata` — who writes the texts and how

| Key | Value | What it does |
|---|---|---|
| `provider` | `local` / `claude` / `auto` / `local_first` | Who writes the text. `auto` — Claude first, then local |
| `channel_context` | text | **Channel description.** Has a significant impact on text quality |
| `output_language` | `en` | Language of the title and description |
| `title_max_chars` | `90` | Soft limit for the title. YouTube’s hard limit is 100 |
| `tags_count` | `15` | Approximate number of tags |
| `require_generated` | `true` | Do not upload the video if the metadata fails |

**Local model:**
| Key | Value | What it does |
|---|---|---|
| `local_url` | `http://127.0.0.1:8888` | Model server address |
| `local_api` | `openai` / `ollama` | Protocol. Unsloth and LM Studio — `openai` |
| `local_model` | name | Text model, used for speech decoding |
| `local_api_key` | token | Required for loading and unloading the model |
| `local_timeout` | `900` | How long to wait for a response, in seconds |
| `local_auto_load` | `true` | Wake up the model if it has been unloaded |
| `local_unload_after` | `true` | Unload after use to free up video memory |
| `local_load_via_api` | `false` | **Do not enable.** Unsloth will re-download the model from the internet via `/v1/load` |

**Frame Recognition:**

| Key | Value | What it does |
|---|---|---|
| `vision` | `true` | Show the model frames from the video |
| `vision_model` | name | A model capable of analyzing images |
| `vision_frames` | `12` | Number of frames to load |
| `vision_width` | `640` | Frame width in pixels |
| `vision_token_budget` | `16000` | Context limit for images |

A 640×360 frame costs about 264 tokens, while a vertical 640×1138 frame costs about 880.
Twelve frames take up 3,200 and 10,500 tokens, respectively. If there isn’t enough space for the requested frames,
the program will automatically reduce their number and log this information.

**Speech Recognition** (disabled; videos are muted):

| Key | Value | What it does |
|---|---|---|
| `transcribe` | `false` | Whether to transcribe speech |
| `whisper_model` | `small` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `whisper_language` | `null` | `null` — detect automatically |
| `transcribe_max_minutes` | `20` | How many minutes of the video to transcribe |

If you enable `transcribe`, install the dependencies:
`pip install -r requirements-extra.txt`. Then the metadata will be generated based on
the transcription, and frames will not be needed—`local_model` will be used instead of
`vision_model`.

---

## Files

| File | What it is |
|---| ---|
| `uploader.py` | Main script and all commands |
| `meta.py` | Frames, cropping, model calls |
| `gauth.py` | Google sign-in, token storage |
| `dashboard.py` | Main dashboard window |
| `settings_window.py` | Settings window |
| `setup_wizard.py` | First-run wizard |
| `ui.py` | Palette, buttons, rounded corners |
| `i18n.py` | Interface strings, Russian and English |
| `config.json` | All settings |
| `state.json` | What has already been uploaded and the scheduled time for the last publication |
| `ui.json` | Selected interface language |
| `thumbs/` | Video thumbnails for the dashboard |
| `logs/uploader.log` | Activity log |
| `client_secret.json`, `token.json` | **Access to your Google account** |

Secrets are listed in `.gitignore` and are not included in the copy for a friend.

---

## Give the program to someone else

```
.\“Build a copy for a friend.ps1”
```
It will create a folder without any of your personal information: no tokens, keys, download history, or
covers. If any personal information is accidentally leaked, the script will delete the folder and stop running.

Your friend will need: Python 3.12+, ffmpeg, their own `client_secret.json` from the Google
Cloud Console, and their own server with an Anthropic model or key. The first-time
setup wizard will guide them through the process step by step.

You can also give them your `client_secret.json`—in that case, they’ll log in with their own account
and get their own token; they won’t have access to your data. However, the daily
YouTube quota will be shared between the two of you, and you’ll need to add it as a test
user to your Google Cloud project.

---

## What You Need to Know

**The YouTube quota is 10,000 units per day**, and each upload costs 1,600. No more than six
videos per day per Google Cloud project.

**While the app is in Testing status** on the Google consent screen, the refresh token
is valid for 7 days. To avoid having to log in again every week, switch the app to
Production—Google verification isn’t required for personal access.

**In Unsloth, “Switch model on demand” must be enabled**
(Settings → API). Without it, the server is only responsible for the uploaded model.

**The computer must be turned on** when the folder is being checked. If the model
is unavailable, the video won’t be uploaded with an incorrect name; it will wait until the next time.

**You can re-upload a video** by deleting its entry from `state.json`.

---

## If something went wrong

First: run `uploader.py check` and check the log in `logs\uploader.log`.

First, run `uploader.py check` and check the log file `logs\uploader.log`.

| Symptom | Cause |
|---|---|
| `Model NOT AVAILABLE` | Unsloth is not running, or `local_url` is incorrect |
| `Model NOT FOUND` | “Switch model on request” is not enabled, or the model has not been downloaded |
| `No model loaded` | The model has been unloaded, and `local_auto_load` is disabled |
| Timeout on `127.0.0.1` | A VPN is intercepting requests to itself. This is worked around in the code, but check the address |
| `SSL: UNEXPECTED_EOF_WHILE_READING` | Connection to Google was lost. Retry mechanisms are built in; this is not a bug |
| `No valid Google token` | Run `gauth.py` |
| `Token did not refresh due to a network error` | Just run the script again |
| `quotaExceeded` | YouTube’s daily quota has been reached |
| The video did not become a Shorts | The source is landscape, but the mode is `video` |
| Metadata from the filename | The model did not respond; check the log |
| `Another run is still in progress` | A `.lock` file remains from a crashed process; delete the file |
