"""Audio collector — normalizes any audio format to PCM16 float32 at 16kHz."""

from __future__ import annotations

import io
import logging
import wave

import numpy as np

logger = logging.getLogger(__name__)


class AudioCollector:
    """Accumulates and normalizes audio chunks for the engine.

    Accepts PCM16 bytes, float32 arrays, or WAV data.
    Outputs 16kHz float32 numpy arrays.
    """

    def __init__(self, target_sample_rate: int = 16000):
        self.target_rate = target_sample_rate
        self._buffer: list[np.ndarray] = []

    def feed_pcm16(self, data: bytes, sample_rate: int = 16000) -> np.ndarray:
        """Feed raw PCM int16 bytes. Returns float32 array at target rate."""
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != self.target_rate:
            audio = self._resample(audio, sample_rate, self.target_rate)
        self._buffer.append(audio)
        return audio

    def feed_float32(self, data: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Feed float32 numpy array. Returns resampled array at target rate."""
        if sample_rate != self.target_rate:
            data = self._resample(data, sample_rate, self.target_rate)
        self._buffer.append(data)
        return data

    def feed_wav(self, wav_bytes: bytes) -> np.ndarray:
        """Feed WAV file bytes. Returns float32 array at target rate."""
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
            sw = wf.getsampwidth()

        if sw == 2:
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sw == 4:
            audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            audio = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0

        if sr != self.target_rate:
            audio = self._resample(audio, sr, self.target_rate)

        self._buffer.append(audio)
        return audio

    def feed_bytes(self, data: bytes, format: str, sample_rate: int = 16000) -> np.ndarray:
        """Feed audio in any supported format."""
        if format in ("pcm16", "pcm", "raw"):
            return self.feed_pcm16(data, sample_rate)
        elif format == "wav":
            return self.feed_wav(data)
        elif format in ("mp3", "ogg", "webm", "m4a"):
            return self._decode_compressed(data, format)
        else:
            raise ValueError(f"Unsupported audio format: {format}")

    def get_accumulated(self) -> np.ndarray:
        """Get all accumulated audio as one array."""
        if not self._buffer:
            return np.array([], dtype=np.float32)
        return np.concatenate(self._buffer)

    def get_accumulated_pcm16(self) -> bytes:
        """Get all accumulated audio as PCM16 bytes."""
        audio = self.get_accumulated()
        return (audio * 32767).astype(np.int16).tobytes()

    def clear(self) -> None:
        """Clear the buffer."""
        self._buffer.clear()

    @staticmethod
    def _resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
        """Simple linear interpolation resampling."""
        if from_rate == to_rate:
            return audio
        ratio = from_rate / to_rate
        new_len = int(len(audio) / ratio)
        indices = np.arange(new_len) * ratio
        lo = np.floor(indices).astype(int)
        hi = np.minimum(lo + 1, len(audio) - 1)
        frac = indices - lo
        return (audio[lo] * (1 - frac) + audio[hi] * frac).astype(np.float32)

    def _decode_compressed(self, data: bytes, format: str) -> np.ndarray:
        """Decode compressed audio (MP3, OGG, etc.) using pydub."""
        try:
            from pydub import AudioSegment
        except ImportError:
            raise ImportError(
                f"pydub required for {format} decoding. "
                "Install: pip install clawvatar-core[audio-codecs]"
            )

        seg = AudioSegment.from_file(io.BytesIO(data), format=format)
        seg = seg.set_channels(1).set_frame_rate(self.target_rate).set_sample_width(2)
        audio = np.frombuffer(seg.raw_data, dtype=np.int16).astype(np.float32) / 32768.0
        self._buffer.append(audio)
        return audio
