"""Haptic textures for the DualSense actuators.

Every texture is a function that returns a mono float signal (numpy array) at 48 kHz.
The player (dualsense.py) pans it between the two actuators and mixes it in real time.

How the actuators feel sound:
- They are voice coils (small speakers without a cone), one in each grip.
- They respond from ~40 to ~500 Hz; the hand is most sensitive around 150-250 Hz.
- Low frequencies (40-90 Hz) feel like weight: impacts, thuds, heartbeats.
- Very short sine "grains" (a few ms) feel like grit: sand, gravel, crackling fire.
- Filtered noise feels like friction: rustling grass, splashes, scraping.

Most textures below are just a few of these building blocks mixed together.
"""

import numpy as np

RATE = 48000


# --- Building blocks ---

def ns(seconds):
    """Seconds -> number of samples."""
    return int(seconds * RATE)


def burst(freq, tau, attack=0.0008, length=None):
    """Sine with a fast attack and exponential decay: the basic "tap" unit.
    `tau` is the decay time: ~2 ms is a click, ~50 ms is a thud."""
    t = np.arange(ns(length or tau * 6)) / RATE
    return np.sin(2 * np.pi * freq * t) * np.exp(-t / tau) * np.minimum(1.0, t / attack)


def chirp(f0, f1, length, tau):
    """Sine sweeping from f0 to f1 Hz, decaying with `tau`."""
    t = np.arange(ns(length)) / RATE
    phase = 2 * np.pi * np.cumsum(np.linspace(f0, f1, len(t))) / RATE
    return np.sin(phase) * np.exp(-t / tau) * np.minimum(1.0, t / 0.001)


def band_noise(n, lo, hi, rng):
    """White noise filtered to the [lo, hi] Hz band, normalized to RMS 1."""
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / RATE)
    edge = max((hi - lo) * 0.2, 5.0)
    spec *= np.clip((f - lo + edge) / edge, 0, 1) * np.clip((hi + edge - f) / edge, 0, 1)
    sig = np.fft.irfft(spec, n)
    return sig / (np.sqrt(np.mean(sig ** 2)) or 1.0)


def envelope(n, attack, release):
    """Linear attack then exponential release (seconds)."""
    t = np.arange(n) / RATE
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-np.maximum(t - attack, 0) / release)


def grain_cloud(n, rate, rng, fmin, fmax, tau_min, tau_max, amp_sigma=0.5):
    """`rate` tiny bursts per second at random times, frequencies and loudness: granular textures."""
    out = np.zeros(n)
    t = rng.exponential(1.0 / rate)
    while t < n / RATE:
        g = burst(rng.uniform(fmin, fmax), rng.uniform(tau_min, tau_max)) * rng.lognormal(0, amp_sigma)
        i = ns(t)
        out[i:i + len(g)] += g[:n - i]
        t += rng.exponential(1.0 / rate)
    return out


def place(out, sig, at, gain=1.0):
    """Adds `sig` into `out` starting at `at` seconds (cut at the end of `out`)."""
    i = ns(at)
    if 0 <= i < len(out):
        out[i:i + len(sig)] += sig[:len(out) - i] * gain
    return out


# --- Footsteps (one step each; the game decides when and on which foot) ---

def step_stone(rng):
    """Stone: short dry thud, a click from the sole and a little grit."""
    out = np.zeros(ns(0.14))
    place(out, burst(82 * rng.uniform(0.95, 1.05), 0.018, length=0.1), 0)
    place(out, burst(rng.uniform(240, 300), 0.0035, length=0.03), 0, 0.45)
    grit = grain_cloud(len(out), 250, rng, 200, 420, 0.001, 0.003, amp_sigma=0.6)
    return out + grit * envelope(len(out), 0.002, 0.03) * 0.25


def step_wood(rng):
    """Wooden floor: hollow knock with a small creak."""
    out = np.zeros(ns(0.2))
    place(out, burst(160, 0.018, length=0.12), 0)
    place(out, burst(95, 0.03, length=0.15), 0, 0.7)
    place(out, chirp(180, 140, 0.18, 0.06), 0.02, 0.25)
    return out


def step_metal(rng):
    """Metal plate: click, thud and a short metallic ring."""
    out = np.zeros(ns(0.35))
    for f, tau, a in ((233, 0.08, 0.5), (377, 0.06, 0.35), (541, 0.045, 0.25)):
        place(out, burst(f * rng.uniform(0.98, 1.02), tau, length=0.35), 0, a)
    place(out, burst(72, 0.025, length=0.12), 0)
    place(out, burst(420, 0.0012), 0, 0.8)
    return out


def step_dirt(rng):
    """Dirt/gravel: dry, irregular crunch on top of a soft thud."""
    n = ns(0.24)
    crunch = grain_cloud(n, 700, rng, 180, 460, 0.0012, 0.004, amp_sigma=0.9) * envelope(n, 0.008, 0.07)
    return place(crunch, burst(70, 0.02, length=0.1), 0, 0.8)


