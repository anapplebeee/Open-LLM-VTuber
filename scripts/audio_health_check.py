"""Measure whether the OBS/CABLE audio path is actually producing samples.

Run while BGM or a TTS reply is audible. This does not modify any device.
"""

from __future__ import annotations

import argparse
import time

import numpy as np

try:
    import sounddevice as sd
except ImportError as exc:  # pragma: no cover
    raise SystemExit("sounddevice is not installed") from exc


def find_cable_output() -> int:
    for index, device in enumerate(sd.query_devices()):
        name = str(device.get("name") or "")
        if "CABLE Output" in name and int(device.get("max_input_channels") or 0) > 0:
            return index
    raise RuntimeError("找不到 CABLE Output 输入设备")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure CABLE Output RMS")
    parser.add_argument("--device", type=int, default=-1)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--threshold", type=float, default=0.001)
    args = parser.parse_args()
    try:
        device = args.device if args.device >= 0 else find_cable_output()
        info = sd.query_devices(device)
        print(f"device={device} {info['name']}")
        sample_rate = int(info["default_samplerate"])
        frames = max(1, int(sample_rate * max(0.5, args.seconds)))
        print(f"recording {args.seconds:.1f}s ...")
        time.sleep(0.1)
        data = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32", device=device)
        sd.wait()
        rms = float(np.sqrt(np.mean(np.square(data))))
        peak = float(np.max(np.abs(data)))
        print(f"rms={rms:.6f} peak={peak:.6f} threshold={args.threshold}")
        if rms < args.threshold:
            print("RESULT: SILENT (OBS/CABLE path may not be receiving audio)")
            return 2
        print("RESULT: AUDIO OK")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
