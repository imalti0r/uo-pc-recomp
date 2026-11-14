"""Watch a playing session: per-thread CPU and the game's own frame-time lines.

    python tools/watch_session.py [minutes]

Runs beside the game, touching it only through GetThreadTimes and by reading its log, so what it
reports is the session the player is actually in. Every interval it prints the frame rate the
engine recorded and which thread is doing the work; at the end it summarises.
"""
import ctypes
import ctypes.wintypes as wt
import pathlib
import re
import sys
import time

THREAD_QUERY_INFORMATION = 0x0040
TH32CS_SNAPTHREAD = 0x00000004
LOG = pathlib.Path(__file__).resolve().parents[1] / 'build' / 'uo-v105' / 'logs' / 'uo.log'


class THREADENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ThreadID', wt.DWORD),
                ('th32OwnerProcessID', wt.DWORD), ('tpBasePri', wt.LONG), ('tpDeltaPri', wt.LONG),
                ('dwFlags', wt.DWORD)]


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


def thread_times(pid):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    out = {}
    ok = k32.Thread32First(snap, ctypes.byref(entry))
    while ok:
        if entry.th32OwnerProcessID == pid:
            h = k32.OpenThread(THREAD_QUERY_INFORMATION, False, entry.th32ThreadID)
            if h:
                c, e, k, u = (wt.FILETIME() for _ in range(4))
                if k32.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                    out[entry.th32ThreadID] = ((k.dwHighDateTime << 32 | k.dwLowDateTime) +
                                               (u.dwHighDateTime << 32 | u.dwLowDateTime))
                k32.CloseHandle(h)
        ok = k32.Thread32Next(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return out


def new_lines(path, offset):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            f.seek(offset)
            data = f.read()
            return data, f.tell()
    except OSError:
        return '', offset


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    end = time.monotonic() + minutes * 60
    offset = LOG.stat().st_size if LOG.is_file() else 0
    frames = []
    print(f'watching {minutes:.0f} min; the player drives, this only reads counters', flush=True)
    while time.monotonic() < end:
        pid = find_pid()
        if not pid:
            print('uo.exe is not running; waiting', flush=True)
            time.sleep(5)
            continue
        a = thread_times(pid)
        time.sleep(10)
        b = thread_times(pid)
        text, offset = new_lines(LOG, offset)
        for m in re.finditer(r'frametime: 60 s: (\d+) frames in [\d.]+ s, mean ([\d.]+) fps, 1% low ([\d.]+) fps'
                             r'(?:, over 25 ms (\d+))?', text):
            frames.append((int(m.group(1)), float(m.group(2)), float(m.group(3)), int(m.group(4) or 0)))
        busy = sorted(((b[t] - a.get(t, b[t])) / 1e7 / 10.0, t) for t in b)[::-1][:4]
        cores = sum((b[t] - a.get(t, b[t])) for t in b) / 1e7 / 10.0
        recent = frames[-1] if frames else None
        rate = f'{recent[1]:.1f} fps (1% low {recent[2]:.1f}, {recent[3]} frames over 25 ms)' if recent else 'no line yet'
        print(f'{time.strftime("%H:%M:%S")}  {rate}; {cores:.2f} cores: '
              + ', '.join(f'tid {t} {s * 100:.0f}%' for s, t in busy), flush=True)
    if frames:
        rates = [f[1] for f in frames]
        lows = [f[2] for f in frames]
        print(f'\nsummary: {len(frames)} windows, mean {sum(rates) / len(rates):.1f} fps, '
              f'min {min(rates):.1f}, max {max(rates):.1f}, worst 1% low {min(lows):.1f}')


if __name__ == '__main__':
    main()
