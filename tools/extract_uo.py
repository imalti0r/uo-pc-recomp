"""Extract UNICORN OVERLORD 1.05 with the pinned nxnative extractor.

The supplied cartridge holds the base game, the 1.05 update and two empty add-on contents in a single
XCI, so it stands in for all of them. The extractor is told the base title; the update comes along
because the XCI is one container. Unlike the Engage extractor there is no DLC marker to check: both
add-on contents of this game are placeholders that carry no NCA of their own.
"""
import argparse
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

from uo_inputs import ROOT, inspect_inputs

TITLE = '010054b01ad92000'
UPDATE = '010054b01ad92800'
TOOL = ROOT / 'toolchain' / 'nx_extract' / 'nx_extract.exe'
OUT = ROOT / 'build' / 'extracted' / 'uo-v105'
LOGS = ROOT / 'build' / 'uo-v105' / 'logs'


def run_tool(keys, arguments, name, title_keys=None):
    environment = dict(os.environ)
    environment.pop('ENGAGE_TITLE_KEYS', None)
    if title_keys is not None and str(title_keys).strip():
        environment['ENGAGE_TITLE_KEYS'] = str(title_keys)
    result = subprocess.run([str(TOOL), str(keys), *map(str, arguments)],
                            capture_output=True, encoding='utf-8', errors='replace', env=environment)
    # External tools must not persist or display possible key-sized values.
    output = re.sub(r'[0-9a-fA-F]{32,}', '<hex removed>', result.stdout + result.stderr)
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / f'{name}.log').write_text(output, encoding='utf-8')
    if result.returncode:
        raise ValueError(f'{name} failed (exit {result.returncode}); see {LOGS / (name + ".log")}')
    return output


def input_path(data, name, optional=False):
    value = data.get(name)
    if optional and (value is None or isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Missing or invalid input.{name}: set it to a file path')
    path = Path(value)
    if not path.is_file():
        raise ValueError(f'Configured input.{name} file is missing or inaccessible')
    return path


def contents(output):
    return [(kind, title.lower(), int(version))
            for kind, title, version in re.findall(r'^content (\w+) ([0-9a-fA-F]{16}) (\d+)\s*$', output, re.M)]


def plan_inputs(data, keys, title_keys):
    """The base and update packages to read. A combined cartridge dump (input.xci) holds both."""
    xci = input_path(data, 'xci', optional=True)
    if xci:
        found = {(t, k): v for k, t, v in contents(run_tool(keys, ['--contents', xci], 'identity-xci', title_keys))}
        if ('Application', TITLE) not in found:
            raise ValueError(f'The combined XCI does not hold the base game {TITLE.upper()}')
        if ('Patch', UPDATE) not in found:
            raise ValueError(f'The combined XCI does not hold the 1.05 update {UPDATE.upper()}; '
                             'pass the update NSP in the update field instead')
        base = update = xci
    else:
        base, update = input_path(data, 'base'), input_path(data, 'update')
        for label, path, kind, title in (('base', base, 'Application', TITLE), ('update', update, 'Patch', UPDATE)):
            if (kind, title) not in {(k, t) for k, t, _ in contents(run_tool(keys, ['--contents', path], f'identity-{label}', title_keys))}:
                raise ValueError(f'The {label} package is not {kind} {title.upper()}')
    for path in (TOOL, keys, base, update):
        if not path.is_file():
            raise ValueError(f'Missing input or extractor: {path}')
    if title_keys is not None and not title_keys.is_file():
        raise ValueError('Configured title_keys file is missing')
    return base, update


def main():
    global TOOL, OUT, LOGS
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path, default=ROOT / 'config.local.toml')
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--tool', type=Path, default=TOOL)
    parser.add_argument('--logs', type=Path, default=LOGS)
    args = parser.parse_args()
    TOOL, OUT, LOGS = args.tool, args.out, args.logs
    try:
        data = tomllib.loads(args.config.read_text(encoding='utf-8')).get('input')
        if not isinstance(data, dict):
            raise ValueError('Invalid input table: add [input] with keys and xci (or base and update) file paths')
        keys = input_path(data, 'keys')
        title_keys = input_path(data, 'title_keys', optional=True)
        base, update = plan_inputs(data, keys, title_keys)
        if (OUT / 'ExeFS').exists() or (OUT / 'RomFS').exists():
            raise ValueError('ExeFS/RomFS already exist: inspect the previous extraction before retrying')
        OUT.mkdir(parents=True, exist_ok=True)
        print('Extracting base + update with integrity checks; this can take several minutes', flush=True)
        output = run_tool(keys, [base, update, OUT, '--app', TITLE], 'base-update', title_keys)
        if not re.search(r'^display version 1\.05\s*$', output, re.M):
            raise ValueError('Extracted control data did not report version 1.05')
        report = inspect_inputs(OUT / 'ExeFS', OUT / 'RomFS')
        (OUT / 'inputs.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print('Verified UNICORN OVERLORD 1.05 and extracted it.')
    except (OSError, ValueError, KeyError, TypeError) as error:
        message = re.sub(r'[0-9a-fA-F]{32,}', '<hex removed>', str(error)).replace('\n', ' ')
        parser.exit(1, f'UO_SETUP_ERROR: {message}\n')


if __name__ == '__main__':
    main()
