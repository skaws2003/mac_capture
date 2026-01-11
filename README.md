# mac_capture

Minimal macOS ScreenCaptureKit recorder written in Python (PyObjC).

## What this does

The sample starts an AppKit run loop, captures the main display plus system audio
using ScreenCaptureKit, and writes a `.mov` file into your `~/Movies` folder.
By default it records for ~10 seconds and exits.

## Requirements

- macOS 13+ (ScreenCaptureKit is only available on newer macOS releases)
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) for dependency management

## Dependency setup (uv)

From the repo root:

```bash
uv sync
```

This will install the required PyObjC frameworks defined in `pyproject.toml`.

## Run

From the repo root:

```bash
uv run python MacCaptureApp/capture_app.py
```

You should see console output like:

```
Capturing to /Users/<you>/Movies/Capture-YYYY-MM-DD-HH-MM-SS.mov
Capture saved
```

Open the resulting `.mov` file in QuickTime Player to verify video + audio.

## Permissions

The first run will prompt for:

- Screen recording permission
- Microphone/system audio permission (if required by your macOS version)

Grant these in System Settings → Privacy & Security if prompted.

## Configuration

Edit `MacCaptureApp/capture_app.py` to adjust:

- **Duration**: change the `NSTimer` interval in `CaptureAppDelegate.applicationDidFinishLaunching_`.
- **Resolution**: adjust `configuration.setWidth_` / `setHeight_`.
- **Frame rate**: change `setMinimumFrameInterval_`.
- **Audio settings**: change `sampleRate` / `channelCount` / AAC bitrate.
- **Output location**: update `_make_output_url()`.

## Troubleshooting

- **No displays available**: Ensure a display is connected and you are on macOS 13+.
- **Permission errors**: Re-check screen recording permissions in System Settings.
- **No audio**: Make sure system audio capture is supported and permitted.

## License

This repo is provided as a minimal example. Add a license if you plan to reuse it.
