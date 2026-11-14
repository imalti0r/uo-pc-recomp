# Setting up Unicorn Overlord PC Recomp

[Published Windows releases](https://github.com/imalti0r/uo-pc-recomp/releases). This guide describes the alpha.1 launcher.

This is still an alpha. Clean Windows installs and the full game remain unverified.

## What you need

| Item | What to use |
| --- | --- |
| Windows | Windows 10 or 11, 64-bit |
| CPU | x86-64-v3, including AVX2 |
| Graphics | A Vulkan 1.3 GPU with a current driver. DLSS needs an RTX card |
| Free space | At least 20 GiB on the install drive, plus space for your dumps |
| Base game | Your Unicorn Overlord NSP or XCI |
| Update | The Unicorn Overlord **1.05** update, or a merged XCI that contains it |
| Keys | Your console's `prod.keys`; optional matching `title.keys` when package tickets are insufficient |
| Firmware | An extracted folder containing the firmware NCA files (18.0.1 matches 1.05) |
| Optional DLC | The two Unicorn Overlord DLC packages |

Python, the compiler and the extractor come with setup. You don't need to install Python, Visual Studio or .NET separately.

## Install

1. Download the Windows setup ZIP and **extract the entire ZIP**. Keep its files and folders together. For published packages, compare the ZIP with the release's `SHA256SUMS`.
2. Run **UO-Launcher.exe**, choose **Setup / Update**, and fill in the file paths. Make sure the update is **1.05**. For firmware, pick the folder with the NCA files inside, not the ZIP. Add either DLC and `title.keys` if needed.
3. Pick a separate, empty install folder, like `D:\Games\UnicornOverlord`, then click **Install Unicorn Overlord**. Allow around 20–60 minutes the first time. Your CPU and drive will affect how long it takes.
4. When setup finishes, it switches to **UO-Launcher.exe** in your game folder. You can delete the original extracted setup folder. Use this installed launcher to play, change settings, manage mods or run **Setup / Update**. Keep its `.launcher` folder with the game. Press **F1** for in-game settings and controls.

Your original dumps aren't changed. The installed game icon and shared fonts come from your files. Keep the built game private — it contains game code.

Select the main game in **Base game** and the 1.05 patch in **Update**. A merged XCI that already carries the update works in **Base game** alone. Selecting a DLC package as the base game stops validation and explains which field to correct. Leave optional `title.keys` empty when it is not needed.

## If something goes wrong

- **File not found:** extract any outer archive first, then select the actual game file. Extract the firmware ZIP before picking its folder too.
- **Wrong update:** you need Unicorn Overlord **1.05**, title ID `010054B01AD92000`. Other updates won't work with this build.
- **DLC selected as the base game:** select the main game in **Base game** and put the DLC in its optional field.
- **Missing title key or ticket:** firmware keys won't replace a game's title key. Use a valid dump from your console and select your matching `title.keys` if needed. Don't paste key values into an issue.
- **No Vulkan 1.3 GPU:** update your graphics driver and try again.
- **Not enough space:** choose a drive with at least 20 GiB free. Setup keeps its temporary extraction and build files on that drive.
- **Setup was interrupted:** open the same setup version, choose the same inputs and install folder, and try again. Completed steps are kept.

Click **Show log** for more detail. The error screen also gives you the log's location. Remove personal paths before sharing a log, and don't attach game files, keys, firmware, generated code, saves or a built executable.

## Saves and settings

Use the installed launcher for settings, RomFS mods and save backups. **F1** opens additional settings in-game. Changes in the launcher are stored in the same `uo.ini` used by the game.

Inside your install folder:

- `profile` holds your saves and player data. Back it up before experimenting.
- `uo.ini` holds your settings.
- `UO-Launcher.exe` opens Play, Mods, Saves, Settings and Setup / Update.
- `.launcher` contains the installed launcher, Python, extractor and compiler tools. Keep it with the game.
- `.setup-work` retains extraction/build checkpoints and generated code for updates. Deleting it can require a fresh rebuild.
- `mods` contains imported RomFS mods; `save-backups` contains the ZIPs made through the launcher.
- `uo.exe` starts the game directly and finds its fonts and profile beside it. Shortcuts work from other folders too.
- `logs/uo.log` holds the last run's log; the four before it are kept as `uo.1.log` to `uo.4.log`.

Setup leaves existing profiles and settings alone. The first time you see an effect, the game may pause while it builds shaders.

## Graphics

The game renders at 1920x1080 and the port upscales that frame. In **F1 → 画面** pick the output resolution (up to 3840x2160) and the upscaler:

- **AMD FSR 1** — works on any GPU, sharpens as it scales.
- **NVIDIA DLSS** — RTX cards only. **DLSS 模式** offers DLAA, Quality, Balanced, Performance, Ultra Performance and **自动**, which picks the ratio from the output size (1440p → Quality, 1800p → Balanced, 2160p → Performance).
- **Bilinear** — a plain stretch, no sharpening.

## Updating

Open **UO-Launcher.exe in your game folder**. It checks for newer published releases automatically. Use **Settings → Check for updates** to check manually. An available update shows **Update now** and **Later**; checking does not send your game paths or files, and an offline check does not stop Play.

Close the game before choosing **Update now**. The launcher asks for confirmation, downloads and verifies the release ZIP, checks the new launcher, then restarts and runs the game-update pipeline. Setup keeps game data, saves, settings and mods.

## RomFS mods

1. Close the game and open **Mods** in the launcher.
2. Select **Import ZIP** or **Import folder** for a compatible Unicorn Overlord 1.05 RomFS mod.
3. Select its row to inspect compatibility, file counts and overlaps.
4. Enable it and start the game. After changing a mod, restart the game.

For manual installation, use `<game folder>/mods/<mod name>/romfs/...`. Each mod gets its own folder; do not overwrite the game's packed RomFS. When enabled mods supply the same file, the first folder alphabetically wins. Code patches, IPS/pchtxt and Cobalt plugins are unsupported.

See [all supported layouts and limitations](launcher.md#romfs-mods).
