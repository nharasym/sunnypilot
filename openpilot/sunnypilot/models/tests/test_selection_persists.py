"""HL-FIX(model-selection-persists): pin validate_active_bundle's two call modes.

The manager passes available_bundles=None while the chestnut is not attached (dock 12V
lags car start), so a persisted chestnut-class selection must NOT be reset just because
the small-model catalog is the one currently fetched. With a real catalog provided, the
upstream reset-on-mismatch behavior must keep working.
"""
import sys
import types
import unittest
from unittest import mock

try:  # on-device / CI the compiled libparams_c exists; locally, stub params first
  from openpilot.common.params import Params  # noqa: F401
except (ImportError, OSError):
  def _stub_native(name):
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: type(attr, (), {"__init__": lambda self, *a, **k: None})
    sys.modules[name] = mod
    return mod

  params_stub = _stub_native("openpilot.common.params")
  params_stub.Params = type("Params", (), {"get": lambda *a, **k: None, "get_bool": lambda *a, **k: False})
  params_stub.UnknownKeyName = type("UnknownKeyName", (Exception,), {})
  _stub_native("msgq.ipc_pyx")

import openpilot.sunnypilot.models.helpers as helpers_mod
from openpilot.cereal import custom


class FakeParams:
  def __init__(self, bundle_dict):
    self._store = {"ModelManager_ActiveBundle": bundle_dict}
    self.removed = []

  def get(self, key, *a, **kw):
    return self._store.get(key)

  def put(self, key, value, *a, **kw):
    self._store[key] = value

  def remove(self, key):
    self.removed.append(key)
    self._store.pop(key, None)


def make_bundle_dict(name="TTTTFBRLM", min_sel=18):
  return {"internalName": name, "displayName": f"{name} Model", "index": 3,
          "minimumSelectorVersion": min_sel, "generation": 12}


def make_catalog_bundle(name, min_sel=18):
  return custom.ModelManagerSP.ModelBundle(internalName=name, displayName=f"{name} Model",
                                           index=1, minimumSelectorVersion=min_sel, generation=12)


class TestSelectionPersists(unittest.TestCase):
  def setUp(self):
    helpers_mod._LAST_VALIDATED_RAW = None

  def test_no_catalog_preserves_selection(self):
    # dock not attached -> manager passes None -> catalog mismatch must be impossible
    params = FakeParams(make_bundle_dict())
    with mock.patch.object(helpers_mod, "_bundle_is_valid_locally", return_value=True):
      helpers_mod.validate_active_bundle(params, None)
    assert params.removed == []
    assert params.get("ModelManager_ActiveBundle") is not None

  def test_wrong_catalog_still_resets(self):
    # dock attached -> full validation: a selection absent from the catalog is reset (upstream behavior)
    params = FakeParams(make_bundle_dict("TTTTFBRLM"))
    catalog = [make_catalog_bundle("Lebowski")]
    with mock.patch.object(helpers_mod, "_bundle_is_valid_locally", return_value=True):
      helpers_mod.validate_active_bundle(params, catalog)
    assert "ModelManager_ActiveBundle" in params.removed

  def test_locally_invalid_still_resets_without_catalog(self):
    # None must not disable the local-files check (missing chunks etc. still reset)
    params = FakeParams(make_bundle_dict())
    with mock.patch.object(helpers_mod, "_bundle_is_valid_locally", return_value=False):
      helpers_mod.validate_active_bundle(params, None)
    assert "ModelManager_ActiveBundle" in params.removed


if __name__ == "__main__":
  unittest.main()
