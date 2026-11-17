"""Read-only NHK license-path audit. Export allowlisted facts, never payloads.

No application code is executed, no process memory is inspected, no HTTP request
is made, and no cookie values, session IDs, license bytes or key bytes are saved.
"""
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json
import re
import struct
import subprocess

from inspect_macho_boundary import MachO
from inspect_tunepat_strings import resolve

BASE = Path.home() / 'Library/Application Support/TunePat VideoGo All-In-One'
RUN = BASE / 'com.tunepat.vidicable.video'
OUT = Path(__file__).resolve().parent
SHIM = 'WidevineCdm/4.10.2934.0/_platform_specific/mac_arm64/libwidevinecdm.dylib'

FUNCTIONS = [
    (SHIM, '_CreateCdmInstance', ['br\tx5']),
    (SHIM, '__ZL11Constructorv', ['APP_NATIVE_PATH', '_dlopen', '_dlsym', 'CreateCdmInstance']),
    ('libdecrypt.dylib', '__ZN30MyContentDecryptionModuleProxy31CreateSessionAndGenerateRequestEjN3cdm11SessionTypeENS0_12InitDataTypeEPKhj',
     ['ParseCustomInitData', 'Sheller6Launch', 'GetShellerByPssh']),
    ('libdecrypt.dylib', '__ZN30MyContentDecryptionModuleProxy19ParseCustomInitDataEPKhjRNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEES9_S9_RN7Sheller10ServerModeER11ConvertMode',
     ['k38InitDataC1', 'ParseFromArray', 'ConvertModeFromString', 'ParseCustomInitData Successed']),
    ('libdecrypt.dylib', '__ZN7CDMHost16OnSessionMessageEPKcjN3cdm11MessageTypeES1_j',
     ['SignedLicenseRequest', 'OnSessionMessage(%p): license request returns empty', 'blr\tx8']),
    ('libdecrypt.dylib', '__ZN30MyContentDecryptionModuleProxy14LicenseRequestERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEE',
     ['LicenseRequestStructC2', 'ShellerManager11GetInstance']),
    ('libdecrypt.dylib', '__ZN14ShellerManager14LicenseRequestERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEEN7Sheller20LicenseRequestStructE',
     ['Sheller14LicenseRequest', 'sheller not ready']),
    ('libdecrypt.dylib', '__ZN7Sheller14LicenseRequestENS_20LicenseRequestStructE',
     ['send licenseRequest %s to shell.', 'SendIpcCommandRpc failed']),
    ('libdecrypt.dylib', '__ZN30MyContentDecryptionModuleProxy13UpdateSessionEjPKcjPKhj',
     ['SignedServiceCertificate', 'SignedLicense', 'license succeeded', 'license failed']),
    ('libdecrypt.dylib', '__ZN7Sheller7LicenseERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEE',
     ['send license %s to shell.', 'SendIpcCommandRpc failed', '"opData"', '"errorCode"', '"keyInfo"']),
    ('wv_shell', '__ZN14CServerManager14LicenseRequestENSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEES6_',
     ['LicenseRequest %s', 'Threadpool']),
    ('wv_shell', '__ZN14CServerManager7LicenseENSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEES6_',
     ['License %s', 'Threadpool']),
    ('wv_shell', '__ZNSt3__120__packaged_task_funcINS_6__bindIRZN14CServerManager14LicenseRequestENS_12basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEEES8_E3$_2JEEENS6_ISB_EEFbvEEclEv',
     ['CDMHost5Reset', 'Proxy7SetHost', 'LicenseRequestResult']),
    ('wv_shell', '__ZNSt3__120__packaged_task_funcINS_6__bindIRZN14CServerManager7LicenseENS_12basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEEES8_E3$_3JEEENS6_ISB_EEFvvEEclEv',
     ['CDMHost15IsKeyChangeSync']),
    ('wv_shell', '__ZN12cdmDecryptor23InitializeDecryptModuleEv',
     ['GetWidevinePath', '_dlopen', '_dlsym', 'CreateCdmInstance']),
]

