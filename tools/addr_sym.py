"""Map an address inside uo.exe to the object file and symbol that covers it.

    python tools/addr_sym.py 0x489e030 [more...]

The profile reports a host address; the linker map says which object file and which symbol the
address falls in, which is what identifies the runtime function (the lifted game code has f_*
names and is the easy case).
"""
import re
import sys

MAP = 'build/uo-v105/uo.map'


def main():
    wanted = [int(a, 16) for a in sys.argv[1:]]
    if not wanted:
        sys.exit('usage: addr_sym.py <hex address> [...]')
    # Collect (start, object, section, symbol) for every line, keeping the running section start.
    entries = []
    obj = None
    obj_start = 0
    sym = None
    sym_start = 0
    for line in open(MAP, encoding='utf-8', errors='replace'):
        m = re.match(r'^([0-9a-f]{8})\s+([0-9a-f]{8})\s+(\d+)\s+(?:(\S+):\((\S+)\))?\s*(.*)$', line.rstrip('\n'))
        if not m:
            continue
        addr, size = int(m.group(1), 16), int(m.group(2), 16)
        obj_name, section, name = m.group(4), m.group(5), m.group(6).strip()
        if obj_name:
            obj, obj_start, sym, sym_start = obj_name, addr, None, addr
        if name:
            sym, sym_start = name, addr
        entries.append((addr, size, obj, section, name))
    for target in wanted:
        best = None
        for addr, size, obj_name, section, name in entries:
            if addr <= target:
                best = (addr, size, obj_name, section, name)
            else:
                break
        if best:
            addr, size, obj_name, section, name = best
            print(f'{target:#x}: object {obj_name} section {section} '
                  f'+{target - addr:#x} (nearest symbol {name!r} at {addr:#x})')
        else:
            print(f'{target:#x}: not in the map')


if __name__ == '__main__':
    main()
