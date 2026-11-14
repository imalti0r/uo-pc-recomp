"""Unicorn Overlord inputs; the upstream installer supplies the window and progress model."""
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

from installer.guilogic import Catalog as BaseCatalog, Form as BaseForm, STEP_ORDER
from installer import ini

TITLE = '010054B01AD92000'
FIELDS = ('base', 'update', 'keys', 'firmware', 'dlc1', 'dlc2', 'title_keys', 'xci')
TEXT = {
    'ui.title': 'Unicorn Overlord Setup',
    'ui.intro': 'Choose your game files and an install folder. Setup builds Unicorn Overlord on this PC.\n'
                'A combined cartridge dump (an XCI holding the base game, the 1.05 update and the DLC) can go in the '
                'first field, leaving Base, Update and the DLC fields empty. Files can also be given separately.\n'
                'The first installation can take 20–60 minutes; you can follow each step below.',
    'ui.notice': 'Experimental alpha. The title screen, the opening story, the first battle and the tactical map '
                 'have been tested.\nFull-game completion and long-session stability remain unverified.',
    'ui.base': 'Base game (NSP / XCI)', 'ui.update': 'Update 1.05 (NSP)',
    'ui.firmware': 'Firmware NCA folder', 'ui.dlc1': 'Add-on content 1 (optional)',
    'ui.dlc2': 'Add-on content 2 (optional)', 'ui.title_keys': 'title.keys (optional)',
    'ui.xci': 'Combined XCI (base + update 1.05 + DLC)',
    'ui.pick_xci': 'Choose the combined cartridge dump (XCI)',
    'ui.xci_note': 'The combined XCI covers the base game, the 1.05 update and any DLC it holds; the fields below '
                   'stay empty unless you want to supply a file the XCI lacks.',
    'ui.need_xci': 'Choose the combined XCI or the base game and update.',
    'ui.keys_private': 'Keys are read from the selected files, never copied into the install folder.\n'
                       'Keep your game dumps and keys private. Allow at least 20 GiB free on the install drive.',
    'ui.need_update': 'Choose the Unicorn Overlord 1.05 update.',
    'ui.need_firmware': 'Choose an extracted firmware NCA folder.',
    'ui.missing_firmware': 'The firmware folder does not exist.',
    'ui.done_text': 'Unicorn Overlord is installed in {dest}. Select Play to start. Your original dumps are unchanged.',
    'ui.update_mode': 'This folder contains an unfinished Unicorn Overlord installation. Setup will resume it.\n'
                      'Use the same input files you used before.',
    'ui.update_ready': 'Update mode: this folder already has Unicorn Overlord installed.\n'
                       'Leave every file field empty and click Update. Your saves and settings are kept, and the '
                       'game is not extracted again.',
    'ui.update_start': 'Update',
    'ui.play': 'Play Unicorn Overlord',
    'ui.start': 'Install Unicorn Overlord',
    'setup.failure': '{message}',
    'step.prepare': 'Check and finish',
    'step.compile': 'Compile Unicorn Overlord',
    'phase.extract.merge': 'Verifying and merging the base game and update…',
    'phase.extract.fonts': 'Extracting and verifying shared fonts…',
    'phase.extract.pack': 'Packing game data on the install drive…',
    'phase.lift.work': 'Recompiling ARM64 game code for this PC…',
    'phase.compile.work': 'Compiling the native executable…',
    'phase.prepare.work': 'Checking the build and installing local files…',
}
for name in ('dlc1', 'dlc2', 'title_keys', 'xci'):
    TEXT['ui.missing_' + name] = f'The selected {name.replace("_", ".")} file does not exist.'


