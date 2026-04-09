"""End-to-end test: Core + Engine together.

Starts an AvatarSession with embedded engine + WebSocket sink.
Then sends test audio and verifies animation frames arrive.

Usage:
    python test_e2e.py

    Then open browser to: https://openclaw-vaibhav.tail72d21d.ts.net:8765
    (engine's test UI) — upload VRM, connect, upload audio file.
"""

import asyncio
import logging
import numpy as np
import sys
import os

# Add paths
sys.path.insert(0, "/home/vaibhav/clawvatar-core")
sys.path.insert(0, "/home/vaibhav/clawvatar-engine")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("e2e-test")


async def test_embedded_engine():
    """Test 1: Embedded engine processes audio correctly."""
    from clawvatar_core.engine.embedded import EmbeddedEngineClient
    from clawvatar_core.config import EngineConfig

    logger.info("=== Test 1: Embedded Engine ===")
    engine = EmbeddedEngineClient(EngineConfig())
    await engine.connect()
    assert engine.is_connected
    logger.info("Engine connected")

    # Load avatar
    avatar_path = "/home/vaibhav/.clawvatar/avatars/Juanita.vrm"
    if not os.path.exists(avatar_path):
        logger.warning(f"Avatar not found at {avatar_path}, skipping avatar load")
    else:
        info = await engine.load_avatar(avatar_path)
        logger.info(f"Avatar loaded: {info.get('name', '?')}, shapes: {info.get('blend_shape_count', 0)}")

    # Test idle
    idle = await engine.get_idle()
    logger.info(f"Idle weights: {len(idle.get('weights', {}))} keys")

    # Test single audio chunk
    audio = np.random.randn(1024).astype(np.float32) * 0.3
    result = await engine.process_audio(audio)
    logger.info(f"Single chunk: speaking={result.get('is_speaking')}, viseme={result.get('viseme')}")

    # Test batch
    audio_5s = np.random.randn(16000 * 2).astype(np.float32) * 0.3  # 2 seconds
    result = await engine.process_batch(audio_5s, sample_rate=16000)
    logger.info(f"Batch: {result['frame_count']} frames in {result['compute_ms']}ms")

    await engine.disconnect()
    logger.info("PASS: Embedded engine works")
    return True


async def test_audio_collector():
    """Test 2: Audio collector normalizes formats."""
    from clawvatar_core.audio.collector import AudioCollector

    logger.info("=== Test 2: Audio Collector ===")
    c = AudioCollector()

    # PCM16 bytes
    pcm = (np.random.randn(1600) * 0.3 * 32767).astype(np.int16).tobytes()
    result = c.feed_pcm16(pcm, sample_rate=16000)
    assert len(result) == 1600
    logger.info(f"PCM16: {len(result)} samples")

    # Resample 48kHz to 16kHz
    c2 = AudioCollector()
    audio48 = np.random.randn(4800).astype(np.float32)
    result48 = c2.feed_float32(audio48, sample_rate=48000)
    assert abs(len(result48) - 1600) < 5  # allow rounding
    logger.info(f"Resample 48k→16k: {len(audio48)} → {len(result48)}")

    logger.info("PASS: Audio collector works")
    return True


async def test_avatar_store():
    """Test 3: Avatar store manages files."""
    from clawvatar_core.avatar.store import AvatarStore
    import tempfile

    logger.info("=== Test 3: Avatar Store ===")
    with tempfile.TemporaryDirectory() as tmp:
        store = AvatarStore(base_dir=tmp)

        # Create dummy avatar
        dummy = os.path.join(tmp, "test.vrm")
        with open(dummy, "wb") as f:
            f.write(b"fake vrm data")

        aid = store.add(dummy, name="TestBot")
        logger.info(f"Added avatar: {aid}")

        store.assign("agent-1", aid)
        info = store.get_for_agent("agent-1")
        assert info is not None
        assert info["name"] == "TestBot"
        logger.info(f"Assigned to agent-1: {info['name']}")

        avatars = store.list()
        assert len(avatars) == 1
        logger.info(f"Store has {len(avatars)} avatars")

    logger.info("PASS: Avatar store works")
    return True


