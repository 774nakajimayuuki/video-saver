"""Read-only static call-boundary checks. Never exports raw vendor logs."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

from inspect_macho_boundary import MachO

BASE = Path.home() / 'Library/Application Support/TunePat VideoGo All-In-One'
RUN = BASE / 'com.tunepat.vidicable.video'
ROOT = RUN / 'native'
OUT = Path(__file__).resolve().parent

TARGETS = [
    ('libMediaConvert.dylib', '_Execute', ['Task7AddTask', 'Operator7Execute']),
    ('libMediaConvert.dylib', '_SetUIOperationCallback', ['SetDeviceOperationCallBack']),
    ('libMediaConvert.dylib', '_SetUIOperationResult', ['ResultCallback']),
    ('libMediaConvert.dylib', '__ZN6CDMApp11DoOperationEP4Task', ['"opType"', '"opData"', 'CDMApp9OnConvert']),
    ('libMediaConvert.dylib', '__ZN6CDMApp9OnConvertEP4TaskRKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEEN4Json5ValueE', ['GetMediaHandle', 'SendIpcMessage']),
    ('libMediaConvert.dylib', '__ZN6CDMApp10DoDownloadERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEES8_S8_', ['"Download"', 'CallUiOperationCmd']),
    ('libMediaConvert.dylib', '__ZN19MsgUiProxyInterface18CallUiOperationCmdEPKcS1_NSt3__18functionIFvS1_S1_EEE', ['"Download"', 'blr\tx8']),
    ('libMediaConvert.dylib', '__ZNSt3__110__function6__funcIZN6CDMApp10DoDownloadERKNS_12basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEEESA_SA_E3$_8NS6_ISB_EEFvPKcSE_EEclEOSE_SH_', ['"DownloadResult"', 'MakeMessageForResult', 'SendIpcMessage']),
    ('media_handle', '__ZN12media_handle11MediaHandle9OnConvertENSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES7_', ['Threadpool6commit']),
    ('media_handle', '__ZN12media_handle11MediaHandle7ConvertENSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES7_', ['GetGlobalInfo', 'GetConfig', 'BuildFFmpegCommand', 'RunTranscode', 'BuildInterleaveCommand', 'SendConvertResult']),
    ('media_handle', '__ZN12media_handle11MediaHandle9GetConfigERKN4Json5ValueE', ['"opData.output"', '"opData.input.tracks"', '"hwaccel"', '"videoCodec"']),
    ('media_handle', '__ZN12media_handle11MediaHandle14StartTranscodeER13exec_stream_tRKNSt3__112basic_stringIcNS3_11char_traitsIcEENS3_9allocatorIcEEEESB_NS3_10shared_ptrINS_13ConvertStatusEEE', ['exec_stream_t5start', 'SendIpcCommandSync', 'Send Convert Message']),
    ('libTranscodeDLL.dylib', '__ZN9transcode7Convert9OnConvertENSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEEN4Json5ValueE', ['ParseConvertOpdata', 'MediaDataReader5Start', 'CreateDecrypter', 'Downloader10Initialize']),
    ('libTranscodeDLL.dylib', '__ZN9transcode7Convert18ParseConvertOpdataENSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEEN4Json5ValueE', ['"input"', '"output"', '"input.tracks"', 'ParseTrackInfo']),
    ('libTranscodeDLL.dylib', '__ZN9transcode7Convert14ParseTrackInfoEN4Json5ValueE', ['"headUrl"', '"segmentUrl"', 'AssignDownloadInfo']),
    ('libTranscodeDLL.dylib', '__ZN9transcode10Downloader15RequestDownloadERNS0_20DownloadRequestParamEPNS0_19DownloadResultParamE', ['"Download"', '"reqId"', '"url"', '"path"', '"offset"', '"length"', 'SendIpcCommandRpc', 'SendIpcCommandSync', 'ParseDownloadResult']),
    ('libTranscodeDLL.dylib', '__ZN9transcode10Downloader19ParseDownloadResultERNS0_19DownloadResultParamERKN4Json5ValueE', ['"error"', '"errorCode"', '"data"', '"reqId"', '"path"', '"progress"']),
    ('libTranscodeDLL.dylib', '__ZN9transcode15MediaDataReader19SwitchToNextSegmentEv', ['CheckDownloadStatus', 'ResetSampleTables']),
    ('libTranscodeDLL.dylib', '__ZN9transcode15MediaDataReader17ResetSampleTablesERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE', ['ReadFile', 'GetSampleTables']),
    ('libTranscodeDLL.dylib', '__ZN9transcode15MediaDataReader14ReadNextSampleER10AP4_SampleR14AP4_DataBuffer', ['AP4_Sample8ReadData']),
    ('libTranscodeDLL.dylib', '__ZN9transcode15MediaDataReader10ReadSampleERNS_15MediaDataBufferERjRi', ['ReadNextSample', 'FromAp4Sample', 'SwitchToNextSegment']),
]


def main():
    images = {name: MachO(ROOT / name) for name, _, _ in TARGETS}
    symbols = {}
    for name in images:
        lines = subprocess.run(['/usr/bin/nm', '-an', str(ROOT / name)], check=True,
                               capture_output=True, text=True).stdout.splitlines()
        symbols[name] = {p[2]: int(p[0], 16) for line in lines
                         if len(p := line.split()) == 3 and re.fullmatch('[0-9a-fA-F]+', p[0])}
    evidence = []
    for name, symbol, expected in TARGETS:
        code = subprocess.run(['/usr/bin/objdump', '--macho', '--disassemble', '--dis-symname',
                               symbol, str(ROOT / name)], capture_output=True, text=True, check=True).stdout
        for term in expected:
            assert term in code, (name, symbol, term)
        start = symbols[name][symbol]
        first, end = images[name].function_at(start)
        assert start == first and end
        offset = images[name].va_to_offset(start)
        evidence.append({'file': name, 'symbol': symbol, 'start': hex(start),
                         'end_exclusive': hex(end), 'sha256': hashlib.sha256(
                             images[name].data[offset:offset + end - start]).hexdigest(),
                         'verified_code_references': expected})
    # ADR in OnConvert binds the actual Convert member before thread-pool dispatch.
    handle = images['media_handle']
    adr_address = 0x100015bd0
    raw = handle.data[handle.va_to_offset(adr_address):handle.va_to_offset(adr_address) + 4]
    insn = int.from_bytes(raw, 'little')
    assert insn & 0x9f000000 == 0x10000000
    immediate = (((insn >> 5) & 0x7ffff) << 2) | ((insn >> 29) & 3)
    if immediate & (1 << 20):
        immediate -= 1 << 21
    bound_target = adr_address + immediate
    convert_symbol = '__ZN12media_handle11MediaHandle7ConvertENSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES7_'
    assert bound_target == symbols['media_handle'][convert_symbol]
    bytecode = (RUN / 'main/main_arm64.jsc').read_bytes()
    terms = ['SetUIOperationCallback', 'SetUIOperationResult', '[DownloadUtil]', 'customDownload',
             'downloadUrlToPath', 'downloadUrlToPathCommon', 'downloadWithRetry', 'callNativeDownload']
    static_terms = [{'term': term, 'first_byte_offset': bytecode.find(term.encode())} for term in terms]
    assert all(item['first_byte_offset'] >= 0 for item in static_terms)
    log_checks = [
        ('TunePat VideoGo All-In-One.log', 1545, 'beforeNativeDownload() entry', 'Controller enters native-download preparation'),
        ('TunePat VideoGo All-In-One.log', 1592, 'callNativeDownload() entry', 'Controller enters native download'),
        ('TunePat VideoGo All-In-One.log', 1651, 'download success:', 'Controller reports download success'),
        ('media_handle.log', 23, "ReplaceConvertMessage: config file doesn't exist", 'Optional convert-message replacement file was absent in the observed job'),
        ('transcode.log', 37, 'RequestDownloadAll', 'Native reader requests segment downloads'),
    ]
    events = []
    for name, number, literal, fact in log_checks:
        line = (BASE / 'Logs' / name).read_text(errors='replace').splitlines()[number - 1]
        assert literal in line, (name, number, fact)
        events.append({'file': name, 'line': number, 'fact': fact})
    result = {
        'inspection_date_jst': '2026-10-10',
        'scope': 'Static code, static identifier names, and allowlisted historical log facts only.',
        'source_files': {name: {'sha256': hashlib.sha256(m.data).hexdigest(), 'bytes': len(m.data)}
                         for name, m in images.items()},
        'functions': evidence,
        'threadpool_bound_method': {'instruction': hex(adr_address), 'target': hex(bound_target), 'symbol': convert_symbol},
        'partial_message_fields': {
            'envelope': ['opType', 'opData', 'token'],
            'convert_data': ['mediaId', 'serviceID', 'input.tracks', 'output.path', 'output.cachePath',
                             'output.hwaccel', 'moduleAuthState'],
            'track_download_fields': ['headUrl', 'segmentUrl'],
            'download_request': ['reqId', 'url', 'path', 'convertToken', 'context', 'length', 'offset'],
            'download_result': ['error', 'errorCode', 'data.reqId', 'data.path', 'data.progress'],
            'limitation': 'Selected names read by code; not a complete validated public API or runnable request.',
        },
        'controller_bytecode': {'sha256': hashlib.sha256(bytecode).hexdigest(), 'strings': static_terms,
                                'limitation': 'Identifiers support a download utility, but do not recover its full call graph or exact network backend.'},
        'historical_log_checks': events,
        'limits': ['The JS callback body and network request implementation have not been decompiled.',
                   'No active requests, credentials, authorization data, license payloads, or decrypted media were collected.',
                   'Native interfaces were not invoked; reconstructed fields are not a complete standalone integration.'],
    }
    destination = OUT / 'tunepat-job-download-evidence.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'saved': str(destination), 'verified_functions': len(evidence),
                      'verified_log_facts': len(events), 'bound_method_target': hex(bound_target)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
