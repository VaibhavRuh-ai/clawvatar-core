"""Tests for core config."""
import tempfile
from pathlib import Path
from clawvatar_core.config import CoreConfig


def test_defaults():
    c = CoreConfig()
    assert c.engine.mode == "embedded"
    assert c.server.port == 8766
    assert c.idle_fps == 10


def test_yaml_roundtrip():
    c = CoreConfig()
    c.server.port = 9999
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
        path = f.name
    c.to_yaml(path)
    loaded = CoreConfig.from_yaml(path)
    assert loaded.server.port == 9999
    Path(path).unlink()
