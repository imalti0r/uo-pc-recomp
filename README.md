# Unicorn Overlord PC Recomp

A native Windows port of **Unicorn Overlord 1.05**, built on [TotK Recomp](https://github.com/valentderah/totk-pc-recomp).

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[Setup guide](docs/installing-uo.md) · [Report a bug](https://github.com/imalti0r/uo-pc-recomp/issues/new) · [Build from source](docs/building-uo.md)

**This is an early alpha.** The opening runs, the tactical map and the first battle tutorial play, and the frame rate holds 60 FPS in a battle. Later chapters, saves and audio mixing haven't been checked end to end. Expect bugs, and back up your saves.

## What this is

The port runs the game's own ARM64 code, statically recompiled to C and compiled for x86-64. Nothing is emulated: guest CPU instructions become host code, and the system services, NVN graphics API, audio and filesystem the game calls are implemented against Windows and Vulkan.

Two things make it work:

- **A lifter** (`recomp/`) that turns the game's NSO modules into C, with the switch dispatch and NEON lowering it needs.
- **A runtime** (`runtime/`) that provides the Horizon OS surface the game calls, and replays NVN command buffers onto Vulkan.

The game has no 3D scene of its own: it composes a 1920x1080 frame and the port upscales that frame to the output resolution with FSR 1, DLSS, or a plain bilinear stretch.

## Installing

The launcher handles installation, Play, settings, RomFS mods, save backups and updates. Python, the compiler and the extractor are included.

1. Extract the entire setup ZIP, keeping its files and folders together.
2. Open **UO-Launcher.exe** and choose **Setup / Update**. Select your base game, the **1.05** update, keys and extracted firmware folder. Both DLCs are optional.
3. Pick an empty install folder with **20 GiB free** and click **Install Unicorn Overlord**.
4. Setup copies the launcher and tools into your game folder, verifies them, and switches to the installed launcher. You can then delete the original extracted setup folder. Use the game folder's **UO-Launcher.exe** from now on; keep its **.launcher** folder with the game.

If you're not sure which files to pick, the [setup guide](docs/installing-uo.md) walks through each field and the common errors.

**F1** opens in-game settings: resolution and DLSS, graphics, audio, controls, language and the log folder.

## What's working

- The full boot chain: ATLUS logo, photosensitivity warning, Vanillaware logo, title screen.
- New Game, the opening story in the rain-soaked courtyard, and the first battle tutorial on the tactical map.
- Chinese, English and Japanese text, from the game's own language setting.
- Output resolutions from 1280x720 to 3840x2160, upscaled with FSR 1 or DLSS (including DLSS auto mode).
- 60 FPS in battle at 1080p on the test machine; the replay costs about 3 ms a frame.
- The F1 settings menu, all six tabs.
- Audio, keyboard and gamepad input.

Saves, later chapters, the DLC content and long play sessions still need testing. Audio mixing balance and movie playback are the known rough edges.

Testing so far has been on Windows 11 with an RTX 5080 and a Ryzen 7 5700X. Other GPUs, clean Windows installs, Linux and Steam Deck haven't been verified.

## Bring your own game files

The game isn't included. You'll need your own base game, the 1.05 update, keys and firmware. Setup builds the executable on your PC and leaves your original dumps alone.

Please don't upload game files, keys, firmware, saves, extracted assets or generated code here.

## Building from source

Follow [this guide](docs/building-uo.md) if you want to build or work on the port yourself. Source builds need developer tools; the setup ZIP already includes what players need.

For a source build:

```
.venv\Scripts\python tools\build_uo.py --jobs 8
build\uo-v105\uo.exe
```

Settings are in `uo.ini`, logs are in `logs/uo.log` next to the executable, and saves stay in the profile folder.

## Helping out

If something breaks, [open an issue](https://github.com/imalti0r/uo-pc-recomp/issues/new). Include your version, GPU/driver, what you were doing and how to reproduce it. Check logs for personal paths before sharing them.

Want to contribute? Read [CONTRIBUTING.md](CONTRIBUTING.md). See also our [code of conduct](CODE_OF_CONDUCT.md), [security policy](SECURITY.md) and [accessibility statement](ACCESSIBILITY.md).

## Credits and license

This project is a fork of **TotK Recomp** by Valiantsin Dzerakh. The host runtime, the NVN front end and the Vulkan replay come from that project and from MacNativeGames' TotK work; their licenses and credits are kept in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The port's code is under the [MIT License](LICENSE). Dependencies keep their own licenses; game content isn't covered by this license. This is an independent fan project, not affiliated with ATLUS, Vanillaware or Nintendo. Unicorn Overlord and Nintendo Switch are their respective owners' trademarks.

The [original TotK README](docs/upstream-readme.md) is kept for reference.
