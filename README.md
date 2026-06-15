# diary

A minimal native GTK4 desktop app for recording a daily video or audio diary
entry and automatically uploading it to YouTube as a **private** video.

## Getting started

### 1. Install dependencies

System packages:

```bash
sudo dnf install python3-gobject gtk4 gstreamer1 gstreamer1-plugins-base \
gstreamer1-plugins-good gstreamer1-plugins-ugly gstreamer1-plugin-libav \
gstreamer1-plugins-bad-free python3-pip ffmpeg
```

`ffmpeg` is required to mux audio-only recordings with a black video track.

Python packages:

```bash
pip install google-api-python-client google-auth-oauthlib
```

### 2. Get `client_secrets.json` from Google Cloud Console

1. Go to console.cloud.google.com
2. Create a new project → name it "diary"
3. **APIs & Services → Enable APIs** → search "YouTube Data API v3" → Enable
4. **APIs & Services → OAuth consent screen** → External → fill in App name
   "diary" → Save
5. **APIs & Services → Credentials → Create Credentials → OAuth Client ID**
   → Desktop App → name it "diary-desktop" → Download JSON → save as
   `~/diary/client_secrets.json`

### 3. Run it

```bash
python3 ~/diary/diary.py
```

On first run:

- `~/diary/` and `~/diary/config.json` are created automatically if missing.
- If `~/diary/client_secrets.json` is missing, a dialog tells you where to
  place it (see step 2 above).
- The first time you record and stop, your browser opens for a one-time
  Google OAuth consent. After that, the access token is cached at
  `~/diary/token.json` and refreshed automatically — you won't be asked
  again.

## Usage

### Recording controls

| Control | Location | Behavior |
| --- | --- | --- |
| **video** / **audio** labels | Bottom-left | Switch recording mode. Disabled while recording. |
| Microphone dropdown | Bottom-right | Selects the audio input device. Choice is saved to `config.json` and restored on next launch. |
| Record button (red circle) | Bottom-right | Click to start recording, click again to stop. |
| Timer | Center, while recording | Shows a blinking dot and elapsed time as `● 00:00`. |

### Output files

Recordings are saved to:

```
~/diary/YYYY-MM-DD_HH-MM.mp4
```

- **Video mode** — H.264 (`openh264enc`) + AAC (`avenc_aac`), muxed to MP4.
- **Audio mode** — audio is encoded as AAC, then combined with a black
  1280x720 video track via `ffmpeg` so the result is a valid video file
  for YouTube.

### After recording

When you stop a recording, a dialog opens with:

- An editable **title** and **description**.
- **Upload to YouTube** — uploads as a **private** video, or
- **Save locally only** — keeps the file in `~/diary/` without uploading.

### Upload status

A status indicator appears in the center of the bar, and a desktop
notification is sent on completion:

| Status | Meaning |
| --- | --- |
| `uploading...` | Upload in progress. |
| `uploaded ✓` | Upload succeeded. The local file is **deleted automatically**. |
| `upload failed ✗` | Upload failed. The local file is **kept** in `~/diary/`. |

### Startup checks

- If no webcam is found, **video** mode is disabled.
- If no microphone is found, **audio** mode is disabled.

## How to use your phone as a microphone

The built-in laptop mic and IEM/earbud mics are often too quiet for diary
recordings. This guide sets up your Android phone as a higher-quality input
source over WiFi, using **AudioRelay** and a PipeWire virtual device.

### Prerequisites

1. Install **AudioRelay** on your Android phone (Play Store).
2. Install AudioRelay on Fedora via Flatpak:

   ```bash
   flatpak install flathub net.audiorelay.AudioRelay
   ```

3. Make sure your phone and laptop are on the **same WiFi network**.

### Set up the virtual microphone

Create `~/use-phone-mic.sh`:

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

Make it executable:

```bash
chmod +x ~/use-phone-mic.sh
```

This script creates a PipeWire virtual sink + source pair named
**"Phone-Mic"**. AudioRelay streams your phone's audio into the sink, and
the paired source shows up as a normal microphone in any app — including
Diary's mic dropdown.

Re-running it first unloads any existing `module-null-sink` /
`module-remap-source` modules, so you don't end up with duplicates like
"Phone-Mic2", "Phone-Mic3", etc.

(Optional) add a shell alias for convenience:

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
4. Open **Diary** → select **"Phone-Mic"** from the mic dropdown → record
   as usual.

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

- **No audio / silence** — confirm both devices are on the same WiFi
  network, and that AudioRelay's laptop **Audio device** is set to
  "Phone-Mic" (not your default speakers/mic).
- **Audio stops mid-recording** — keep your phone screen on while
  recording, since AudioRelay's server needs to run in the foreground.

### Why AudioRelay?

A few alternatives were tried before settling on this setup:

- **Bluetooth HFP** (hands-free/mic profile) wasn't available on this
  system.
- **WO Mic's Linux client** had audio glitches from ALSA loopback
  buffering issues.
- **AudioRelay** uses the Opus codec with proper buffering, giving clean
  audio with no glitches.

Because the result is a virtual PipeWire device, any app — Diary, Discord,
or otherwise — can use the phone mic as a normal input, not just Diary.
