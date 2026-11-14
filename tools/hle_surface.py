"""List the symbols a game imports that no module defines: the HLE surface the runtime must implement.

    .venv\\Scripts\\python tools/hle_surface.py --exefs build/extracted/ffrs-v101/ExeFS [--missing-only]

Each entry is either implemented as a HOST_OVERRIDE in runtime/, or resolves to zero as a weak import,
or stops the guest with "call to unresolved import" the first time it is called.
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'recomp'))
from nso import Nso  # noqa: E402


def demangle_prefix(name):
    """The nested-name prefix of an Itanium symbol: '_ZN2nn2fs8OpenFileE...' -> ['nn', 'fs', 'OpenFile']."""
    if not name.startswith('_ZN') and not name.startswith('_ZNK'):
        return None
    i = 4 if name.startswith('_ZNK') else 3
    parts = []
    while i < len(name):
        if name[i] == 'E':
            break
        match = re.match(r'(\d+)', name[i:])
        if not match:
            break
        length = int(match.group(1))
        i += len(match.group(1))
        parts.append(name[i:i + length])
        i += length
    return parts


def runtime_overrides():
    names = set()
    for path in (ROOT / 'runtime').glob('*.c'):
        text = path.read_text(encoding='utf-8', errors='replace')
        names.update(re.findall(r'HOST_(?:OVERRIDE|OBSERVER)\("([^"]+)"', text))
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--exefs', type=Path, required=True)
    parser.add_argument('--missing-only', action='store_true')
    args = parser.parse_args()
    defined, undefined, weak = set(), {}, set()
    for name in ('main', 'subsdk0', 'sdk', 'rtld'):
        path = args.exefs / name
        if not path.is_file():
            continue
        mod = Nso(str(path))
        for sym in mod.syms:
            sym_name = sym[0]
            if not sym_name:
                continue
            if isinstance(sym_name, bytes):
                sym_name = sym_name.decode('utf-8', 'replace')
            if sym[2]:
                defined.add(sym_name)
            else:
                undefined.setdefault(sym_name, name)
                if (sym[1] >> 4) == 2:
                    weak.add(sym_name)
    overrides = runtime_overrides()
    hle = {n: m for n, m in undefined.items() if n not in defined}
    implemented = sorted(n for n in hle if n in overrides)
    weak_only = sorted(n for n in hle if n not in overrides and n in weak)
    missing = sorted(n for n in hle if n not in overrides and n not in weak)
    groups = Counter()
    for name in hle:
        parts = demangle_prefix(name)
        groups['::'.join(parts[:2]) if parts else '(C symbol)'] += 1
    print(f'{len(hle)} imported symbols are not defined by any module: {len(implemented)} overridden, '
          f'{len(weak_only)} weak (resolve to 0), {len(missing)} missing')
    print('by namespace:')
    for group, count in groups.most_common(30):
        done = sum(1 for n in hle if (lambda p: '::'.join(p[:2]) if p else '(C symbol)')(demangle_prefix(n)) == group
                   and n in overrides)
        print(f'  {count:5}  {group}   ({done} overridden)')
    if args.missing_only:
        print('missing (a call stops the guest):')
        for name in missing:
            print(f'  {name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
