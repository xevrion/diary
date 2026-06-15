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

- Switch between **video** and **audio** mode using the labels in the
  bottom-left of the bar (disabled while recording).
- A microphone dropdown in the bottom-right lets you pick which audio
  input device to record from. Your choice is saved to `config.json` and
  restored automatically next time you open the app.
- Click the round red button to start recording, click again to stop.
- While recording, the center of the bar shows a blinking dot and the
  elapsed time (`● 00:00`).
- Recordings are saved to `~/diary/YYYY-MM-DD_HH-MM.mp4`.
- After stopping, a dialog lets you edit the title/description and choose
  to upload to YouTube (as a **private** video) or save locally only.
- Upload status ("uploading...", "uploaded ✓", or "upload failed ✗") is
  shown in the center of the bar, and a desktop notification is sent on
  success or failure. On a successful upload, the local recording file is
  deleted automatically; failed uploads are kept locally.

## Using your phone as a microphone

The built-in laptop mic and IEM/earbud mics are often too quiet for diary
recordings. You can use your Android phone's mic as a much better quality
input source over WiFi, via **AudioRelay** + a PipeWire virtual device.

### Prerequisites

1. Install **AudioRelay** on your Android phone (Play Store).
2. Install AudioRelay on Fedora via Flatpak:

   ```bash
   flatpak install flathub net.audiorelay.AudioRelay
   ```

3. Make sure both your phone and laptop are on the **same WiFi network**.

### The `~/use-phone-mic.sh` script

This script creates a clean PipeWire virtual sink + source pair named
**"Phone-Mic"**, which AudioRelay streams your phone's audio into, and
which then shows up as a normal microphone in any app (including Diary).

```bash
#!/bin/bash
# Clean old ones first
pactl unload-module module-null-sink 2>/dev/null
pactl unload-module module-remap-source 2>/dev/null
sleep 0.5
# Create one clean virtual mic
pactl load-module module-null-sink sink_name=audiorelay-virtual-mic-sink sink_properties=device.description=Phone-Mic
pactl load-module module-remap-source master=audiorelay-virtual-mic-sink.monitor source_name=audiorelay-virtual-mic-sink source_properties=device.description=Phone-Mic
echo "✅ Done! Now open AudioRelay → Player → select Phone-Mic → click your phone"
```

What it does:

- Unloads any existing `module-null-sink` / `module-remap-source` modules,
  so re-running it doesn't create duplicate "Phone-Mic2", "Phone-Mic3", etc.
- Creates a virtual sink called `audiorelay-virtual-mic-sink`, labeled
  **"Phone-Mic"**.
- Creates a matching virtual source (remapped from that sink's monitor) so
  it appears as a regular microphone input — this is what shows up in
  Diary's mic dropdown and in `pactl list sources`.

Save it to `~/use-phone-mic.sh` and make it executable:

```bash
chmod +x ~/use-phone-mic.sh
```

(Optional) add an alias to your shell config for convenience:

```bash
alias phonemic="~/use-phone-mic.sh"
```

### Daily workflow

1. Run the script:

   ```bash
   ~/use-phone-mic.sh
   # or: phonemic
   ```

2. On your **phone**: open AudioRelay → **Server** tab → **Microphone** →
   **Start**.
3. On your **laptop**: open AudioRelay → **Player** tab → set **Audio
   device** to **"Phone-Mic"** → click your phone in the server list to
   connect.
4. Open the **Diary** app → select **"Phone-Mic"** from the mic dropdown
   → record as usual.

### Why this approach

- Bluetooth HFP (hands-free/mic) profile wasn't available on this system.
- The WO Mic Linux client had audio glitches from ALSA loopback buffering
  issues.
- AudioRelay uses the Opus codec with proper buffering — clean audio, no
  glitches.
- Because it's a virtual PipeWire device, **any** app (Diary, Discord,
  etc.) can use the phone mic as a normal input.

### Troubleshooting

- **"Phone-Mic" doesn't show up in Diary's mic dropdown** — re-run
  `~/use-phone-mic.sh`, then restart Diary so it re-enumerates devices.
- **Duplicate devices ("Phone-Mic", "Phone-Mic2", ...)** — clean up
  manually, then re-run the script:

  ```bash
  pactl unload-module module-null-sink
  pactl unload-module module-remap-source
  ~/use-phone-mic.sh
  ```

- **No audio / silence** — make sure both devices are on the same WiFi
  network, and that AudioRelay's laptop **Audio device** is set to
  "Phone-Mic" (not your default speakers/mic).
- **Audio stops mid-recording** — your phone screen must stay on while
  recording, since AudioRelay's server needs to run in the foreground.

## Notes

- Video recordings use H.264 (`openh264enc`) + AAC (`avenc_aac`) muxed to
  MP4.
- Audio-only recordings are encoded as AAC, then combined with a black
  1280x720 video track via `ffmpeg` so the result is a valid video file
  for YouTube.
- If no webcam is found at startup, video mode is disabled. If no
  microphone is found, audio mode is disabled.
