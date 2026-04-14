"""Avatar video streamer — headless render + FFmpeg → HLS/RTMP/file.

Architecture:
  Xvfb (virtual display) → Chromium (renders 3D scene) → FFmpeg (capture)
    ↓
  HLS stream  |  RTMP push  |  MP4 file  |  v4l2loopback (virtual camera)

Audio: Chromium plays agent audio → PulseAudio virtual sink → FFmpeg captures
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AvatarStreamer:
    """Manages headless avatar rendering + video/audio capture pipeline."""

    def __init__(
        self,
        server_url: str = "https://localhost:8766",
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
    ):
        self.server_url = server_url
        self.width = width
        self.height = height
        self.fps = fps
        self._display_num = 99

        # Processes
        self._xvfb: Optional[subprocess.Popen] = None
        self._chromium: Optional[subprocess.Popen] = None
        self._ffmpeg: Optional[subprocess.Popen] = None
        self._pulse_module_id: Optional[str] = None

        # State
        self.status = "stopped"  # stopped | starting | streaming | error
        self.agent_id = ""
        self.room = ""
        self.started_at: Optional[float] = None
        self.hls_dir: Optional[str] = None
        self.outputs: dict = {}
        self._error_msg = ""

    @property
    def is_streaming(self) -> bool:
        return (
            self._ffmpeg is not None
            and self._ffmpeg.poll() is None
            and self.status == "streaming"
        )

    @property
    def hls_manifest(self) -> Optional[str]:
        if self.hls_dir:
            p = Path(self.hls_dir) / "stream.m3u8"
            if p.exists():
                return str(p)
        return None

    def get_status(self) -> dict:
        """Return current streamer status."""
        return {
            "status": self.status,
            "agent_id": self.agent_id,
            "room": self.room,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "outputs": self.outputs,
            "uptime": int(time.time() - self.started_at) if self.started_at else 0,
            "hls_available": self.hls_manifest is not None,
            "error": self._error_msg,
        }

    async def start(
        self,
        agent_id: str = "",
        room: str = "",
        outputs: Optional[dict] = None,
    ):
        """Start headless render + capture pipeline.

        Args:
            agent_id: OpenClaw agent to stream
            room: LiveKit room name (optional, auto-generated if empty)
            outputs: Dict with keys:
                hls (bool) — HLS stream segments
                rtmp (str) — RTMP push URL (e.g. rtmp://a.rtmp.youtube.com/live2/key)
                file (str) — record to MP4 file path
                v4l2 (str) — virtual camera device (e.g. /dev/video10)
        """
        if self.is_streaming:
            raise RuntimeError("Already streaming — stop first")

        if outputs is None:
            outputs = {"hls": True}

        self.agent_id = agent_id
        self.room = room
        self.outputs = outputs
        self.status = "starting"
        self._error_msg = ""

        try:
            await self._start_xvfb()
            await self._setup_pulse()
            await self._start_chromium(agent_id, room)
            await self._start_ffmpeg(outputs)
            self.status = "streaming"
            self.started_at = time.time()
            logger.info(
                f"Streaming started: agent={agent_id} {self.width}x{self.height}@{self.fps}fps"
            )
        except Exception as e:
            self._error_msg = str(e)
            self.status = "error"
            logger.error(f"Stream start failed: {e}")
            await self.stop()
            raise

    async def stop(self):
        """Stop all streaming processes."""
        logger.info("Stopping stream...")

        # Stop FFmpeg first (graceful SIGINT for clean HLS close)
        if self._ffmpeg and self._ffmpeg.poll() is None:
            try:
                self._ffmpeg.send_signal(signal.SIGINT)
                self._ffmpeg.wait(timeout=5)
            except Exception:
                self._ffmpeg.kill()
            self._ffmpeg = None

        # Stop Chromium
        if self._chromium and self._chromium.poll() is None:
            try:
                self._chromium.terminate()
                self._chromium.wait(timeout=3)
            except Exception:
                self._chromium.kill()
            self._chromium = None

        # Unload PulseAudio sink
        if self._pulse_module_id:
            try:
                subprocess.run(
                    ["pactl", "unload-module", self._pulse_module_id],
                    capture_output=True,
                    timeout=3,
                )
            except Exception:
                pass
            self._pulse_module_id = None

        # Stop Xvfb
        if self._xvfb and self._xvfb.poll() is None:
            try:
                self._xvfb.terminate()
                self._xvfb.wait(timeout=3)
            except Exception:
                self._xvfb.kill()
            self._xvfb = None

        self.status = "stopped"
        self.started_at = None
        logger.info("Stream stopped")

    # ---- Internal pipeline stages ----

    async def _start_xvfb(self):
        """Start virtual X11 display."""
        # Kill any existing Xvfb on our display
        subprocess.run(
            ["pkill", "-f", f"Xvfb :{self._display_num}"],
            capture_output=True,
        )
        await asyncio.sleep(0.5)

        self._xvfb = subprocess.Popen(
            [
                "Xvfb",
                f":{self._display_num}",
                "-screen",
                "0",
                f"{self.width}x{self.height}x24",
                "-ac",
                "+extension",
                "GLX",
                "+render",
                "-noreset",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(1.0)
        if self._xvfb.poll() is not None:
            raise RuntimeError("Xvfb failed to start")
        logger.info(f"Xvfb started on :{self._display_num}")

    async def _setup_pulse(self):
        """Create PulseAudio null sink for capturing Chromium audio."""
        env = {**os.environ, "DISPLAY": f":{self._display_num}"}
        try:
            result = subprocess.run(
                [
                    "pactl",
                    "load-module",
                    "module-null-sink",
                    "sink_name=clawvatar_stream",
                    "sink_properties=device.description=Clawvatar_Stream_Audio",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=5,
            )
            if result.returncode == 0:
                self._pulse_module_id = result.stdout.strip()
                # Set as default sink so Chromium outputs here
                subprocess.run(
                    ["pactl", "set-default-sink", "clawvatar_stream"],
                    capture_output=True,
                    env=env,
                    timeout=3,
                )
                logger.info(f"PulseAudio sink created: module {self._pulse_module_id}")
            else:
                logger.warning(
                    f"PulseAudio setup failed: {result.stderr} — streaming without audio"
                )
        except Exception as e:
            logger.warning(f"PulseAudio not available: {e} — streaming without audio")

    async def _start_chromium(self, agent_id: str, room: str):
        """Launch Chromium on virtual display with stream view."""
        env = {**os.environ, "DISPLAY": f":{self._display_num}"}

        # Build stream URL
        params = "mode=stream"
        if agent_id:
            params += f"&agent_id={agent_id}"
        if room:
            params += f"&room={room}"

        url = f"{self.server_url}/?{params}"

        self._chromium = subprocess.Popen(
            [
                "chromium",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--use-gl=swiftshader",
                f"--window-size={self.width},{self.height}",
                "--window-position=0,0",
                "--kiosk",
                "--autoplay-policy=no-user-gesture-required",
                "--ignore-certificate-errors",
                "--disable-features=TranslateUI",
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        # Wait for page to load and 3D scene to render
        await asyncio.sleep(5)
        if self._chromium.poll() is not None:
            raise RuntimeError("Chromium failed to start")
        logger.info(f"Chromium started: {url}")

    async def _start_ffmpeg(self, outputs: dict):
        """Start FFmpeg to capture display + audio → outputs."""
        env = {**os.environ, "DISPLAY": f":{self._display_num}"}

        cmd = ["ffmpeg", "-y"]

        # Video input: X11 grab
        cmd.extend(
            [
                "-f",
                "x11grab",
                "-framerate",
                str(self.fps),
                "-video_size",
                f"{self.width}x{self.height}",
                "-draw_mouse",
                "0",
                "-i",
                f":{self._display_num}",
            ]
        )

        # Audio input: PulseAudio monitor (if available)
        has_audio = self._pulse_module_id is not None
        if has_audio:
            cmd.extend(
                [
                    "-f",
                    "pulse",
                    "-i",
                    "clawvatar_stream.monitor",
                ]
            )

        # Video encoding (fast, low-latency)
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",
                "-g",
                str(self.fps * 2),  # keyframe every 2 seconds
                "-b:v",
                "2500k",
                "-maxrate",
                "3000k",
                "-bufsize",
                "6000k",
            ]
        )

        # Audio encoding
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "44100"])

        output_count = 0

        # HLS output
        if outputs.get("hls"):
            self.hls_dir = tempfile.mkdtemp(prefix="clawvatar_hls_")
            hls_path = os.path.join(self.hls_dir, "stream.m3u8")
            cmd.extend(
                [
                    "-f",
                    "hls",
                    "-hls_time",
                    "2",
                    "-hls_list_size",
                    "10",
                    "-hls_flags",
                    "delete_segments+append_list",
                    "-hls_segment_filename",
                    os.path.join(self.hls_dir, "seg_%05d.ts"),
                    hls_path,
                ]
            )
            output_count += 1

        # RTMP output
        rtmp_url = outputs.get("rtmp", "")
        if rtmp_url:
            cmd.extend(["-f", "flv", rtmp_url])
            output_count += 1

        # File recording
        file_path = outputs.get("file", "")
        if file_path:
            cmd.extend(["-movflags", "+faststart", file_path])
            output_count += 1

        # v4l2loopback virtual camera
        v4l2_dev = outputs.get("v4l2", "")
        if v4l2_dev and Path(v4l2_dev).exists():
            cmd.extend(
                ["-f", "v4l2", "-pix_fmt", "yuv420p", v4l2_dev]
            )
            output_count += 1

        if output_count == 0:
            raise RuntimeError("No output targets configured")

        logger.info(f"FFmpeg command: {' '.join(cmd[:20])}...")

        self._ffmpeg = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )
        await asyncio.sleep(3)
        if self._ffmpeg.poll() is not None:
            stderr = self._ffmpeg.stderr.read().decode() if self._ffmpeg.stderr else ""
            raise RuntimeError(f"FFmpeg failed: {stderr[-500:]}")
        logger.info("FFmpeg capture started")
