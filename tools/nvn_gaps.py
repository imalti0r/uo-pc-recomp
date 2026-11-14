"""List NVN procs that only have the weak stub (nvn_procs.c), i.e. those that stop the game when called."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
inc = (ROOT / 'runtime/nvn_procs.inc').read_text(encoding='utf-8')
procs = re.findall(r'NVN_PROC\((\w+)\)', inc)

implemented = set()
for path in (ROOT / 'runtime').glob('*.c'):
    text = path.read_text(encoding='utf-8', errors='replace')
    implemented.update(re.findall(r'NVN_API\((\w+)\)', text))
    implemented.update(re.findall(r'void nvnp_(\w+)\(Cpu', text))

missing = [p for p in procs if p not in implemented]
print(f'{len(procs)} procs, {len(implemented & set(procs))} implemented, {len(missing)} stubs')
for name in missing:
    print(' ', name)
