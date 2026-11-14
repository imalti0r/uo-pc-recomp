"""Local extraction and compilation; no game executable is distributed with setup."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from installer.events import Reporter
from installer.guilogic import STEP_ORDER
from installer import ini
from .model import Catalog, FIELDS, Form, TITLE, installed_state, layout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from uo_inputs import inspect_inputs

GIB = 1024 ** 3
# The game's RomFS is five CriWare CPK archives (3.4 GiB), which are packed as they are; the rest of the install is
# the executable, its caches and the profile.
DEST_NEED = 20 * GIB


def redact(text):
    return re.sub(r'(?i)[0-9a-f]{32,}', '<hex removed>', text)


def write_json(path, data):
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    os.replace(pending, path)


def validate_destination(dest, inputs, root):
    dest = dest.resolve()
    root = Path(root).resolve()
    from .installed_launcher import installed_game
    own_launcher = installed_game(root) == dest
    if dest == Path(dest.anchor) or dest == root or (root.is_relative_to(dest) and not own_launcher) or dest.is_relative_to(root):
        raise ValueError('Choose a separate install folder outside the setup package.')
    if any(p.resolve() == dest or p.resolve().is_relative_to(dest) or dest.is_relative_to(p.resolve()) for p in inputs):
        raise ValueError('Keep the install folder separate from your input files and firmware.')
    # Resolve existing reparse points before deciding ownership; never recursively delete through one.
    if dest.exists() and any(dest.iterdir()) and installed_state(dest) is None:
        raise ValueError('The install folder is not empty. Choose a new folder or an existing Unicorn Overlord installation.')
    return dest


@contextmanager
def lock_destination(dest):
    path = dest / '.uo-setup.lock'
    with path.open('a+b') as stream:
        stream.seek(0)
        stream.write(b'\0')
        stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Another setup is using this install folder. Close it before retrying.') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def remove_work(path, work):
    """Only erase known temporary children in this install's owned work directory."""
    work = work.resolve()
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Setup work contains a directory link. Choose a fresh install folder.')
    if not path.resolve().is_relative_to(work) or path.resolve() == work:
        raise ValueError('Refusing to remove a path outside setup work.')
    if path.exists():
        for folder, dirs, files in os.walk(path, followlinks=False):
            for name in dirs:
                child = Path(folder) / name
                if child.is_symlink() or (hasattr(child, 'is_junction') and child.is_junction()):
                    raise ValueError('Setup work contains a directory link.')
        shutil.rmtree(path)


