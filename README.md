# DualSense haptics: Python demo

A small, standalone demo of the haptic textures from the [Lies of P DualSense mod](https://www.nexusmods.com/liesofp/mods/514). It also shows how to drive
the adaptive triggers and lightbar, and how to play a voice on the controller speaker (how the mod plays Gemini's
lines). It doesn't include the mod itself. Use it to feel the textures and to borrow the parts you need for a mod
of your own.

| File | What it does |
|---|---|
| `textures.py` | The textures. Each one is a function that returns a mono 48 kHz signal. |
| `dualsense.py` | Controller access: 4-channel audio with a real-time mixer, plus HID (triggers, lightbar, speaker routing). |
| `demo.py` | Scenes that react the way the mod does when the game reports an event. |

## Running it

The DualSense (or DualSense Edge) must be **connected by USB cable**. Over Bluetooth there is no sound card, so
there are no HD haptics.

```sh
pip install -r requirements.txt
python demo.py              # guided tour
python demo.py list         # textures
python demo.py play parry step_wood heartbeat
python demo.py walk snow
python demo.py rain
python demo.py combat
python demo.py voice line.ogg
```

- **Windows:** the controller shows up as *Speakers (DualSense Wireless Controller)* and the demo opens it through
  WASAPI. It needs 4 channels: if the demo can't find it, go to Sound Control Panel > that device > Configure and
  pick *Quadraphonic*, and set the format to 48000 Hz.
- **Linux:** the controller has to be on its 4-channel audio profile (*Direct* or *Surround 4.0*; see the main
  README). With PipeWire the demo uses its JACK device. You also need read/write access to `/dev/hidraw*`, which the
  `steam-devices` udev rules give.
- `--list-devices` and `--device N` let you pick the audio device by hand.

## How the controller works

Over USB, the DualSense appears as two devices:

1. **A 4-channel, 48 kHz sound card.** Channels 1-2 go to the headphone jack or the built-in speaker. Channels 3-4
   drive the left and right **haptic actuators**. HD haptics are just audio sent to channels 3-4. Anything you can
   synthesize or load as a WAV can be felt.
2. **A HID device.** Output report `0x02` (48 bytes, 64 on the Edge) sets the adaptive triggers, the lightbar and
   the **audio routing**. Setting byte 8 to `0x30` sends channels 1-2 to the speaker, which is how Gemini's voice
   comes out of the controller (`DualSense.route_to_speaker()`). The controller forgets the routing when it
   reconnects, so send it again after a reconnect.

### Making textures

The actuators are voice coils (small speakers with no cone). They respond from about 40 to 500 Hz, and the hand
feels most around 150-250 Hz. Almost every texture is a mix of three things:

- **Low sine bursts (40-90 Hz)** for weight: impacts, landings, heartbeats.
- **Tiny sine grains (2-8 ms, 150-400 Hz) at random times** for grit: sand, gravel, rain, fire. The rate sets the
  feel: about 60 per second is rain, 700 per second is crunching gravel.
- **Band-filtered noise** for friction: grass, splashes, scraping.

Panning a texture between the two grips gives direction (the left foot on the left grip). Normalize each texture
and soft-limit the final mix (`tanh`), so that many overlapping events don't clip.

## Making a mod like this for another game

### 1. Find out which engine the game uses

Look at the game's install folder:

| What you see | Engine |
|---|---|
| `<Name>-Win64-Shipping.exe`, `.pak`/`.utoc` files, an `Engine/` folder | Unreal Engine 4/5 |
| `UnityPlayer.dll` and `<Name>_Data/Managed/Assembly-CSharp.dll` | Unity (Mono) |
| `UnityPlayer.dll`, `GameAssembly.dll` and `global-metadata.dat` | Unity (IL2CPP) |
| `.pck` file | Godot |
| None of these | Custom engine (Detect It Easy or `strings` on the exe can help) |

### 2. Pick the modding framework, and with it the language

- **Unreal:** [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS). It supports **Lua** for hooks and **C++** mods for
  native work. The Lies of P mod uses both. UE4SS can also dump every class and function in the game, which is
  where you find hooks.
- **Unity (Mono):** [BepInEx](https://github.com/BepInEx/BepInEx) with Harmony patches, in **C#**. Read the game
  code with dnSpy or ILSpy.
- **Unity (IL2CPP):** BepInEx 6 or MelonLoader, also in **C#** (through Il2CppInterop). Dump the game with
  Il2CppDumper.
- **Custom engine:** a **C++** DLL injected into the game, with MinHook to hook functions. You find the functions
  yourself with x64dbg, Ghidra or Cheat Engine. This is much more work.
- Check first whether the game has an official mod or scripting API.

### 3. Split it in two

- **Inside the game: the hooks.** They notice that something happened, send a short message (`step left stone`,
  `parry`, `rain 2.0 1`) and return straight away.
- **The haptics engine.** It owns the controller's audio and HID, keeps the mixer running and turns messages into
  textures. That is what `dualsense.py` and `demo.py` show. The engine doesn't depend on the game, so you can
  develop it and test it on its own.

The first version of the Lies of P mod was exactly this: Lua hooks writing lines to a named pipe, and a Python
engine reading them. A local socket works too. Later the engine was rewritten in C++ and moved inside the game
process. Start with the separate process, because it's much faster to iterate on.

### 4. Find the events

Hook many candidate functions with logging, do the action in the game, and see what fires. Write down what you
confirm. Some events have a function you can hook. Others don't, and you **poll** them every frame instead. In
Lies of P:

| Event | How the mod gets it |
|---|---|
| Attacks, dodges, guard, sprint, Legion Arm | Hook: the player's generic "action started" event, which carries a category and a skill name. |
| Parry | Hook: the hit-processing component's "perfect guard occurred" function. |
| Blocks, hits landed, damage taken | Hooks: the guard-hit actions, the weapon's hit-damage function, the damage action. |
| **Rain** | **Hook:** the player's "attach rain hit sound" function. It fires about 10 times a second while it rains, with an intensity and whether the player is outdoors. No message for a second means the rain stopped. |
| Footsteps | **Polled.** No usable footstep event exists. Each frame, the mod checks the foot bones' height to detect a step and asks the game for the ground's physical surface (stone, wood, water...). |
| Health (heartbeat) | Polled every frame. |
| Status effects (fire, shock...) | Hooks: the HUD widget functions that draw the status bars. |
| Gemini's voice | Hook: the sound system's "get dialogue sound" function, which gives the line's name. The mod plays the same file on the controller speaker and mutes the game's copy (volume 1%, because 0 makes the game cut the line short). |

Tips:

- **Sound and UI functions are often the best hooks.** The game has already worked out the event in order to
  play a sound or draw the HUD. The rain hook is a sound function, for example.
- **Movement is easier to poll than to hook:** read speed, position or bones in the per-frame tick.
- **Keep hooks cheap.** Logging every event to the console was enough to cause micro-stutters.
- **Run your periodic work in the game's own tick.** In Lies of P, timers on other threads that touched game
  objects crashed the game.

### 5. Pitfalls to know about

- **The game's own vibration.** With Steam Input on (the default), the game sees an Xbox pad and Steam mixes its
  rumble with your haptics. With native DualSense support, the game writes HID reports itself. If it leaves
  flag0 bit 1 ("haptics select") set, the controller ignores the audio haptics until a report clears that bit.
- **The Steam overlay** can hide the DualSense from HID enumeration inside the game process. That's another reason
  to run the engine as a separate process.
- **The Edge** uses a 64-byte output report, not 48. On Windows, writing the wrong size fails.
- **Game audio is copyrighted.** Don't ship sounds extracted from the game. Extract them from the player's own
  copy at runtime, as the Lies of P mod does.
