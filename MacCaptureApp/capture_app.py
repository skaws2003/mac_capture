#!/usr/bin/env python3
"""Minimal macOS ScreenCaptureKit sample written in Python.

This script starts an AppKit run loop, records the main display + system audio
for a short duration, and writes a .mov file to ~/Movies.

Requirements:
  * macOS 13+
  * PyObjC with ScreenCaptureKit and AVFoundation bindings
"""

import argparse
import datetime
import pathlib
import signal
import os
import sys
import threading
import tty
import termios

from Cocoa import NSApp, NSApplication, NSObject, NSTimer
from Foundation import NSURL
from AVFoundation import (
    AVAssetWriter,
    AVAssetWriterInput,
    AVAssetWriterInputPixelBufferAdaptor,
    AVEncoderBitRateKey,
    AVFormatIDKey,
    AVMediaTypeAudio,
    AVMediaTypeVideo,
    AVNumberOfChannelsKey,
    AVSampleRateKey,
    AVVideoCodecKey,
    AVVideoCodecTypeH264,
    AVVideoHeightKey,
    AVVideoWidthKey,
)
import ScreenCaptureKit as SCK
import CoreMedia
import Quartz as CoreVideo
import objc
from dispatch import dispatch_queue_create, DISPATCH_QUEUE_SERIAL, dispatch_after, dispatch_time, DISPATCH_TIME_NOW, dispatch_get_main_queue, dispatch_async


# Global delegate reference for signal handling
_global_delegate = None
_keyboard_monitor_stop = threading.Event()


def trigger_interrupt():
    """Trigger interrupt handling (called by signal handler or keyboard monitor)"""
    global _global_delegate
    # Print immediately without buffering
    sys.stdout.write("\n\n*** Keyboard interrupt detected ***\n")
    sys.stdout.flush()

    if _global_delegate and _global_delegate.manager:
        # Set stop_reason but NOT is_stopping yet - stopCapture needs to run the stream stop logic
        _global_delegate.manager.stop_reason = "interrupt"

        # Try to stop gracefully
        def do_stop():
            sys.stdout.write("Stopping capture...\n")
            sys.stdout.flush()
            _global_delegate.manager.stopCapture()

        dispatch_async(dispatch_get_main_queue(), do_stop)

        # Failsafe: if not stopped in 2 seconds, force exit
        def force_exit():
            sys.stdout.write("\nForce exiting...\n")
            sys.stdout.flush()
            # Use sys.exit() instead of os._exit() to allow proper cleanup and file flushing
            sys.exit(0)

        dispatch_after(
            dispatch_time(DISPATCH_TIME_NOW, 2_000_000_000),  # 2 seconds
            dispatch_get_main_queue(),
            force_exit
        )


def handle_sigint(signum, frame):
    """Handle SIGINT signal"""
    trigger_interrupt()


def keyboard_monitor():
    """Monitor keyboard input in a separate thread for ESC key"""
    original_settings = None
    try:
        # Save original terminal settings
        original_settings = termios.tcgetattr(sys.stdin)
        # Set terminal to cbreak mode (unbuffered, but preserve line discipline)
        tty.setcbreak(sys.stdin.fileno())
        # Disable echo
        new_settings = termios.tcgetattr(sys.stdin)
        new_settings[3] = new_settings[3] & ~termios.ECHO
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, new_settings)

        while not _keyboard_monitor_stop.is_set():
            ch = sys.stdin.read(1)
            # ESC key is ASCII 27 (0x1b)
            if ch == '\x1b':
                trigger_interrupt()
                break
    except KeyboardInterrupt:
        trigger_interrupt()
    except:
        pass
    finally:
        # Restore original terminal settings
        if original_settings is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, original_settings)
            except:
                pass


