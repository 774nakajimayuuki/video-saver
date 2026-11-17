from pathlib import Path
import argparse
import hashlib
import json
import subprocess
from run_probe import DEFAULT_LIBRARY, HERE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', default='h264-annexb-fixture.bin')
    parser.add_argument('--label', default='annexb')
    parser.add_argument('--cenc-config', action='store_true')
    args = parser.parse_args()
    if not args.label.replace('-', '').isalnum():
        parser.error('Label must contain letters, numbers, or hyphens only')
    fixture = (HERE / args.fixture).resolve(strict=True)
    raw = HERE / (args.label + '-frames.yuv')
    if raw.exists():
        parser.error('Output already exists; choose a new label')
    command = [str(HERE / 'decode_probe'), str(DEFAULT_LIBRARY), str(fixture), str(raw)]
    if args.cenc_config:
        command.append('cenc-config')
    try:
        child = subprocess.run(command, cwd=HERE, capture_output=True, timeout=20)
        stdout, code, timeout = child.stdout, child.returncode, False
    except subprocess.TimeoutExpired as exc:
        stdout, code, timeout = exc.stdout or b'', None, True
    events = [json.loads(line[len('CDM_PROBE '):]) for line in stdout.decode(errors='replace').splitlines()
              if line.startswith('CDM_PROBE ')]
    summary = {'fixture': fixture.name, 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
               'library_sha256': hashlib.sha256(DEFAULT_LIBRARY.read_bytes()).hexdigest(),
               'source_sha256': hashlib.sha256((HERE / 'decode_probe.cpp').read_bytes()).hexdigest(),
               'host_sha256': hashlib.sha256((HERE / 'cdm_probe.cpp').read_bytes()).hexdigest(),
               'cenc_decoder_config': args.cenc_config, 'input_is_encrypted': False,
               'exit_code': code, 'timeout': timeout, 'events': events,
               'raw_output_bytes': raw.stat().st_size if raw.exists() else 0}
    result = HERE / (args.label + '-result.json')
    result.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