def step_grass(rng):
    """Grass: soft rustle (filtered noise)."""
    n = ns(0.3)
    return band_noise(n, 140, 420, rng) * envelope(n, 0.04, 0.09) * 0.7


def step_sand(rng):
    """Sand: slow granular hiss."""
    n = ns(0.38)
    hiss = band_noise(n, 70, 260, rng) * 0.35
    grains = grain_cloud(n, 900, rng, 140, 300, 0.0015, 0.004, amp_sigma=0.4) * 0.5
    return (hiss + grains) * envelope(n, 0.05, 0.11)


def step_snow(rng):
    """Snow: slow compression with a low squeak."""
    n = ns(0.42)
    t = np.arange(n) / RATE
    env = np.clip(t / 0.14, 0, 1) * np.exp(-np.maximum(t - 0.14, 0) / 0.06)
    squeak = grain_cloud(n, 350, rng, 90, 210, 0.003, 0.007, amp_sigma=0.3)
    return (squeak * 0.6 + band_noise(n, 40, 140, rng) * 0.3) * env


def step_water(rng):
    """Shallow water: muffled step, splash and drops falling back."""
    n = ns(0.45)
    t = np.arange(n) / RATE
    splash = band_noise(n, 150, 400, rng) * envelope(n, 0.015, 0.12)
    spray = grain_cloud(n, 320, rng, 180, 350, 0.003, 0.008, amp_sigma=0.6)
    spray *= np.clip((t - 0.04) / 0.03, 0, 1) * np.exp(-np.maximum(t - 0.07, 0) / 0.12)
    return place(splash + spray * 0.8, burst(60, 0.03, length=0.12), 0, 0.35)


def step_mud(rng):
    """Mud: low, wobbly squelch and a suction pop."""
    n = ns(0.5)
    t = np.arange(n) / RATE
    squelch = chirp(38, 95, 0.5, 0.16) * (1 + 0.25 * np.sin(2 * np.pi * 9 * t))
    goo = band_noise(n, 30, 110, rng) * envelope(n, 0.08, 0.15) * 0.5
    return place(squelch + goo, burst(140, 0.01), 0.3, 0.5)


# --- Weather ---

def rain_drop(rng):
    """One raindrop: a tiny random tap. Rain is many of these at random times (see demo.py)."""
    return burst(rng.uniform(170, 330), rng.uniform(0.003, 0.009)) * rng.lognormal(0, 0.5)


# --- Combat ---

def clash(rng, strength=1.0):
    """Blade against blade (block/parry): sharp click, thud and metallic ring. Higher strength rings longer."""
    out = np.zeros(ns(0.6))
    for f, tau, a in ((310, 0.12, 0.6), (467, 0.09, 0.4), (689, 0.07, 0.3), (920, 0.05, 0.2)):
        place(out, burst(f * rng.uniform(0.98, 1.02), tau * strength, length=0.6), 0, a)
    place(out, burst(80, 0.03, length=0.15), 0)
    place(out, band_noise(ns(0.01), 400, 700, rng) * envelope(ns(0.01), 0.0003, 0.002), 0, 1.2)
    return out * strength


def parry(rng):
    """Perfect parry: the strong clash."""
    return clash(rng, 1.5)


def block(rng):
    """Regular block: a weaker, shorter clash."""
    return clash(rng, 0.8)


def critical(rng):
    """Critical hit: heavy thud with a bright crack."""
    out = burst(48, 0.04, length=0.25)
    place(out, burst(rng.uniform(250, 290), 0.04, length=0.18), 0, 0.3)
    crack = grain_cloud(ns(0.03), 2500, rng, 200, 320, 0.0004, 0.001) * envelope(ns(0.03), 0.0002, 0.01)
    return place(out, crack, 0, 1.3)


def hit_taken(rng):
    """Taking a hit: heavy, muffled impact and the body pushing back."""
    n = ns(0.45)
    t = np.arange(n) / RATE
    press = np.sin(2 * np.pi * 70 * t) * envelope(n, 0.03, 0.12) * 0.4
    return place(press, burst(40, 0.07, length=0.4), 0)


def explosion(rng):
    """Explosion / thrown bomb: low blast with shrapnel."""
    n = ns(0.5)
    shards = grain_cloud(n, 600, rng, 160, 300, 0.0006, 0.0015, amp_sigma=0.7) * envelope(n, 0.001, 0.08)
    return place(shards, burst(40, 0.07, length=0.4), 0)


def landing(rng, height=0.5):
    """Landing from a jump/fall; `height` 0-1 makes it lower and longer."""
    n = ns(0.3 + 0.4 * height)
    t = np.arange(n) / RATE
    thud = burst(50 - 15 * height, 0.04 + 0.06 * height, length=0.3 + 0.4 * height)
    absorb = np.sin(2 * np.pi * 70 * t) * envelope(n, 0.04, 0.1 + 0.1 * height) * 0.3 * height
    dust = grain_cloud(n, 300, rng, 130, 260, 0.001, 0.003, amp_sigma=0.6) * envelope(n, 0.003, 0.05) * 0.3
    return place(absorb + dust, thud, 0)


