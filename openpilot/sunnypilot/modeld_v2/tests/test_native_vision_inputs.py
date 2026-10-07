"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FIX(native-vision-inputs)
import re
from pathlib import Path

from openpilot.sunnypilot.modeld_v2.model_adapters import NativeTinygradAdapter

MODELD_V2 = Path(__file__).resolve().parents[1] / "modeld.py"


class TestNativeVisionInputs:
  def test_native_adapter_uses_the_fixed_narrow_wide_pair(self):
    # comma's precompiled models expose JIT names ('new_img', 'state_img_q'); the camera slots are
    # positional (input_frame[0] narrow, [1] wide) and must be named like the stock runner does
    assert NativeTinygradAdapter.VISION_INPUT_NAMES == ('img', 'big_img')

  def test_names_match_the_wide_camera_rule_in_modeld(self):
    # modeld_v2 main() routes the wide camera to every name containing 'big' and the narrow camera
    # to the rest; exactly one of the two names must say 'big', and it must be slot 1
    src = MODELD_V2.read_text()
    assert re.search(r"buf_extra if 'big' in name else buf_main", src), "the routing rule moved; re-check the slot order"
    names = NativeTinygradAdapter.VISION_INPUT_NAMES
    assert [('big' in n) for n in names] == [False, True]

  def test_metadata_names_are_never_used_for_cameras(self):
    src = (Path(__file__).resolve().parents[1] / "model_adapters.py").read_text()
    native = src[src.index("class NativeTinygradAdapter"):]
    assert "if 'img' in k]" not in native, "camera names derived from metadata again (the two-narrow-crops bug)"
