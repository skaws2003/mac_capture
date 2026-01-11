# Running guide

This guide shows how to run the Python ScreenCaptureKit sample and what to
expect when it starts capturing.

## Dependency setup (uv)

From the repo root:

```bash
uv sync
```

## Non-Python dependencies

None. This sample only requires the PyObjC packages installed via `uv` on
macOS 13+ (including CoreMedia and Quartz for CoreVideo symbols).

## Quick start

From the repo root:

```bash
uv run python MacCaptureApp/capture_app.py
```

Expected output:

```
Capturing to /Users/<you>/Movies/Capture-YYYY-MM-DD-HH-MM-SS.mov
Capture saved
```

Open the `.mov` file in QuickTime Player to verify video + audio.

## Example: record for longer

The script currently stops after ~10 seconds. To record longer, edit
`CaptureAppDelegate.applicationDidFinishLaunching_` in
`MacCaptureApp/capture_app.py` and increase the timer interval.

Example change (10 seconds → 30 seconds):

```python
NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
    30.0, self, "stopCapture:", None, False
)
```

## Example: change output location

Update `_make_output_url()` in `MacCaptureApp/capture_app.py`.

Example: save to Desktop instead of Movies:

```python
movies_dir = pathlib.Path.home() / "Desktop"
```

## Example: change resolution or frame rate

Update the configuration in `CaptureManager.startCapture` in
`MacCaptureApp/capture_app.py`:

```python
configuration.setWidth_(display.width())
configuration.setHeight_(display.height())
configuration.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 30))
```

## Permissions

On first run, macOS should prompt for Screen Recording access. If you deny it,
open System Settings → Privacy & Security and enable Screen Recording for the
Python interpreter you use to run the script.
