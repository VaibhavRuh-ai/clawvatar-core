"""Tests for audio collector."""
import numpy as np
from clawvatar_core.audio.collector import AudioCollector


def test_feed_pcm16():
    c = AudioCollector()
    pcm = (np.random.randn(1600) * 0.3 * 32767).astype(np.int16).tobytes()
    result = c.feed_pcm16(pcm, sample_rate=16000)
    assert len(result) == 1600
    assert result.dtype == np.float32


def test_resample():
    c = AudioCollector(target_sample_rate=16000)
    audio = np.random.randn(4800).astype(np.float32)  # 48kHz for 100ms
    result = c.feed_float32(audio, sample_rate=48000)
    assert len(result) == 1600  # 16kHz for 100ms


def test_accumulate():
    c = AudioCollector()
    c.feed_pcm16(np.zeros(800, dtype=np.int16).tobytes())
    c.feed_pcm16(np.zeros(800, dtype=np.int16).tobytes())
    acc = c.get_accumulated()
    assert len(acc) == 1600


def test_clear():
    c = AudioCollector()
    c.feed_pcm16(np.zeros(800, dtype=np.int16).tobytes())
    c.clear()
    assert len(c.get_accumulated()) == 0
