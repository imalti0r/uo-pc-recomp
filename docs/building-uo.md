# Building Unicorn Overlord PC Recomp

These instructions are for building the Windows port yourself. If you just want to play, use the [setup guide](installing-uo.md).

Use a writable folder with plenty of space. The extracted game and packed RomFS together take several GB, before counting your dumps, tools and build files.

You'll need Git, Python 3.12 and 3.14, the .NET 10 SDK, Visual Studio C++ build tools for the shader translator, and Windows PowerShell. To run the game, you need an x86-64-v3 CPU with AVX2 and a Vulkan 1.3 GPU.

## Prepare tools

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r tools/requirements-engage-lifter.txt
py -3.14 -m venv .venv-py314
.venv-py314/Scripts/python -m pip install -r tools/requirements-installer.txt
.venv-py314/Scripts/python tools/setup_engage_toolchain.py

dotnet publish tools/nx_extract/nx_extract.csproj -c Release --artifacts-path build/nx-extract-artifacts -o toolchain/nx_extract --no-self-contained

.venv/Scripts/python tools/build_xlate.py
```

Toolchain downloads are checked against pinned hashes. The extractor uses LibHac 0.19.0.

## Supply your own inputs

Copy `config.example.toml` to `config.local.toml` and fill in your local file paths: the base game, the **1.05** update, keys and the extracted firmware folder. DLC is optional. The update needs a valid ticket or your matching title key. Game files, firmware and keys aren't provided here.

Unicorn Overlord's title ID is `010054B01AD92000`; the 1.05 update is the entry to select. A merged XCI (game + update + DLC in one file) works too.

```powershell
.venv-py314/Scripts/python tools/uo_extract.py
```

The extractor checks the packages, update version and content integrity.

## Recompile locally

```powershell
.venv/Scripts/python recomp/gen.py --exefs build/extracted/uo-v105/ExeFS --out generated/uo-v105 --patches recomp/patches-uo-v105.txt
.venv/Scripts/python tools/build_uo.py --jobs 12
.\build\uo-v105\uo.exe
```

Lower `--jobs` if your machine is running out of memory. The first build takes a while: the lifter produces about 72,000 functions across the two modules, and the compiler turns them into one `uo.exe` with the runtime linked in.

The game has no `subsdk0`, so the layout holds two modules (`main` and `sdk`).

You can launch `uo.exe` directly. Vulkan is the default. **F1** opens settings, **F10** cycles the performance overlay, and **F11** toggles fullscreen. Keep the profile folder when rebuilding — that's your player data.

## Patches

`recomp/patches-uo-v105.txt` holds the only patch set this port has: it lets the runtime choose the output size. Every patch site is checked against the module's build ID and original instruction word before generation, so a patch cannot silently apply to the wrong build.

Unlike the upstream TotK port there are no 60 FPS timing patches, no LOD or fade patches and no aspect-ratio patches: Unicorn Overlord runs at 60 FPS natively and the port scales its finished frame instead of editing its internals.

## Resolution and DLSS

The game composes its whole frame at 1920x1080 and the port upscales that frame on the present chain. `UO_OUTPUT_RES` selects the output size, `UO_UPSCALER` picks `fsr`, `bilinear` or `dlss`, and `UO_DLSS_MODE` picks the DLSS ratio (or `auto`, which derives it from the output size). See [resolution](uo-resolution.md).

DLSS needs four Streamline DLLs beside the executable: `sl.interposer.dll`, `sl.common.dll`, `sl.dlss.dll` and `nvngx_dlss.dll`, under `upscalers/streamline/`.

## Check changes

```powershell
.venv/Scripts/python tools/check_content.py
.venv/Scripts/python -m unittest discover -s tests -p "*_test.py"
.venv/Scripts/python tests/continuations_test.py
build/uo-v105/uo.exe --selftest
build/uo-v105/uo.exe --probe-kernel
```

For lifter changes, also run the differential tests in [the engine build guide](building.md). Passing CI doesn't tell us whether a whole playthrough works. Check [the porting notes](uo-port.md) for what has actually been tested.
