#!/usr/bin/env python3
"""
HL-FEAT(dm-voice): builds pay_attention.wav, the spoken driver-distracted prompt ("Hey! Pay attention!").

Shaped on Navi's "Hey! Listen!" (Ocarina of Time), measured from a reference recording on 2026-10-18:
"Hey!" 0.39 s at ~550 Hz, a 0.47 s gap, "Listen!" 0.52 s at ~520 Hz (0.27 s per syllable), spectral
centroid 2.3-2.7 kHz, no bell, the second word a little quieter. Nothing from the recording is used: the
voice is macOS `say`, moved into Navi's range either with the classic voices' [[pbas]] (Kathy, Junior)
or by resampling a modern voice (Karen, Samantha, Shelley; their pitch base clamps near 330-400 Hz),
with a high shelf on the resampled ones because the formant shift drags the centroid to 5 kHz.

macOS only (needs `say`), numpy only. From the repo root:
  python3 openpilot/selfdrive/assets/sounds/hihy/make_pay_attention.py <out_dir> [--install NAME]
--install copies <out_dir>/NAME.wav over pay_attention.wav next to this script. Output is mono 16-bit
48 kHz, the format soundd asserts; the loop period is 2.3 s because soundd finishes the running loop
before it stops, so a longer clip would talk on after the driver looks back.
"""
import argparse
import os
import shutil
import subprocess
import wave

import numpy as np

SR = 48000
PEAK = 0.84          # dm_warning.wav peaks at 0.84
PERIOD_S = 2.3
GAP_S = 0.45
HEY_S, PHRASE_S = 0.40, 1.05
NOMINAL_RATE = 170

# name: (say voice, pitch base, resample factor, high shelf)
CANDIDATES = {
  "F_karen_x1.45_shelf": ("Karen", 70, 1.45, True),
  "G_shelley_x1.35": ("Shelley (English (US))", 70, 1.35, False),
  "H_samantha_x1.6_shelf": ("Samantha", 70, 1.60, True),
  "I_kathy_native": ("Kathy", 62, 1.0, False),
  "K_junior_native": ("Junior", 62, 1.0, False),
}
DEFAULT = "F_karen_x1.45_shelf"
HERE = os.path.dirname(os.path.abspath(__file__))