TEXT_ZH = {
    'ui.title': '圣兽之王 安装器',
    'ui.intro': '选择你的游戏文件和安装文件夹。安装器会在本机构建《圣兽之王》。\n'
                '合并的卡带导出（一个包含本体、1.05 更新和 DLC 的 XCI）可以放在第一个输入框，'
                '本体、更新和 DLC 字段留空即可。也可以分开提供文件。\n'
                '首次安装可能需要 20–60 分钟；你可以跟随下面的每一步。',
    'ui.notice': '实验性 alpha 版本。标题画面、开场剧情、第一场战斗和战术地图已经测试过。\n'
                 '完整通关和长时间稳定性尚未验证。',
    'ui.base': '本体游戏（NSP / XCI）', 'ui.update': '更新 1.05（NSP）',
    'ui.firmware': '固件 NCA 文件夹', 'ui.dlc1': '追加内容 1（可选）',
    'ui.dlc2': '追加内容 2（可选）', 'ui.title_keys': 'title.keys（可选）',
    'ui.xci': '合并 XCI（本体 + 1.05 更新 + DLC）',
    'ui.pick_xci': '选择合并的卡带导出（XCI）',
    'ui.xci_note': '合并 XCI 覆盖本体、1.05 更新以及其中包含的 DLC；下面的字段保持为空，'
                   '除非你想补充 XCI 里没有的文件。',
    'ui.need_xci': '请选择合并 XCI，或选择本体和更新。',
    'ui.keys_private': '密钥从所选文件就地读取，绝不会复制进安装文件夹。\n'
                       '请妥善保管你的游戏导出和密钥。安装盘至少需要 20 GiB 可用空间。',
    'ui.need_update': '请选择《圣兽之王》1.05 更新。',
    'ui.need_firmware': '请选择已解包的固件 NCA 文件夹。',
    'ui.missing_firmware': '固件文件夹不存在。',
    'ui.done_text': '《圣兽之王》已安装到 {dest}。点击「启动」开始。你的原始导出未被改动。',
    'ui.update_mode': '这个文件夹里有一次未完成的《圣兽之王》安装。安装器会继续它。\n'
                      '请使用与上次相同的输入文件。',
    'ui.update_ready': '更新模式：这个文件夹里已经装好了《圣兽之王》。\n'
                       '把所有文件字段留空并点击「更新」。存档和设置会保留，游戏不会被重新解包。',
    'ui.update_start': '更新',
    'ui.play': '启动《圣兽之王》',
    'ui.start': '安装《圣兽之王》',
}


@dataclass
class Form(BaseForm):
    """The installer's form; FIELDS names the inputs the pipeline reads."""
    base: str = ''
    update: str = ''
    keys: str = ''
    firmware: str = ''
    dlc1: str = ''
    dlc2: str = ''
    title_keys: str = ''
    xci: str = ''

    def problems(self):
        found = []
        combined = bool(self.xci.strip())
        finished = bool(self.dest.strip()) and installed_state(self.dest) == 'done'
        need = ('xci', 'keys', 'firmware') if combined else ('base', 'update', 'keys', 'firmware')
        if not finished:
            for name in need:
                if not getattr(self, name).strip():
                    found.append('ui.need_' + name)
        for name in FIELDS:
            value = getattr(self, name).strip()
            if value and not (Path(value).is_dir() if name == 'firmware' else Path(value).is_file()):
                found.append('ui.missing_' + name)
        if self.lang not in ini.LANGUAGES:
            found.append('ui.need_lang')
        return found

    def command(self, python, root=None, temp=None, verify=False):
        command = [str(python), '-X', 'utf8', '-m', 'uo_setup']
        for name in (*FIELDS, 'lang', 'dest'):
            value = getattr(self, name).strip()
            if value:
                command += ['--' + name.replace('_', '-'), value]
        if root:
            command += ['--root', str(root)]
        return command


def layout(root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    dev = not (root / 'python/python.exe').is_file()
    channel_path = root / 'BUILD_CHANNEL'
    channel = channel_path.read_text().strip() if channel_path.is_file() else 'dev' if dev else 'release'
    return SimpleNamespace(root=root, dev=dev, release=(root / 'VERSION').read_text().strip(),
        channel=channel,
        python=root / '.venv-py314/Scripts/python.exe' if dev else root / 'python/python.exe',
        runtime=root / 'runtime', llvm=root / 'toolchain/engage/llvm' if dev else root / 'llvm',
        deps=root / 'toolchain/engage/runtime/deps' if dev else root / 'runtime/deps',
        ninja=root / 'toolchain/engage/ninja.exe' if dev else root / 'bin/ninja.exe',
        extractor=root / 'toolchain/nx_extract/nx_extract.exe' if dev else root / 'bin/nx_extract/nx_extract.exe',
        xlate=root / 'build/xlate/totk_xlate.exe' if dev else root / 'bin/totk_xlate.exe',
        hostcheck=root / 'build/release/uo-hostcheck.exe' if dev else root / 'bin/hostcheck.exe')


def installed_state(dest):
    """'done' when dest holds a finished install (deploy wrote its state file), else None."""
    path = Path(dest) / 'uo-install.json'
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return 'done' if state.get('done') == list(STEP_ORDER[1:]) else None


def Catalog():
    table = dict(TEXT)
    table.update(TEXT_ZH)
    return BaseCatalog(table)
