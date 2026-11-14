"""Screenshot the launcher window to build/launcher_shot.png, for checking its layout.

    python tools/shot_launcher.py [out.png]

Uses PrintWindow with PW_RENDERFULLCONTENT so the window is captured even when it is
behind another window, and falls back to a screen grab of its rectangle.
"""
import ctypes
import ctypes.wintypes as wt
import pathlib
import sys

from PIL import Image

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
user32.SetProcessDPIAware()


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG),
                ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wt.DWORD * 3)]


def find_window(title_part):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_part.lower() in buf.value.lower():
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(visit, 0)
    return found


def capture(hwnd):
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    screen = user32.GetDC(0)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(memory, bitmap)
    # PW_RENDERFULLCONTENT (2) captures DWM-composed content, including child controls.
    ok = user32.PrintWindow(hwnd, memory, 2)
    header = BITMAPINFO()
    header.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.bmiHeader.biWidth, header.bmiHeader.biHeight = w, -h
    header.bmiHeader.biPlanes, header.bmiHeader.biBitCount = 1, 32
    buffer = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memory, bitmap, 0, h, buffer, ctypes.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(0, screen)
    return ok, Image.frombuffer('RGBA', (w, h), buffer, 'raw', 'BGRA', 0, 1).convert('RGB')


def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'build/launcher_shot.png')
    windows = find_window(sys.argv[2] if len(sys.argv) > 2 else 'Unicorn Overlord')
    if not windows:
        sys.exit('no launcher window found: start UO-Launcher.exe first')
    hwnd, title = windows[0]
    ok, image = capture(hwnd)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print(f'{title!r} {image.width}x{image.height} PrintWindow={ok} -> {out}')


if __name__ == '__main__':
    main()
