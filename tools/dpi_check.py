"""Ask Windows how a running process declares its DPI awareness.

    python tools/dpi_check.py [image name]

Prints GetProcessDpiAwareness: 0 unaware, 1 system aware, 2 per-monitor aware. A process that is
unaware gets its window stretched by Windows at a display scale other than 100%, which is exactly
what makes the overlay look blurred on a 200% display.
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys

shcore = ctypes.WinDLL('shcore')
kernel32 = ctypes.windll.kernel32


def find_pid(name):
    out = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {name}', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            return int(parts[1])
    return 0


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else 'uo.exe'
    pid = find_pid(name)
    if not pid:
        sys.exit(f'{name} is not running')
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        sys.exit('cannot open the process')
    value = ctypes.c_int(-1)
    hr = shcore.GetProcessDpiAwareness(ctypes.c_void_p(handle), ctypes.byref(value))
    names = {0: 'unaware', 1: 'system aware', 2: 'per-monitor aware'}
    print(f'{name} pid {pid}: GetProcessDpiAwareness hr={hr} value={value.value} '
          f'({names.get(value.value, "unknown")})')
    if value.value == 0:
        print('  A DPI-unaware process is stretched by Windows: the picture and the overlay are blurred '
              'at any display scale other than 100%.')


if __name__ == '__main__':
    main()
