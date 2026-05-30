"""Lockfile tests (PRD §12): single-holder concurrency lock."""

import pytest

from aesculap.lockfile import FileLock, LockHeld


def test_acquire_release(tmp_path):
    lock = FileLock(tmp_path / "x.lock")
    lock.acquire()
    assert lock.held
    lock.release()
    assert not lock.held


def test_second_holder_blocked(tmp_path):
    path = tmp_path / "x.lock"
    a = FileLock(path)
    a.acquire()
    try:
        b = FileLock(path)
        with pytest.raises(LockHeld):
            b.acquire()
    finally:
        a.release()


def test_reacquire_after_release(tmp_path):
    path = tmp_path / "x.lock"
    a = FileLock(path)
    a.acquire()
    a.release()
    b = FileLock(path)
    b.acquire()  # should succeed now
    assert b.held
    b.release()


def test_context_manager(tmp_path):
    path = tmp_path / "x.lock"
    with FileLock(path) as lock:
        assert lock.held
    assert not lock.held