class CaptureManager(NSObject):
    def init(self):
        self = objc.super(CaptureManager, self).init()
        if self is None:
            return None
        self.stream = None
        self.writer = None
        self.video_input = None
        self.audio_input = None
        self.pixel_adaptor = None
        self.session_start_time = None
        self.stop_reason = None  # "interrupt" or "time"
        self.is_stopping = False  # Prevent multiple stop calls
        self.selected_display_index = 0  # Default to first display
        self.capture_queue = dispatch_queue_create(
            b"com.example.maccapture.capture", DISPATCH_QUEUE_SERIAL
        )
        return self

    def startCapture(self):
        def handler(content, error):
            if error is not None:
                print(f"Failed to get shareable content: {error}")
                NSApp.terminate_(None)
                return

            displays = content.displays()
            if not displays:
                print("No displays available")
                NSApp.terminate_(None)
                return

            if self.selected_display_index >= len(displays):
                print(f"Error: Display {self.selected_display_index} not found. Available displays: {len(displays)}")
                NSApp.terminate_(None)
                return

            display = displays[self.selected_display_index]
            print(f"Using display {self.selected_display_index}: {self._get_display_info(display)}")
            filter = self._make_filter(display)
            if filter is None:
                print("Failed to build content filter for display")
                NSApp.terminate_(None)
                return
            configuration = SCK.SCStreamConfiguration.alloc().init()
            configuration.setWidth_(1920)
            configuration.setHeight_(1080)
            configuration.setCapturesAudio_(True)
            configuration.setSampleRate_(48_000)
            configuration.setChannelCount_(2)
            configuration.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 30))
            configuration.setQueueDepth_(5)

            output_url = self._make_output_url()
            if not self._setup_writer(output_url, configuration):
                NSApp.terminate_(None)
                return

            self.stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
                filter, configuration, self
            )

            screen_added, screen_error = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self, SCK.SCStreamOutputTypeScreen, self.capture_queue, None
            )
            if not screen_added:
                print(f"Failed to add screen output: {screen_error}")
                NSApp.terminate_(None)
                return

            audio_added, audio_error = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self, SCK.SCStreamOutputTypeAudio, self.capture_queue, None
            )
            if not audio_added:
                print(f"Failed to add audio output: {audio_error}")
                NSApp.terminate_(None)
                return

            def start_handler(error):
                if error is not None:
                    print(f"Failed to start capture: {error}")
                    NSApp.terminate_(None)
                else:
                    print("Capture started successfully")

            self.stream.startCaptureWithCompletionHandler_(start_handler)

            print(f"Capturing to {output_url.path()}")

        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(handler)

    def _get_display_info(self, display):
        """Get display information (resolution)"""
        frame = display.frame()
        width = frame.size.width
        height = frame.size.height
        return f"{int(width)}x{int(height)}"

    def _make_filter(self, display):
        if hasattr(SCK.SCContentFilter, "filterWithDisplay_excludingWindows_exceptingApplications_"):
            return SCK.SCContentFilter.filterWithDisplay_excludingWindows_exceptingApplications_(
                display, [], []
            )
        if hasattr(SCK.SCContentFilter, "filterWithDisplay_excludingWindows_"):
            return SCK.SCContentFilter.filterWithDisplay_excludingWindows_(display, [])

        instance = SCK.SCContentFilter.alloc()
        if hasattr(instance, "initWithDisplay_excludingWindows_exceptingApplications_"):
            return instance.initWithDisplay_excludingWindows_exceptingApplications_(
                display, [], []
            )
        if hasattr(instance, "initWithDisplay_excludingWindows_"):
            return instance.initWithDisplay_excludingWindows_(display, [])
        return None

    def stopCapture(self):
        if self.stream is None:
            return

        # Mark inputs as finished immediately to stop accepting new data
        if self.video_input is not None:
            self.video_input.markAsFinished()
        if self.audio_input is not None:
            self.audio_input.markAsFinished()

        # Prevent multiple stream stop calls
        if self.is_stopping:
            # Already stopping, just schedule finish writing immediately
            dispatch_after(
                dispatch_time(DISPATCH_TIME_NOW, 100_000_000),  # 0.1 seconds
                dispatch_get_main_queue(),
                self._finish_writing
            )
            return

        self.is_stopping = True

        def stop_handler(error):
            # Ignore stream stop errors (stream may already be stopping)
            if error is not None and "already" not in str(error):
                print(f"Error stopping capture: {error}")

            # Delay briefly to allow pending data to be processed, then finish writing
            dispatch_after(
                dispatch_time(DISPATCH_TIME_NOW, 200_000_000),  # 0.2 seconds
                dispatch_get_main_queue(),
                self._finish_writing
            )

        self.stream.stopCaptureWithCompletionHandler_(stop_handler)

    def _finish_writing(self):
        """Finish writing the video file after stream is stopped"""
        if self.writer is None:
            return

        # Check writer status: 0=unknown, 1=writing, 2=finished, 3=failed, 4=cancelled
        status = self.writer.status()
        if status != 1:  # Only finish if currently writing
            reason_text = "time limit reached" if self.stop_reason == "time" else "user interrupted"
            print(f"Capture saved successfully (stopped by: {reason_text})")
            NSApp.terminate_(None)
            return

        try:
            def finish_handler():
                reason_text = "time limit reached" if self.stop_reason == "time" else "user interrupted"
                print(f"Capture saved successfully (stopped by: {reason_text})")
                # Schedule app termination on main queue after a short delay
                dispatch_after(
                    dispatch_time(DISPATCH_TIME_NOW, 500_000_000),  # 0.5 seconds
                    dispatch_get_main_queue(),
                    lambda: NSApp.terminate_(None)
                )

            self.writer.finishWritingWithCompletionHandler_(finish_handler)
        except Exception as e:
            print(f"Error finishing writing: {e}")
            NSApp.terminate_(None)

    def _make_output_url(self):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        file_name = f"Capture-{timestamp}.mov"
        project_dir = pathlib.Path(__file__).parent.parent
        captured_videos_dir = project_dir / "captured_videos"
        captured_videos_dir.mkdir(parents=True, exist_ok=True)
        return NSURL.fileURLWithPath_(str(captured_videos_dir / file_name))

    def _setup_writer(self, output_url, configuration):
        writer, error = AVAssetWriter.alloc().initWithURL_fileType_error_(
            output_url, "com.apple.quicktime-movie", None
        )
        if writer is None:
            print(f"Failed to create writer: {error}")
            return False

        video_settings = {
            AVVideoCodecKey: AVVideoCodecTypeH264,
            AVVideoWidthKey: configuration.width(),
            AVVideoHeightKey: configuration.height(),
        }
        video_input = AVAssetWriterInput.alloc().initWithMediaType_outputSettings_(
            AVMediaTypeVideo, video_settings
        )
        video_input.setExpectsMediaDataInRealTime_(True)

        adaptor = AVAssetWriterInputPixelBufferAdaptor.alloc().initWithAssetWriterInput_sourcePixelBufferAttributes_(
            video_input,
            {
                CoreVideo.kCVPixelBufferPixelFormatTypeKey: CoreVideo.kCVPixelFormatType_32BGRA,
                CoreVideo.kCVPixelBufferWidthKey: configuration.width(),
                CoreVideo.kCVPixelBufferHeightKey: configuration.height(),
            },
        )

        audio_settings = {
            AVFormatIDKey: 0x61616320,
            AVSampleRateKey: configuration.sampleRate(),
            AVNumberOfChannelsKey: configuration.channelCount(),
            AVEncoderBitRateKey: 256_000,
        }
        audio_input = AVAssetWriterInput.alloc().initWithMediaType_outputSettings_(
            AVMediaTypeAudio, audio_settings
        )
        audio_input.setExpectsMediaDataInRealTime_(True)

        if writer.canAddInput_(video_input):
            writer.addInput_(video_input)
        if writer.canAddInput_(audio_input):
            writer.addInput_(audio_input)

        self.writer = writer
        self.video_input = video_input
        self.audio_input = audio_input
        self.pixel_adaptor = adaptor
        return True

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        if not CoreMedia.CMSampleBufferDataIsReady(sample_buffer):
            return
        if self.writer is None:
            return

        if self.session_start_time is None:
            self.session_start_time = CoreMedia.CMSampleBufferGetPresentationTimeStamp(
                sample_buffer
            )
            self.writer.startWriting()
            self.writer.startSessionAtSourceTime_(self.session_start_time)

        if output_type == SCK.SCStreamOutputTypeScreen:
            if self.video_input is None or not self.video_input.isReadyForMoreMediaData():
                return
            pixel_buffer = CoreMedia.CMSampleBufferGetImageBuffer(sample_buffer)
            if pixel_buffer is None:
                return
            time = CoreMedia.CMSampleBufferGetPresentationTimeStamp(sample_buffer)
            self.pixel_adaptor.appendPixelBuffer_withPresentationTime_(pixel_buffer, time)
        elif output_type == SCK.SCStreamOutputTypeAudio:
            if self.audio_input is None or not self.audio_input.isReadyForMoreMediaData():
                return
            self.audio_input.appendSampleBuffer_(sample_buffer)

    def stream_didStopWithError_(self, stream, error):
        print(f"Stream stopped: {error}")


class CaptureAppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self.manager = CaptureManager.alloc().init()
        self.manager.selected_display_index = self.display_index
        self.manager.startCapture()

        # Schedule stopCapture to run after specified duration
        def stop_callback():
            # Only set stop_reason to "time" if not already stopped by interrupt
            if not self.manager.is_stopping:
                self.manager.stop_reason = "time"
            self.manager.stopCapture()

        duration_ns = int(self.duration_seconds * 1_000_000_000)
        dispatch_after(
            dispatch_time(DISPATCH_TIME_NOW, duration_ns),
            dispatch_get_main_queue(),
            stop_callback
        )


def list_displays():
    """List all available displays"""
    def handler(content, error):
        if error is not None:
            sys.stderr.write(f"Failed to get shareable content: {error}\n")
            sys.stderr.flush()
        else:
            displays = content.displays()
            if not displays:
                sys.stdout.write("No displays available\n")
                sys.stdout.flush()
            else:
                sys.stdout.write("Available displays:\n")
                sys.stdout.flush()
                for i, display in enumerate(displays):
                    frame = display.frame()
                    width = int(frame.size.width)
                    height = int(frame.size.height)
                    origin_x = int(frame.origin.x)
                    origin_y = int(frame.origin.y)
                    sys.stdout.write(f"  Display {i}: {width}x{height} at position ({origin_x}, {origin_y})\n")
                    sys.stdout.flush()

        # Exit the event loop after handler completes
        def exit_app():
            NSApp.terminate_(None)
        dispatch_after(
            dispatch_time(DISPATCH_TIME_NOW, 100_000_000),  # 0.1 seconds
            dispatch_get_main_queue(),
            exit_app
        )

    app = NSApplication.sharedApplication()
    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(handler)
    app.run()