def read(f):
  with wave.open(f) as w:
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SR), (f, w.getparams())
    return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def write(f, x):
  with wave.open(f, "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def say(voice, rate, text, f):
  subprocess.run(["say", "-v", voice, "-r", str(int(rate)), "--data-format=LEI16@48000", "-o", f, text], check=True, capture_output=True)
  return read(f)


def trim(x, thr=0.012, pad_s=0.015):
  idx = np.nonzero(np.abs(x) > thr)[0]
  return x[max(0, idx[0] - int(pad_s * SR)):min(len(x), idx[-1] + int(pad_s * SR))]


def fade(x, ms=6):
  n = int(SR * ms / 1000)
  r = np.linspace(0, 1, n, dtype=np.float32)
  x = x.copy()
  x[:n] *= r
  x[-n:] *= r[::-1]
  return x


def resample(x, f):
  # plays f times faster: pitch, formants and tempo all up by f (the small-creature effect)
  n = int(len(x) / f)
  return np.interp(np.arange(n) * f, np.arange(len(x)), x).astype(np.float32)


def shelf(x, knee=2800.0, hi_gain=0.35, width=3000.0):
  spec = np.fft.rfft(x)
  fr = np.fft.rfftfreq(len(x), 1 / SR)
  g = 1 - (1 - hi_gain) * np.clip((fr - knee) / width, 0, 1)
  return np.fft.irfft(spec * g, n=len(x)).astype(np.float32)


def rms_active(x, thr=0.01):
  nz = x[np.abs(x) > thr]
  return float(np.sqrt((nz ** 2).mean()))


def f0_median(x, frame=1440, hop=480, fmin=120, fmax=1500):
  vals = []
  for i in range(0, len(x) - frame, hop):
    fr = x[i:i + frame] * np.hanning(frame)
    if np.sqrt((fr ** 2).mean()) < 0.01:
      continue
    ac = np.correlate(fr, fr, "full")[frame - 1:]
    ac /= ac[0] + 1e-9
    lo, hi = int(SR / fmax), int(SR / fmin)
    k = lo + int(np.argmax(ac[lo:hi]))
    if ac[k] > 0.5:
      vals.append(SR / k)
  return float(np.median(vals)) if vals else float("nan")


def centroid(x):
  spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
  f = np.fft.rfftfreq(len(x), 1 / SR)
  return float((spec * f).sum() / (spec.sum() + 1e-9))


def part(tmp, voice, pbas, text, target_s, f, tag):
  """speak text so that, after resampling by f, it lasts about target_s (say's rate is not linear: iterate)"""
  pre = f"[[pbas {pbas}]] " if pbas else ""
  rate = NOMINAL_RATE
  raw = trim(say(voice, rate, pre + text, f"{tmp}/{tag}.wav"))
  for _ in range(4):
    ratio = (len(raw) / SR) / (target_s * f)
    if abs(ratio - 1) < 0.06:
      break
    rate = float(np.clip(rate * ratio, 60, 500))
    raw = trim(say(voice, rate, pre + text, f"{tmp}/{tag}.wav"))
  return fade(resample(raw, f))


def assemble(hey, phrase):
  hey = hey * (PEAK / np.abs(hey).max())
  phrase = phrase * (0.8 * rms_active(hey) / rms_active(phrase))     # Navi's second word is the quieter one
  if np.abs(phrase).max() > 0.95:
    phrase *= 0.95 / np.abs(phrase).max()
  x = np.concatenate([np.zeros(int(0.02 * SR), np.float32), hey, np.zeros(int(GAP_S * SR), np.float32), phrase])
  assert len(x) < PERIOD_S * SR, len(x) / SR
  return np.concatenate([x, np.zeros(int(PERIOD_S * SR) - len(x), np.float32)]), hey, phrase


def build(out_dir, name):
  voice, pbas, f, use_shelf = CANDIDATES[name]
  tmp = os.path.join(out_dir, "parts")
  os.makedirs(tmp, exist_ok=True)
  hey = part(tmp, voice, pbas, "Hey!", HEY_S, f, name + "_hey")
  phrase = part(tmp, voice, pbas, "Pay attention!", PHRASE_S, f, name + "_phrase")
  if use_shelf:
    hey, phrase = shelf(hey), shelf(phrase)
  x, hey, phrase = assemble(hey, phrase)
  write(os.path.join(out_dir, name + ".wav"), x)
  hey_s = f"hey {len(hey)/SR:.2f}s {f0_median(hey):.0f}Hz c{centroid(hey):.0f}"
  phrase_s = f"phrase {len(phrase)/SR:.2f}s {f0_median(phrase):.0f}Hz c{centroid(phrase):.0f}"
  print(f"{name:24s} {hey_s} | {phrase_s} | peak {np.abs(x).max():.2f} rms {rms_active(x):.2f} total {len(x)/SR:.1f}s")


def main():
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("out_dir")
  ap.add_argument("--install", metavar="NAME", help=f"copy NAME.wav over pay_attention.wav (default candidate: {DEFAULT})")
  ap.add_argument("--only", metavar="NAME", help="build one candidate instead of all")
  args = ap.parse_args()
  os.makedirs(args.out_dir, exist_ok=True)
  print("target (Navi): hey 0.39s 552Hz c2702 | phrase 0.52s 516Hz c2335")
  for name in ([args.only] if args.only else CANDIDATES):
    build(args.out_dir, name)
  if args.install:
    shutil.copyfile(os.path.join(args.out_dir, args.install + ".wav"), os.path.join(HERE, "pay_attention.wav"))
    print("installed", args.install, "as pay_attention.wav")


if __name__ == "__main__":
  main()
