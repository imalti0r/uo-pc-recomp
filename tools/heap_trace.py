"""What the busiest threads of a running uo.exe are doing, named by module and offset.

    python tools/heap_trace.py [seconds]

Ranks the threads by CPU time first (GetThreadTimes), then samples only those, so the report is
about the threads that hold the frame rather than about the fifty that are parked. Each sample is
attributed to a module and an offset; the offsets seen in this build are named, an unknown one is
printed as a bare offset rather than guessed.
"""
import collections
import ctypes
import ctypes.wintypes as wt
import random
import sys
import time

from attach_profile import (CONTEXT, CONTEXT_CONTROL_AMD64, THREAD_GET_CONTEXT, THREAD_QUERY_INFORMATION,
                            THREAD_SUSPEND_RESUME, find_pid, module_ranges, thread_ids)

k32 = ctypes.WinDLL('kernel32', use_last_error=True)

# Offsets seen in profiles of this build, with what the function there does.
KNOWN = {
    ('ucrtbase', 0x315d7): 'heap allocator (malloc/free path)',
    ('ucrtbase', 0x315e8): 'heap allocator',
    ('ucrtbase', 0x315c3): 'heap allocator',
    ('ucrtbase', 0x315c6): 'heap allocator',
    ('ucrtbase', 0x315dc): 'heap allocator',
    ('ucrtbase', 0x315d3): 'heap allocator',
    ('ucrtbase', 0xee223): 'memcpy',
    ('ucrtbase', 0xee227): 'memcpy',
    ('ntdll', 0x161dd4): 'lock wait (RtlpWaitForCriticalSection)',
    ('ntdll', 0x1659c4): 'lock wait (RtlpWaitForCriticalSection)',
    ('ntdll', 0x1628a4): 'event wait',
    ('ntdll', 0x165a24): 'event wait',
    ('ntdll', 0x161e74): 'wait',
    ('ntdll', 0x6ed2d): 'RtlAllocateHeap/RtlFreeHeap',
    ('ntdll', 0x59870): 'interlocked',
}


def cpu_times(pid, tids):
    out = {}
    for tid in tids:
        h = k32.OpenThread(0x0040, False, tid)          # THREAD_QUERY_INFORMATION
        if not h:
            continue
        c, e, k, u = (wt.FILETIME() for _ in range(4))
        if k32.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
            out[tid] = ((k.dwHighDateTime << 32 | k.dwLowDateTime) +
                        (u.dwHighDateTime << 32 | u.dwLowDateTime))
        k32.CloseHandle(h)
    return out


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    pid = find_pid()
    if not pid:
        sys.exit('uo.exe is not running')
    mods = module_ranges(pid)
    tids = thread_ids(pid)
    a = cpu_times(pid, tids)
    time.sleep(3)
    b = cpu_times(pid, tids)
    ranked = sorted(((b[t] - a.get(t, b[t])) / 1e7 / 3.0, t) for t in b)[::-1]
    busy = [t for share, t in ranked[:3] if share > 0.02]
    print(f'busiest threads (CPU over 3 s): ' + ', '.join(f'tid {t} {s * 100:.0f}%' for s, t in ranked[:4]))
    if not busy:
        sys.exit('nothing is running')

    handles = {}
    for tid in busy:
        h = k32.OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, False, tid)
        if h:
            handles[tid] = h
    hits = collections.Counter()
    total = collections.Counter()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        tid = random.choice(busy)
        h = handles.get(tid)
        if not h:
            continue
        if k32.SuspendThread(h) != 0xFFFFFFFF:
            ctx = CONTEXT()
            ctx.ContextFlags = CONTEXT_CONTROL_AMD64
            if k32.GetThreadContext(h, ctypes.byref(ctx)):
                rip = ctx.Rip
                for lo, hi, path in mods:
                    if lo <= rip < hi:
                        short = path.replace('\\', '/').rsplit('/', 1)[-1].lower()
                        short = short.rsplit('.', 1)[0]
                        off = rip - lo
                        hits[(tid, short, off)] += 1
                        total[tid] += 1
                        break
            k32.ResumeThread(h)
        time.sleep(0.0002)
    for h in handles.values():
        k32.CloseHandle(h)

    for tid in busy:
        n = total[tid] or 1
        print(f'\n  tid {tid}: {total[tid]} samples')
        rows = collections.Counter()
        for (t, mod, off), c in hits.items():
            if t == tid:
                rows[(mod, off)] += c
        for (mod, off), c in rows.most_common(9):
            label = KNOWN.get((mod, off))
            where = f'{label}' if label else f'{mod}+{off:#x}'
            print(f'    {c * 100.0 / n:6.2f}%  {c:6d}  {where}')


if __name__ == '__main__':
    main()
