"""Strict parser for the selected public Shaka CENC fragments, not arbitrary URLs."""
import hashlib
import json
import struct
from prepare_public_media import boxes, HERE

CONTAINERS = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'mvex', b'moof', b'traf', b'sinf', b'schi', b'edts'}


def walk(data, start=0, end=None):
    for box in boxes(data, start, end):
        kind, _, p, e = box
        yield box
        skip = {b'stsd': 8, b'encv': 78, b'avc1': 78, b'enca': 28, b'mp4a': 28}.get(kind)
        if kind in CONTAINERS:
            yield from walk(data, p, e)
        elif skip is not None:
            yield from walk(data, p + skip, e)


def parse_track(label):
    path = HERE / f'public-{label}-cenc.mp4'
    data = path.read_bytes()
    provenance = next(r for r in json.loads((HERE / 'public-cenc-provenance.json').read_text()) if r['track'] == label)
    assert hashlib.sha256(data).hexdigest() == provenance['sha256']
    all_boxes = list(walk(data))
    def one(kind):
        found = [b for b in all_boxes if b[0] == kind]
        assert len(found) == 1, (kind, len(found))
        return found[0]
    def u32(pos): return struct.unpack_from('>I', data, pos)[0]
    _, _, p, e = one(b'tenc')
    assert data[p:p+4] == b'\0' * 4 and data[p+4:p+7] == b'\0\0\1'
    iv_size = data[p+7]
    assert iv_size in (8, 16) and e == p + 24
    kid = data[p+8:p+24]  # Public key identifier, never the decryption key.
    _, _, p, _ = one(b'schm')
    assert data[p+4:p+8] == b'cenc'
    _, _, p, _ = one(b'mdhd')
    assert data[p] == 0
    timescale = u32(p+12)
    _, moof, _, _ = one(b'moof')
    _, _, p, end = one(b'tfhd')
    flags = u32(p) & 0xffffff
    assert flags & 0x020000 and not flags & 1 and flags & 2
    cursor = p + 8
    sample_description = u32(cursor); cursor += 4
    assert sample_description == 1, 'selected segment must use encrypted sample entry'
    duration = u32(cursor) if flags & 8 else 0
    cursor += 4 if flags & 8 else 0
    size = u32(cursor) if flags & 16 else 0
    cursor += 4 if flags & 16 else 0
    cursor += 4 if flags & 32 else 0
    assert cursor == end
    _, _, p, _ = one(b'tfdt')
    assert data[p] == 0
    timestamp = u32(p+4)
    _, _, p, end = one(b'trun')
    version, flags = data[p], u32(p) & 0xffffff
    assert flags & 1 and not flags & ~0xf05
    count = u32(p+4)
    assert 0 < count <= 500
    offset = moof + struct.unpack_from('>i', data, p+8)[0]
    cursor = p+12
    cursor += 4 if flags & 4 else 0
    samples = []
    for _ in range(count):
        sample_duration = u32(cursor) if flags & 0x100 else duration
        cursor += 4 if flags & 0x100 else 0
        sample_size = u32(cursor) if flags & 0x200 else size
        cursor += 4 if flags & 0x200 else 0
        cursor += 4 if flags & 0x400 else 0
        composition = struct.unpack_from('>i' if version else '>I', data, cursor)[0] if flags & 0x800 else 0
        cursor += 4 if flags & 0x800 else 0
        assert sample_duration > 0 and 0 < sample_size <= 2 * 1024 * 1024
        samples.append({'offset': offset, 'size': sample_size,
                        'timestamp': round((timestamp + composition) * 1_000_000 / timescale)})
        offset += sample_size
        timestamp += sample_duration
    assert cursor == end
    _, _, mdat, mdat_end = one(b'mdat')
    assert samples[0]['offset'] >= mdat and offset <= mdat_end
    _, _, p, end = one(b'senc')
    assert data[p] == 0
    flags = u32(p) & 0xffffff
    assert flags in (0, 2) and u32(p+4) == count
    cursor = p+8
    cipher_bytes = 0
    for sample in samples:
        sample['kid'] = kid
        sample['iv'] = data[cursor:cursor+iv_size]; cursor += iv_size
        subs = []
        if flags & 2:
            n = struct.unpack_from('>H', data, cursor)[0]; cursor += 2
            assert 0 < n <= 256
            for _ in range(n):
                clear, cipher = struct.unpack_from('>HI', data, cursor); cursor += 6
                subs.append((clear, cipher))
            assert sum(a+b for a, b in subs) == sample['size']
        sample['subsamples'] = subs
        cipher_bytes += sum(b for _, b in subs) if subs else sample['size']
    assert cursor == end and cipher_bytes > 0
    template = bytearray(data)
    for kind, s, _, _ in all_boxes:
        if kind in (b'sinf', b'senc', b'saiz', b'saio', b'pssh'):
            template[s+4:s+8] = b'free'
        elif kind in (b'encv', b'enca'):
            template[s+4:s+8] = b'avc1' if kind == b'encv' else b'mp4a'
    return {'label': label, 'data': data, 'template': template, 'samples': samples,
            'cipher_bytes': cipher_bytes}


