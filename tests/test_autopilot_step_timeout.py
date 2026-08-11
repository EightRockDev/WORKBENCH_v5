"""A hung autopilot step is killed and reported, never wedging the cycle
(2026-08-11: old-code arcgissales hang orphaned the whole autopilot)."""

import scripts.autopilot_run as ar


def test_hung_step_killed_with_exit_124(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "REPORTS", tmp_path)
    monkeypatch.setattr(ar, "STEP_TIMEOUT", 1)
    out, code = ar.run_step("hang", ["-c", "import time; time.sleep(30)"],
                            "hang.txt")
    assert code == 124
    assert "killed after 1s" in out.read_text()


def test_normal_step_unaffected(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "REPORTS", tmp_path)
    out, code = ar.run_step("ok", ["-c", "print('fine')"], "ok.txt")
    assert code == 0
    assert "fine" in out.read_text()