def main():
    parser = argparse.ArgumentParser(
        description="Minimal macOS ScreenCaptureKit recorder - captures screen and audio to a .mov file"
    )
    parser.add_argument(
        "-t", "--time",
        type=int,
        default=3600,
        help="Maximum recording duration in seconds (default: 3600 = 1 hour)"
    )
    parser.add_argument(
        "-d", "--display",
        type=int,
        default=0,
        help="Display index to record (default: 0). Use --list-displays to see available displays"
    )
    parser.add_argument(
        "--list-displays",
        action="store_true",
        help="List all available displays and exit"
    )
    parser.add_argument(
        "-s", "--simulate-interrupt",
        type=int,
        metavar="SECONDS",
        help="Simulate keyboard interrupt after N seconds (for testing)"
    )
    args = parser.parse_args()

    # Handle --list-displays
    if args.list_displays:
        list_displays()
        return

    if args.time <= 0:
        print("Error: Recording duration must be greater than 0")
        return

    if args.display < 0:
        print("Error: Display index must be >= 0")
        return

    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, handle_sigint)

    # Start keyboard monitor thread to catch Ctrl+C
    monitor_thread = threading.Thread(target=keyboard_monitor, daemon=True)
    monitor_thread.start()

    app = NSApplication.sharedApplication()
    delegate = CaptureAppDelegate.alloc().init()
    delegate.duration_seconds = args.time
    delegate.display_index = args.display

    global _global_delegate
    _global_delegate = delegate

    app.setDelegate_(delegate)

    # If simulate-interrupt is set, automatically trigger interrupt after specified time
    if args.simulate_interrupt is not None:
        print(f"Recording will stop after {args.time} seconds, or simulate interrupt after {args.simulate_interrupt} seconds")

        def simulate_interrupt():
            sys.stdout.write(f"\n[SIMULATED INTERRUPT at {args.simulate_interrupt}s]\n")
            sys.stdout.flush()
            handle_sigint(signal.SIGINT, None)

        interrupt_ns = int(args.simulate_interrupt * 1_000_000_000)
        dispatch_after(
            dispatch_time(DISPATCH_TIME_NOW, interrupt_ns),
            dispatch_get_main_queue(),
            simulate_interrupt
        )
    else:
        print(f"Recording will stop after {args.time} seconds, or press ESC to stop early")

    app.run()


if __name__ == "__main__":
    main()
