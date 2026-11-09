"""Talks to a DualSense connected by USB: audio (haptics + speaker) and HID (triggers, lightbar, audio routing).

Over USB the controller is two devices:

1. A 4-channel, 48 kHz sound card. Channels 1-2 go to the speaker/headphone jack,
   channels 3-4 drive the left/right haptic actuators. HD haptics are just audio sent to channels 3-4.
   (Over Bluetooth there is no sound card, so none of this works wirelessly.)
2. A HID device. Output report 0x02 sets the adaptive triggers, the lightbar and where channels 1-2 go
   (headphones or the built-in speaker).

The game (or this demo) never waits for a sound to finish: `play()` drops a texture into a mixer and the
audio callback keeps mixing everything that is playing, so many events can overlap (rain + steps + a parry).
"""

import sys
import threading

import numpy as np
import sounddevice as sd

RATE = 48000
VENDOR = 0x054C
PRODUCTS = (0x0CE6, 0x0DF2)  # DualSense, DualSense Edge


# --- Adaptive triggers (11-byte effect blocks, see Nielk1's TriggerEffectGenerator) ---

def _zones(position, strength):
    active, force = 0, 0
    for i in range(position, 10):
        active |= 1 << i
        force |= ((strength - 1) & 7) << (3 * i)
    return active, force


def trigger_off():
    return bytes([0x05] + [0] * 10)


def trigger_resistance(position, strength):
    """Constant resistance from `position` (0-9) to the end, strength 1-8."""
    active, force = _zones(position, strength)
    return bytes([0x21, active & 0xFF, active >> 8, *force.to_bytes(4, "little"), 0, 0, 0, 0])


def trigger_weapon(start, end, strength):
    """Resistance between `start` (2-7) and `end` that "snaps" like a gun trigger, strength 1-8."""
    zones = (1 << start) | (1 << end)
    return bytes([0x25, zones & 0xFF, zones >> 8, (strength - 1) & 7, 0, 0, 0, 0, 0, 0, 0])


def trigger_vibration(position, amplitude, frequency):
    """The trigger itself vibrates from `position` (0-9), amplitude 1-8, frequency in Hz (1-255)."""
    active, force = _zones(position, amplitude)
    return bytes([0x26, active & 0xFF, active >> 8, *force.to_bytes(4, "little"), 0, 0, frequency & 0xFF, 0])


# --- HID ---

FLAG0_RIGHT_TRIGGER, FLAG0_LEFT_TRIGGER = 0x04, 0x08
FLAG0_SPEAKER_VOLUME, FLAG0_AUDIO_ROUTE = 0x20, 0x80
FLAG1_LIGHTBAR = 0x04
ROUTE_SPEAKER = 0x30  # byte 8, bits 4-5 = 3: channels 1-2 to the speaker only (0 = headphone jack)


class HID:
    def __init__(self):
        if sys.platform.startswith("linux"):
            import hidraw as hid  # hidapi's `hid` module uses libusb on Linux, which detaches the kernel driver
        else:
            import hid
        for d in hid.enumerate(VENDOR):
            if d["product_id"] in PRODUCTS:
                self.dev = hid.device()
                self.dev.open_path(d["path"])
                # USB output report size: 48 bytes on the DualSense, 64 on the Edge (Windows rejects any other size)
                self.size = 64 if d["product_id"] == 0x0DF2 else 48
                return
        raise RuntimeError("DualSense not found over HID (connect it with a USB cable)")

    def send(self, left_trigger=None, right_trigger=None, rgb=None, route=None, speaker_volume=None):
        """Sends one output report. Fields left as None are not changed on the controller."""
        r = bytearray(self.size)
        r[0] = 0x02
        if right_trigger is not None:
            r[1] |= FLAG0_RIGHT_TRIGGER
            r[11:22] = right_trigger
        if left_trigger is not None:
            r[1] |= FLAG0_LEFT_TRIGGER
            r[22:33] = left_trigger
        if speaker_volume is not None:
            r[1] |= FLAG0_SPEAKER_VOLUME
            r[6] = speaker_volume
        if route is not None:
            r[1] |= FLAG0_AUDIO_ROUTE
            r[8] = route
        if rgb is not None:
            r[2] |= FLAG1_LIGHTBAR
            r[45:48] = bytes(rgb)
        if self.dev.write(bytes(r)) < 0:
            raise OSError("HID write failed")

    def close(self):
        self.dev.close()


