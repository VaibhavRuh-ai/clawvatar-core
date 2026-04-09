"""Tests for avatar store."""
import tempfile
from pathlib import Path
from clawvatar_core.avatar.store import AvatarStore


def test_add_and_get(tmp_path):
    store = AvatarStore(base_dir=str(tmp_path))
    # Create a dummy VRM file
    dummy = tmp_path / "test.vrm"
    dummy.write_bytes(b"fake vrm data")

    aid = store.add(str(dummy), name="TestAvatar")
    assert aid.startswith("av_")

    info = store.get(aid)
    assert info["name"] == "TestAvatar"
    assert Path(info["path"]).exists()


def test_assign(tmp_path):
    store = AvatarStore(base_dir=str(tmp_path))
    dummy = tmp_path / "test.vrm"
    dummy.write_bytes(b"fake")
    aid = store.add(str(dummy))

    store.assign("agent-1", aid)
    result = store.get_for_agent("agent-1")
    assert result is not None
    assert result["id"] == aid


def test_unassigned_returns_none(tmp_path):
    store = AvatarStore(base_dir=str(tmp_path))
    assert store.get_for_agent("nonexistent") is None


def test_list(tmp_path):
    store = AvatarStore(base_dir=str(tmp_path))
    d1 = tmp_path / "a.vrm"
    d1.write_bytes(b"a")
    d2 = tmp_path / "b.vrm"
    d2.write_bytes(b"b")
    store.add(str(d1), name="A")
    store.add(str(d2), name="B")
    assert len(store.list()) == 2