class Pipeline:
    def __init__(self, form, paths, reporter):
        self.form, self.paths, self.reporter = form, paths, reporter
        self.dest = Path(form.dest).resolve()
        self.work = self.dest / '.setup-work'
        self.extracted = self.work / 'extracted'
        self.generated = self.work / 'generated'
        self.build = self.work / 'build'
        self.state = None
        self.step = 'check'

    def command(self, arguments, timeout=None):
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        temp = self.work / 'tmp'
        temp.mkdir(exist_ok=True)
        environment = dict(os.environ, TEMP=str(temp), TMP=str(temp))
        if timeout:
            result = subprocess.run(list(map(str, arguments)), cwd=self.paths.root, capture_output=True,
                encoding='utf-8', errors='replace', creationflags=flags, timeout=timeout, env=environment)
            self.reporter.log(redact(result.stdout + result.stderr))
            if result.returncode:
                raise ValueError(f'{self.step.capitalize()} check failed. Open the setup log for details.')
            return
        child = subprocess.Popen(list(map(str, arguments)), cwd=self.paths.root, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, encoding='utf-8', errors='replace', creationflags=flags, env=environment)
        # Child shares setup's process group: the existing GUI cancellation controller stops the whole job.
        failure_reason = None
        try:
            for line in child.stdout:
                self.reporter.log(redact(line.rstrip()))
                if line.startswith('UO_SETUP_ERROR: '):
                    failure_reason = redact(line.removeprefix('UO_SETUP_ERROR: ').strip())
                match = re.search(r'^\[(\d+)/(\d+)\]', line)
                if match:
                    self.reporter.progress(self.step, 'work', done=int(match[1]), total=int(match[2]), unit='objects')
            code = child.wait(timeout=timeout)
            if code:
                if failure_reason:
                    raise ValueError(failure_reason)
                raise ValueError(f'{self.step.capitalize()} failed (exit {code}). Open the setup log for details.')
        finally:
            if child.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    child.kill()
                child.wait()

    def python(self, script, *arguments):
        self.command([self.paths.python, '-X', 'utf8', self.paths.root / script, *arguments])

    def check(self):
        problems = self.form.problems()
        if problems:
            raise ValueError(' '.join(Catalog()(p) for p in problems))
        inputs = [Path(getattr(self.form, n)) for n in FIELDS if getattr(self.form, n)]
        validate_destination(self.dest, inputs, self.paths.root)
        for path in (self.paths.python, self.paths.llvm / 'bin/clang.exe', self.paths.ninja,
                     self.paths.extractor, self.paths.xlate, self.paths.hostcheck):
            if not path.is_file():
                raise ValueError(f'Setup is incomplete: {path.name} is missing. Extract the whole setup ZIP again.')
        host = json.loads(subprocess.check_output([str(self.paths.hostcheck)], creationflags=
            subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, timeout=20))
        if not host.get('x86_64_v3'):
            raise ValueError('This build requires an x86-64-v3 CPU with AVX2 support.')
        if not any(d.get('api', []) >= [1, 3, 0] for d in host.get('devices', [])):
            raise ValueError('A Vulkan 1.3 GPU and driver are required. Update your graphics driver and retry.')
        ancestor = self.dest
        while not ancestor.exists():
            ancestor = ancestor.parent
        free = shutil.disk_usage(ancestor).free
        self.reporter.emit('space', dest=str(self.dest), need=DEST_NEED, free=free, temp=str(self.work),
                           temp_need=0, temp_free=free, same_disk=True)
        if free < DEST_NEED:
            raise ValueError(f'Allow at least {DEST_NEED // GIB} GiB free on the install drive, then retry.')

    def fingerprint(self):
        inputs = {}
        for name in FIELDS:
            value = getattr(self.form, name)
            if value:
                p = Path(value).resolve()
                st = p.stat()
                inputs[name] = [str(p), st.st_size, st.st_mtime_ns]
        return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()

    def extract(self):
        config = self.work / 'inputs.toml'
        lines = ['[input]']
        if self.form.xci.strip():
            lines.append(f'xci = {json.dumps(self.form.xci)}')
        for name in ('base', 'update', 'keys', 'title_keys'):
            value = getattr(self.form, name)
            if value:
                lines.append(f'{name} = {json.dumps(value)}')
        config.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        try:
            # An unfinished extraction is never treated as verified output.
            remove_work(self.extracted, self.work)
            self.reporter.progress('extract', 'merge')
            self.python('tools/extract_uo.py', '--config', config, '--out', self.extracted,
                        '--tool', self.paths.extractor, '--logs', self.work / 'logs')
            self.reporter.progress('extract', 'pack')
            # The runtime reads <romfs>/romfs.bin and index.json (runtime/romfs.c), not a loose tree, so the extracted
            # CPK archives are packed into that image here and the build points at it. The loose tree stays in the
            # work folder and is removed with it.
            self.python('tools/pack_romfs.py', '--source', self.extracted / 'RomFS',
                        '--out', self.extracted / 'packed-romfs')
        finally:
            config.unlink(missing_ok=True)

    def lift_key(self):
        # Everything the generated C depends on: the lifter's sources and the game's modules.
        root = self.paths.root
        digest = hashlib.sha256()
        files = sorted((root / 'recomp').rglob('*.py'))
        files += sorted(p for p in (self.extracted / 'ExeFS').iterdir() if p.is_file())
        for path in files:
            digest.update(path.name.encode() + b"|" + path.read_bytes())
        return digest.hexdigest()

    def lift(self):
        self.reporter.progress('lift', 'work')
        self.python('recomp/gen.py', '--exefs', self.extracted / 'ExeFS', '--out', self.generated,
                    '--modules', 'main,sdk', '--no-callee-saved-trust', '--continuations',
                    '--jobs', min(8, os.cpu_count() or 1))
        self.state['lift_key'] = self.lift_key()

    def compile(self):
        runtime = self.paths.root / 'bin/uo.exe'
        prebuilt = ['--runtime-exe', runtime] if runtime.is_file() else []
        self.python('tools/build_uo.py', '--extracted', self.extracted, '--translator', self.paths.xlate.parent,
                    '--gen', self.generated, '--out', self.build, '--romfs', self.extracted / 'packed-romfs',
                    '--profile', self.dest / 'profile', '--toolchain', self.paths.llvm, '--deps', self.paths.deps,
                    '--ninja', self.paths.ninja, '--runtime', self.paths.runtime, '--jobs', min(8, os.cpu_count() or 1),
                    *prebuilt)

    def prepare(self):
        self.reporter.progress('prepare', 'work')
        self.command([self.build / 'uo.exe', '--selftest'], timeout=120)
        for source, target in ((self.extracted / 'packed-romfs', self.dest / 'romfs'),
                               (self.extracted / 'ExeFS', self.dest / 'exefs')):
            if source.is_dir():
                if target.exists():
                    if target.name in self.state.get('assets', []):
                        continue
                    raise ValueError(f'{target.name} already exists. Use a fresh install folder for a new build.')
                shutil.copytree(source, target) if target.name == 'exefs' else shutil.move(str(source), str(target))
                self.state.setdefault('assets', []).append(target.name)
                write_json(self.dest / 'uo-install.json', self.state)
        for pattern in ('*.dll', 'uo_xlate.*'):
            for source in self.build.glob(pattern):
                self.deploy(source)
        self.deploy(self.build / 'romfs-index.bin')
        # Never replace the player's settings or profile.
        settings = self.dest / 'uo.ini'
        if not settings.exists():
            settings.write_text(ini.set_language('[display]\nrender_scale = 100\n\n[system]\n', self.form.lang),
                                encoding='utf-8')
        launcher = '@echo off\n"%~dp0uo.exe" %*\nif errorlevel 1 pause\n'
        (self.dest / 'Play Unicorn Overlord.cmd').write_text(launcher, encoding='utf-8')
        self.deploy(self.build / 'uo.exe')
        with (self.dest / 'uo.exe').open('rb') as stream:
            self.state['exe_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()

    def deploy(self, source):
        pending = self.dest / (source.name + '.new')
        shutil.copy2(source, pending)
        try:
            os.replace(pending, self.dest / source.name)
        except PermissionError:
            raise ValueError('Close Unicorn Overlord before replacing the game executable, then retry setup.') from None

    def cleanup(self):
        # generated/ stays so an update can skip the lift (see lift_key).
        for path in (self.build / 'obj',):
            remove_work(path, self.work)

    def run(self):
        self.reporter.emit('start', release=self.paths.release, mode='install', dest=str(self.dest),
                           log=str(self.reporter.log_path), steps=list(STEP_ORDER))
        self.reporter.step('check', 'started')
        self.check()
        self.reporter.step('check', 'done')
        self.dest.mkdir(parents=True, exist_ok=True)
        with lock_destination(self.dest):
            old = installed_state(self.dest)
            stamp = self.fingerprint()
            if old and (old.get('release') != self.paths.release) and old.get('done') == list(STEP_ORDER[1:]):
                # Update: the game data is already extracted and deployed, so only lift, compile and prepare rerun.
                self.reuse_exefs()
                # The generated C is kept after an install; it is reused when nothing that feeds the lifter changed.
                cached = old.get('lift_key') == self.lift_key() and (self.generated / 'layout.json').is_file()
                old.update(release=self.paths.release, done=['extract', 'lift'][:2 if cached else 1],
                           updated_from=old.get('release'))
            if old and old.get('updated_from') is None and (old.get('inputs') != stamp or old.get('release') != self.paths.release):
                raise ValueError('Inputs or setup version changed. Choose a fresh install folder; keep your existing profile.')
            self.state = old or {'format': 1, 'title_id': TITLE, 'release': self.paths.release,
                                'lang': self.form.lang, 'inputs': stamp, 'done': []}
            write_json(self.dest / 'uo-install.json', self.state)
            if self.work.is_symlink() or (hasattr(self.work, 'is_junction') and self.work.is_junction()):
                raise ValueError('Setup work is a directory link. Choose a fresh install folder.')
            self.work.mkdir(exist_ok=True)
            self.validate_resume()
            for step in STEP_ORDER[1:]:
                self.step = step
                if step in self.state['done']:
                    self.reporter.step(step, 'skipped', reason='current')
                    continue
                self.reporter.step(step, 'started')
                getattr(self, step)()
                self.state['done'].append(step)
                write_json(self.dest / 'uo-install.json', self.state)
                self.reporter.step(step, 'done')
            if not getattr(self.paths, 'dev', True):
                self.reporter.log('Installing the launcher and setup tools beside the game…')
                from .installed_launcher import install_launcher
                install_launcher(self.paths.root, self.dest)
        self.reporter.emit('end', result='ok', seconds=self.reporter.elapsed())

    def reuse_exefs(self):
        # Cleanup keeps extracted/ExeFS, but restore it from the deployed copy if it is gone.
        if not (self.extracted / 'ExeFS').is_dir():
            shutil.copytree(self.dest / 'exefs', self.extracted / 'ExeFS')

    def validate_resume(self):
        done = self.state.get('done')
        if not isinstance(done, list) or done != list(STEP_ORDER[1:1 + len(done)]):
            raise ValueError('Install state is invalid. Choose a fresh install folder.')
        if 'prepare' in done:
            exe = self.dest / 'uo.exe'
            if not exe.is_file():
                raise ValueError('The installed executable is missing. Choose a fresh install folder.')
            with exe.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != self.state.get('exe_sha256'):
                raise ValueError('The installed executable changed. Choose a fresh install folder.')
            self.check_assets(self.dest / 'romfs', self.dest / 'exefs')
        elif 'extract' in done:
            self.check_assets(self.extracted / 'RomFS', self.extracted / 'ExeFS', allow_deployed=True)
        if 'lift' in done and 'prepare' not in done and not (self.generated / 'layout.json').is_file():
            raise ValueError('Generated build files are missing. Choose a fresh install folder.')
        if 'compile' in done and 'prepare' not in done and not (self.build / 'uo.exe').is_file():
            raise ValueError('Compiled build files are missing. Choose a fresh install folder.')

    def check_assets(self, romfs, exefs, allow_deployed=False):
        if allow_deployed:
            if 'romfs' in self.state.get('assets', []):
                romfs = self.dest / 'romfs'
        try:
            inspect_inputs(exefs, romfs)
        except (OSError, KeyError, ValueError) as error:
            raise ValueError('Installed work is incomplete. Choose a fresh install folder. ' + str(error)) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (*FIELDS, 'dest', 'lang'):
        parser.add_argument('--' + name.replace('_', '-'), default='en-US' if name == 'lang' else '')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    report = Reporter(log_path=Path(tempfile.gettempdir()) / 'Unicorn-Overlord-Setup' / f'setup-{os.getpid()}.log')
    report.catalog.update(Catalog().texts)
    pipeline = Pipeline(Form(**{n: getattr(args, n) for n in (*FIELDS, 'dest', 'lang')}), layout(args.root), report)
    try:
        pipeline.run()
    except KeyboardInterrupt:
        report.emit('end', result='cancelled', seconds=report.elapsed())
        raise SystemExit(130)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report.error('setup.failure', {'message': redact(str(error))}, step=pipeline.step)
        report.emit('end', result='failed', seconds=report.elapsed())
        raise SystemExit(1)


if __name__ == '__main__':
    main()
