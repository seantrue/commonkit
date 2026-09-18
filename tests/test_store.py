"""The store, addressed through an audio scheme rather than an image one.

Everything here is the property the whole design rests on: **a missing segment
is never an error, only a slower path**. Absent root, full filesystem, reaped
segment, torn file, malformed key -- all return None or False.
"""
import os

import numpy as np
import pytest

from commonkit import KeyScheme, SharedArrayStore, register

D1 = "ab" * 16
D2 = "cd" * 16

WAV = register(KeyScheme(domain="ckaudio", version="v1",
                         forms=("wav16k", "f32chw"), variants=r"^sr\d+$",
                         dtypes={"wav16k": "int16"}))


def key(digest=D1, form="wav16k"):
    return WAV.key(digest, "sr16000", form)


def _wave(n=200_000):
    return (np.arange(n) % 3001).astype(np.int16)


@pytest.fixture
def store(tmp_path):
    return SharedArrayStore(str(tmp_path / "seg"), mode="readwrite",
                            max_bytes=64 * 2**20, min_free_bytes=1,
                            min_array_bytes=1024, max_array_bytes=8 * 2**20,
                            ttl_s=900, reap_interval_s=0)


def test_publish_then_attach_returns_the_same_samples(store):
    a = _wave()
    assert store.publish(key(), a) is True
    b = store.attach(key())
    assert np.array_equal(np.asarray(b), a)
    assert b.dtype == a.dtype and b.shape == a.shape


def test_attached_arrays_are_read_only_and_stay_that_way(store):
    """mmap_mode='r' makes read-only ENFORCED, not advisory: a caller cannot
    set the flag back, so a shared base cannot be mutated under its readers."""
    store.publish(key(), _wave())
    b = store.attach(key())
    assert b.flags.writeable is False
    with pytest.raises(ValueError):
        b[0] = 9
    with pytest.raises(ValueError):
        b.flags.writeable = True


def test_a_miss_is_none_not_an_exception(store):
    assert store.attach(key(D2)) is None


def test_the_path_stays_under_the_root(store):
    p = store.path_for(key())
    assert os.path.commonpath([p, store.root]) == store.root


def test_an_unregistered_domain_is_a_miss_not_a_crash(store):
    """A key the store cannot address must be counted and shrugged off."""
    assert store.attach(f"nobody:v1:pil:{D1}:o1") is None
    assert store.drain_stats()["shm_bad_key"] == 1


# ------------------------------------------------- dtype policy is the scheme's

def test_a_form_with_a_declared_dtype_refuses_anything_else(store):
    """The rule that used to be a hardcoded ``form != "pil"`` branch inside the
    store. A bad segment is published once and poisons every later reader."""
    assert store.publish(key(), np.zeros(200_000, dtype=bool)) is False
    assert store.drain_stats()["shm_declined_dtype"] == 1


def test_a_form_with_no_declared_dtype_is_unconstrained(store):
    """``f32chw`` holds model tensors; a blanket rule would break it."""
    a = np.zeros((3, 256, 256), dtype=np.float32)
    assert store.publish(key(form="f32chw"), a) is True
    assert store.attach(key(form="f32chw")).dtype == np.float32


def test_fortran_order_and_dtype_survive(store):
    a = np.asfortranarray(np.zeros((50, 600), dtype=np.float32))
    store.publish(key(form="f32chw"), a)
    b = store.attach(key(form="f32chw"))
    assert b.dtype == np.float32
    assert b.flags.f_contiguous == a.flags.f_contiguous


# ------------------------------------------------------------------ admission

def test_declines_below_the_minimum_size(store):
    assert store.publish(key(), np.zeros(16, dtype=np.int16)) is False
    assert store.drain_stats()["shm_declined_small"] == 1


def test_declines_above_the_per_item_ceiling(store):
    """Not optional: an hour of audio as a waveform would swallow the tree."""
    assert store.publish(key(), np.zeros(9 * 2**20, dtype=np.int16)) is False
    assert store.drain_stats()["shm_declined_size"] == 1


def test_declines_when_the_filesystem_is_near_full_and_never_blocks(
        store, monkeypatch):
    class _FakeVfs:
        f_bavail, f_frsize = 1, 4096
    monkeypatch.setattr(os, "statvfs", lambda p: _FakeVfs())
    store._statvfs_at = 0.0
    store.min_free_bytes = 8 * 2**30
    assert store.publish(key(), _wave()) is False
    assert store.drain_stats()["shm_declined_space"] == 1


def test_read_mode_attaches_but_never_publishes(tmp_path):
    rw = SharedArrayStore(str(tmp_path / "s"), mode="readwrite",
                          min_free_bytes=1, min_array_bytes=1024)
    rw.publish(key(), _wave())
    ro = SharedArrayStore(str(tmp_path / "s"), mode="read", min_free_bytes=1,
                          min_array_bytes=1024)
    assert ro.attach(key()) is not None
    assert ro.publish(key(D2), _wave()) is False


def test_mode_off_is_entirely_inert(tmp_path):
    s = SharedArrayStore(str(tmp_path / "s"), mode="off")
    assert s.disabled
    assert s.attach(key()) is None and s.publish(key(), _wave()) is False


def test_an_unusable_root_degrades_instead_of_raising():
    s = SharedArrayStore("/proc/nonexistent/segments", mode="readwrite")
    assert s.disabled is True
    assert s.attach(key()) is None and s.publish(key(), _wave()) is False


# ------------------------------------------------------------------ integrity

def test_unlink_while_mapped_keeps_a_live_reader_working(store):
    """The reaper's whole licence: it unlinks on policy without knowing who is
    attached, because a live mapping survives the name going away."""
    a = _wave()
    store.publish(key(), a)
    b = store.attach(key())
    os.unlink(store.path_for(key()))
    assert np.array_equal(np.asarray(b), a)
    assert store.attach(key()) is None


def test_a_truncated_segment_is_a_miss_not_a_crash(store):
    store.publish(key(), _wave())
    with open(store.path_for(key()), "r+b") as fh:
        fh.truncate(64)
    assert store.attach(key()) is None
    assert store.drain_stats()["shm_torn"] == 1


def test_concurrent_construction_never_disables_a_store(tmp_path):
    """K workers starting together on one root must ALL come up enabled."""
    import threading

    root, results, errors = tmp_path / "race", [], []

    def build():
        try:
            results.append(SharedArrayStore(
                str(root), mode="readwrite", min_free_bytes=1,
                min_array_bytes=1).disabled)
        except Exception as exc:                              # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=build) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [] and results.count(True) == 0
    assert [p.name for p in root.iterdir() if p.name.startswith(".writable")] == []


# ------------------------------------------------------------------- reaping

def test_reaper_refuses_a_suspicious_root(tmp_path):
    s = SharedArrayStore(str(tmp_path / "s"), mode="readwrite", min_free_bytes=1)
    s.root = "/"
    assert s.reap()["reaped"] == 0
    assert s.drain_stats()["shm_reap_refused"] == 1


def test_reaper_only_touches_its_own_segments(store):
    store.publish(key(), _wave())
    bystander = os.path.join(store.root, "IMPORTANT.txt")
    with open(bystander, "w") as fh:
        fh.write("not a segment")
    store.ttl_s = -1
    store.reap(budget_s=5)
    assert os.path.exists(bystander)
    assert store.attach(key()) is None


def test_reaper_leaves_fresh_segments_alone(store):
    store.publish(key(), _wave())
    assert store.reap(budget_s=5)["reaped"] == 0
    assert store.attach(key()) is not None
