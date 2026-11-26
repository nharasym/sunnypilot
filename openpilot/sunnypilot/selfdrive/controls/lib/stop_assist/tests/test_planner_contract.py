"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): the planner contract Stop Assist relies on, checked on the source (ast, nothing imported, so it
# runs on the Mac where the compiled MPC is missing). It fails loudly when an upstream sync changes it:
#  - the stock planner builds ONE unconditional candidates list with the model's e2e candidate in it,
#  - our single hook line follows it immediately, then min() by accel and any() over every stop flag (no filter),
#  - nothing else touches the list (comma's v0.11.2 appends the model only in Experimental mode: Stop Assist must then
#    see no e2e candidate and stay out), and nothing after the any() reassigns the outputs,
#  - the e2e candidate is the model's own action (through accel_boost), and long_mpc's LongitudinalPlanSource is the
#    log enum SOURCE_E2E comes from,
#  - the sunnypilot planner clears events_sp only in update() (before the hook runs), never replaces it, and fills
#    stopAssist before it sends.
# unittest.TestCase, setUp only.
import ast
import os
import re
import unittest

import openpilot

OPENPILOT = os.path.dirname(os.path.abspath(openpilot.__file__))
STOCK = os.path.join(OPENPILOT, "selfdrive", "controls", "lib", "longitudinal_planner.py")
SP = os.path.join(OPENPILOT, "sunnypilot", "selfdrive", "controls", "lib", "longitudinal_planner.py")
LONG_MPC = os.path.join(OPENPILOT, "selfdrive", "controls", "lib", "longitudinal_mpc_lib", "long_mpc.py")
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
    # one plain loop over every candidate: an `if` clause (e.g. skipping the e2e source) would drop a hold's stop flag
    assert isinstance(gen, ast.GeneratorExp) and len(gen.generators) == 1, ast.unparse(call)
    assert not gen.generators[0].ifs and ast.unparse(gen.generators[0].iter) == "candidates", ast.unparse(call)
    target = gen.generators[0].target
    assert isinstance(target, ast.Tuple) and len(target.elts) == 3
    assert ast.unparse(gen.elt) == ast.unparse(target.elts[2]), "the OR must be over every candidate's stop flag"

  def test_nothing_overrides_the_selection_afterwards(self):
    # the min() and the OR are final: only the stock clip follows, and no later statement reassigns either output
    nxt = self.body[self.i + 4]
    assert ast.unparse(nxt) == "self.output_a_target = np.clip(output_a_target, ACCEL_MIN, ACCEL_MAX)", ast.unparse(nxt)
    for stmt in self.body[self.i + 5:]:
      for node in ast.walk(stmt):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
          targets = node.targets if isinstance(node, ast.Assign) else [node.target]
          for t in targets:
            assert ast.unparse(t) not in ("self.output_should_stop", "self.output_a_target", "output_a_target"), ast.unparse(node)

  def test_the_model_candidate_is_the_models_action(self):
    # Stop Assist only acts when the planner really uses the model's action (an e2e candidate that is present but
    # neutered, e.g. set to ACCEL_MAX outside Experimental mode, would make our entry the only one braking)
    def values(name):
      return [ast.unparse(n.value) for n in ast.walk(self.update)
              if isinstance(n, ast.Assign) and any(ast.unparse(t) == name for t in n.targets)]
    assert values("output_a_target_e2e") == [
      "sm['modelV2'].action.desiredAcceleration",
      "self.accel_boost.update(sm, output_a_target_e2e, output_a_target_mpc, self.a_cruise)",
    ], values("output_a_target_e2e")
    assert values("output_should_stop_e2e") == ["sm['modelV2'].action.shouldStop"], values("output_should_stop_e2e")
    assert not [n for n in ast.walk(self.update) if isinstance(n, ast.AugAssign) and
                ast.unparse(n.target) in ("output_a_target_e2e", "output_should_stop_e2e")]

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


class TestLongMpcSourceEnum(unittest.TestCase):
  def test_long_mpc_reexports_the_stock_log_enum(self):
    # SOURCE_E2E is log.LongitudinalPlan.LongitudinalPlanSource.e2e, which the stock planner compares through
    # long_mpc's re-export (the integration test's fake long_mpc hard-codes the same line)
    src = _read(LONG_MPC)
    assert re.findall(r"^LongitudinalPlanSource = (.*)$", src, re.M) == ["log.LongitudinalPlan.LongitudinalPlanSource"]
    assert re.search(r"^from openpilot\.cereal import log$", src, re.M)


class TestPackageImports(unittest.TestCase):
  """The package imports only cheap, Mac-importable modules: never long_mpc, a planner or longcontrol."""
  ALLOWED = {"math", "numpy", "openpilot.cereal", "openpilot.common.params", "openpilot.common.swaglog",
             "openpilot.common.realtime", "openpilot.common.constants", "openpilot.sunnypilot"}
  PACKAGE = "openpilot.sunnypilot.selfdrive.controls.lib.stop_assist"

  def test_allowed_imports_only(self):
    pkg_dir = os.path.join(OPENPILOT, "sunnypilot", "selfdrive", "controls", "lib", "stop_assist")
    for name in sorted(os.listdir(pkg_dir)):
      if not name.endswith(".py"):
        continue
      for node in ast.walk(ast.parse(_read(os.path.join(pkg_dir, name)))):
        if isinstance(node, ast.Import):
          mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
          mods = [node.module or ""]
        else:
          continue
        for mod in mods:
          assert mod in self.ALLOWED or mod == self.PACKAGE or mod.startswith(self.PACKAGE + "."), (name, mod)


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

  def test_events_are_cleared_only_in_update_and_never_replaced(self):
    # a clear() between the hook and to_msg() (e.g. in publish) would drop "Stopping" every frame; a new EventsSP()
    # after __init__ would leave Stop Assist adding to an orphaned object
    where = {"clear": [], "assign": []}
    for fn in ast.walk(self.tree):
      if isinstance(fn, ast.FunctionDef):
        for node in ast.walk(fn):
          if isinstance(node, ast.Call) and ast.unparse(node.func) == "self.events_sp.clear":
            where["clear"].append(fn.name)
          if isinstance(node, ast.Assign) and any(ast.unparse(t) == "self.events_sp" for t in node.targets):
            where["assign"].append(fn.name)
    assert where == {"clear": ["update"], "assign": ["__init__"]}, where
    assert "events_sp" not in _read(STOCK)

  def test_fill_comes_right_before_the_send_and_after_the_events(self):
    pub = _method(self.tree, "LongitudinalPlannerSP", "publish_longitudinal_plan_sp")
    stmts = [ast.unparse(s) for s in pub.body]
    assert stmts[-2:] == ["self.stop_assist.fill(longitudinalPlanSP.stopAssist)", "pm.send('longitudinalPlanSP', plan_sp_send)"]
    assert stmts.index("longitudinalPlanSP.events = self.events_sp.to_msg()") < len(stmts) - 2