LOG_FACTS = [
    ('TunePat VideoGo All-In-One.log', 1509, ['[NHKDL]', 'preparePlay'], 'NHK controller prepares the episode playback page'),
    ('TunePat VideoGo All-In-One.log', 1519, ['[BaseVideoParser]', 'hookUrl widevine/getlicense'], 'Common parser observes the NHK license URL'),
    ('TunePat VideoGo All-In-One.log', 1531, ['[BaseVideoParser]', 'hookUrl widevine/getlicense'], 'Common parser again observes the NHK license URL'),
    ('TunePat VideoGo All-In-One.log', 1532, ['[BaseVideoParser]', 'hookUrl widevine/getlicense'], 'Common parser observes the NHK license URL near license response processing'),
    ('native.log', 378, ['ParseCustomInitData Successed'], 'Browser-side bridge successfully parses the custom init data in the NHK job'),
    ('native.log', 385, ['OnSessionMessage type:4'], 'Bridge sees protocol type 4: service certificate request'),
    ('native.log', 394, ['UpdateSession', 'message type: 5'], 'Bridge receives protocol type 5: service certificate'),
    ('native.log', 398, ['OnSessionMessage type:1'], 'Bridge sees protocol type 1: license request'),
    ('native.log', 406, ['send licenseRequest', 'to shell.'], 'Bridge sends license request work to the shell process'),
    ('native2.log', 47, ['LicenseRequest'], 'Shell begins handling license request work'),
    ('native2.log', 67, ['LicenseRequest'], 'Shell reaches the next license request checkpoint'),
    ('native.log', 408, ['host_ OnSessionMessage.'], 'Bridge delivers the prepared message through the browser host callback'),
    ('native.log', 409, ['UpdateSession', 'message type: 2'], 'Bridge receives protocol type 2: license response'),
    ('native.log', 412, ['send license ', 'to shell.'], 'Bridge sends the license response to the shell process'),
    ('native2.log', 68, ['License'], 'Shell receives license-response work'),
    ('native2.log', 72, ['Host::OnSessionKeysChange'], 'Shell host receives the CDM key status notification'),
    ('native2.log', 82, ['LicenseResult'], 'Shell reports a license result'),
    ('native2.log', 83, ['ShellReady'], 'Shell reports readiness for the saved job'),
    ('native.log', 413, ['UpdateSession', 'license succeeded'], 'Browser-side bridge explicitly records successful license processing'),
    ('native.log', 416, ['Host::OnSessionKeysChange'], 'Browser-side host also receives key status notification'),
    ('com.tunepat.vidicable.video.log', 199, ['Convert mediaId:', 'Q4YVTYHXB2'], 'Following conversion belongs to the requested NHK episode'),
]


