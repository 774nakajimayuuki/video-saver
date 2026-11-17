"""Make two seconds of locally generated video; never downloads media."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess

HERE = Path(__file__).resolve().parent
FFMPEG = '/opt/homebrew/bin/ffmpeg'
FFPROBE = '/opt/homebrew/bin/ffprobe'


def main():
    target = HERE / 'synthetic-source.mp4'
    if not target.exists():
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24', '-t', '2',
                        '-an', '-c:v', 'libx264', '-profile:v', 'baseline', '-preset', 'fast',
                        '-pix_fmt', 'yuv420p', '-bf', '0', '-g', '24', str(target)], check=True)
    data = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'v:0',
                                      '-show_streams', '-show_packets', '-show_data', '-of', 'json',
                                      str(target)], capture_output=True, text=True, check=True).stdout)
    stream = data['streams'][0]
    extra = b''.join(bytes.fromhex(line.split(':', 1)[1].split('  ')[0].strip())
                     for line in stream['extradata'].splitlines() if ':' in line)
    assert len(extra) == stream['extradata_size']
    source = target.read_bytes()
    content = bytearray(b'CDMPROBE')
    # Public CDM enums: H264=2, H264 baseline=2 (1 is kProfileNotNeeded).
    content.extend(struct.pack('<IIIII', stream['width'], stream['height'], 2, 2, len(extra)))
    content.extend(extra)
    content.extend(struct.pack('<I', len(data['packets'])))
    for packet in data['packets']:
        size, offset = int(packet['size']), int(packet['pos'])
        payload = source[offset:offset + size]
        assert len(payload) == size
        content.extend(struct.pack('<qI', round(float(packet['pts_time']) * 1_000_000), size))
        content.extend(payload)
    (HERE / 'h264-fixture.bin').write_bytes(content)
    annexb = HERE / 'synthetic-source.h264'
    if not annexb.exists():
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                        '-i', str(target), '-c:v', 'copy', '-bsf:v', 'h264_mp4toannexb',
                        '-f', 'h264', str(annexb)], check=True)
    annex = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_packets', '-show_streams',
                                      '-show_data', '-of', 'json', str(annexb)],
                                     capture_output=True, text=True, check=True).stdout)
    annex_extra = b''.join(bytes.fromhex(line.split(':', 1)[1].split('  ')[0].strip())
                           for line in annex['streams'][0]['extradata'].splitlines() if ':' in line)
    annex_bytes = bytearray(b'CDMPROBE')
    annex_bytes.extend(struct.pack('<IIIII', stream['width'], stream['height'], 2, 2, len(annex_extra)))
    annex_bytes.extend(annex_extra)
    annex_bytes.extend(struct.pack('<I', len(annex['packets'])))
    annex_source = annexb.read_bytes()
    for index, packet in enumerate(annex['packets']):
        size, offset = int(packet['size']), int(packet['pos'])
        annex_bytes.extend(struct.pack('<qI', round(index * 1_000_000 / 24), size))
        annex_bytes.extend(annex_source[offset:offset + size])
    (HERE / 'h264-annexb-fixture.bin').write_bytes(annex_bytes)
    metadata = {'source': 'FFmpeg locally generated testsrc2', 'duration_seconds': 2,
                'width': stream['width'], 'height': stream['height'], 'frames': len(data['packets']),
                'codec': stream['codec_name'], 'profile': stream['profile'], 'encrypted': False,
                'fixture_sha256': hashlib.sha256(content).hexdigest(),
                'annexb_fixture_sha256': hashlib.sha256(annex_bytes).hexdigest()}
    (HERE / 'fixture-info.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
