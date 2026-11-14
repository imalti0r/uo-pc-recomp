"""Name the lifted function containing a module-relative address.

The generated C names every function by its absolute guest address (f_<hex>), so the function list is
the ground truth; exported symbols from the NSO give a readable name when one is nearby.

    .venv\\Scripts\\python tools/symbol_at.py sdk 0x165f5c
    .venv\\Scripts\\python tools/symbol_at.py main 0x1aac8 0x1091278
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'recomp'))
from nso import Nso  # noqa: E402

GEN = ROOT / 'generated/ffrs-v101'
CACHE = ROOT / 'build/symbol-cache.json'
BASES = {'main': 0x7000000000, 'subsdk0': 0x7009c00000, 'sdk': 0x700a400000}


def starts(module):
    out = []
    for path in (GEN / module).glob('*.c'):
        with path.open('r', encoding='utf-8', errors='replace') as stream:
            for line in stream:
                if line.startswith('void f_'):
                    out.append(int(line[7:line.index('(')], 16))
    out.sort()
    return out


def named(module):
    path = ROOT / 'build/extracted/ffrs-v101/ExeFS' / module
    names = []
    if path.is_file():
        mod = Nso(str(path))
        for s in mod.syms:
            n = s[0]
            if not n or not s[2]:
                continue
            if isinstance(n, bytes):
                n = n.decode('utf8', 'replace')
            names.append((s[3], n))
    names.sort()
    return names


def main():
    module = sys.argv[1]
    table = starts(module)
    names = named(module)
    base = BASES[module]
    for arg in sys.argv[2:]:
        off = int(arg, 0)
        addr = base + off
        lo, hi = 0, len(table) - 1
        hit = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if table[mid] <= addr:
                hit = table[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        label = ''
        for a, n in names:
            if a <= off:
                label = f'{n} +{off - a:#x}'
            else:
                break
        print(f'{module}+{off:#x} (f_{hit:#x}, +{addr - hit:#x}) {label}')


if __name__ == '__main__':
    main()
