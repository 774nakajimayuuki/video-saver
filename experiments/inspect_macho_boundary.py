"""Minimal, read-only Mach-O metadata reader for local static analysis."""
from pathlib import Path
import bisect
import struct


class MachO:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        if struct.unpack_from('<I', self.data)[0] != 0xFEEDFACF:
            raise ValueError('Expected a thin little-endian 64-bit Mach-O file')
        self.sections = []
        self.segments = []
        self.starts = []
        offset = 32
        functions = None
        for _ in range(struct.unpack_from('<I', self.data, 16)[0]):
            command, size = struct.unpack_from('<II', self.data, offset)
            if command == 0x19:
                name = self.data[offset + 8:offset + 24].split(b'\0')[0].decode()
                va, vs, fo, fs = struct.unpack_from('<QQQQ', self.data, offset + 24)
                self.segments.append(dict(name=name, addr=va, size=vs, offset=fo, file_size=fs))
                nsects = struct.unpack_from('<I', self.data, offset + 64)[0]
                for j in range(nsects):
                    p = offset + 72 + j * 80
                    sect = self.data[p:p + 16].split(b'\0')[0].decode()
                    seg = self.data[p + 16:p + 32].split(b'\0')[0].decode()
                    addr, length, off = struct.unpack_from('<QQI', self.data, p + 32)
                    self.sections.append(dict(segment=seg, name=sect, addr=addr, size=length, offset=off))
            elif command == 0x26:
                functions = struct.unpack_from('<II', self.data, offset + 8)
            offset += size
        if functions:
            off, length = functions
            current = next(s['addr'] for s in self.segments if s['name'] == '__TEXT')
            end = off + length
            while off < end:
                shift = value = 0
                while True:
                    byte = self.data[off]
                    off += 1
                    value |= (byte & 127) << shift
                    if not byte & 128:
                        break
                    shift += 7
                if value == 0:
                    break
                current += value
                self.starts.append(current)

    def offset_to_va(self, offset):
        for s in self.segments:
            if s['offset'] <= offset < s['offset'] + s['file_size']:
                return s['addr'] + offset - s['offset']
        raise ValueError('Offset has no mapped segment')

    def va_to_offset(self, address):
        for s in self.segments:
            if s['addr'] <= address < s['addr'] + s['file_size']:
                return s['offset'] + address - s['addr']
        raise ValueError('Address has no file-backed segment')

    def section_at(self, address):
        return next((s for s in self.sections if s['addr'] <= address < s['addr'] + s['size']), None)

    def function_at(self, address):
        i = bisect.bisect_right(self.starts, address) - 1
        if i < 0:
            return None
        return self.starts[i], self.starts[i + 1] if i + 1 < len(self.starts) else None

    def cstring(self, address, max_bytes=160):
        sec = self.section_at(address)
        if not sec or sec['name'] != '__cstring':
            return None
        p = self.va_to_offset(address)
        b = self.data[p:p + max_bytes].split(b'\0')[0]
        return b.decode(errors='replace')

    def pointer_refs_to_string(self, text):
        p = self.data.find(text.encode() + b'\0')
        if p < 0:
            return []
        address = self.offset_to_va(p)
        needle = struct.pack('<Q', address)
        refs = []
        pos = 0
        while True:
            pos = self.data.find(needle, pos)
            if pos < 0:
                break
            refs.append(self.offset_to_va(pos))
            pos += 1
        return refs

    def u64(self, address):
        return struct.unpack_from('<Q', self.data, self.va_to_offset(address))[0]
