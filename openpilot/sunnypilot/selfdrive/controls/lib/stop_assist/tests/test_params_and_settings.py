"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): the two settings exist where the device and sunnylink read them. Source-text checks (stdlib
# only, no native Params on the Mac): params_keys.h, the vehicle.yaml source and the compiled settings_ui.json.
# A key missing from params_keys.h makes Params raise on the device. unittest.TestCase, setUp only.
import ast
import json
import os
import re
import unittest

import openpilot

OPENPILOT = os.path.dirname(os.path.abspath(openpilot.__file__))
PARAMS_KEYS_H = os.path.join(OPENPILOT, "common", "params_keys.h")
VEHICLE_YAML = os.path.join(OPENPILOT, "sunnypilot", "sunnylink", "settings_ui_src", "pages", "vehicle.yaml")
SETTINGS_JSON = os.path.join(OPENPILOT, "sunnypilot", "sunnylink", "settings_ui.json")
PACKAGE = os.path.join(OPENPILOT, "sunnypilot", "selfdrive", "controls", "lib", "stop_assist")

KEYS = {"StopAssistLights": ("BOOL", "0"), "StopAssistLightsOffsetFt": ("INT", "3")}
FORBIDDEN_RULES = {"offroad_only", "not_engaged"}


def _read(path: str) -> str:
  with open(path) as f:
    return f.read()


def _rule_types(rules) -> set[str]:
  out = set()
  for r in rules or []:
    out.add(r.get("type"))
    if "condition" in r:
      out |= _rule_types([r["condition"]])
    out |= _rule_types(r.get("conditions"))
  return out


def _yaml_item_block(text: str, key: str) -> list[str]:
  """The lines of `- key: <key>` up to the next item at the same indent (stdlib only: PyYAML is not on the device)."""
  lines = text.splitlines()
  for i, line in enumerate(lines):
    m = re.match(r"^(\s*)- key: (\w+)\s*$", line)
    if m and m.group(2) == key:
      indent = len(m.group(1))
      block = [line]
      for nxt in lines[i + 1:]:
        stripped = nxt.lstrip()
        if stripped and (len(nxt) - len(stripped)) <= indent and not stripped.startswith('#'):
          break
        block.append(nxt)
      return block
  raise AssertionError(f"{key} not in vehicle.yaml")


class TestParamsKeys(unittest.TestCase):
  def setUp(self):
    self.text = _read(PARAMS_KEYS_H)

  def test_both_keys_declared_with_type_and_default(self):
    for key, (typ, default) in KEYS.items():
      m = re.search(r'\{"' + key + r'",\s*\{([^,}]+),\s*(\w+),\s*"([^"]*)"\}\}', self.text)
      assert m, f"{key} missing from params_keys.h"
      flags, t, d = m.group(1), m.group(2), m.group(3)
      assert t == typ and d == default, (key, t, d)
      assert "PERSISTENT" in flags and "BACKUP" in flags, flags
      assert self.text.count('{"' + key + '",') == 1

  def test_every_key_the_package_reads_is_declared(self):
    read = set()
    for name in os.listdir(PACKAGE):
      if name.endswith(".py"):
        for node in ast.walk(ast.parse(_read(os.path.join(PACKAGE, name)))):
          if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("get", "get_bool") and node.args and \
             isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            read.add(node.args[0].value)
    assert read == set(KEYS), read
    for key in read:
      assert '{"' + key + '",' in self.text, key


class TestVehicleYaml(unittest.TestCase):
  def setUp(self):
    self.text = _read(VEHICLE_YAML)

  def test_items_sit_in_the_toyota_section(self):
    start = self.text.index("- id: toyota")
    nxt = self.text.find("\n- id: ", start + 1)
    section = self.text[start:nxt if nxt > 0 else None]
    for key in KEYS:
      assert f"- key: {key}\n" in section, key

  def test_toggle(self):
    block = "\n".join(_yaml_item_block(self.text, "StopAssistLights"))
    assert "widget: toggle" in block
    assert "$ref: '#/macros/longitudinal'" in block and "key: ToyotaEnforceStockLongitudinal" in block
    for bad in ("offroad", "not_engaged", "needs_onroad_cycle"):
      assert bad not in block, bad

  def test_offset_option(self):
    block = _yaml_item_block(self.text, "StopAssistLightsOffsetFt")
    fields = dict(line.strip().split(": ", 1) for line in block[1:] if re.match(r"^\s+(widget|min|max|step|unit): ", line))
    assert fields == {"widget": "option", "min": "0", "max": "10", "step": "1", "unit": "ft"}, fields
    text = "\n".join(block)
    assert "key: StopAssistLights\n" in text and "equals: true" in text
    assert "offroad" not in text and "not_engaged" not in text


class TestCompiledSettings(unittest.TestCase):
  def setUp(self):
    items = json.loads(_read(SETTINGS_JSON))["vehicle_settings"]["toyota"]["items"]
    self.items = {i["key"]: i for i in items}

  def test_both_items_compiled(self):
    for key in KEYS:
      assert key in self.items, key
    keys = list(self.items)
    assert keys.index("StopAssistLights") + 1 == keys.index("StopAssistLightsOffsetFt")

  def test_toggle_enablement_is_longitudinal_only(self):
    item = self.items["StopAssistLights"]
    assert item["widget"] == "toggle" and "needs_onroad_cycle" not in item
    rules = item["enablement"]
    assert {"type": "capability", "field": "has_longitudinal_control", "equals": True} in rules
    assert {"type": "param", "key": "ToyotaEnforceStockLongitudinal", "equals": False} in rules
    assert not (_rule_types(rules) & FORBIDDEN_RULES)

  def test_offset_option(self):
    item = self.items["StopAssistLightsOffsetFt"]
    assert (item["widget"], item["min"], item["max"], item["step"], item["unit"]) == ("option", 0, 10, 1, "ft")
    assert item["enablement"] == [{"type": "param", "key": "StopAssistLights", "equals": True}]
    assert not (_rule_types(item["enablement"]) & FORBIDDEN_RULES)