def prepare():
    tracks = [parse_track(label) for label in ('video', 'audio')]
    count = sum(len(track['samples']) for track in tracks)
    fixture = bytearray(b'CENCTST1' + struct.pack('<I', count))
    report = []
    for track_index, track in enumerate(tracks):
        for sample in track['samples']:
            fixture += struct.pack('<Iq', track_index, sample['timestamp']) + sample['kid']
            fixture += struct.pack('<I', len(sample['iv'])) + sample['iv']
            fixture += struct.pack('<I', len(sample['subsamples']))
            for clear, cipher in sample['subsamples']:
                fixture += struct.pack('<II', clear, cipher)
            fixture += struct.pack('<I', sample['size'])
            fixture += track['data'][sample['offset']:sample['offset']+sample['size']]
        report.append({'track': track['label'], 'samples': len(track['samples']),
                       'encrypted_bytes': track['cipher_bytes'],
                       'first_timestamp_seconds': track['samples'][0]['timestamp'] / 1_000_000})
    (HERE / 'public-cenc-fixture.bin').write_bytes(fixture)
    (HERE / 'public-cenc-fixture-info.json').write_text(json.dumps(report, indent=2) + '\n')
    return tracks


def restore():
    tracks = [parse_track(label) for label in ('video', 'audio')]
    data = (HERE / 'public-clear-samples.bin').read_bytes()
    assert data[:8] == b'CLEARTST'
    assert struct.unpack_from('<I', data, 8)[0] == sum(len(t['samples']) for t in tracks)
    cursor = 12
    result = []
    for track in tracks:
        changed = 0
        for sample in track['samples']:
            size = struct.unpack_from('<I', data, cursor)[0]; cursor += 4
            assert size == sample['size'] and cursor + size <= len(data)
            clear = data[cursor:cursor+size]; cursor += size
            offset = sample['offset']
            encrypted = track['data'][offset:offset+size]
            assert encrypted != clear, 'each selected sample must actually change'
            sub_offset = 0
            for clear_size, cipher_size in sample['subsamples']:
                assert clear[sub_offset:sub_offset+clear_size] == encrypted[sub_offset:sub_offset+clear_size]
                sub_offset += clear_size + cipher_size
            changed += 1
            track['template'][offset:offset+size] = clear
        target = HERE / f"public-{track['label']}-clear.mp4"
        target.write_bytes(track['template'])
        result.append({'track': track['label'], 'changed_samples': changed,
                       'clear_subsamples_unchanged': True, 'file': target.name})
    assert cursor == len(data)
    return result


if __name__ == '__main__':
    prepare()
    print((HERE / 'public-cenc-fixture-info.json').read_text())
