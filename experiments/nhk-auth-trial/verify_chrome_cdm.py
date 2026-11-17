"""Verify the recorded Chrome CDM trial locally; no token reads or network."""
from pathlib import Path
import hashlib
import json
from run_nhk_license import CHROME_LIBRARY, CHROME_HASH, HERE


def main():
    actual_hash = hashlib.sha256(CHROME_LIBRARY.read_bytes()).hexdigest()
    assert actual_hash == CHROME_HASH
    manifest = json.loads((CHROME_LIBRARY.parents[2] / 'manifest.json').read_text())
    assert manifest['version'] == '4.10.3112.0'
    bootstrap = json.loads((HERE.parent / 'minimal-cdm-trial/chrome-cdm-bootstrap-result.json').read_text())
    assert bootstrap['library_sha256'] == actual_hash
    assert len(bootstrap['trials']) == 1 and bootstrap['trials'][0]['initialized']
    offline = json.loads((HERE / 'offline-chrome-license-result.json').read_text())
    assert offline['offline'] and not any(e['attempted'] for e in offline['http_exchanges'])
    assert offline['http_exchanges'][0]['request_bytes'] == 1717
    result = json.loads((HERE / 'chrome-license-result.json').read_text())
    assert result['cdm_source'] == 'chrome' and result['library_sha256'] == actual_hash
    assert result['exit_code'] == 0 and result['usable_license'] is True
    assert result['sensitive_payloads_saved'] is False
    assert len(result['http_exchanges']) == 1
    assert result['http_exchanges'][0]['http_status'] == 200
    assert {'event': 'usable_key_count', 'value': 3} in result['cdm_events']
    assert (HERE / 'chrome-license-attempt.json').exists()
    old = json.loads((HERE / 'nhk-license-result.json').read_text())
    assert old['library_sha256'] != actual_hash and old['usable_license'] is True
    proof = {'cdm_path': str(CHROME_LIBRARY), 'cdm_sha256': actual_hash,
             'cdm_version': manifest['version'], 'interface_version': 10,
             'initialization_verified': True, 'offline_request_generation_verified': True,
             'nhk_license_http_status': 200, 'usable_key_count': 3,
             'http_attempts_in_chrome_trial': 1,
             'historical_tunepat_result_preserved': True,
             'verification_network_requests': 0,
             'remaining_dependencies': ['Existing TunePat NHK login', 'Installed Google Chrome Widevine CDM'],
             'not_verified': ['NHK sample decryption and MP4 output', 'App-owned login and renewal', 'Distribution arrangements']}
    (HERE / 'chrome-cdm-verification.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'verified': True, 'cdm_version': manifest['version'], 'usable_keys': 3, 'new_requests': 0}))


if __name__ == '__main__':
    main()
