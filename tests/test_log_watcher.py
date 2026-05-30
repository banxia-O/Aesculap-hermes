"""Log watcher tests (PRD §2): tail -F, pattern match, rotation, truncation."""

from queue import Queue

from aesculap.detectors.log_watcher import LogWatcher, _fingerprint
from aesculap.events import DetectionEvent, EventSource


def make_watcher(q, path, from_end=True):
    return LogWatcher(q, [str(path)], ["Traceback", "CRITICAL"],
                      from_end=from_end)


def test_matches_new_error_lines(tmp_path):
    log = tmp_path / "h.log"
    log.write_text("startup ok\n")
    q: Queue = Queue()
    w = make_watcher(q, log)  # from_end -> skip existing content
    w.poll_once()
    assert q.qsize() == 0
    with log.open("a") as f:
        f.write("Traceback (most recent call last)\n")
        f.write("normal line\n")
        f.write("CRITICAL boom\n")
    w.poll_once()
    assert q.qsize() == 2  # two matching lines


def test_from_start_reads_existing(tmp_path):
    log = tmp_path / "h.log"
    log.write_text("Traceback here\nok\n")
    q: Queue = Queue()
    w = make_watcher(q, log, from_end=False)
    w.poll_once()
    assert q.qsize() == 1
    e = q.get()
    assert e.source is EventSource.LOG_WATCHER
    assert "Traceback" in e.evidence


def test_no_match_silent(tmp_path):
    log = tmp_path / "h.log"
    log.write_text("")
    q: Queue = Queue()
    w = make_watcher(q, log)
    with log.open("a") as f:
        f.write("everything is fine\n")
    w.poll_once()
    assert q.qsize() == 0


def test_handles_truncation(tmp_path):
    log = tmp_path / "h.log"
    log.write_text("")
    q: Queue = Queue()
    w = make_watcher(q, log)
    w.poll_once()  # open + seek-to-end while empty, establishing position 0
    with log.open("a") as f:
        f.write("CRITICAL one\n")
    w.poll_once()
    assert q.qsize() == 1
    # copytruncate: file shrinks to 0, then new content appended
    log.write_text("")           # truncate
    w.poll_once()                 # observe truncation, reset position
    with log.open("a") as f:
        f.write("CRITICAL two\n")
    w.poll_once()
    assert q.qsize() == 2


def test_handles_rotation(tmp_path):
    log = tmp_path / "h.log"
    log.write_text("")
    q: Queue = Queue()
    w = make_watcher(q, log)
    w.poll_once()  # establish position while empty
    with log.open("a") as f:
        f.write("CRITICAL a\n")
    w.poll_once()
    assert q.qsize() == 1
    # rotate: move old aside, create a brand-new file (new inode)
    log.rename(tmp_path / "h.log.1")
    log.write_text("CRITICAL b\n")
    w.poll_once()
    assert q.qsize() == 2


def test_fingerprint_normalizes_variable_parts():
    a = _fingerprint("/x.log", "error at 0xdeadbeef pid 1234")
    b = _fingerprint("/x.log", "error at 0xfeedface pid 5678")
    assert a == b  # digits/hex normalized -> same fingerprint


def test_fingerprint_distinguishes_messages():
    a = _fingerprint("/x.log", "disk full")
    b = _fingerprint("/x.log", "api down")
    assert a != b