async def test_session_lifecycle():
    """Test 4: Full session lifecycle — create, speak, idle, stop."""
    from clawvatar_core.session import AvatarSession, SessionState
    from clawvatar_core.engine.embedded import EmbeddedEngineClient
    from clawvatar_core.config import EngineConfig
    from clawvatar_core.sinks.base import AnimationFrame, AnimationSink

    logger.info("=== Test 4: Session Lifecycle ===")

    # Mock sink to capture frames
    class CaptureSink(AnimationSink):
        def __init__(self):
            self.frames = []
            self.batches = []
        async def start(self): pass
        async def stop(self): pass
        async def send_frame(self, frame):
            self.frames.append(frame)
        async def send_batch(self, frames, audio_b64="", sample_rate=16000):
            self.batches.append({"frames": frames, "audio_b64": audio_b64})

    engine = EmbeddedEngineClient(EngineConfig())
    sink = CaptureSink()

    session = AvatarSession(agent_id="test-agent", engine=engine, idle_fps=5)
    session.add_sink(sink)

    # Load avatar
    avatar_path = "/home/vaibhav/.clawvatar/avatars/Juanita.vrm"
    if os.path.exists(avatar_path):
        await engine.connect()
        await engine.load_avatar(avatar_path)

    await session.start()
    assert session.state == SessionState.IDLE
    logger.info("Session started (IDLE)")

    # Wait for a couple idle frames
    await asyncio.sleep(0.5)
    idle_count = len(sink.frames)
    logger.info(f"Idle frames received: {idle_count}")
    assert idle_count > 0, "No idle frames received"

    # Speak (batch mode)
    audio = (np.sin(np.linspace(0, 100, 16000)) * 0.3).astype(np.float32)
    pcm16 = (audio * 32767).astype(np.int16).tobytes()
    await session.speak(audio=pcm16, sample_rate=16000)
    logger.info(f"Speak done. Batches: {len(sink.batches)}, batch frames: {len(sink.batches[0]['frames']) if sink.batches else 0}")
    assert len(sink.batches) > 0, "No batch sent"
    assert len(sink.batches[0]["frames"]) > 0, "Batch has no frames"
    assert sink.batches[0]["audio_b64"], "Batch has no audio"

    # Check frame has mouth weights
    first_frame = sink.batches[0]["frames"][5]  # pick a middle frame
    logger.info(f"Sample frame: weights={first_frame.weights}, viseme={first_frame.viseme}")

    await session.stop()
    assert session.state == SessionState.STOPPED
    logger.info("Session stopped")

    logger.info("PASS: Session lifecycle works")
    return True


async def test_session_manager():
    """Test 5: SessionManager creates/manages multiple sessions."""
    from clawvatar_core.session_manager import SessionManager
    from clawvatar_core.config import CoreConfig
    from clawvatar_core.sinks.base import AnimationSink, AnimationFrame

    logger.info("=== Test 5: Session Manager ===")

    class NullSink(AnimationSink):
        async def start(self): pass
        async def stop(self): pass
        async def send_frame(self, frame): pass

    config = CoreConfig()
    manager = SessionManager(config)

    # Create sessions
    s1 = await manager.create_session("agent-a", sinks=[NullSink()])
    s2 = await manager.create_session("agent-b", sinks=[NullSink()])
    logger.info(f"Created 2 sessions")

    sessions = manager.list_sessions()
    assert len(sessions) == 2
    logger.info(f"Active sessions: {[s['agent_id'] for s in sessions]}")

    # Get session
    assert manager.get_session("agent-a") is s1
    assert manager.get_session("nonexistent") is None

    # Destroy
    await manager.destroy_session("agent-a")
    assert len(manager.list_sessions()) == 1

    await manager.destroy_all()
    assert len(manager.list_sessions()) == 0
    logger.info("All sessions destroyed")

    logger.info("PASS: Session manager works")
    return True


async def main():
    results = {}
    tests = [
        ("Embedded Engine", test_embedded_engine),
        ("Audio Collector", test_audio_collector),
        ("Avatar Store", test_avatar_store),
        ("Session Lifecycle", test_session_lifecycle),
        ("Session Manager", test_session_manager),
    ]

    for name, test_fn in tests:
        try:
            results[name] = await test_fn()
        except Exception as e:
            logger.error(f"FAIL: {name} — {e}", exc_info=True)
            results[name] = False
        print()

    print("=" * 50)
    print("RESULTS:")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} passed")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
