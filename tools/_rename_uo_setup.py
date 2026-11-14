import pathlib

subs = [
    ('engage.exe', 'uo.exe'),
    ('engage.ini', 'uo.ini'),
    ('engage.ico', 'uo.ico'),
    ('engage-install.json', 'uo-install.json'),
    ('engage_game.dll', 'uo_game.dll'),
    ('engage-setup.ico', 'uo-setup.ico'),
    ('Engage-Launcher.exe', 'UO-Launcher.exe'),
    ('Engage-Setup-', 'UO-Setup-'),
    ('Engage-Launcher-', 'UO-Launcher-'),
    ('engage_setup', 'uo_setup'),
    ('engage-pc-recomp', 'uo-recomp'),
    ('Engage PC Recomp', 'Unicorn Overlord PC Recomp'),
    ('Play Engage', 'Play Unicorn Overlord'),
    ('Set up Engage', 'Set up Unicorn Overlord'),
    ('Update Engage', 'Update Unicorn Overlord'),
    ('Engage could not start', 'Unicorn Overlord could not start'),
    ('Engage-Setup', 'UO-Setup'),
    ('Engage-Launcher', 'UO-Launcher'),
    ("Engage's", "Unicorn Overlord's"),
    ('Engage-%Y%m%d', 'UnicornOverlord-%Y%m%d'),
    ('Engage', 'Unicorn Overlord'),
]

files = [
    'uo_setup/gui.py', 'uo_setup/launcher.py', 'uo_setup/launcher_gui.py', 'uo_setup/updates.py',
    'uo_setup/installed_launcher.py', 'uo_setup/dpi.py', 'uo_setup/game_icon.py', 'uo_setup/launcher_home.py',
    'uo_setup/__init__.py',
]
for name in files:
    path = pathlib.Path(name)
    text = path.read_text(encoding='utf-8')
    original = text
    for old, new in subs:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding='utf-8')
        print('changed', name)
