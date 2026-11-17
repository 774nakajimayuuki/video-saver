"""Run each public CDM interface in a bounded child process; export safe events only."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import time

HERE = Path(__file__).resolve().parent
DEFAULT_LIBRARY = (Path.home() / 'Library/Application Support/TunePat VideoGo All-In-One'
                   '/com.tunepat.vidicable.video/native/sig_files/libwidevinecdm.dylib')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument('--versions', nargs='+', choices=['10', '11', '12'], default=['10', '11', '12'])
    parser.add_argument('--output', type=Path, default=HERE / 'bootstrap-result.json')
    args = parser.parse_args()
    library = args.library.resolve(strict=True)
    result = {
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
        'source_sha256': hashlib.sha256((HERE / 'cdm_probe.cpp').read_bytes()).hexdigest(),
        'scope': 'Public CDM load/create/initialize/destroy only. No license requests or media download.',
        'host_features': {'persistent_state': False, 'distinctive_identifier': False,
                          'hardware_secure_codecs': False, 'platform_challenge': 'unavailable',
                          'output_protection_status': 'unavailable'},
        'trials': [],
    }
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'TMPDIR', 'LANG', 'HOME')}
    # Our host does not open user profiles or implement persistent CDM FileIO.
    for version in args.versions:
        started = time.monotonic()
        try:
            child = subprocess.run([str(HERE / 'cdm_probe'), str(library), version],
                                   cwd=HERE, env=env, capture_output=True, timeout=20)
            stdout = child.stdout
            trial = {'interface_version': int(version), 'exit_code': child.returncode, 'timeout': False}
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b''
            trial = {'interface_version': int(version), 'exit_code': None, 'timeout': True}
        events = []
        for line in stdout.decode(errors='replace').splitlines():
            if line.startswith('CDM_PROBE '):
                try:
                    item = json.loads(line[len('CDM_PROBE '):])
                    if set(item) == {'event', 'value'} and isinstance(item['value'], int):
                        events.append(item)
                except (ValueError, TypeError):
                    pass
        trial['events'] = events
        trial['elapsed_seconds'] = round(time.monotonic() - started, 3)
        trial['initialized'] = any(e == {'event': 'initialized', 'value': 1} for e in events)
        result['trials'].append(trial)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(trial, ensure_ascii=False), flush=True)
    print(json.dumps({'saved': str(args.output), 'any_initialized': any(t['initialized'] for t in result['trials'])}))


if __name__ == '__main__':
    main()
