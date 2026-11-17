"""Fetch two indexed segments of two fixed official Shaka tracks (clear + encrypted)."""
import argparse
import hashlib
import json
import struct
import urllib.request
import xml.etree.ElementTree as ET
from run_probe import HERE
from run_public_license import NoRedirect

BASE = 'https://storage.googleapis.com/shaka-demo-assets/angel-one-widevine/'
TRACKS = {'video': 'v-0480p-1000k-libx264.mp4', 'audio': 'a-eng-0128k-aac-2c.mp4'}


def boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    cursor = start
    while cursor < end:
        if cursor + 8 > end:
            raise ValueError('truncated box header')
        size, kind = struct.unpack_from('>I4s', data, cursor)
        header = 8
        if size == 1:
            size = struct.unpack_from('>Q', data, cursor + 8)[0]
            header = 16
        elif size == 0:
            size = end - cursor
        if size < header or cursor + size > end:
            raise ValueError('box outside parent')
        yield kind, cursor, cursor + header, cursor + size
        cursor += size


def fetch(url, start, end):
    if url not in {BASE + name for name in TRACKS.values()} or end - start > 8 * 1024 * 1024:
        raise ValueError('request outside fixed public fixture')
    request = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        if response.status != 206 or not response.headers['Content-Range'].startswith(f'bytes {start}-{end}/'):
            raise ValueError('unexpected range response')
        data = response.read(end - start + 2)
    if len(data) != end - start + 1:
        raise ValueError('range length mismatch')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    namespace = {'d': 'urn:mpeg:dash:schema:mpd:2011'}
    manifest = ET.parse(HERE / 'public-test.mpd')
    results = []
    for label, filename in TRACKS.items():
        destination = HERE / f'public-{label}-encrypted.mp4'
        rep = next(r for r in manifest.findall('.//d:Representation', namespace)
                   if r.findtext('d:BaseURL', namespaces=namespace) == filename)
        segment_base = rep.find('d:SegmentBase', namespace)
        _, index_end = map(int, segment_base.attrib['indexRange'].split('-'))
        if not args.download:
            print(json.dumps({'track': label, 'url': BASE + filename, 'initial_range_end': index_end}))
            continue
        previous = destination.read_bytes() if destination.exists() else b''
        if previous:
            old = json.loads((HERE / 'public-media-provenance.json').read_text())
            record = next(item for item in old if item['track'] == label)
            assert record['sha256'] == hashlib.sha256(previous).hexdigest()
        header = previous[:index_end + 1] if previous else fetch(BASE + filename, 0, index_end)
        sidx = next(b for b in boxes(header) if b[0] == b'sidx')
        _, _, p, e = sidx
        version = header[p]
        timescale = struct.unpack_from('>I', header, p + 8)[0]
        if version != 0:
            raise ValueError('only version-zero sidx used by this fixture is supported')
        first_offset = struct.unpack_from('>I', header, p + 16)[0]
        ref_count = struct.unpack_from('>H', header, p + 22)[0]
        refs = [struct.unpack_from('>III', header, p + 24 + i * 12) for i in range(2)]
        if ref_count < 2 or any(size >> 31 or not sap >> 31 for size, _, sap in refs):
            raise ValueError('unsupported segment index')
        ref_size = sum(row[0] for row in refs)
        duration = sum(row[1] for row in refs)
        if duration / timescale > 10:
            raise ValueError('duration bound exceeded')
        start = e + first_offset
        # Preserve absolute offsets and the original sidx for the demuxer.
        if start != len(header):
            raise ValueError('unexpected gap before first fragment')
        begin = len(previous) if previous else start
        stop = start + ref_size
        if begin > stop:
            raise ValueError('existing media exceeds intended extent')
        extra = fetch(BASE + filename, begin, stop - 1) if begin < stop else b''
        media = (previous if previous else header) + extra
        destination.write_bytes(media)
        results.append({'track': label, 'url': BASE + filename, 'file': destination.name,
                        'bytes': len(media), 'sha256': hashlib.sha256(media).hexdigest(),
                        'segment_count': 2, 'duration_seconds': duration / timescale,
                        'acquired_byte_ranges': [[0, index_end], [start, stop - 1]]})
    if args.download:
        (HERE / 'public-media-provenance.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
