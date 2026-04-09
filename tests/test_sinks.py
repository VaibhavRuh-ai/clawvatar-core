"""Tests for animation sinks."""
import asyncio
from clawvatar_core.sinks.base import AnimationFrame, AnimationSink
from clawvatar_core.sinks.composite import CompositeSink


class MockSink(AnimationSink):
    def __init__(self):
        self.frames = []
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send_frame(self, frame):
        self.frames.append(frame)


def test_animation_frame_from_engine():
    data = {
        "weights": {"aa": 0.5},
        "head": {"yaw": 1.0, "pitch": -0.5, "roll": 0.1},
        "viseme": "A",
        "is_speaking": True,
    }
    f = AnimationFrame.from_engine_response(data)
    assert f.weights["aa"] == 0.5
    assert f.head_yaw == 1.0
    assert f.is_speaking is True


def test_animation_frame_to_ws():
    f = AnimationFrame(weights={"aa": 0.5}, viseme="A")
    msg = f.to_ws_message()
    assert msg["type"] == "weights"
    assert msg["weights"]["aa"] == 0.5


def test_composite_sink():
    s1, s2 = MockSink(), MockSink()
    comp = CompositeSink([s1, s2])
    frame = AnimationFrame(weights={"aa": 0.5})

    asyncio.run(comp.start())
    assert s1.started and s2.started

    asyncio.run(comp.send_frame(frame))
    assert len(s1.frames) == 1 and len(s2.frames) == 1

    asyncio.run(comp.stop())
    assert s1.stopped and s2.stopped
