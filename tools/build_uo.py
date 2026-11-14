"""Build the UNICORN OVERLORD 1.05 executable with the local pinned toolchain.

A thin adaptation of tools/build_ffrs.py: same toolchain, same runtime, but the game identity
(title ID, control data, window title, log stem) is Unicorn Overlord and the bring-up build is
one uo.exe with the lifted code linked in. The game has no subsdk0, so the layout holds two
modules.

    .venv\\Scripts\\python tools\\build_uo.py --jobs 6
    .venv\\Scripts\\python tools\\build_uo.py --extracted <installed game folder> --out build/uo-installed
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import build_game
from uo_inputs import ROOT, inspect_inputs

EXE = 'uo.exe'


def main():
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--extracted', type=Path, default=ROOT / 'build' / 'extracted' / 'uo-v105')
    parser.add_argument('--translator', type=Path, default=ROOT / 'build' / 'xlate')
    # The setup passes the toolchain it ships, so these must be honoured rather than assumed.
    parser.add_argument('--toolchain', type=Path, default=ROOT / 'toolchain' / 'engage' / 'llvm')
    parser.add_argument('--deps', type=Path, default=ROOT / 'toolchain' / 'engage' / 'runtime' / 'deps')
    parser.add_argument('--ninja', type=Path, default=ROOT / 'toolchain' / 'engage' / 'ninja.exe')
    parser.add_argument('--profile', type=Path, default=ROOT / 'build' / 'profile-uo')
    parser.add_argument('--gen', type=Path, default=ROOT / 'generated' / 'uo-v105')
    parser.add_argument('--romfs', type=Path)
    parser.add_argument('--runtime-only', action='store_true',
                        help='compile only runtime/ into libuort.a, so a setup can prove the shipped sources build')
    parser.add_argument('--runtime', type=Path, default=ROOT / 'runtime',
                        help='runtime source directory (the setup passes its own copy)')
    extra, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    if extra.runtime_only:
        # Nothing of the game is needed here: no ExeFS, no RomFS, no generated C. A setup candidate runs this to prove
        # the shipped runtime sources compile with the shipped toolchain. runtime/module_layout.h includes layout.h,
        # which the generator normally writes, so a placeholder of the same shape goes beside the objects: compiling
        # against it checks the sources, and no layout constant from a game copy is needed for that.
        out_dir = Path(remaining[remaining.index('--out') + 1]).resolve() if '--out' in remaining else ROOT / 'build' / 'uo-v105'
        out_dir.mkdir(parents=True, exist_ok=True)
        placeholder = out_dir / 'layout.h'
        if not placeholder.exists():
            placeholder.write_text(
                '// Placeholder for a runtime-only build: runtime/module_layout.h includes layout.h, and compiling the\n'
                '// runtime needs one of that shape. A real build gets the generator\'s layout.h here.\n'
                '#pragma once\n\n'
                '#define MOD_MAIN_BASE 0x7000000000ull\n'
                '#define MOD_MAIN_SIZE 0x100000ull\n'
                '#define MOD_SDK_BASE 0x7001000000ull\n'
                '#define MOD_SDK_SIZE 0x100000ull\n\n'
                '#define MOD_COUNT 2\n'
                '#define MOD_LIST(X) X(main, MAIN) X(sdk, SDK)\n', encoding='utf-8')
        sys.argv = ['build_game.py', '--make-runtime-lib', '--runtime', str(extra.runtime),
                    '--toolchain', str(extra.toolchain), '--deps', str(extra.deps),
                    '--ninja', str(extra.ninja), *remaining]
        build_game.main()
        return
    extracted = extra.extracted.resolve()
    output = Path(sys.argv[sys.argv.index('--out') + 1]).resolve() if '--out' in sys.argv else ROOT / 'build' / 'uo-v105'
    expected = output / 'expected.json'
    expected.parent.mkdir(parents=True, exist_ok=True)
    # An installed game folder keeps its RomFS packed (romfs.bin + index.json); a development
    # extraction keeps the loose tree. Windows paths are case-insensitive, so tell the two
    # shapes apart by their contents instead of insisting on one name.
    romfs_dir = next((p for p in (extracted / 'RomFS', extracted / 'romfs') if p.is_dir()), None)
    packed = romfs_dir is not None and (romfs_dir / 'romfs.bin').is_file() and (romfs_dir / 'index.json').is_file()
    report = inspect_inputs(extracted / 'ExeFS', None if packed or romfs_dir is None else romfs_dir)
    expected.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    # The port's own icon (runtime/uo.ico, made by tools/make_icon.py from original artwork this repository does not
    # ship) is embedded in uo.exe. Without it, windres gets a placeholder of the same shape so a fresh checkout builds.
    icon = output / 'uo-runtime.ico'
    source_icon = ROOT / 'runtime' / 'uo.ico'
    if source_icon.is_file():
        shutil.copy2(source_icon, icon)
    else:
        from make_engage_release import setup_icon
        setup_icon(icon)
    resource = output / 'uo.rc'
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
      VALUE "FileDescription", "Unicorn Overlord PC Recomp\\0"
      VALUE "ProductName", "Unicorn Overlord PC Recomp\\0"
      VALUE "FileVersion", "0.1.0\\0"
      VALUE "OriginalFilename", "uo.exe\\0"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
'''
    build_game.write_if_changed(resource, content)
    build_game.RUNTIME = extra.runtime.resolve()
    original_sources = build_game.runtime_sources

    def sources_with_resource(skip=()):
        """The runtime sources, with this build's generated uo.rc in place of runtime/uo.rc.

        The generated script is always the one compiled: it carries the version information and absolute paths to the
        icon and the manifest, while runtime/uo.rc names them relative to runtime/. A package may not carry runtime/uo.rc
        at all either. Without a resource script uo.exe has no icon and no manifest, and a process without the manifest
        is not DPI aware, so Windows stretches its picture and the overlay looks blurred."""
        sources = [p for p in original_sources(skip) if p.name != 'uo.rc']
        return [*sources, resource]

    build_game.runtime_sources = sources_with_resource
    build_game.APP_ICON = icon
    old_ini, new_ini = output / 'uo.ini', output / 'uo.ini'
    if old_ini.exists() and not new_ini.exists():
        shutil.copy2(old_ini, new_ini)
    build_game.EXE = EXE
    romfs = extra.romfs or (extracted / 'packed-romfs' if (extracted / 'packed-romfs').is_dir()
                            else romfs_dir or extracted / 'romfs')
    sys.argv = ['build_game.py', '--version', 'uo-v105',
                '--expected', str(expected),
                '--exefs', str(extracted / 'ExeFS'),
                '--romfs', str(romfs),
                '--gen', str(extra.gen),
                '--profile', str(extra.profile),
                '--toolchain', str(extra.toolchain),
                '--deps', str(extra.deps),
                '--ninja', str(extra.ninja), *sys.argv[1:]]
    build_game.main()
    if '--compile-only' not in sys.argv:
        for name in ('uo_xlate.exe', 'uo_xlate.id'):
            source = extra.translator / name
            if source.is_file():
                shutil.copy2(source, output / name)


if __name__ == '__main__':
    main()