def main():
    images = {name: MachO(RUN / 'native' / name) for name, _, _ in FUNCTIONS}
    symbols = {}
    for name in images:
        raw = subprocess.run(['/usr/bin/nm', '-an', str(RUN / 'native' / name)],
                             capture_output=True, text=True, check=True).stdout
        symbols[name] = {p[2]: int(p[0], 16) for line in raw.splitlines()
                         if len(p := line.split()) == 3 and re.fullmatch('[0-9a-fA-F]+', p[0])}
    functions = []
    for name, symbol, expected in FUNCTIONS:
        code = subprocess.run(['/usr/bin/objdump', '--macho', '--disassemble', '--dis-symname',
                               symbol, str(RUN / 'native' / name)],
                              capture_output=True, text=True, check=True).stdout
        for term in expected:
            assert term in code, (name, symbol, term)
        start = symbols[name][symbol]
        first, end = images[name].function_at(start)
        assert first == start and end
        offset = images[name].va_to_offset(start)
        functions.append({'file': name, 'symbol': symbol, 'start': hex(start),
                          'end_exclusive': hex(end), 'verified_references': expected,
                          'code_sha256': hashlib.sha256(images[name].data[offset:offset+end-start]).hexdigest()})
    # Constructor loads 16 bytes of the literal and the terminating 'b' separately.
    shim = images[SHIM]
    assert shim.data[shim.va_to_offset(0x3ebc):shim.va_to_offset(0x3ebc)+16] == b'/libdecrypt.dyli'
    assert struct.unpack_from('<I', shim.data, shim.va_to_offset(0x3778))[0] == 0x52800c48

    logs = {name: (BASE / 'Logs' / name).read_text(errors='replace').splitlines()
            for name, _, _, _ in LOG_FACTS}
    facts = []
    for name, number, expected, fact in LOG_FACTS:
        line = logs[name][number-1]
        assert all(term in line for term in expected), (name, number, expected)
        time = re.search(r'\b\d{2}:\d{2}:\d{2}[.:]\d{3}\b', line)
        item = {'file': name, 'line': number, 'time_jst': time[0] if time else None, 'fact': fact}
        if 'hookUrl' in line:
            match = re.search(r'https://[^\s\"<>]+', line)
            url = urlsplit(match[0])
            assert url.hostname == 'licence.hsk.st.nhk' and url.path == '/widevine/license'
            item['endpoint_without_query'] = 'https://licence.hsk.st.nhk/widevine/license'
        facts.append(item)
    states = []
    for number in (78, 79, 80):
        line = logs['native2.log'][number-1]
        status = int(re.search(r'\bstatus:\s*(\d+)', line)[1])
        system_code = int(re.search(r'\bsystem_code:\s*(\d+)', line)[1])
        assert status == 0 and system_code == 0
        states.append({'line': number, 'status': status, 'system_code': system_code})
    received_session = re.search(r'session_id:\s*([^\s.]+)', logs['native.log'][409])[1]
    successful_session = re.search(r'session id:\s*([^\s.]+)', logs['native.log'][412])[1]
    assert received_session == successful_session
    del received_session, successful_session

    preloads = []
    for leaf in ('i.js', 'p.js'):
        code, metadata = resolve(RUN / 'main/preload/nhk' / leaf)
        expected = ['MediaKeySession', 'generateRequest', 'createMediaKeys', 'cdm-create-session',
                    'get-custom-init-data', 'licenseStatus', 'getLicenseStatus', 'setLicenseStatus']
        assert all(term in code for term in expected)
        assert re.search(r'\[\"invoke\"\]\(\"get-custom-init-data\",', code)
        preloads.append({'file': 'main/preload/nhk/' + leaf,
                         'source_sha256': hashlib.sha256((RUN / 'main/preload/nhk' / leaf).read_bytes()).hexdigest(),
                         'resolver': metadata, 'verified_identifiers': expected,
                         'finding': 'Wraps browser EME request generation; CENC init data is passed through an Electron IPC handler before calling the original function.',
                         'limit': 'Resolver is a static navigation aid, not a full scope-aware decompiler.'})
    bytecode = (RUN / 'main/main_arm64.jsc').read_bytes()
    terms = ['get-custom-init-data', 'cdm-create-session', 'hookUrl widevine/getlicense',
             'uploadData', 'onCompleted', 'onBeforeSendHeaders', 'onHeadersReceived',
             'interceptRequests', 'requestHeaders', 'responseHeaders', 'replaceHeaders',
             '/widevine/license', 'window.setLicenseStatus(true)', 'NHKDL', 'NHKVP']
    static_names = [{'name': term, 'first_byte_offset': bytecode.find(term.encode())} for term in terms]
    assert all(row['first_byte_offset'] >= 0 for row in static_names)
    proto = (RUN / 'main/wv_proto2.proto').read_text()
    types = {'LICENSE_REQUEST': 1, 'LICENSE': 2, 'SERVICE_CERTIFICATE_REQUEST': 4, 'SERVICE_CERTIFICATE': 5}
    assert all(re.search(r'\b' + name + r'\s*=\s*' + str(value) + r'\s*;', proto) for name, value in types.items())
    init_schema = (RUN / 'main/init_data.proto').read_text()
    assert all(re.search(r'\b' + name + r'\s*=\s*' + str(value) + r'\s*;', init_schema)
               for name, value in {'mode': 1, 'pssh': 2, 'convert_mode': 3}.items())
    header = (OUT / 'minimal-cdm-trial/include/content_decryption_module.h').read_text()
    assert 'kUsable = 0' in header
    result = {
        'inspection_date_jst': '2026-10-10',
        'scope': 'Installed TunePat 3.1.0 ARM static code and historical NHK job logs only',
        'network_requests_made': 0, 'application_modified': False,
        'privacy': 'No cookies, tokens, session IDs, license bodies, private keys or raw vendor logs exported',
        'native_files': {name: {'sha256': hashlib.sha256(image.data).hexdigest(), 'bytes': len(image.data)}
                         for name, image in images.items()},
        'functions': functions, 'browser_cdm_shim_target': 'native/libdecrypt.dylib',
        'historical_facts': facts,
        'cdm_key_states': {'file': 'native2.log', 'usable_key_count': len(states), 'states_without_key_ids': states},
        'license_response_and_success_same_browser_session': True,
        'protocol_message_types': types,
        'message_type_note': 'These are SignedMessage protobuf types, not the CDM MessageType enum.',
        'preload_findings': preloads,
        'main_bytecode': {'sha256': hashlib.sha256(bytecode).hexdigest(), 'identifiers': static_names,
                          'limit': 'Identifier existence and proximity do not establish every executed JS branch.'},
        'custom_init_schema': ['mode', 'pssh', 'convert_mode'],
        'limits': [
            'The precise NHK HTTP headers, cookies, token derivation and refresh rules were not recovered.',
            'Three hook log entries do not prove that three distinct HTTP requests were sent.',
            'No license payload was compared byte-for-byte across IPC boundaries.',
            'Some native calls are indirect or unresolved statically; successful runtime stage logs provide additional evidence.',
            'The main V8 bytecode handler for custom init data and network interception is not fully decompiled.',
            'Reproducing NHK license acquisition in the standalone prototype has not been tested.',
        ],
    }
    target = OUT / 'tunepat-nhk-license-evidence.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'saved': str(target), 'verified_functions': len(functions),
                      'verified_log_facts': len(facts), 'usable_key_count': len(states),
                      'license_response_and_success_same_browser_session': True,
                      'network_requests_made': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
