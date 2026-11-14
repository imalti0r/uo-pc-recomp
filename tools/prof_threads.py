"""Per-thread breakdown of a UO profile: which thread runs, and on what.

    python tools/prof_threads.py <profile> <uo.map>

The whole-profile report averages every thread together, so a run where one thread is busy and
fifty are blocked reads as "99% blocked". This splits the samples by thread first and prints the
top functions of the busiest threads, with blocked time (ntdll/win32u) shown apart from running.
"""
import bisect
import collections
import re
import sys

BLOCKED = ('ntdll.dll', 'win32u.dll', 'KERNELBASE.dll')


def main():
    profile, map_path = sys.argv[1], sys.argv[2]
    base = 0
    samples = 0
    per_thread = collections.Counter()
    per_thread_rip = collections.defaultdict(collections.Counter)
    stacks = []
    modules = []
    for line in open(profile, encoding='utf-8', errors='replace'):
        p = line.split()
        if not p:
            continue
        if p[0] == 'base':
            base = int(p[1], 16)
        elif p[0] == 'samples':
            samples = int(p[1])
        elif p[0] == 'thread':
            per_thread[int(p[1])] += int(p[2])
        elif p[0] == 'rip':
            per_thread_rip[int(p[1], 16)] += 1
        elif p[0] == 'module':
            modules.append((int(p[1], 16), int(p[2], 16), p[3]))
        elif p[0] == 'stack':
            stacks.append((int(p[1]), [int(x, 16) for x in p[2:]]))

    sym = {}
    for line in open(map_path, encoding='utf-8', errors='replace'):
        f = line.split()
        if len(f) == 5 and re.fullmatch(r'[0-9a-f]{8}', f[0]) and re.fullmatch(r'[0-9a-f]{8}', f[1]):
            sym[int(f[0], 16)] = f[4]
    keys = sorted(sym)

    def name(addr):
        for lo, size, path in modules:
            if lo <= addr < lo + size:
                if path.lower().endswith('.exe'):
                    off = addr - lo
                    i = bisect.bisect_right(keys, off) - 1
                    return sym[keys[i]] if i >= 0 else f'exe+{off:#x}'
                short = path.replace('\\', '/').rsplit('/', 1)[-1]
                return f'{short}+{addr - lo:#x}'
        return f'{addr:#x}'

    print(f'{samples} samples in {len(per_thread)} threads')
    for tid, n in per_thread.most_common(6):
        print(f'  tid {tid}: {n}')

    # Chains are only kept for the profiled thread(s), so the split has to come from the rips the
    # sampler attributed per thread when chains are off; with chains on, the chain itself carries it.
    print('\nbusiest threads by chain samples:')
    by_tid = collections.Counter()
    for n, chain in stacks:
        by_tid[chain[-1]] += n
    for tid, n in by_tid.most_common(5):
        print(f'  tid {tid}: {n}')

    total = sum(n for n, _ in stacks) or 1
    for tid, tn in by_tid.most_common(3):
        blocked = 0
        funcs = collections.Counter()
        for n, chain in stacks:
            if chain[-1] != tid:
                continue
            leaf = name(chain[0])
            if any(leaf.startswith(b) for b in BLOCKED):
                blocked += n
            else:
                funcs[leaf] += n
        print(f'\ntid {tid}: {tn} samples, {blocked * 100.0 / tn:.1f}% blocked')
        running = tn - blocked
        for f, n in funcs.most_common(12):
            print(f'   {n * 100.0 / max(running, 1):6.2f}% of running  {n:7d}  {f}')


if __name__ == '__main__':
    main()
