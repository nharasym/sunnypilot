"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): the planner contract Stop Assist relies on, checked on the source (ast, nothing imported, so it
# runs on the Mac where the compiled MPC is missing). It fails loudly when an upstream sync changes it:
#  - the stock planner builds ONE unconditional candidates list with the model's e2e candidate in it,
#  - our single hook line follows it immediately, then min() by accel and any() over the stop flags,
#  - nothing else touches the list (comma's v0.11.2 appends the model only in Experimental mode: Stop Assist must then
#    see no e2e candidate and stay out),
#  - the sunnypilot planner clears events_sp before the hook runs and fills stopAssist before it sends.
# unittest.TestCase, setUp only.
import ast
import os
import unittest

import openpilot

OPENPILOT = os.path.dirname(os.path.abspath(openpilot.__file__))
STOCK = os.path.join(OPENPILOT, "selfdrive", "controls", "lib", "longitudinal_planner.py")
SP = os.path.join(OPENPILOT, "sunnypilot", "selfdrive", "controls", "lib", "longitudinal_planner.py")
HOOK = "candidates += self.stop_assist.update(sm, candidates)"
MARKER = "# HL-FEAT(stop-assist)"


def _read(path):
  with open(path) as f:
    return f.read()


def _method(tree, cls, name):
  for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == cls:
      for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == name:
          return item
  raise AssertionError(f"{cls}.{name} not found")


def _touches_candidates(node) -> bool:
  if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Name) and t.id == "candidates" for t in targets)
  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
    return isinstance(node.func.value, ast.Name) and node.func.value.id == "candidates" and \
      node.func.attr in ("append", "extend", "insert", "pop", "remove", "clear", "sort")
  return False


class TestStockPlannerContract(unittest.TestCase):
  def setUp(self):
    self.src = _read(STOCK)
    self.tree = ast.parse(self.src)
    self.update = _method(self.tree, "LongitudinalPlanner", "update")
    body = self.update.body
    idx = [i for i, s in enumerate(body) if isinstance(s, ast.Assign) and _touches_candidates(s)]
    assert len(idx) == 1, "the candidates list must be built exactly once, unconditionally, at the top of update()"
    self.i = idx[0]
    self.body = body

  def test_candidates_list_holds_the_model_candidate(self):
    value = self.body[self.i].value
    assert isinstance(value, ast.List) and len(value.elts) == 3, ast.unparse(value)
    sources = [ast.unparse(e.elts[1]) for e in value.elts if isinstance(e, ast.Tuple) and len(e.elts) == 3]
    assert sources == ["self.mpc.source", "LongitudinalPlanSource.cruise", "LongitudinalPlanSource.e2e"], sources

  def test_e2e_is_the_stock_enum(self):
    # SOURCE_E2E (log.LongitudinalPlan.LongitudinalPlanSource.e2e) must be the enum this file compares against
    imports = [n for n in ast.walk(self.tree) if isinstance(n, ast.ImportFrom) and
               any(a.name == "LongitudinalPlanSource" for a in n.names)]
    assert [n.module for n in imports] == ["openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc"]

  def test_hook_line_follows_the_list_then_min_then_any(self):
    hook, select, flag = self.body[self.i + 1], self.body[self.i + 2], self.body[self.i + 3]
    assert isinstance(hook, ast.AugAssign) and ast.unparse(hook) == HOOK, ast.unparse(hook)
    assert isinstance(select, ast.Assign) and ast.unparse(select.value) == "min(candidates, key=lambda c: c[0])"
    assert isinstance(flag, ast.Assign) and ast.unparse(flag.targets[0]) == "self.output_should_stop"
    call = flag.value
    assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "any" and len(call.args) == 1
    gen = call.args[0]
    assert isinstance(gen, ast.GeneratorExp) and ast.unparse(gen.generators[0].iter) == "candidates"
    target = gen.generators[0].target
    assert isinstance(target, ast.Tuple) and len(target.elts) == 3
    assert ast.unparse(gen.elt) == ast.unparse(target.elts[2]), "the OR must be over every candidate's stop flag"

  def test_nothing_else_touches_the_list(self):
    touched = [n for n in ast.walk(self.update) if _touches_candidates(n)]
    assert len(touched) == 2, [ast.unparse(n) for n in touched]  # the list and our hook

  def test_the_hunk_is_one_marked_line_and_nothing_else(self):
    lines = [ln for ln in self.src.splitlines() if "stop_assist" in ln or "HL-FEAT(stop-assist)" in ln]
    assert lines == [f"    {HOOK}  {MARKER}"], lines

  def test_sp_update_runs_before_the_candidates(self):
    calls = [i for i, s in enumerate(self.body) if "LongitudinalPlannerSP.update(self, sm)" in ast.unparse(s)]
    assert calls and calls[0] < self.i, "events_sp.clear() (in LongitudinalPlannerSP.update) must run before the hook"

  def test_publish_sends_the_sp_plan(self):
    publish = _method(self.tree, "LongitudinalPlanner", "publish")
    assert "self.publish_longitudinal_plan_sp(sm, pm)" in ast.unparse(publish)
    assert "longitudinalPlan.longitudinalPlanSource = self.mpc.source" in ast.unparse(publish)


class TestSunnypilotPlannerContract(unittest.TestCase):
  def setUp(self):
    self.src = _read(SP)
    self.tree = ast.parse(self.src)

  def test_import_construction_and_fill(self):
    marked = [ln.strip() for ln in self.src.splitlines() if MARKER in ln]
    assert marked == [
      f"from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import StopAssist  {MARKER}",
      f"self.stop_assist = StopAssist(CP, self.events_sp)  {MARKER}",
      f"self.stop_assist.fill(longitudinalPlanSP.stopAssist)  {MARKER}",
    ], marked

  def test_constructed_right_after_events_sp(self):
    init = _method(self.tree, "LongitudinalPlannerSP", "__init__")
    stmts = [ast.unparse(s) for s in init.body]
    i = stmts.index("self.events_sp = EventsSP()")
    assert stmts[i + 1] == "self.stop_assist = StopAssist(CP, self.events_sp)"

  def test_update_clears_the_events(self):
    update = _method(self.tree, "LongitudinalPlannerSP", "update")
    assert "self.events_sp.clear()" in [ast.unparse(s) for s in update.body]

  def test_fill_comes_right_before_the_send_and_after_the_events(self):
    pub = _method(self.tree, "LongitudinalPlannerSP", "publish_longitudinal_plan_sp")
    stmts = [ast.unparse(s) for s in pub.body]
    assert stmts[-2:] == ["self.stop_assist.fill(longitudinalPlanSP.stopAssist)", "pm.send('longitudinalPlanSP', plan_sp_send)"]
    assert stmts.index("longitudinalPlanSP.events = self.events_sp.to_msg()") < len(stmts) - 2
