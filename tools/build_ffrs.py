"""Build the FINAL FANTASY RESONANCE 1.0.1 executable with the local pinned toolchain.

A thin adaptation of tools/build_engage.py: same toolchain, same runtime, but the game
identity (title ID, control data, window title, log stem) is FFRS and there is no game
DLL split yet -- the bring-up build is one ffrs.exe with the lifted code linked in.

    .venv\\Scripts\\python tools\\build_ffrs.py --jobs 6
    .venv\\Scripts\\python tools\\build_ffrs.py --extracted <installed game folder> --out build/ffrs-installed
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import build_game
from ffrs_inputs import ROOT, inspect_inputs

EXE = 'ffrs.exe'


def main():
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--extracted', type=Path, default=ROOT / 'build' / 'extracted' / 'ffrs-v101')
    parser.add_argument('--translator', type=Path, default=ROOT / 'build' / 'xlate')
    extra, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    extracted = extra.extracted.resolve()
    output = Path(sys.argv[sys.argv.index('--out') + 1]).resolve() if '--out' in sys.argv else ROOT / 'build' / 'ffrs-v101'
    expected = output / 'expected.json'
    expected.parent.mkdir(parents=True, exist_ok=True)
    # An installed game folder keeps its RomFS packed (romfs.bin + index.json); a development
    # extraction keeps the loose tree. Windows paths are case-insensitive, so tell the two
    # shapes apart by their contents instead of insisting on one name.
    romfs_dir = next((p for p in (extracted / 'RomFS', extracted / 'romfs') if p.is_dir()), None)
    packed = romfs_dir is not None and (romfs_dir / 'romfs.bin').is_file() and (romfs_dir / 'index.json').is_file()
    report = inspect_inputs(extracted / 'ExeFS', None if packed or romfs_dir is None else romfs_dir)
    expected.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    from make_engage_release import setup_icon
    icon = output / 'ffrs-runtime.ico'
    setup_icon(icon)
    resource = output / 'ffrs.rc'
    content = f'''1 24 "{(ROOT / 'runtime' / 'uo.manifest').as_posix()}"
1 ICON "{icon.as_posix()}"
1 VERSIONINFO
FILEVERSION 0,1,0,0
PRODUCTVERSION 0,1,0,0
FILEOS 0x40004
FILETYPE 1
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904b0"
    BEGIN
      VALUE "FileDescription", "FFRS PC Recomp\\0"
      VALUE "ProductName", "FFRS PC Recomp\\0"
      VALUE "FileVersion", "0.1.0\\0"
      VALUE "OriginalFilename", "ffrs.exe\\0"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
'''
    build_game.write_if_changed(resource, content)
    original_sources = build_game.runtime_sources
    build_game.runtime_sources = lambda skip=(): [resource if p.name == 'uo.rc' else p for p in original_sources(skip)]
    build_game.APP_ICON = icon
    old_ini, new_ini = output / 'uo.ini', output / 'ffrs.ini'
    if old_ini.exists() and not new_ini.exists():
        shutil.copy2(old_ini, new_ini)
    build_game.EXE = EXE
    sys.argv = ['build_game.py', '--version', 'ffrs-v101',
                '--expected', str(expected),
                '--exefs', str(extracted / 'ExeFS'),
                '--romfs', str(extracted / 'packed-romfs' if (extracted / 'packed-romfs').is_dir() else romfs_dir or extracted / 'romfs'),
                '--profile', str(ROOT / 'build' / 'profile-ffrs'),
                '--toolchain', str(ROOT / 'toolchain' / 'engage' / 'llvm'),
                '--deps', str(ROOT / 'toolchain' / 'engage' / 'runtime' / 'deps'),
                '--ninja', str(ROOT / 'toolchain' / 'engage' / 'ninja.exe'), *sys.argv[1:]]
    build_game.main()
    if '--compile-only' not in sys.argv:
        for name in ('uo_xlate.exe', 'uo_xlate.id'):
            source = extra.translator / name
            if source.is_file():
                shutil.copy2(source, output / name)


if __name__ == '__main__':
    main()
