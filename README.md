# diary

A minimal native GTK4 desktop app for recording a daily video or audio diary
entry and automatically uploading it to YouTube as a **private** video.

## Dependencies

Install system packages:

```bash
sudo dnf install python3-gobject gtk4 gstreamer1 gstreamer1-plugins-base \
gstreamer1-plugins-good gstreamer1-plugins-ugly gstreamer1-plugin-libav \
gstreamer1-plugins-bad-free python3-pip
```

Install Python packages:

```bash
pip install google-api-python-client google-auth-oauthlib
```

`ffmpeg` is also required (used to mux audio-only recordings with a black
video track). On Fedora:

```bash
sudo dnf install ffmpeg
```

## Getting `client_secrets.json` from Google Cloud Console

1. Go to console.cloud.google.com
2. Create new project → name it "diary"
3. APIs & Services → Enable APIs → search "YouTube Data API v3" → Enable
4. APIs & Services → OAuth consent screen → External → fill App name "diary", save
5. APIs & Services → Credentials → Create Credentials → OAuth Client ID → Desktop App → name "diary-desktop" → Download JSON → save as ~/diary/client_secrets.json
6. Run python ~/diary/diary.py — first launch opens browser for one-time auth

## Running

```bash
python3 ~/diary/diary.py
```

On first run:
- `~/diary/` and `~/diary/config.json` are created automatically if missing.
- If `~/diary/client_secrets.json` is missing, a dialog will tell you where
  to place it (see steps above).
- The first time you record and stop, your browser will open for a one-time
  Google OAuth consent. After that, the access token is cached at
  `~/diary/token.json` and refreshed automatically — you won't be asked
  again.

## Usage

- Switch between **video** and **audio** mode using the buttons in the
  bottom-left (disabled while recording).
- A dropdown next to the mode buttons lets you pick which camera or
  microphone to use.
- Click the round red button to start recording, click again to stop.
- A timer in the bottom-right shows elapsed recording time.
- Recordings are saved to `~/diary/YYYY-MM-DD_HH-MM.mp4`.
- Immediately after stopping, the recording uploads to YouTube in the
  background as a **private** video titled `diary — YYYY-MM-DD HH:MM`.
- Upload status ("uploading...", "uploaded ✓", or "upload failed ✗") is
  shown below the record button, and a desktop notification is sent on
  success or failure.

## Notes

- Video recordings use H.264 (`openh264enc`) + AAC (`avenc_aac`) muxed to
  MP4.
- Audio-only recordings are encoded as AAC, then combined with a black
  1280x720 video track via `ffmpeg` so the result is a valid video file
  for YouTube.
- If no webcam is found at startup, video mode is disabled. If no
  microphone is found, audio mode is disabled.
