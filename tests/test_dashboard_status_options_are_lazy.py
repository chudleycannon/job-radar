"""The status dropdown must not ship its options, and must not drift.

Ten options on every row is 43,600 `<option>` nodes on a 4,362-role board and
2.3MB of a 9MB page, all of it built before the browser will paint. The board
was unresponsive for seconds on load because of a control nobody had touched
yet, so the options are added on first interaction instead.

The list therefore lives in the JavaScript as a literal, away from
`store.STATUSES`, which is a drift the CLI and the browser have disagreed
about here before: the dashboard once offered two of the ten statuses, so a
browser could not record an interview date. The second test is the guard.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import store
from jobradar.output import interactive


class StatusOptionsAreLazy(unittest.TestCase):
    def test_the_javascript_status_list_matches_the_store(self):
        m = re.search(r"const STATUSES=\[(.*?)\];", interactive._JS)
        self.assertIsNotNone(m, "STATUSES literal missing from the dashboard JS")
        in_js = re.findall(r"'([^']+)'", m.group(1))
        self.assertEqual(in_js, list(store.STATUSES))

    def test_the_row_template_ships_one_option_not_ten(self):
        src = Path(interactive.__file__).read_text(encoding="utf-8")
        block = src[src.index('class="setstatus"'):]
        block = block[:block.index("</select>")]
        # The placeholder is the only <option> written per row. A loop over
        # the statuses here is the regression this test exists for.
        self.assertNotIn("for s in store.STATUSES", block)
        self.assertEqual(block.count("<option"), 1)


if __name__ == "__main__":
    unittest.main()
