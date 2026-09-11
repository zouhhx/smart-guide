import queue
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from voice_gateway.audio import (
    AudioError,
    MicrophoneSource,
    assess_audio_levels,
    assess_recognized_speech_levels,
    audio_levels,
    frame_audio,
    read_pcm16_wav,
    resample_linear,
)


class AudioTests(unittest.TestCase):
    @staticmethod
    def _callback_source(maxsize=2):
        source = MicrophoneSource.__new__(MicrophoneSource)
        source._queue = queue.Queue(maxsize=maxsize)
        source.dropped_blocks = 0
        source.callback_status_events = 0
        source.last_callback_status = ""
        return source

    def test_resample_length(self):
        source = np.linspace(-1, 1, 48000, dtype=np.float32)
        output = resample_linear(source, 48000, 16000)
        self.assertEqual(len(output), 16000)

    def test_frames_are_fixed_size(self):
        frames = list(frame_audio(np.ones(1000, dtype=np.float32), 512))
        self.assertEqual([len(item) for item in frames], [512, 512])
        self.assertTrue(np.all(frames[-1][488:] == 0))

    def test_read_stereo_pcm16(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stereo.wav"
            stereo = np.column_stack(
                (
                    np.full(1600, 1000, dtype=np.int16),
                    np.full(1600, -1000, dtype=np.int16),
                )
            )
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(stereo.astype("<i2").tobytes())
            audio = read_pcm16_wav(path)
            self.assertEqual(len(audio.samples), 1600)
            self.assertLess(float(np.max(np.abs(audio.samples))), 1e-6)

    def test_levels(self):
        rms, peak, clipping = audio_levels(np.array([0.5, -0.5], dtype=np.float32))
        self.assertAlmostEqual(rms, -6.0206, places=3)
        self.assertAlmostEqual(peak, -6.0206, places=3)
        self.assertEqual(clipping, 0)

    def test_level_assessment(self):
        self.assertEqual(assess_audio_levels(-22, -4, 0)[0], "ok")
        self.assertEqual(assess_audio_levels(-80, -70, 0)[0], "no_signal")
        self.assertEqual(assess_audio_levels(-10, -0.1, 0.01)[0], "clipping")
        self.assertEqual(
            assess_recognized_speech_levels(-47, -35, 0)[0], "normal"
        )
        self.assertEqual(
            assess_recognized_speech_levels(-10, -0.1, 0.01)[0], "clipping"
        )

    def test_transient_callback_status_does_not_stop_capture(self):
        source = self._callback_source()
        source._callback(
            np.array([[0.25]], dtype=np.float32),
            1,
            None,
            "input overflow",
        )

        block = source._queue.get_nowait()
        self.assertIsInstance(block, np.ndarray)
        self.assertAlmostEqual(float(block[0]), 0.25)
        self.assertEqual(source.callback_status_events, 1)
        self.assertEqual(source.last_callback_status, "input overflow")

    def test_full_queue_keeps_newest_audio(self):
        source = self._callback_source(maxsize=2)
        for value in (1.0, 2.0, 3.0):
            source._callback(
                np.array([[value]], dtype=np.float32),
                1,
                None,
                False,
            )

        values = [
            float(source._queue.get_nowait()[0]),
            float(source._queue.get_nowait()[0]),
        ]
        self.assertEqual(values, [2.0, 3.0])
        self.assertEqual(source.dropped_blocks, 1)

    def test_discard_pending_returns_number_of_blocks(self):
        source = self._callback_source(maxsize=4)
        for value in (1.0, 2.0, 3.0):
            source._queue.put_nowait(np.array([value], dtype=np.float32))

        self.assertEqual(source.discard_pending(), 3)
        self.assertTrue(source._queue.empty())

    def test_cleanup_error_does_not_mask_active_audio_error(self):
        class BrokenStream:
            def abort(self):
                raise RuntimeError("abort failed")

            def close(self):
                raise RuntimeError("close failed")

        source = self._callback_source()
        source._stream = BrokenStream()

        source.__exit__(AudioError, AudioError("original"), None)

        self.assertIsNone(source._stream)


if __name__ == "__main__":
    unittest.main()
