"""Record local static evidence for the packet-to-frame boundary; execute no app code."""
from pathlib import Path
import ctypes
import hashlib
import json
import struct
import subprocess

from inspect_macho_boundary import MachO

ROOT = Path.home() / 'Library/Application Support/TunePat VideoGo All-In-One/com.tunepat.vidicable.video/native'
OUT = Path(__file__).resolve().parent
images = {name: MachO(ROOT / name) for name in ['libavformat.59.dylib', 'libavcodec.59.dylib', 'libTranscodeDLL.dylib']}

checks = [
    ('libTranscodeDLL.dylib', 0x534c, 0xaa0003f3, 'ReadHeader retains its first argument'),
    ('libTranscodeDLL.dylib', 0x58d4, 0xaa0103f3, 'ReadPacket retains its second argument as packet output'),
    ('libTranscodeDLL.dylib', 0x5a4c, 0x52800600, 'Packet metadata allocation is 48 bytes'),
    ('libTranscodeDLL.dylib', 0x5aac, 0xf9000260, 'Created AVBufferRef is stored at packet +0x00'),
    ('libTranscodeDLL.dylib', 0x5ad0, 0xf9002a76, 'Auxiliary metadata is stored at packet +0x50'),
    ('libTranscodeDLL.dylib', 0x5b18, 0xb90012df, 'Video metadata pixel format is set to zero'),
    ('libavformat.59.dylib', 0x152cf4, 0xd63f0100, 'Resolved ReadHeader function is called indirectly'),
    ('libavformat.59.dylib', 0x15328c, 0xd63f0100, 'Resolved ReadPacket function is called indirectly'),
    ('libavcodec.59.dylib', 0xeb9628, 0xf9402874, 'Decoder reads packet opaque at +0x50'),
    ('libavcodec.59.dylib', 0xeb9638, 0xb9007428, 'Decoder assigns frame format at +0x74'),
    ('libavcodec.59.dylib', 0xeb9654, 0xf90072a8, 'Decoder stores a retained buffer in frame buf[0]'),
    ('libavcodec.59.dylib', 0xeb9664, 0x0ea00800, 'Decoder swaps metadata height/width word order'),
    ('libavcodec.59.dylib', 0xeb96b8, 0xb9000268, 'Decoder marks a frame as produced'),
]
verified = []
for name, address, expected, fact in checks:
    m = images[name]
    actual = struct.unpack_from('<I', m.data, m.va_to_offset(address))[0]
    if actual != expected:
        raise ValueError(f'Static check differs: {name} {address:#x}: {actual:#x} != {expected:#x}')
    verified.append({'file': name, 'virtual_address': hex(address), 'instruction_word': hex(actual), 'fact': fact})

codec = images['libavcodec.59.dylib']
descriptor = codec.pointer_refs_to_string('media_decoder')
assert descriptor == [0x1751aa0]
callback = codec.u64(descriptor[0] + 0x98)
assert callback == 0xeb9610
assert codec.function_at(callback) == (0xeb9610, 0xeb96ec)
format_image = images['libavformat.59.dylib']
library_table = [format_image.cstring(format_image.u64(0x305ec0 + 8*i)) for i in range(2)]
assert library_table == ['libTranscodeDLL.dylib', 'libTranscodeRecord.dylib']
for address, expected in [(0x2b4921, 'Initialize'), (0x2b492c, 'Deinitialize'), (0x2b4939, 'ReadHeader'), (0x2b4944, 'ReadPacket')]:
    assert format_image.cstring(address) == expected
stubs = subprocess.run(['/usr/bin/objdump', '--macho', '--indirect-symbols', str(codec.path)], capture_output=True, text=True, check=True).stdout
assert any('0x0000000001269e94' in line and '_av_buffer_ref' in line for line in stubs.splitlines())

class FrameMetadataProjection(ctypes.Structure):
    _fields_ = [('owner_holder', ctypes.c_uint64), ('height', ctypes.c_uint32),
                ('width', ctypes.c_uint32), ('pixel_format', ctypes.c_int32),
                ('plane_offsets', ctypes.c_uint32 * 3), ('line_sizes', ctypes.c_uint32 * 3)]

