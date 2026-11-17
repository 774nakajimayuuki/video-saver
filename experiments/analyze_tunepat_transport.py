"""Read-only structural evidence; exports schema names, never runtime payloads."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

from inspect_macho_boundary import MachO

ROOT = Path.home() / 'Library/Application Support/TunePat VideoGo All-In-One/com.tunepat.vidicable.video/native'
OUT = Path(__file__).resolve().parent


def varint(data, position):
    value = shift = 0
    while True:
        byte = data[position]
        position += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, position
        shift += 7
        if shift > 70:
            raise ValueError('Invalid varint')


def fields(data):
    position = 0
    output = []
    while position < len(data):
        tag, position = varint(data, position)
        if not tag:
            break
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, position = varint(data, position)
        elif wire == 2:
            size, position = varint(data, position)
            if position + size > len(data):
                raise ValueError('Truncated field')
            value = data[position:position + size]
            position += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value = data[position:position + size]
            position += size
        else:
            raise ValueError('Unsupported wire type')
        output.append((field, value))
    return output


def message_schema(data):
    values = fields(data)
    result = {'name': next(v.decode() for k, v in values if k == 1), 'fields': []}
    for number, value in values:
        if number == 2:
            f = dict(fields(value))
            result['fields'].append({'name': f[1].decode(), 'number': f[3], 'type': f[5], 'type_name': f.get(6, b'').decode()})
        elif number == 3:
            result.setdefault('nested', []).append(message_schema(value))
        elif number == 4:
            enum = fields(value)
            entry = {'name': next(v.decode() for k, v in enum if k == 1), 'values': []}
            for k, v in enum:
                if k == 2:
                    e = dict(fields(v))
                    entry['values'].append({'name': e[1].decode(), 'number': e[2]})
            result.setdefault('enums', []).append(entry)
    return result


TARGETS = [
    ('libTranscodeDLL.dylib', '__ZN9transcode7Convert13DecryptSampleEiRNS_15MediaDataBufferES2_', ['br\tx4']),
    ('libTranscodeDLL.dylib', '__ZN9transcode10DecrypterC7DecryptERNS_15MediaDataBufferES2_', ['SetCipherData', 'SendCipherData', 'ProcessOutputData']),
    ('libTranscodeDLL.dylib', '__ZN9transcode10DecrypterC11ReadMappingERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE', ['ParseFromArray', 'MappingClient3Get', 'MediaDataBufferC1']),
    ('libTranscodeDLL.dylib', '__ZN9transcode10DecrypterC17ProcessOutputDataERNS_15MediaDataBufferES2_', ['mutex4lock', 'mutex6unlock']),
    ('libTranscodeDLL.dylib', '__ZNK9transcode11MappingBase11OpenMappingEPKcRiRPc', ['_shm_open', '_mmap']),
    ('wv_shell', '__ZN30MyContentDecryptionModuleProxy13DecryptSampleERKN9transcode10CipherDataE', ['SetInputBuffer', 'PushFrameToCache', 'DecryptedBlockToPlainData']),
    ('wv_shell', '__ZN30MyContentDecryptionModuleProxy21DecryptAndDecodeFrameERKN3cdm13InputBuffer_2EPNS0_10VideoFrameE', ['#0x70', 'blr\tx8']),
    ('wv_shell', '__ZN30MyContentDecryptionModuleProxy16PushFrameToCacheENSt3__110unique_ptrI12MyVideoFrameNS0_14default_deleteIS2_EEEEN3cdm6StatusEb', ['VideoFrameToPlainData', 'SerializeToArray', 'Cache4Push']),
    ('wv_shell', '__ZN30MyContentDecryptionModuleProxy21VideoFrameToPlainDataERNSt3__110unique_ptrI12MyVideoFrameNS0_14default_deleteIS2_EEEE', ['PlainDataC1', 'AP4_ConvertTime', 'PlainData_FrameInfo']),
    ('wv_shell', '__ZN7CDMHost8AllocateEj', ['MappingServer3Get']),
    ('wv_shell', '__ZN12cdmDecryptor23InitializeDecryptModuleEv', ['GetWidevinePath', '_dlopen', '_dlsym', 'CreateCdmInstance']),
    ('wv_shell', '_CreateCdmInstance', ['ContentDecryptionModule_10', 'SetHost']),
]


def main():
    images = {name: MachO(ROOT / name) for name, _, _ in TARGETS}
    symbols = {}
    for name in images:
        result = subprocess.run(['/usr/bin/nm', '-an', str(ROOT / name)], capture_output=True, text=True, check=True)
        symbols[name] = {parts[2]: int(parts[0], 16) for line in result.stdout.splitlines()
                         if len(parts := line.split()) == 3 and re.fullmatch(r'[0-9a-fA-F]+', parts[0])}
    evidence = []
    for name, symbol, expected in TARGETS:
        code = subprocess.run(['/usr/bin/objdump', '--macho', '--disassemble', '--dis-symname', symbol, str(ROOT / name)],
                              capture_output=True, text=True, check=True).stdout
        for term in expected:
            assert term in code, (name, symbol, term)
        image = images[name]
        start = symbols[name][symbol]
        first, end = image.function_at(start)
        assert first == start and end
        offset = image.va_to_offset(start)
        evidence.append({'file': name, 'symbol': symbol, 'start': hex(start), 'end_exclusive': hex(end),
                         'sha256': hashlib.sha256(image.data[offset:offset + end - start]).hexdigest(),
                         'verified_code_references': expected})
    shell = images['wv_shell']
    descriptor_symbol = '__ZL51descriptor_table_protodef_decrypt_5fmessage_2eproto'
    descriptor_address = symbols['wv_shell'][descriptor_symbol]
    offset = shell.va_to_offset(descriptor_address)
    descriptor = fields(shell.data[offset:offset + 5000])
    assert next(v for k, v in descriptor if k == 1) == b'decrypt_message.proto'
    schemas = [message_schema(v) for k, v in descriptor if k == 4]
    selected = [s for s in schemas if s['name'] in ['PlainData', 'DecoderConfig']]
    assert len(selected) == 2
    plain = next(s for s in selected if s['name'] == 'PlainData')
    assert {'width', 'height', 'y', 'u', 'v', 'pts', 'name', 'offset', 'data_size'} <= {f['name'] for f in plain['fields']}
    assert plain['enums'][0]['values'][-1] == {'name': 'Frame', 'number': 2}
    result = {
        'inspection_date_jst': '2026-10-10',
        'scope': 'Static code and embedded schema inspection only; no runtime DRM, license, or frame data extracted.',
        'source_files': {name: {'sha256': hashlib.sha256(m.data).hexdigest(), 'bytes': len(m.data)} for name, m in images.items()},
        'functions': evidence,
        'embedded_schema': {'symbol': descriptor_symbol, 'address': hex(descriptor_address), 'selected_messages': selected},
        'interpretation': [
            'A ContentDecryptionModule_10 proxy calls the CDM video decode interface and packages returned frames.',
            'PlainData carries mapping metadata and frame properties, not the whole pixel buffer as an inline bytes field.',
            'Mapped data transport uses POSIX shared memory and mmap.',
            'The receiver parses PlainData, obtains the mapping, and builds MediaDataBuffer for the packet/frame adapter.',
        ],
        'limits': ['Code paths are statically established; not every branch was observed during the saved job.',
                   'CDM internal algorithms and actual license/session contents were not inspected.'],
    }
    destination = OUT / 'tunepat-decode-transport-evidence.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'saved': str(destination), 'verified_functions': len(evidence), 'schema_messages': [s['name'] for s in selected]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
