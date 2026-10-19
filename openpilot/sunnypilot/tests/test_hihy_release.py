"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(release-stamp)
import tempfile
import unittest
from pathlib import Path
import re

from openpilot.sunnypilot.hihy_release import get_hihy_release, STAMP_PATH


class TestHihyRelease(unittest.TestCase):
  def test_committed_stamp_is_well_formed(self):
    tag = get_hihy_release()
    assert re.fullmatch(r"r\d{1,4}", tag), f"HIHY_RELEASE must be like r23, got {tag!r}"

  def test_stamp_file_is_a_single_trimmed_line(self):
    raw = open(STAMP_PATH).read()
    assert raw == raw.strip() + "\n", "exactly one line, newline-terminated"

  def test_missing_file_is_empty_not_an_exception(self):
    with tempfile.TemporaryDirectory() as _td:
      tmp_path = Path(_td)
      assert get_hihy_release(str(tmp_path / "nope")) == ""

  def test_garbage_is_empty_not_shown(self):
    with tempfile.TemporaryDirectory() as _td:
      tmp_path = Path(_td)
      p = tmp_path / "HIHY_RELEASE"
      for bad in ("", "22", "release 22", "r", "r22 r23", "<script>"):
        p.write_text(bad)
        assert get_hihy_release(str(p)) == "", bad

  def test_valid_tag_round_trips_with_whitespace(self):
    with tempfile.TemporaryDirectory() as _td:
      tmp_path = Path(_td)
      p = tmp_path / "HIHY_RELEASE"
      p.write_text("  r23\n\n")
      assert get_hihy_release(str(p)) == "r23"
