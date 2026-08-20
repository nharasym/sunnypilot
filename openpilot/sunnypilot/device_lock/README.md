# Device Lock

Lock the device so the car reverts to its **factory driver-assist systems** while the comma stays
powered and plugged in. Intended for leaving the car with someone else (e.g. a dealership) without
them being able to use or unlock openpilot.

Unlock with a **symbol pattern on the device**, or **remotely from the sunnylink dashboard**.

## How it works

`DeviceLocked` is the **source of truth**. `OffroadMode` is a *derived effect*, re-asserted from it
on every boot and every UI frame:

```
DeviceLocked=1
  -> OffroadMode=True            (manager.py at boot; DeviceLockMount._tick every frame)
  -> deviceState.started=False   (hardwared.py)
  -> pandad is_onroad=False, always_offroad=True
  -> panda forced to NO_OUTPUT   -> relay closed, factory ADAS runs, openpilot cannot engage
```

No panda / pandad / hardwared changes are needed — that chain already existed as "Always Offroad Mode".

### Why turning Always Offroad off on the device doesn't unlock it

Three layers, in order of importance:

1. **The lock screen** (`locked_overlay.py`) is pushed onto the nav stack, which disables every
   widget beneath it. Settings — and therefore the Always Offroad toggle — is simply unreachable.
2. **Enforcement self-heals.** `DeviceLock.enforce()` runs every frame; if `OffroadMode` is cleared
   it's restored within a frame. So even if the toggle were reached, the change wouldn't stick.
3. **The toggle refuses** while `DeviceLocked` is set (`settings/device.py`).

Only clearing `DeviceLocked` — correct PIN, or remote `saveParams` — actually unlocks.

### Unlock pattern (PIN)

Four controller-style symbols — cross / circle / triangle / square — not digits. Six large targets
in a 3x2 grid beats twelve cramped ones on mici's 536x240 screen, and a shape pattern is easy to
remember. Glyphs are drawn with raylib primitives, so nothing depends on font coverage.

Internally a pattern is the characters `"0".."3"` (indices into `PIN_SYMBOLS`), so the hashing is
alphabet-agnostic: PBKDF2-HMAC-SHA256, random per-pattern salt, stored as `salt$hash` in
`DeviceLockPinHash`. Plaintext is never stored or logged. Wrong attempts persist in
`DeviceLockAttempts`; after 5 an escalating cooldown starts (60s doubling, capped at 1h). Must be
**set on-device** because only the device computes the hash.

`DeviceLockPinFormat` records which alphabet the stored hash used. If it doesn't match the current
one, `has_pin()` reports False — a hash from an older alphabet can never match what the keypad now
produces, so the lock screen directs you to the dashboard instead of offering input that cannot
succeed. Bump `PIN_FORMAT_CURRENT` if the alphabet ever changes again.

**Threat model (owner's, explicit):** stop a dealership from *using* openpilot on a test drive or
while moving the car around the lot. Not a determined attacker with the car all day. 4 symbols x 4
positions = 256 combinations; the rate limiter makes casual guessing pointless. Note the cooldown
timer is in-memory, so a reboot grants one fresh attempt (~65s each) — irrelevant for casual
misuse, but if the threat model ever hardens, persist the cooldown and/or raise `PIN_MIN_LENGTH`.

### Remote unlock

`DeviceLocked` and `DeviceLockPinHash` are deliberately **not** in sunnylinkd's `BLOCKED_PARAMS`, so
the dashboard can clear the lock or reset a forgotten PIN via `saveParams`. There's a regression test
asserting this stays true.

## Files

Self-contained package (move this whole folder on a re-port):

| File | Purpose |
|---|---|
| `constants.py` | param names + all tunables |
| `lock.py` | state machine: lock/unlock, PIN hash/verify, rate limit, `enforce()` |
| `pin_screen.py` | shared full-screen symbol-pattern entry + glyph drawing (big UI + mici) |
| `locked_overlay.py` | the lock screen |
| `lock_setup.py` | set-pattern-and-lock flow + the big-UI Settings row |
| `lock_setup_mici.py` | the mici main-settings circle button (different widget vocabulary) |
| `mount.py` | lifecycle: pushes/pops the overlay, runs `enforce()` every frame |
| `tests/` | unit tests, runnable off-device |

## Re-porting onto a new sunnypilot release

Copy this folder, then re-add the hook lines. Find them all with:

**Note there are THREE separate settings UIs** — big UI, mici, and the sunnylink dashboard — each
with its own tree. Missing one is easy: the row was invisible on the comma 4 the first time because
only the big-UI panel had been wired.

```bash
git grep -n 'HL-FEAT(device-lock)'
```

| File | Hook |
|---|---|
| `common/params_keys.h` | 6 declarations (5 DeviceLock* params + Offroad_DeviceLocked alert key) |
| `system/manager/manager.py` | boot: `if DeviceLocked: OffroadMode=True` |
| `selfdrive/ui/layouts/main.py` | import + `install_device_lock(self)` (last in `__init__`) |
| `selfdrive/ui/mici/layouts/main.py` | same, for mici |
| `selfdrive/ui/sunnypilot/layouts/settings/device.py` | big UI: import + `device_lock_item()` row + Always-Offroad toggle guard |
| `selfdrive/ui/sunnypilot/mici/layouts/settings.py` | **mici (comma 4)**: import + `device_lock_circle_button_mici()` appended beside the always-offroad circle buttons on the MAIN settings page (not nested under Device) |
| `sunnypilot/sunnylink/settings_ui_src/pages/device.yaml` | dashboard `DeviceLocked` toggle (then re-run `compile_settings_ui.py`) |
| `selfdrive/selfdrived/alerts_offroad.json` | `Offroad_DeviceLocked` entry |

Verify with:

```bash
.venv/bin/python -m unittest openpilot.sunnypilot.device_lock.tests.test_device_lock openpilot.sunnypilot.device_lock.tests.test_mount -v
```

## Testing on-device

1. mici: **Settings → lock circle button** (beside Always Offroad). Big UI: Settings → Device →
   **Lock Device**. Set a pattern (entered twice). Screen locks immediately.
2. Confirm the car uses its factory ADAS and openpilot cannot engage
   (`pandaState.safetyModel == noOutput`).
3. Power-cycle a few times — it must come back locked every time.
4. Confirm Settings is unreachable while locked.
5. Unlock with the pattern. Then re-lock and unlock remotely via the sunnylink dashboard
   (`saveParams({"DeviceLocked": "0"})`).

**Note:** the lock only engages while parked/offroad, so it can never remove control mid-drive.

**Residual risk:** SSH is out of scope. Anyone with the device and your SSH key can clear the param
directly — accepted for the dealership threat model.
