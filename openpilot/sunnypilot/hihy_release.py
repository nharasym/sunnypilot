"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(release-stamp): the fork's rebase-generation number ("r23"), shown after the
# version on the home screen. It lives in a committed file rather than being derived from
# git because the deployed tree is a shallow clone of master-hihy with no rN branches in
# it, and params survive deploys (a deploy-time stamp would go stale across a rollback).
# Bump openpilot/sunnypilot/HIHY_RELEASE with every cut; an archived rN branch therefore
# carries its own number. Missing or malformed file -> empty string, never an exception:
# this runs in the UI process.
import os
import re

from openpilot.common.basedir import BASEDIR

STAMP_PATH = os.path.join(BASEDIR, "openpilot", "sunnypilot", "HIHY_RELEASE")
_PATTERN = re.compile(r"^r\d{1,4}$")


def get_hihy_release(path: str = STAMP_PATH) -> str:
  try:
    with open(path) as f:
      tag = f.read().strip()
  except OSError:
    return ""
  return tag if _PATTERN.match(tag) else ""