expected_layout = {'owner_holder': 0, 'height': 8, 'width': 12, 'pixel_format': 16,
                   'plane_offsets': 20, 'line_sizes': 32}
assert ctypes.sizeof(FrameMetadataProjection) == 48
for field, offset in expected_layout.items():
    assert getattr(FrameMetadataProjection, field).offset == offset

ranges = {
    'libTranscodeDLL.dylib': [('Initialize', 0x5104), ('Deinitialize', 0x5264),
                            ('ReadHeader', 0x5328), ('ReadPacket', 0x58b4),
                            ('PacketFromPlainBuffer release callback', 0x67b8)],
    'libavformat.59.dylib': [('Custom input initialization/header', 0x152b90),
                           ('Custom input packet callback', 0x153278)],
    'libavcodec.59.dylib': [('media_decoder decode callback', 0xeb9610)],
}
functions = []
for name, entries in ranges.items():
    m = images[name]
    for label, start in entries:
        first, end = m.function_at(start)
        assert first == start and end
        offset = m.va_to_offset(start)
        functions.append({'file': name, 'label': label, 'start': hex(start), 'end_exclusive': hex(end),
                          'sha256': hashlib.sha256(m.data[offset:offset+end-start]).hexdigest()})

result = {
    'date_jst': '2026-10-10',
    'scope': 'Static analysis of installed ARM code only. No private API execution, process attachment, memory read, new download, or application modification.',
    'sources': {n: {'sha256': hashlib.sha256(m.data).hexdigest(), 'bytes': len(m.data)} for n, m in images.items()},
    'verified_instruction_checks': verified,
    'function_ranges': functions,
    'custom_input': {'library_selection_table': library_table, 'resolved_entries': ['Initialize', 'Deinitialize', 'ReadHeader', 'ReadPacket']},
    'media_decoder': {'descriptor': hex(descriptor[0]), 'decode_callback': hex(callback),
                      'retained_buffer_function': 'av_buffer_ref', 'retained_buffer_stub': '0x1269e94'},
    'metadata_projection': {'size': 48, 'offsets': {k: hex(v) for k, v in expected_layout.items()},
                            'caution': 'Analyst-assigned names. Partial observed contract, not an original header or a callable SDK.'},
    'reconstructed_api_shapes': [
        {'name': 'Initialize', 'shape': 'status Initialize()', 'confidence': 'No incoming argument use observed; initializes IPC, reader, convert, and memory pool.'},
        {'name': 'ReadHeader', 'shape': 'status ReadHeader(AVFormatContext *context)', 'confidence': 'First argument is passed to avformat_new_stream; writes streams and codec parameters.'},
        {'name': 'ReadPacket', 'shape': 'status ReadPacket(AVFormatContext *context, AVPacket *out)', 'confidence': 'Caller forwards context and packet; implementation populates packet argument. Context is unused by the observed exported body.'},
        {'name': 'Deinitialize', 'shape': 'status Deinitialize()', 'confidence': 'No incoming argument use observed; requests Convert exit and returns zero.'},
    ],
    'references': [
        'https://ffmpeg.org/doxygen/5.1/packet_8h_source.html',
        'https://ffmpeg.org/doxygen/5.1/frame_8h_source.html',
        'https://ffmpeg.org/doxygen/5.1/codec__internal_8h_source.html',
        'https://ffmpeg.org/doxygen/5.1/pixfmt_8h_source.html',
    ],
    'limitations': [
        'No runtime call tracing for these functions was performed.',
        'No original compressed video or decoded frame pixels were extracted.',
        'The upstream production of MediaDataBuffer, full IPC contract, and DRM internals remain untraced.',
        'Ownership observations apply to this local packet-to-frame boundary; end-to-end zero-copy is not established.',
        'Public FFmpeg 5.1 definitions are comparison material; the bundled custom build is not assumed identical in every field.',
    ],
}
destination = OUT / 'tunepat-frame-boundary-evidence.json'
destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'saved': str(destination), 'verified_instruction_checks': len(verified),
                  'function_ranges': len(functions), 'metadata_projection_size': ctypes.sizeof(FrameMetadataProjection)}, ensure_ascii=False))
