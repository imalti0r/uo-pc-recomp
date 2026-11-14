"""Sample a running uo.exe from outside and report per-thread hotspots.

    python tools/attach_profile.py [seconds] [--map build/uo-v105/uo.map]

No restart, no cooperation from the game: every few milliseconds each thread is suspended,
its instruction pointer read and it is resumed again. Suspending a thread that holds a lock the
sampler needs would deadlock, so the sampler takes no lock and only calls
SuspendThread/GetThreadContext/ResumeThread, none of which allocates in the target.
"""
import bisect
import collections
import ctypes
import ctypes.wintypes as wt
import pathlib
import re
import sys
import time

THREAD_SUSPEND_RESUME = 0x0002
THREAD_GET_CONTEXT = 0x0008
THREAD_QUERY_INFORMATION = 0x0040
TH32CS_SNAPTHREAD = 0x00000004
CONTEXT_CONTROL_AMD64 = 0x00100001


class THREADENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ThreadID', wt.DWORD),
                ('th32OwnerProcessID', wt.DWORD), ('tpBasePri', wt.LONG), ('tpDeltaPri', wt.LONG),
                ('dwFlags', wt.DWORD)]


class M128A(ctypes.Structure):
    _fields_ = [('Low', ctypes.c_uint64), ('High', ctypes.c_int64)]


class CONTEXT(ctypes.Structure):
    _fields_ = [('P1Home', ctypes.c_uint64), ('P2Home', ctypes.c_uint64), ('P3Home', ctypes.c_uint64),
                ('P4Home', ctypes.c_uint64), ('P5Home', ctypes.c_uint64), ('P6Home', ctypes.c_uint64),
                ('ContextFlags', wt.DWORD), ('MxCsr', wt.DWORD),
                ('SegCs', wt.WORD), ('SegDs', wt.WORD), ('SegEs', wt.WORD), ('SegFs', wt.WORD),
                ('SegGs', wt.WORD), ('SegSs', wt.WORD), ('EFlags', wt.DWORD),
                ('Dr0', ctypes.c_uint64), ('Dr1', ctypes.c_uint64), ('Dr2', ctypes.c_uint64), ('Dr3', ctypes.c_uint64),
                ('Dr6', ctypes.c_uint64), ('Dr7', ctypes.c_uint64),
                ('Rax', ctypes.c_uint64), ('Rcx', ctypes.c_uint64), ('Rdx', ctypes.c_uint64), ('Rbx', ctypes.c_uint64),
                ('Rsp', ctypes.c_uint64), ('Rbp', ctypes.c_uint64), ('Rsi', ctypes.c_uint64), ('Rdi', ctypes.c_uint64),
                ('R8', ctypes.c_uint64), ('R9', ctypes.c_uint64), ('R10', ctypes.c_uint64), ('R11', ctypes.c_uint64),
                ('R12', ctypes.c_uint64), ('R13', ctypes.c_uint64), ('R14', ctypes.c_uint64), ('R15', ctypes.c_uint64),
                ('Rip', ctypes.c_uint64),
                ('FltSave', ctypes.c_byte * 512),
                ('VectorRegister', M128A * 26), ('VectorControl', ctypes.c_uint64),
                ('DebugControl', ctypes.c_uint64), ('LastExceptionToRip', ctypes.c_uint64),
                ('LastExceptionFromRip', ctypes.c_uint64)]


k32 = ctypes.WinDLL('kernel32', use_last_error=True)


def find_pid():
    import subprocess
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq uo.exe', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == 'uo.exe':
            return int(parts[1])
    return 0


