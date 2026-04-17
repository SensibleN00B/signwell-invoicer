# tests/test_tracking.py
from invoicer.tracking import Tracker


def test_downloaded_is_valid_status(tmp_path):
    tracker = Tracker(tmp_path / "sent.sqlite")
    tracker.insert_draft(
        document_id="doc-001", client_key="acme", client_email="a@b.com",
        file_path="/tmp/inv.pdf", file_sha256="aaa", test_mode=False,
    )
    tracker.update_status("doc-001", "completed")
    tracker.update_status("doc-001", "downloaded")  # must not raise
    row = tracker.get("doc-001")
    assert row["status"] == "downloaded"


def test_list_completed_not_downloaded_returns_only_completed(tmp_path):
    tracker = Tracker(tmp_path / "sent.sqlite")
    for doc_id, status in [
        ("doc-sent",      "sent"),
        ("doc-completed", "completed"),
        ("doc-downloaded", "completed"),
    ]:
        tracker.insert_draft(
            document_id=doc_id, client_key="acme", client_email="a@b.com",
            file_path="/tmp/inv.pdf", file_sha256=doc_id, test_mode=False,
        )
        tracker.update_status(doc_id, status)
    tracker.mark_downloaded("doc-downloaded")

    results = tracker.list_completed_not_downloaded()
    ids = [r["document_id"] for r in results]
    assert ids == ["doc-completed"]
    assert "doc-sent" not in ids
    assert "doc-downloaded" not in ids


def test_mark_downloaded_sets_status(tmp_path):
    tracker = Tracker(tmp_path / "sent.sqlite")
    tracker.insert_draft(
        document_id="doc-x", client_key="acme", client_email="a@b.com",
        file_path="/tmp/inv.pdf", file_sha256="bbb", test_mode=False,
    )
    tracker.update_status("doc-x", "completed")
    tracker.mark_downloaded("doc-x")
    assert tracker.get("doc-x")["status"] == "downloaded"
