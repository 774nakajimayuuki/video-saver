"""Select one indexed public test segment beyond the clear lead."""
import hashlib
import json
import struct
from prepare_public_media import BASE, TRACKS, boxes, fetch, HERE


def main():
    result = []
    for label, name in TRACKS.items():
        data = (HERE / f'public-{label}-encrypted.mp4').read_bytes()
        sidx = next(b for b in boxes(data) if b[0] == b'sidx')
        _, start, p, end = sidx
        timescale = struct.unpack_from('>I', data, p + 8)[0]
        first_offset = struct.unpack_from('>I', data, p + 16)[0]
        refs = [struct.unpack_from('>III', data, p + 24 + i * 12) for i in range(4)]
        assert all(size >> 31 == 0 for size, _, _ in refs)
        segment_start = end + first_offset + sum(r[0] for r in refs[:3])
        size = refs[3][0]
        destination = HERE / f'public-{label}-cenc.mp4'
        if destination.exists():
            raise FileExistsError(destination.name)
        fragment = fetch(BASE + name, segment_start, segment_start + size - 1)
        header = bytearray(data[:end])
        # Index refers to omitted segments. Keep offsets stable, ignore the index.
        header[start+4:start+8] = b'free'
        combined = header + fragment
        destination.write_bytes(combined)
        result.append({'track': label, 'url': BASE + name, 'file': destination.name,
                       'range': [segment_start, segment_start + size - 1],
                       'source_start_seconds': sum(r[1] for r in refs[:3]) / timescale,
                       'duration_seconds': refs[3][1] / timescale,
                       'bytes': len(combined), 'sha256': hashlib.sha256(combined).hexdigest()})
    (HERE / 'public-cenc-provenance.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