def thread_ids(pid):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    out = []
    ok = k32.Thread32First(snap, ctypes.byref(entry))
    while ok:
        if entry.th32OwnerProcessID == pid:
            out.append(entry.th32ThreadID)
        ok = k32.Thread32Next(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return out


def module_ranges(pid):
    """Every module of the process: (lo, hi, name)."""
    psapi = ctypes.WinDLL('psapi')
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return []
    needed = wt.DWORD()
    psapi.EnumProcessModules(h, None, 0, ctypes.byref(needed))
    count = needed.value // ctypes.sizeof(wt.HMODULE)
    mods = (wt.HMODULE * count)()
    out = []
    if psapi.EnumProcessModules(h, mods, needed.value, ctypes.byref(needed)):
        class MODINFO(ctypes.Structure):
            _fields_ = [('lpBaseOfDll', wt.LPVOID), ('SizeOfImage', wt.DWORD), ('EntryPoint', wt.LPVOID)]
        for m in mods:
            mi = MODINFO()
            if psapi.GetModuleInformation(h, ctypes.c_void_p(m), ctypes.byref(mi), ctypes.sizeof(mi)):
                buf = ctypes.create_unicode_buffer(1024)
                psapi.GetModuleFileNameExW(h, ctypes.c_void_p(m), buf, 1024)
                lo = ctypes.cast(mi.lpBaseOfDll, ctypes.c_void_p).value
                out.append((lo, lo + mi.SizeOfImage, buf.value))
    k32.CloseHandle(h)
    return out


def load_map(path):
    """Function start -> name, from the linker map.

    A map line is "<addr> <size> <align> [<object>(<section>)] <symbol>", and a symbol may itself contain
    spaces, so the name is everything after the fixed-width columns. Only the lifted functions (f_<guest
    address>) are kept: they are what the hot-code report is about, and the C++ runtime names in the same
    map would otherwise win the nearest-symbol lookup for addresses between functions.
    """
    sym = {}
    for line in open(path, encoding='utf-8', errors='replace'):
        m = re.match(r'^([0-9a-f]{8})\s+([0-9a-f]{8})\s+(\d+)\s+(?:(\S+):\((\S+)\))?\s*(.*)$', line.rstrip('\n'))
        if not m:
            continue
        name = m.group(6).strip()
        if not name.startswith('f_'):
            continue
        sym[int(m.group(1), 16)] = name
    return sym, sorted(sym)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    seconds = float(args[0]) if args else 10.0
    map_path = pathlib.Path(__file__).resolve().parents[1] / 'build' / 'uo-v105' / 'uo.map'
    if '--map' in sys.argv:
        map_path = pathlib.Path(sys.argv[sys.argv.index('--map') + 1])
    sym, keys = load_map(map_path) if map_path.is_file() else ({}, [])

    pid = find_pid()
    if not pid:
        sys.exit('uo.exe is not running')
    mods = module_ranges(pid)
    exe = next((m for m in mods if m[2].lower().endswith('uo.exe')), None)

    def name(addr):
        for lo, hi, path in mods:
            if lo <= addr < hi:
                short = path.replace('\\', '/').rsplit('/', 1)[-1]
                if exe and (lo, hi, path) == exe:
                    off = addr - lo
                    i = bisect.bisect_right(keys, off) - 1
                    return sym[keys[i]] if i >= 0 else f'uo.exe+{off:#x}'
                return f'{short}+{addr - lo:#x}'
        return f'{addr:#x}'

    hits = collections.Counter()          # (tid, rip) -> samples
    total = collections.Counter()         # tid -> samples
    running = collections.Counter()       # tid -> samples whose rip is inside uo.exe
    end = time.monotonic() + seconds
    samples = 0
    # One random thread per iteration. Picking uniformly means each thread's own samples are unbiased, so
    # "samples of T inside uo.exe / samples of T" is the share of T's life spent running game code. Sampling
    # every thread per round would instead freeze the process and report everything as blocked.
    import random
    tids = thread_ids(pid)
    refreshed = time.monotonic()
    handles = {}
    while time.monotonic() < end:
        now = time.monotonic()
        if now - refreshed > 2.0:     # threads come and go
            for h in handles.values():
                k32.CloseHandle(h)
            handles.clear()
            tids = thread_ids(pid)
            refreshed = now
        if not tids:
            break
        tid = random.choice(tids)
        h = handles.get(tid)
        if h is None:
            h = k32.OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, False, tid)
            if not h:
                continue
            handles[tid] = h
        if k32.SuspendThread(h) != 0xFFFFFFFF:
            ctx = CONTEXT()
            ctx.ContextFlags = CONTEXT_CONTROL_AMD64
            if k32.GetThreadContext(h, ctypes.byref(ctx)):
                hits[(tid, ctx.Rip)] += 1
                total[tid] += 1
                samples += 1
                if exe and exe[0] <= ctx.Rip < exe[1]:
                    running[tid] += 1
            k32.ResumeThread(h)
        time.sleep(0.0003)            # ~3000 samples/s: fast enough, and it leaves the process its cores
    for h in handles.values():
        k32.CloseHandle(h)

    print(f'{samples} samples from pid {pid} over {seconds:.0f} s')
    print('\nthreads doing work (share of their own samples inside uo.exe):')
    for tid, n in sorted(running.items(), key=lambda kv: -kv[1])[:8]:
        print(f'  tid {tid:6d}: {running[tid] * 100.0 / total[tid]:5.1f}% running  '
              f'({running[tid]} of {total[tid]} samples)')
    print('\nwhere the time goes, per busy thread (all samples, exe code vs the rest):')
    for tid, n in sorted(running.items(), key=lambda kv: -kv[1])[:4]:
        leaves = collections.Counter()
        for (t, rip), c in hits.items():
            if t == tid:
                leaves[name(rip)] += c
        print(f'\n  tid {tid} ({total[tid]} samples):')
        for f, c in leaves.most_common(6):
            print(f'    {c * 100.0 / total[tid]:6.2f}%  {c:6d}  {f}')
    print('\nhot game code, by thread:')
    for tid, n in sorted(running.items(), key=lambda kv: -kv[1])[:3]:
        funcs = collections.Counter()
        for (t, rip), c in hits.items():
            if t == tid and exe and exe[0] <= rip < exe[1]:
                funcs[name(rip)] += c
        if not funcs:
            continue
        print(f'\n  tid {tid} ({running[tid]} running samples):')
        for f, c in funcs.most_common(12):
            print(f'    {c * 100.0 / running[tid]:6.2f}%  {c:6d}  {f}')


if __name__ == '__main__':
    main()
