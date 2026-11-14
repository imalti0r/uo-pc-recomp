"""Compare a game's imported symbols with the runtime's host overrides and guest definitions.

Every import the game calls that no module defines and no HOST_OVERRIDE implements becomes a
fatal "call to unresolved import" at run time, so the gap list is the port's work queue.

    .venv\\Scripts\\python tools/import_audit.py --exefs build/extracted/ffrs-v101/ExeFS
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'recomp'))
from nso import Nso  # noqa: E402


def runtime_overrides():
    names = set()
    for path in (ROOT / 'runtime').glob('*.c'):
        text = path.read_text(encoding='utf-8', errors='replace')
        names.update(re.findall(r'HOST_(?:OVERRIDE|OBSERVER)\("([^"]+)"', text))
    return names


def module_symbols(exefs):
    defined, undefined = {}, {}
    for name in ('main', 'subsdk0', 'sdk', 'rtld'):
        path = exefs / name
        if not path.is_file():
            continue
        mod = Nso(str(path))
        for sym in mod.syms:
            sym_name, shndx = sym[0], sym[2]
            if not sym_name:
                continue
            if isinstance(sym_name, bytes):
                sym_name = sym_name.decode('utf-8', 'replace')
            if shndx:
                defined.setdefault(sym_name, name)
            else:
                undefined.setdefault(sym_name, name)
    return defined, undefined


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--exefs', type=Path, required=True)
    args = parser.parse_args()
    defined, undefined = module_symbols(args.exefs)
    overrides = runtime_overrides()
    gaps = []
    for name, mod in sorted(undefined.items()):
        if name in defined or name in overrides:
            continue
        gaps.append((mod, name))
    print(f'{len(undefined)} imported symbols; {len(defined)} defined by the modules; '
          f'{len(overrides)} host overrides')
    print(f'{len(gaps)} unresolved after link and override:')
    for mod, name in gaps:
        print(f'  {mod:8} {name}')


if __name__ == '__main__':
    main()