# --- Audio ---

def find_audio_device():
    """The DualSense output with 4 channels. Prefers WASAPI on Windows and PipeWire's JACK on Linux,
    where the raw ALSA device (hw:X,0) is usually held by PipeWire."""
    apis = [a["name"] for a in sd.query_hostapis()]
    found = [(i, d) for i, d in enumerate(sd.query_devices())
             if "dualsense" in d["name"].lower() and d["max_output_channels"] >= 4]
    if not found:
        raise RuntimeError("No 4-channel DualSense audio output found. On Windows check that it shows up as "
                           "'Speakers (DualSense Wireless Controller)'; on Linux it needs the 4-channel profile "
                           "(see README). Use --list-devices and --device to pick one by hand.")
    rank = lambda item: next((k for k, name in enumerate(("WASAPI", "JACK")) if name in apis[item[1]["hostapi"]]), 9)
    return min(found, key=rank)[0]


class Mixer:
    """Sounds playing at the same time. Each voice is a (N, 2) float array and a read position."""

    def __init__(self):
        self.voices, self.lock = [], threading.Lock()

    def add(self, stereo):
        with self.lock:
            self.voices.append([stereo, 0])

    def render(self, n):
        out = np.zeros((n, 2), np.float32)
        with self.lock:
            for v in self.voices:
                chunk = v[0][v[1]:v[1] + n]
                out[:len(chunk)] += chunk
                v[1] += n
            self.voices = [v for v in self.voices if v[1] < len(v[0])]
        return out


class DualSense:
    def __init__(self, device=None, gain=0.8):
        self.gain = gain
        self.haptics, self.speaker = Mixer(), Mixer()
        try:
            self.hid = HID()
        except Exception as e:  # haptics still work without HID; only triggers/lightbar/speaker routing are lost
            print(f"HID unavailable ({e}): no triggers, lightbar or speaker routing")
            self.hid = None
        device = find_audio_device() if device is None else device
        print(f"audio: {sd.query_devices(device)['name']}")
        self.stream = sd.OutputStream(samplerate=RATE, channels=4, dtype="float32", device=device,
                                      latency="low", callback=self._callback)
        self.stream.start()

    def _callback(self, out, frames, time_info, status):
        out[:, 0:2] = np.clip(self.speaker.render(frames), -1, 1)                # speaker / headphones
        out[:, 2:4] = np.tanh(self.haptics.render(frames) * self.gain * 1.5)     # actuators, soft-limited

    def play(self, mono, pan=0.0, gain=1.0):
        """Plays a texture on the actuators. pan -1 = left grip, 0 = both, 1 = right grip."""
        sig = mono / (np.max(np.abs(mono)) or 1.0) * gain
        a = (np.clip(pan, -1, 1) + 1) * np.pi / 4  # constant-power pan
        self.haptics.add(np.stack([sig * np.cos(a), sig * np.sin(a)], axis=1).astype(np.float32))

    def speak(self, mono, gain=1.0):
        """Plays a sound on channels 1-2 (the speaker, once route_to_speaker() was called)."""
        sig = np.clip(mono * gain, -1, 1).astype(np.float32)
        self.speaker.add(np.stack([sig, sig], axis=1))

    def route_to_speaker(self, volume=100):
        """Sends channels 1-2 to the built-in speaker instead of the headphone jack. The controller forgets this
        when reconnected, so send it again after a reconnect."""
        self.send(route=ROUTE_SPEAKER, speaker_volume=volume)

    def lightbar(self, r, g, b):
        self.send(rgb=(r, g, b))

    def triggers(self, left=None, right=None):
        self.send(left_trigger=left, right_trigger=right)

    def send(self, **fields):
        if self.hid:
            self.hid.send(**fields)

    def close(self):
        self.triggers(trigger_off(), trigger_off())
        self.stream.stop()
        self.stream.close()
        if self.hid:
            self.hid.close()
