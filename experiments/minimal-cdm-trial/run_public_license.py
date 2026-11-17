"""Bounded exchange with the license URL in Shaka's official public demo config.

License messages and session IDs remain in process memory and an anonymous local
socket. Only HTTP status, byte counts, and CDM status events enter the report.
"""
from pathlib import Path
import argparse
import base64
import datetime
import hashlib
import json
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from run_probe import DEFAULT_LIBRARY, HERE

LICENSE_URL = 'https://proxy.uat.widevine.com/proxy'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def receive(channel, size):
    data = bytearray()
    while len(data) < size:
        block = channel.recv(size - len(data))
        if not block:
            raise EOFError
        data.extend(block)
    return bytes(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true',
                        help='Inspect only the first request locally; never construct an HTTP request')
    parser.add_argument('--max-requests', type=int, choices=[1, 2, 3], default=1,
                        help='Hard limit on HTTP attempts; defaults to one, with no retries')
    parser.add_argument('--media', action='store_true',
                        help='Decrypt the prepared public test samples in the same temporary session')
    args = parser.parse_args()
    if args.media:
        from public_cenc_fixture import prepare
        prepare()
    filename = ('offline-' if args.offline else 'public-') + ('media-' if args.media else '') + 'license-result.json'
    if args.media and not args.offline and (HERE / filename).exists():
        raise FileExistsError('This media exchange already has a recorded result; no automatic repeat')
    official_config = (HERE / 'shaka-assets.js').read_text()
    selection = official_config.split("'Angel One (multicodec, multilingual, Widevine)'", 1)[1].split('new ShakaDemoAssetInfo', 1)[0]
    assert LICENSE_URL in selection
    manifest = ET.parse(HERE / 'public-test.mpd')
    pssh = manifest.find('.//{urn:mpeg:cenc:2013}pssh')
    data = base64.b64decode(pssh.text, validate=True)
    (HERE / 'public-test-pssh.bin').write_bytes(data)
    parent, child = socket.socketpair()
    parent.settimeout(3)
    command = [str(HERE / 'license_probe'), str(DEFAULT_LIBRARY),
               str(HERE / 'public-test-pssh.bin'), str(child.fileno())]
    if args.media:
        command.extend([str(HERE / 'public-cenc-fixture.bin'), str(HERE / 'public-clear-samples.bin')])
    process = subprocess.Popen(command,
                               pass_fds=(child.fileno(),), cwd=HERE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child.close()
    exchanges = []
    deadline = time.monotonic() + 55
    transport_error = None
    request_limit_reached = False
    opener = urllib.request.build_opener(NoRedirect())
    try:
        while process.poll() is None and time.monotonic() < deadline:
            try:
                size = struct.unpack('<I', receive(parent, 4))[0]
            except socket.timeout:
                continue
            except EOFError:
                break
            if size > 1024 * 1024:
                raise ValueError('message bound exceeded')
            if len(exchanges) >= args.max_requests:
                request_limit_reached = True
                parent.sendall(struct.pack('<I', 0))
                break
            payload = receive(parent, size)
            entry = {'request_bytes': size}
            if args.offline:
                entry.update(sent=False, standard_certificate_request=(payload == b'\x08\x04'))
                exchanges.append(entry)
                parent.sendall(struct.pack('<I', 0))
                break
            request = urllib.request.Request(LICENSE_URL, data=payload,
                                             headers={'Content-Type': 'application/octet-stream'}, method='POST')
            entry['attempted'] = True
            exchanges.append(entry)
            try:
                with opener.open(request, timeout=15) as response:
                    body = response.read(1024 * 1024 + 1)
                    if len(body) > 1024 * 1024:
                        raise ValueError('response bound exceeded')
                    entry.update(http_status=response.status, response_bytes=len(body))
                parent.sendall(struct.pack('<I', len(body)) + body)
            except urllib.error.HTTPError as exc:
                # Never retain the raw response body or echo server messages.
                entry.update(http_status=exc.code)
                parent.sendall(struct.pack('<I', 0))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        transport_error = type(exc).__name__
    finally:
        parent.close()
        try:
            stdout, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
    events = [json.loads(line[len('CDM_PROBE '):]) for line in stdout.decode(errors='replace').splitlines()
              if line.startswith('CDM_PROBE ')]
    result = {'scope': 'Official Shaka public test asset; no NHK session or account data used',
              'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'offline': args.offline,
              'media_trial': args.media,
              'max_http_attempts': args.max_requests,
              'request_limit_reached': request_limit_reached,
              'license_url': LICENSE_URL, 'library_sha256': hashlib.sha256(DEFAULT_LIBRARY.read_bytes()).hexdigest(),
              'exit_code': process.returncode, 'transport_error_type': transport_error,
              'http_exchanges': exchanges, 'cdm_events': events,
              'usable_license': any(e['event'] == 'usable_key_count' and e['value'] > 0 for e in events)}
    (HERE / filename).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
