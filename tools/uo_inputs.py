"""Verify an extracted UNICORN OVERLORD ExeFS/RomFS before the lifter runs.

The build IDs below are the ones this port was developed against (base 1.05, from the Korean/Chinese
cartridge dump). A different build means a different game: the runtime's module layout, boot constants
and any code patch would have to be re-measured, so the check refuses rather than guessing.
"""
import argparse
import hashlib
import json
from pathlib import Path

from check_content import GENERATED_MARKER

ROOT = Path(__file__).resolve().parents[1]
BUILDS = {
    'main': '9c3116f0333ea157',
    'sdk': '12f161039300861f',
}
# The RomFS of this game is five CriWare CPK archives: the base package, its patch and one per
# translation. These are the markers that say the extraction is the whole game.
ROMFS_FILES = (
    'Unicorn.CPK',
    'UnicornPatch.CPK',
)


def inspect_module(path, expected):
    with path.open('rb') as stream:
        header = stream.read(0x100)
        if len(header) != 0x100 or header[:4] != b'NSO0':
            raise ValueError(f'{path.name}: expected an NSO0 module with a complete header')
        build_id = header[0x40:0x60].hex()
        if not build_id.startswith(expected):
            raise ValueError(f'{path.name}: build ID {build_id[:16]} is not the 1.05 build this port was made for')
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'build_id': build_id, 'sha256': digest, 'size': path.stat().st_size}


def inspect_inputs(exefs, romfs=None):
    modules = {name: inspect_module(exefs / name, build) for name, build in BUILDS.items()}
    report = {
        'marker': GENERATED_MARKER,
        'game': 'UNICORN OVERLORD',
        'title_id': '010054B01AD92000',
        'version': '1.05',
        'modules': modules,
        'exefs_sha256': {name: module['sha256'] for name, module in modules.items()},
    }
    npdm = exefs / 'main.npdm'
    if npdm.is_file():
        report['exefs_sha256']['main.npdm'] = hashlib.sha256(npdm.read_bytes()).hexdigest()
    rtld = exefs / 'rtld'
    if rtld.is_file():
        report['exefs_sha256']['rtld'] = hashlib.sha256(rtld.read_bytes()).hexdigest()
    if romfs is not None:
        missing = [name for name in ROMFS_FILES if not (romfs / name).is_file()]
        if missing:
            raise ValueError(f'RomFS is missing {missing[0]}: it must be the extracted tree with the CPK archives')
        report['romfs_marker_present'] = True
    report['note'] = 'Local inventory only; not an independent integrity check or proof of runtime compatibility.'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--exefs', type=Path, required=True)
    parser.add_argument('--romfs', type=Path)
    args = parser.parse_args()
    try:
        report = inspect_inputs(args.exefs, args.romfs)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    out = ROOT / 'build' / 'uo-v105' / 'inputs.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('UNICORN OVERLORD 1.05 module build IDs match. Local inventory: build/uo-v105/inputs.json')


if __name__ == '__main__':
    main()
