# Device Lock

Lock the device so the car reverts to its **factory driver-assist systems** while the comma stays
powered and plugged in. Intended for leaving the car with someone else (e.g. a dealership) without
them being able to use or unlock openpilot.

Unlock with a **PIN on the device**, or **remotely from the sunnylink dashboard**.

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

### PIN

PBKDF2-HMAC-SHA256, random per-PIN salt, stored as `salt$hash` in `DeviceLockPinHash`. Plaintext is
never stored or logged. Wrong attempts persist in `DeviceLockAttempts`; after 5 an escalating
cooldown starts (60s doubling, capped at 1h). The PIN must be **set on-device** because only the
device computes the hash — the dashboard never sees plaintext.

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
| `pin_screen.py` | shared full-screen numeric PIN entry (big UI + mici) |
| `locked_overlay.py` | the lock screen |
| `lock_setup.py` | set-PIN-and-lock flow + the Settings row |
| `mount.py` | lifecycle: pushes/pops the overlay, runs `enforce()` every frame |
| `tests/` | unit tests, runnable off-device |

## Re-porting onto a new sunnypilot release

Copy this folder, then re-add the hook lines. Find them all with:

```bash
git grep -n 'HL-FEAT(device-lock)'
```

| File | Hook |
|---|---|
| `common/params_keys.h` | 4 param declarations |
| `system/manager/manager.py` | boot: `if DeviceLocked: OffroadMode=True` |
| `selfdrive/ui/layouts/main.py` | import + `install_device_lock(self)` (last in `__init__`) |
| `selfdrive/ui/mici/layouts/main.py` | same, for mici |
| `selfdrive/ui/sunnypilot/layouts/settings/device.py` | import + `device_lock_item()` row + toggle guard |
| `selfdrive/selfdrived/alerts_offroad.json` | `Offroad_DeviceLocked` entry |

Verify with:

```bash
uv run --with pytest python -m pytest sunnypilot/device_lock/tests/ --noconftest -o addopts="" -q
```

## Testing on-device

1. Settings → Device → **Lock Device**, set a PIN. Screen should lock immediately.
2. Confirm the car uses its factory ADAS and openpilot cannot engage
   (`pandaState.safetyModel == noOutput`).
3. Power-cycle a few times — it must come back locked every time.
4. Confirm Settings is unreachable while locked.
5. Unlock with the PIN. Then re-lock and unlock remotely via the sunnylink dashboard
   (`saveParams({"DeviceLocked": "0"})`).

**Note:** the lock only engages while parked/offroad, so it can never remove control mid-drive.

**Residual risk:** SSH is out of scope. Anyone with the device and your SSH key can clear the param
directly — accepted for the dealership threat model.
