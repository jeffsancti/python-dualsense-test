#!/usr/bin/env python3
"""DualSense haptic textures demo (USB only).

  python demo.py                      guided tour of everything below
  python demo.py list                 lists the textures
  python demo.py play [names...]      plays textures (all if none): left grip, both, right grip
  python demo.py walk [surface]       footsteps (stone, wood, metal, dirt, grass, sand, snow, water, mud)
  python demo.py rain                 rain starting and stopping
  python demo.py combat               blocks, a parry, a critical hit, getting hit, an explosion
  python demo.py heartbeat            low health: heartbeat speeding up
  python demo.py fire                 burning status
  python demo.py voice [file]         a sound (wav/ogg/flac) on the controller speaker, like Gemini's voice

Options: --device N (audio device, see --list-devices), --gain 0-1.

Each scene is written the way the mod reacts to the game: the game reports an event (a step, "it's raining",
a parry) and the scene picks a texture and drops it into the mixer. The timing below stands in for the game.
"""

import argparse
import time

import numpy as np
import sounddevice as sd

import textures as tx
from dualsense import DualSense, trigger_off, trigger_resistance, trigger_vibration

rng = np.random.default_rng()
SURFACES = ["stone", "wood", "metal", "dirt", "grass", "sand", "snow", "water", "mud"]


def play(ds, names):
    for name in names:
        print(f"  {name}: {tx.TEXTURES[name].__doc__.splitlines()[0]}")
        for pan in (-1, 0, 1):
            ds.play(tx.TEXTURES[name](rng), pan=pan)
            time.sleep(0.8)


def walk(ds, surface="stone", seconds=4.0):
    """In the mod: a "step" event with the foot (left/right) and the ground's physical surface."""
    print(f"  walking on {surface}")
    step = getattr(tx, f"step_{surface}")
    # soft ground pulls on the triggers a little
    heavy = {"mud": trigger_resistance(0, 5), "sand": trigger_resistance(1, 3), "snow": trigger_resistance(2, 2)}
    ds.triggers(heavy.get(surface, trigger_off()), heavy.get(surface, trigger_off()))
    side, end = -1, time.monotonic() + seconds
    while time.monotonic() < end:
        ds.play(step(rng), pan=0.65 * side, gain=rng.uniform(0.85, 1.0))  # each foot on its own grip
        side = -side
        time.sleep(0.55 * rng.uniform(0.92, 1.08))
    ds.triggers(trigger_off(), trigger_off())


def rain(ds, seconds=10.0):
    """In the mod: the game's rain hook fires ~10 times a second while it rains (with an intensity and whether
    the player is under a roof). The engine keeps a rain level that fades towards it and, on every tick, drops
    a random number of raindrops at random places. When the hook stops arriving, the rain fades out."""
    print("  rain (stops after a while)")
    ds.lightbar(40, 90, 200)
    level, dt, start = 0.0, 0.01, time.monotonic()
    while (t := time.monotonic() - start) < seconds:
        target = 2.0 if t < seconds * 0.6 else 0.0      # what the game reports (0 once it stops)
        fade = 1.5 if target > level else 2.0           # seconds to fade in / out
        level += (target - level) * min(dt / fade, 1.0)
        for _ in range(rng.poisson(level * 30 * dt)):   # ~60 drops/s in full rain
            ds.play(tx.rain_drop(rng), pan=rng.uniform(-0.8, 0.8), gain=rng.uniform(0.25, 0.65))
        time.sleep(dt)


def combat(ds):
    """In the mod: hooks on the player's actions (guard hit, perfect guard, weapon hit, damage taken)."""
    print("  block, block, PARRY, critical, hit taken, explosion")
    for _ in range(2):
        ds.play(tx.block(rng), pan=rng.choice([-0.6, 0.6]), gain=0.5)
        time.sleep(0.7)
    # perfect parry: strong clash, lightbar flash, both triggers buzz for a moment
    ds.play(tx.parry(rng))
    ds.lightbar(255, 200, 40)
    ds.triggers(trigger_vibration(0, 8, 40), trigger_vibration(0, 8, 40))
    time.sleep(0.25)
    ds.triggers(trigger_off(), trigger_off())
    ds.lightbar(120, 60, 255)
    time.sleep(1.0)
    ds.play(tx.critical(rng))
    ds.speak(tx.chime(rng), 0.5)  # this one also plays on the controller speaker
    time.sleep(1.2)
    ds.play(tx.hit_taken(rng), pan=-0.3)
    ds.lightbar(255, 0, 0)
    time.sleep(1.0)
    ds.play(tx.explosion(rng), pan=0.5)
    time.sleep(1.2)


