"""De-dup + cooldown tests (PRD §11, §12)."""

from aesculap.notify.dedup import NotificationDeduper


def test_first_notify_allowed(tmp_path):
    d = NotificationDeduper(str(tmp_path / "open.json"), cooldown_seconds=3600)
    assert d.should_notify("fp1", now=1000.0)


def test_within_cooldown_suppressed(tmp_path):
    d = NotificationDeduper(str(tmp_path / "open.json"), cooldown_seconds=3600)
    d.mark_notified("fp1", now=1000.0)
    assert not d.should_notify("fp1", now=1500.0)  # 500s < 3600s


def test_after_cooldown_allowed(tmp_path):
    d = NotificationDeduper(str(tmp_path / "open.json"), cooldown_seconds=3600)
    d.mark_notified("fp1", now=1000.0)
    assert d.should_notify("fp1", now=1000.0 + 3601)


def test_resolve_clears(tmp_path):
    d = NotificationDeduper(str(tmp_path / "open.json"), cooldown_seconds=3600)
    d.mark_notified("fp1", now=1000.0)
    d.resolve("fp1")
    assert d.should_notify("fp1", now=1001.0)
    assert "fp1" not in d.open_issues()


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "open.json")
    d1 = NotificationDeduper(path, cooldown_seconds=3600)
    d1.mark_notified("fp1", now=1000.0)
    d2 = NotificationDeduper(path, cooldown_seconds=3600)
    assert not d2.should_notify("fp1", now=1100.0)


def test_count_increments(tmp_path):
    d = NotificationDeduper(str(tmp_path / "open.json"), cooldown_seconds=0)
    d.mark_notified("fp1", now=1.0)
    d.mark_notified("fp1", now=2.0)
    assert d._open["fp1"]["count"] == 2


def test_corrupt_state_file_tolerated(tmp_path):
    path = tmp_path / "open.json"
    path.write_text("{not valid json")
    d = NotificationDeduper(str(path), cooldown_seconds=3600)
    assert d.should_notify("fp1")  # starts empty, no crash
