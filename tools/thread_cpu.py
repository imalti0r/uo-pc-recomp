"""Per-thread CPU time of a running process, sampled twice.

    python tools/thread_cpu.py [seconds]

Answers "is this CPU bound, and on which thread" without touching the game: every thread's
kernel+user time is read with GetThreadTimes, so a thread at ~100% is the one holding the frame.
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

THREAD_QUERY_INFORMATION = 0x0040
TH32CS_SNAPTHREAD = 0x00000004


class THREADENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ThreadID', wt.DWORD),
                ('th32OwnerProcessID', wt.DWORD), ('tpBasePri', wt.LONG), ('tpDeltaPri', wt.LONG),
                ('dwFlags', wt.DWORD)]


kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)


def find_pid(name):
    """The pid of the first process with this image name."""
    import subprocess
    out = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {name}', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            return int(parts[1])
    return 0


def times(handle):
    c, e, k, u = (wt.FILETIME() for _ in range(4))
    if not kernel32.GetThreadTimes(handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
        return None
    return (k.dwHighDateTime << 32 | k.dwLowDateTime) + (u.dwHighDateTime << 32 | u.dwLowDateTime)


def threads(pid):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    out = []
    ok = kernel32.Thread32First(snap, ctypes.byref(entry))
    while ok:
        if entry.th32OwnerProcessID == pid:
            out.append(entry.th32ThreadID)
        ok = kernel32.Thread32Next(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    return out


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    pid = find_pid('uo.exe')
    if not pid:
        sys.exit('uo.exe is not running')
    first = {}
    handles = {}
    for tid in threads(pid):
        h = kernel32.OpenThread(THREAD_QUERY_INFORMATION, False, tid)
        if h:
            handles[tid] = h
            t = times(h)
            if t is not None:
                first[tid] = t
    time.sleep(seconds)
    rows = []
    for tid, h in handles.items():
        t = times(h)
        if t is not None and tid in first:
            rows.append((t - first[tid], tid))
    for h in handles.values():
        kernel32.CloseHandle(h)
    rows.sort(reverse=True)
    total = sum(r[0] for r in rows)
    print(f'uo.exe pid {pid}, {len(rows)} threads, {seconds:.1f} s window')
    print(f'  total CPU: {total / 1e7:.2f} s = {total / 1e7 / seconds:.2f} cores')
    for cpu, tid in rows[:10]:
        share = cpu / 1e7 / seconds
        print(f'  tid {tid:6d}: {share * 100:6.1f}% of one core ({cpu / 1e7:6.2f} s)')


if __name__ == '__main__':
    main()