def heartbeat(ds, seconds=8.0):
    """In the mod: health is read every tick; below a threshold the heartbeat loops, faster as health drops."""
    print("  low health")
    start = time.monotonic()
    while (t := time.monotonic() - start) < seconds:
        bpm = 70 + 70 * t / seconds
        ds.play(tx.heartbeat(rng, bpm))
        ds.lightbar(255, 0, 20)
        time.sleep(0.12)
        ds.lightbar(50, 0, 4)
        time.sleep(60 / bpm - 0.12)


def fire(ds, seconds=6.0):
    """In the mod: a status-effect hook says "fire on"; the engine loops crackle waves until "fire off"."""
    print("  burning")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ds.play(tx.fire_crackle(rng), pan=rng.uniform(-0.5, 0.5), gain=rng.uniform(0.6, 1.0))
        ds.lightbar(255, int(rng.uniform(60, 140)), 0)
        time.sleep(rng.uniform(0.35, 0.5))


def voice(ds, path=None):
    """In the mod: a hook sees which voice line the game is about to play, mutes it in the game and plays the same
    file (extracted from the game's data) on channels 1-2, with the controller routed to its speaker."""
    ds.route_to_speaker(volume=100)
    if path:
        import soundfile
        sound, rate = soundfile.read(path, dtype="float32", always_2d=True)
        sound = sound.mean(axis=1)  # the speaker is mono
        if rate != 48000:
            sound = np.interp(np.arange(0, len(sound), rate / 48000), np.arange(len(sound)), sound)
        print(f"  speaker: {path} ({len(sound) / 48000:.1f} s)")
    else:
        sound = np.concatenate([tx.chime(rng) for _ in range(3)])
        print("  speaker: chimes (pass a wav/ogg file to hear a voice)")
    ds.speak(sound, 0.9)
    time.sleep(len(sound) / 48000 + 0.3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", nargs="?", default="tour")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--device", help="audio device index or name (default: finds the DualSense)")
    ap.add_argument("--gain", type=float, default=0.8, help="haptics strength 0-1 (default 0.8)")
    ap.add_argument("--list-devices", action="store_true")
    a = ap.parse_args()

    if a.list_devices:
        print(sd.query_devices())
        return
    if a.scene == "list":
        for name, fn in tx.TEXTURES.items():
            print(f"  {name:<15} {fn.__doc__.splitlines()[0]}")
        return
    if a.scene == "play" and not all(n in tx.TEXTURES for n in a.args):
        raise SystemExit("unknown texture; see: python demo.py list")

    device = int(a.device) if a.device and a.device.isdigit() else a.device
    ds = DualSense(device, gain=a.gain)
    try:
        if a.scene == "play":
            play(ds, a.args or list(tx.TEXTURES))
        elif a.scene == "walk":
            for s in a.args or SURFACES:
                walk(ds, s)
        elif a.scene == "voice":
            voice(ds, a.args[0] if a.args else None)
        elif a.scene in ("rain", "combat", "heartbeat", "fire"):
            globals()[a.scene](ds)
        elif a.scene == "tour":
            for s in SURFACES:
                walk(ds, s, 3.0)
            rain(ds)
            combat(ds)
            heartbeat(ds)
            fire(ds)
            voice(ds)
        else:
            raise SystemExit(f"unknown scene '{a.scene}'; see python demo.py --help")
        time.sleep(0.5)  # let the last texture finish
    except KeyboardInterrupt:
        pass
    finally:
        ds.lightbar(0, 0, 64)
        ds.close()


if __name__ == "__main__":
    main()
