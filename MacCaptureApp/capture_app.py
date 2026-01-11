#!/usr/bin/env python3
"""Minimal macOS ScreenCaptureKit sample written in Python.

This script starts an AppKit run loop, records the main display + system audio
for a short duration, and writes a .mov file to ~/Movies.

Requirements:
  * macOS 13+
  * PyObjC with ScreenCaptureKit and AVFoundation bindings
"""

import datetime
import pathlib

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
from dispatch import dispatch_queue_create, DISPATCH_QUEUE_SERIAL, dispatch_after, dispatch_time, DISPATCH_TIME_NOW, dispatch_get_main_queue


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
        self.capture_queue = dispatch_queue_create(
            b"com.example.maccapture.capture", DISPATCH_QUEUE_SERIAL
        )
        return self

    def startCapture(self):
        def handler(content, error):
            if error is not None:
                print(f"Failed to get shareable content: {error}")
                return

            displays = content.displays()
            if not displays:
                print("No displays available")
                return

            display = displays[0]
            filter = self._make_filter(display)
            if filter is None:
                print("Failed to build content filter for display")
                return
            configuration = SCK.SCStreamConfiguration.alloc().init()
            configuration.setWidth_(display.width())
            configuration.setHeight_(display.height())
            configuration.setCapturesAudio_(True)
            configuration.setSampleRate_(48_000)
            configuration.setChannelCount_(2)
            configuration.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 60))
            configuration.setQueueDepth_(5)

            output_url = self._make_output_url()
            if not self._setup_writer(output_url, configuration):
                return

            self.stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
                filter, configuration, self
            )

            screen_added, screen_error = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self, SCK.SCStreamOutputTypeScreen, self.capture_queue, None
            )
            if not screen_added:
                print(f"Failed to add screen output: {screen_error}")
                return

            audio_added, audio_error = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self, SCK.SCStreamOutputTypeAudio, self.capture_queue, None
            )
            if not audio_added:
                print(f"Failed to add audio output: {audio_error}")
                return

            def start_handler(error):
                if error is not None:
                    print(f"Failed to start capture: {error}")
                else:
                    print("Capture started successfully")

            self.stream.startCaptureWithCompletionHandler_(start_handler)

            print(f"Capturing to {output_url.path()}")

        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(handler)

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

        def stop_handler(error):
            if error is not None:
                print(f"Error stopping capture: {error}")

        self.stream.stopCaptureWithCompletionHandler_(stop_handler)
        if self.video_input is not None:
            self.video_input.markAsFinished()
        if self.audio_input is not None:
            self.audio_input.markAsFinished()
        if self.writer is not None:
            def finish_handler():
                print("Capture saved successfully")
                # Schedule app termination on main queue after a short delay
                dispatch_after(
                    dispatch_time(DISPATCH_TIME_NOW, 500_000_000),  # 0.5 seconds
                    dispatch_get_main_queue(),
                    lambda: NSApp.terminate_(None)
                )

            self.writer.finishWritingWithCompletionHandler_(finish_handler)
        else:
            print("Capture saved")
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
            AVEncoderBitRateKey: 128_000,
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
        self.manager.startCapture()

        # Schedule stopCapture to run after 10 seconds on the main queue
        def stop_callback():
            self.manager.stopCapture()

        dispatch_after(
            dispatch_time(DISPATCH_TIME_NOW, 10_000_000_000),  # 10 seconds in nanoseconds
            dispatch_get_main_queue(),
            stop_callback
        )


def main():
    app = NSApplication.sharedApplication()
    delegate = CaptureAppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()


if __name__ == "__main__":
    main()
