import pathlib
import re
import sys

sys.path.insert(0, 'recomp')
from nso import Nso

aliases = {}
for p in pathlib.Path('runtime').glob('*.c'):
    t = p.read_text(encoding='utf-8', errors='replace')
    for name, value in re.findall(r'#define\s+(\w+)\s+"([^"]+)"', t):
        aliases[name] = value

handled = set()
for p in pathlib.Path('runtime').glob('*.c'):
    t = p.read_text(encoding='utf-8', errors='replace')
    for arg in re.findall(r'HOST_(?:OVERRIDE|OBSERVER)\(\s*([^,]+),', t):
        arg = arg.strip()
        handled.add(arg.strip('"') if arg.startswith('"') else aliases.get(arg, arg))
    for arg in re.findall(r'hostfn_add_override\(\s*([^,]+),', t):
        arg = arg.strip()
        handled.add(arg.strip('"') if arg.startswith('"') else aliases.get(arg, arg))

prefix = sys.argv[1] if len(sys.argv) > 1 else '_ZN2nn2fs'
module = sys.argv[2] if len(sys.argv) > 2 else 'main'
m = Nso('build/extracted/ffrs-v101/ExeFS/' + module)
names = []
for s in m.syms:
    n = s[0]
    if not n or s[2]:
        continue
    if isinstance(n, bytes):
        n = n.decode('utf8', 'replace')
    if n.startswith(prefix):
        names.append(n)
covered = [n for n in names if n in handled]
missing = sorted(n for n in names if n not in handled)
print(f'{module}: {len(names)} {prefix} imports; {len(covered)} handled, {len(missing)} missing')
for n in missing:
    print('  ', n)
