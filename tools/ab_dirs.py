"""Run the same uo.exe in two directories and compare the frame-time windows.

    python tools/ab_dirs.py <exe> <dirA> <dirB> [seconds]

Copies the exe into both directories (same binary, byte for byte), runs it in each with the same
environment and the same input script, and prints the frame-time windows. If the two disagree the
difference is in the directory; if they agree it is the binary.
"""
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools' / 'input' / 'uo_full.txt'


def run(exe, work, seconds):
    log = work / 'logs' / 'uo.log'
    before = log.stat().st_size if log.is_file() else 0
    env = {'UO_BACKGROUND_WINDOW': '1', 'UO_INPUT': 'none', 'UO_INPUT_SCRIPT': str(SCRIPT),
           'PATH': 'C:\\Windows\\System32;C:\\Windows', 'SYSTEMROOT': 'C:\\Windows'}
    subprocess.run([str(exe), '--seconds', str(seconds)], cwd=work, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=seconds + 300)
    text = ''
    if log.is_file():
        with open(log, encoding='utf-8', errors='replace') as f:
            f.seek(before)
            text = f.read()
    return re.findall(r'frametime: 60 s: (\d+) frames in [\d.]+ s, mean ([\d.]+) fps', text)


def main():
    exe = pathlib.Path(sys.argv[1]).resolve()
    dirs = [pathlib.Path(sys.argv[2]).resolve(), pathlib.Path(sys.argv[3]).resolve()]
    seconds = int(sys.argv[4]) if len(sys.argv) > 4 else 200
    for work in dirs:
        target = work / 'ab-test.exe'
        shutil.copy2(exe, target)
        rows = run(target, work, seconds)
        print(f'{work}: ' + ', '.join(f'{int(n)} frames {fps} fps' for n, fps in rows), flush=True)
        target.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
