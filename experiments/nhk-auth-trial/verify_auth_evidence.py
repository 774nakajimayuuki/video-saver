"""Local verification only. Never reads token values or makes network requests."""
from pathlib import Path
import hashlib
import json
import sqlite3

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / 'public-scripts'
STATIC_CHECKS = {
    '04-atl6h9qw66.js': [
        'authZ:{accessToken:"z_at",type:"authz_type"}',
        'authN:{accessToken:"n_at"}',
        'getAuthZAccessToken(){return U(this,u).get(s.authZ.accessToken)}',
        'let e=this.cookie.getAuthZAccessToken()??""',
        'accountAuthZ:{accessToken:t===n.accountAuthZ?e:""}',
        'getTokenBFFEndpoint', 'token/refresh', 'credentials:"include"',
    ],
    '41xs654-03lni.js': [
        'e.accountAuthZ.accessToken||e.accountlessAuthZ.accessToken||null',
        't&&e&&e.updateDRMToken(t)',
        'widevineLicenseUrl:"https://licence.hsk.st.nhk/widevine/license"',
        'onShouldUpdateDRMToken', 'handleRefreshToken',
    ],
    '1a0_75ftee6_a.js': ['749433', 'static/chunks/1dq-6-f7-bazf.js', '892553'],
    '1dq-6-f7-bazf.js': [
        'updateDRMToken(e){this.playerCore.setDRMToken(e)}',
        'setDRMToken(e){this.drmToken=e}',
        'licenseXhrSetup:(e,t)=>{this.drmToken&&(e.open("POST",t,!0),e.setRequestHeader("Authorization",`Bearer ${this.drmToken}`))}',
        'this.setupLicenseXHR(n,a,e,t).then', 't.send(i)',
    ],
}


def main():
    proof = []
    for filename, terms in STATIC_CHECKS.items():
        data = (SCRIPTS / filename).read_bytes()
        source = data.decode()
        assert all(term in source for term in terms), filename
        proof.append({'file': filename, 'sha256': hashlib.sha256(data).hexdigest(),
                      'matched_identifiers': terms})
    cookie_path = Path.home() / 'Library/Application Support/TunePat VideoGo All-In-One/Partitions/webviewsession/Cookies'
    with sqlite3.connect(cookie_path.as_uri() + '?mode=ro', uri=True) as database:
        # Metadata only. Do not SELECT value or encrypted_value here.
        rows = database.execute(
            "SELECT name, host_key, is_secure, is_httponly FROM cookies WHERE host_key = '.web.nhk' AND name IN ('z_at','n_at','bff-rt-authn','bff-rt-authz')"
        ).fetchall()
    metadata = [dict(zip(('name', 'domain', 'secure', 'http_only'), row)) for row in rows]
    assert any(row['name'] == 'z_at' and row['http_only'] == 0 for row in metadata)
    result = json.loads((HERE / 'nhk-license-result.json').read_text())
    assert result['offline'] is False and result['exit_code'] == 0 and result['usable_license'] is True
    assert len(result['http_exchanges']) == 1
    exchange = result['http_exchanges'][0]
    assert exchange['attempted'] is True and exchange['http_status'] == 200
    assert exchange['request_bytes'] == 1717 and exchange['response_bytes'] == 1063
    assert {'event': 'usable_key_count', 'value': 3} in result['cdm_events']
    assert result['cookie_header_sent'] is False and result['token_refresh_requests'] == 0
    assert (HERE / 'nhk-license-attempt.json').exists()
    saved = {
        'static_checks': proof,
        'cookie_metadata_only': metadata,
        'actual_trial': {'http_attempts': 1, 'http_status': 200, 'usable_key_count': 3, 'cdm_exit_code': 0},
        'limits': [
            'Static source shows NHK player behavior, not a full decompilation of TunePat main-process interception.',
            'Trial used a currently valid existing viewing token; token renewal was not exercised.',
            'Trial reused an installed vendor CDM and existing TunePat login; app-owned browser login is not implemented.',
            'No video sample decryption or MP4 output was attempted in this NHK authentication trial.',
            'Request headers are sufficient for this trial; minimum necessary headers and other account/device conditions were not tested.',
        ],
        'verification_network_requests': 0,
    }
    (HERE / 'auth-evidence.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'verified_static_files': len(proof), 'verified_http_attempts': 1,
                      'verified_usable_keys': 3, 'verification_network_requests': 0}))


if __name__ == '__main__':
    main()
