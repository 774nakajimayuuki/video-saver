"""One bounded NHK license-authentication trial using the user's existing session.

Default: offline. --send-once permits one HTTPS POST, with no retry or redirect.
Only z_at is read from TunePat's NHK browser cookie store, in memory. No account
password, refresh token, Cookie header, token value, license body, key ID, or key
value is logged or exported. The installed CDM is referenced, not redistributed.
No video segments are downloaded or decrypted by this trial. The default CDM is
the installed Chrome ARM64 component. The historical TunePat result remains
available with --cdm-source tunepat-legacy and its original one-attempt guard.
"""
from pathlib import Path
import argparse
import base64
import datetime
import hashlib
import http.client
import json
import os
import socket
import sqlite3
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
TRIAL = HERE.parent / 'minimal-cdm-trial'
BASE = Path.home() / 'Library/Application Support/TunePat VideoGo All-In-One'
COOKIE_DB = BASE / 'Partitions/webviewsession/Cookies'
LIBRARY = BASE / 'com.tunepat.vidicable.video/native/sig_files/libwidevinecdm.dylib'
EXPECTED_CDM_HASH = 'c22f00b43796d3d2ffed7643291b68c8f51679111ff709ce3b4476b6997f4a06'
CHROME_LIBRARY = Path('/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/154.0.8037.98/Libraries/WidevineCdm/_platform_specific/mac_arm64/libwidevinecdm.dylib')
CHROME_HASH = 'e1e9f6b0c77f4251542a2f49dda419c85eda88c5d9ef2721d5d594dc195a6e5f'
LICENSE_HOST = 'licence.hsk.st.nhk'
LICENSE_PATH = '/widevine/license'
ENDPOINT = 'https://' + LICENSE_HOST + LICENSE_PATH
LIMIT = 1024 * 1024
EVENT_NAMES = {
    'host_interface_requested', 'initialized', 'session_created_length',
    'promise_resolved', 'promise_rejected', 'system_code', 'error_message_length',
    'session_message_type', 'session_message_length', 'keys_usable', 'key_count',
    'session_closed', 'platform_challenge_unavailable', 'output_protection_unavailable',
    'output_protection_query_unavailable', 'persistent_storage_disabled',
    'storage_id_unavailable', 'usable_key_count', 'license_exchange_count',
    'license_transport_failed', 'finished',
}


def read_token():
    # Exact host + exact cookie name; never enumerate other credential values.
    with sqlite3.connect(COOKIE_DB.as_uri() + '?mode=ro', uri=True) as database:
        rows = database.execute(
            "SELECT value FROM cookies WHERE host_key = '.web.nhk' AND name = 'z_at' AND path = '/'"
        ).fetchall()
    if len(rows) != 1 or not rows[0][0]:
        raise ValueError('viewing_token_unavailable')
    token = rows[0][0]
    if '\r' in token or '\n' in token or len(token) > 16384:
        raise ValueError('invalid_token_format')
    pieces = token.split('.')
    if len(pieces) != 3:
        raise ValueError('invalid_token_format')
    claims = json.loads(base64.urlsafe_b64decode(pieces[1] + '=' * (-len(pieces[1]) % 4)))
    # exp is a local freshness check, not verification of the token's signature.
    if type(claims.get('exp')) not in (int, float) or claims['exp'] <= time.time() + 60:
        raise ValueError('viewing_token_expired')
    return token


def receive(channel, size):
    result = bytearray()
    while len(result) < size:
        chunk = channel.recv(size - len(result))
        if not chunk:
            raise EOFError
        result.extend(chunk)
    return bytes(result)


def safe_events(raw):
    result = []
    for line in raw.decode(errors='replace').splitlines():
        if not line.startswith('CDM_PROBE '):
            continue
        try:
            item = json.loads(line[len('CDM_PROBE '):])
            if set(item) == {'event', 'value'} and item['event'] in EVENT_NAMES and type(item['value']) is int:
                result.append(item)
        except (ValueError, TypeError):
            pass
    return result


