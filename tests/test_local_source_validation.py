"""The dashboard can validate sources without relying on a Git checkout."""

import json
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

from jobradar import serve, store
from jobradar.output import interactive


def test_a_successful_validation_is_recorded_even_when_nothing_is_dead():
    root = Path(tempfile.mkdtemp())
    db = root / "job-radar.db"
    con = store.connect(db)
    con.close()

    class Completed:
        stdout = iter(["  5/12\n", "  12/12\n"])

        def wait(self):
            return 0

    def completed(cmd, **kwargs):
        Path(cmd[cmd.index("--report") + 1]).write_text(json.dumps({
            "checked": "2026-09-03T12:00:00", "total": 12,
            "dead": [], "mismatch": [],
            "rows": [{"verdict": "live"} for _ in range(12)],
        }), encoding="utf-8")
        return Completed()

    with mock.patch.object(serve.subprocess, "Popen", side_effect=completed):
        serve._run_source_validation(db, None, root)

    status = serve._validation_status(db)
    assert status == {
        "state": "complete", "checked": "2026-09-03", "done": 12,
        "total": 12, "elapsed": 0,
        "dead": 0, "unreachable": 0, "mismatch": 0, "error": "",
    }


def test_the_dashboard_uses_a_newer_local_validation_date():
    con = store.connect(":memory:")
    store.set_meta(con, "source_validation_checked", date.today().isoformat())
    with mock.patch("jobradar.sources.age_days", return_value=30):
        html = interactive.render(con)
    assert "sources validated today" in html
    assert 'id="validate"' not in html


def test_the_button_starts_validation_instead_of_running_git_pull():
    assert "id=\"validate\"" in interactive.render(store.connect(":memory:")) or \
           "const validateBtn" in interactive._JS
    assert "/api/source-validation" in interactive._JS
    assert "/api/pull" not in interactive._JS


def test_the_button_shows_live_count_percentage_and_elapsed_time():
    js = interactive._JS
    assert "d.done.toLocaleString()" in js
    assert "d.total.toLocaleString()" in js
    assert "Math.floor(d.done*100/d.total)" in js
    assert "mmss(d.elapsed||0)" in js