# --- Status effects and body ---

def heartbeat(rng, bpm=72):
    """One heartbeat: low "lub" and a shorter "dub". Loop it while health is low."""
    gap = min(0.27, 0.27 * 72 / bpm + 0.05)
    out = np.zeros(ns(gap + 0.22))
    place(out, burst(48, 0.05, attack=0.006, length=0.25), 0)
    place(out, burst(95, 0.02, attack=0.003, length=0.1), 0, 0.4)
    return place(out, burst(58, 0.035, attack=0.005, length=0.2), gap, 0.65)


def fire_crackle(rng):
    """Burning: irregular crackling of firewood (one ~0.5 s wave; loop it while on fire)."""
    n = ns(0.5)
    t = np.arange(n) / RATE
    bed = band_noise(n, 90, 220, rng) * (0.25 + 0.15 * np.sin(2 * np.pi * rng.uniform(5, 9) * t)) * 0.5
    crackle = grain_cloud(n, rng.uniform(25, 45), rng, 170, 320, 0.002, 0.006, amp_sigma=0.9)
    if rng.random() < 0.6:  # a knot bursting in the wood
        place(crackle, chirp(rng.uniform(170, 210), rng.uniform(100, 130), 0.14, 0.04), rng.uniform(0.03, 0.3))
    return (bed + crackle) * np.clip(t / 0.03, 0, 1) * np.clip((0.5 - t) / 0.05, 0, 1)


def electric_shock(rng):
    """Electrified: rough, buzzing jolt (near-square wave with random roughness)."""
    n = ns(0.45)
    t = np.arange(n) / RATE
    ph = 2 * np.pi * rng.uniform(175, 205) * t
    square = np.sin(ph) + np.sin(3 * ph) / 3 + np.sin(5 * ph) / 5
    buzz = square * (0.4 + 0.6 * rng.random(n) ** 0.5) * np.exp(-t / 0.18)
    snap = grain_cloud(ns(0.03), 2000, rng, 220, 320, 0.0005, 0.001) * 1.5
    return place(buzz, snap, 0) * np.clip(t / 0.002, 0, 1)


def frozen_shiver(rng):
    """Frozen: low tremor at ~9 Hz with small ice crackles."""
    n = ns(rng.uniform(0.5, 0.8))
    t = np.arange(n) / RATE
    tremor = (0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(7, 11) * t)) ** 2
    body = np.sin(2 * np.pi * rng.uniform(55, 70) * t) * tremor * np.sin(np.pi * t / t[-1])
    return body + grain_cloud(n, 25, rng, 200, 280, 0.0006, 0.0012, amp_sigma=0.6) * 0.8


def heal(rng):
    """Healing: warm wave rising through the body, pulsing slowly, with a fine shimmer."""
    n = ns(1.1)
    t = np.arange(n) / RATE
    x = t / t[-1]
    swell = np.sin(2 * np.pi * np.cumsum(50 + 40 * x) / RATE) * np.sin(np.pi * x) ** 1.2
    swell *= 0.75 + 0.25 * np.sin(2 * np.pi * 3 * t)
    shimmer = grain_cloud(n, 160, rng, 220, 310, 0.001, 0.0025, amp_sigma=0.5) * np.sin(np.pi * x) ** 2
    return swell + shimmer * 0.4


# --- Speaker sounds (these go to the controller speaker, not the actuators) ---

def chime(rng):
    """Bright metallic "shing" with a short bell, for the speaker (e.g. on a critical hit)."""
    n = ns(0.6)
    t = np.arange(n) / RATE
    f0 = rng.uniform(1650, 1750)
    bell = sum(a * np.sin(2 * np.pi * f0 * r * t) * np.exp(-t / d)
               for r, a, d in ((1.0, 1.0, 0.25), (1.51, 0.6, 0.18), (2.76, 0.35, 0.1), (4.07, 0.2, 0.06)))
    slice_ = band_noise(ns(0.05), 3000, 8000, rng) * envelope(ns(0.05), 0.0005, 0.015)
    return place(bell * 0.6, slice_, 0, 0.8) * np.clip(t / 0.002, 0, 1)


# name -> function, for the demo's list. Footsteps are also looked up by surface name.
TEXTURES = {
    "step_stone": step_stone, "step_wood": step_wood, "step_metal": step_metal, "step_dirt": step_dirt,
    "step_grass": step_grass, "step_sand": step_sand, "step_snow": step_snow, "step_water": step_water,
    "step_mud": step_mud, "rain_drop": rain_drop, "parry": parry, "block": block, "critical": critical,
    "hit_taken": hit_taken, "explosion": explosion, "landing": landing, "heartbeat": heartbeat,
    "fire_crackle": fire_crackle, "electric_shock": electric_shock, "frozen_shiver": frozen_shiver, "heal": heal,
}