def send_once(payload, token, exchange, marker=None):
    # A durable, exclusive marker prevents accidental duplicate submissions,
    # including after a crash or uncertain network outcome.
    marker = marker or HERE / 'nhk-license-attempt.json'
    with marker.open('x') as file:
        json.dump({'endpoint': ENDPOINT, 'max_attempts': 1,
                   'started_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}, file)
        file.flush()
        os.fsync(file.fileno())
    connection = http.client.HTTPSConnection(LICENSE_HOST, timeout=15)
    try:
        exchange['attempted'] = True
        connection.request('POST', LICENSE_PATH, body=payload, headers={
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/octet-stream',
            'Origin': 'https://www.web.nhk',
            'Referer': 'https://www.web.nhk/',
            'User-Agent': 'Mozilla/5.0',
        })
        response = connection.getresponse()
        exchange['http_status'] = response.status
        body = response.read(LIMIT + 1)
        exchange['response_bytes'] = len(body)
        if len(body) > LIMIT:
            raise ValueError('response_too_large')
        # Never follow a redirect or log an error response / response headers.
        return body if response.status == 200 else b''
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-once', action='store_true')
    parser.add_argument('--cdm-source', choices=['chrome', 'tunepat-legacy'], default='chrome')
    args = parser.parse_args()
    chrome = args.cdm_source == 'chrome'
    library = CHROME_LIBRARY if chrome else LIBRARY
    expected_hash = CHROME_HASH if chrome else EXPECTED_CDM_HASH
    marker = HERE / ('chrome-license-attempt.json' if chrome else 'nhk-license-attempt.json')
    if args.send_once and marker.exists():
        print(json.dumps({'stopped': 'prior_attempt_exists', 'new_requests': 0}))
        return 2
    if hashlib.sha256(library.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('cdm_changed')
    pssh = (HERE / 'nhk-init-data.bin').read_bytes()
    assert 32 <= len(pssh) <= 4096 and pssh[4:8] == b'pssh'
    token = read_token()
    parent, child = socket.socketpair()
    parent.settimeout(20)
    env = {key: value for key, value in os.environ.items() if key in ('PATH', 'TMPDIR', 'LANG', 'HOME')}
    process = subprocess.Popen(
        [str(TRIAL / 'license_probe'), str(library), str(HERE / 'nhk-init-data.bin'), str(child.fileno())],
        pass_fds=(child.fileno(),), cwd=HERE, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    child.close()
    exchanges = []
    error = None
    try:
        size = struct.unpack('<I', receive(parent, 4))[0]
        if not 0 < size <= LIMIT:
            raise ValueError('request_too_large')
        payload = receive(parent, size)
        entry = {'attempted': False, 'request_bytes': size,
                 'standard_certificate_request': payload == b'\x08\x04'}
        exchanges.append(entry)
        body = send_once(payload, token, entry, marker) if args.send_once else b''
        token = None
        parent.sendall(struct.pack('<I', len(body)) + body)
        # Closing the only broker connection ensures no second request can be sent.
    except (OSError, ValueError, http.client.HTTPException, EOFError) as exc:
        error = type(exc).__name__
    finally:
        token = None
        parent.close()
        try:
            stdout, _ = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
    events = safe_events(stdout)
    result = {
        'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope': 'NHK ONE Q4YVTYHXB2 license authentication only; no video download or decryption',
        'offline': not args.send_once,
        'license_endpoint': ENDPOINT,
        'auth_source': 'Existing user login in TunePat NHK browser partition; z_at cookie only',
        'auth_header': 'Authorization: Bearer [not retained]',
        'other_explicit_headers': ['Content-Type', 'Origin', 'Referer', 'User-Agent'],
        'cookie_header_sent': False,
        'token_refresh_requests': 0,
        'max_http_attempts': 1,
        'http_exchanges': exchanges,
        'transport_error_type': error,
        'exit_code': process.returncode,
        'cdm_events': events,
        'usable_license': any(e['event'] == 'usable_key_count' and e['value'] > 0 for e in events),
        'cdm_source': args.cdm_source,
        'library_sha256': expected_hash,
        'library_path': str(library),
        'probe_sha256': hashlib.sha256((TRIAL / 'license_probe').read_bytes()).hexdigest(),
        'sensitive_payloads_saved': False,
        'authentication_data_sent_to_nhk': any(e['attempted'] for e in exchanges),
    }
    filename = ('chrome-license-result.json' if chrome else 'nhk-license-result.json') if args.send_once else ('offline-chrome-license-result.json' if chrome else 'offline-license-result.json')
    destination = HERE / filename
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Exception strings from HTTP/database/token handling can contain secrets.
        print(json.dumps({'stopped': type(exc).__name__, 'payloads_logged': False}))
        sys.exit(1)
