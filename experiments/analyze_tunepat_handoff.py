"""Read-only analysis. Export allowlisted facts, never complete vendor log lines."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

OUT = Path(__file__).resolve().parent
BASE = Path.home() / 'Library/Application Support/TunePat VideoGo All-In-One'
RUN = BASE / 'com.tunepat.vidicable.video'
LOG = BASE / 'Logs'

# Only predetermined, non-secret facts are retained. Raw messages can contain
# clipboard contents, device identifiers, or authentication/session material.
checks = [
    ('TunePat VideoGo All-In-One.log', 34, r'load MC result:\s*true', 'MC library loaded successfully'),
    ('TunePat VideoGo All-In-One.log', 35, r'getAllInterfaces', 'Controller obtains native interface references'),
    ('TunePat VideoGo All-In-One.log', 1429, r'notifyPrepareDownloadStart', 'Controller starts download preparation'),
    ('TunePat VideoGo All-In-One.log', 1509, r'\[NHKDL\].*preparePlay', 'NHK download controller prepares playback'),
    ('TunePat VideoGo All-In-One.log', 1599, r'notifyPrepareDownloadEnd ok', 'Controller completes download preparation'),
    ('com.tunepat.vidicable.video.log', 23, r'SetUIOperationCallback', 'Native UI callback is registered'),
    ('com.tunepat.vidicable.video.log', 37, r'ExecuteMediaHandle', 'Bridge launches media_handle'),
    ('com.tunepat.vidicable.video.log', 40, r'mediahandle_21990', 'media_handle PID 21990 connects to IPC'),
    ('com.tunepat.vidicable.video.log', 168, r'PrepareDownload', 'Native bridge handles PrepareDownload'),
    ('com.tunepat.vidicable.video.log', 198, r'do operation:\s*Convert', 'Native bridge executes Convert'),
    ('com.tunepat.vidicable.video.log', 199, r'Convert mediaId:\s*Q4YVTYHXB2', 'Convert job is associated with the requested episode'),
    ('com.tunepat.vidicable.video.log', 203, r'transcode_22700', 'Transcode PID 22700 connects to IPC'),
    ('com.tunepat.vidicable.video.log', 206, r'initialize complete', 'Transcode initialization is acknowledged'),
    ('com.tunepat.vidicable.video.log', 227, r'Convert end', 'Native bridge finishes Convert'),
    ('media_handle.log', 20, r'Convert Mode:\s*C', 'Selected internal conversion mode is C'),
    ('media_handle.log', 21, r'/native/ffmpeg', 'media_handle selects the bundled ffmpeg executable'),
    ('media_handle.log', 30, r'recode\s+1.*hwaccel\s+1', 'Re-encoding and hardware acceleration are enabled'),
    ('media_handle.log', 31, r'-f media.*-c:v media_decoder.*media://file.*h264_videotoolbox.*-q:v 70.*-t 300', 'Custom media input and media_decoder feed H.264 VideoToolbox; quality argument 70, duration limit 300 seconds'),
    ('media_handle.log', 35, r'Input #0, media, from.*media://file', 'FFmpeg opens the proprietary media input'),
    ('media_handle.log', 37, r'rawvideo.*yuv420p.*1920x1080', 'FFmpeg receives raw video described as yuv420p, 1920x1080'),
    ('media_handle.log', 59, r'rawvideo \(media_decoder\).*h264 \(h264_videotoolbox\)', 'Video mapping explicitly decodes custom raw input and re-encodes H.264'),
    ('media_handle.log', 60, r'\(copy\)', 'Audio is copied in this conversion stage'),
    ('media_handle.log', 61, r'mov_text', 'Subtitle mapping targets mov_text'),
    ('media_handle.log', 68, r'Lavc59\.38\.100 h264_videotoolbox', 'Output encoder matches the measured saved file'),
    ('media_handle.log', 74, r'Transcode Over:\s*Q4YVTYHXB2\s+0', 'Episode transcode finishes with reported status 0'),
    ('media_handle.log', 150, r'Stream #0:0 -> #0:0 \(copy\)', 'Subsequent packaging step copies the already encoded video'),
    ('transcode.log', 1, r'Transcode start', 'Transcode process starts'),
    ('transcode.log', 9, r'Initialize', 'Transcode process initializes IPC'),
    ('transcode.log', 12, r'SetConvertMode:\s*C', 'Transcode process receives mode C'),
    ('transcode.log', 14, r'CreateDecrypter', 'Transcode enters the decrypter setup path'),
    ('transcode.log', 16, r'MappingServer', 'Transcode initializes a mapped-data transport component'),
    ('transcode.log', 26, r'Download', 'Transcode begins downloading'),
    ('transcode.log', 37, r'RequestDownloadAll', 'Segment download requests begin'),
    ('transcode.log', 41, r'Read Header start', 'Custom input begins reading stream headers'),
    ('transcode.log', 105, r'ReadHeader', 'Custom input finishes reading stream headers'),
    ('native2.log', 1, r'wv_shell', 'wv_shell process is present during this job'),
    ('native2.log', 83, r'ShellReady', 'wv_shell signals readiness'),
    ('native2.log', 101, r'Cipher Data input end', 'wv_shell logs completion of encrypted input'),
    ('native2.log', 102, r'Plain Data output end', 'wv_shell logs completion of plain output'),
]

files = {}
events = []
failures = []
for name, number, pattern, statement in checks:
    if name not in files:
        raw = (LOG / name).read_bytes()
        files[name] = {
            'lines': raw.decode(errors='replace').splitlines(),
            'sha256': hashlib.sha256(raw).hexdigest(),
            'bytes': len(raw),
        }
    lines = files[name]['lines']
    line = lines[number - 1] if number <= len(lines) else ''
    if not re.search(pattern, line):
        failures.append({'file': name, 'line': number, 'expected_fact': statement})
        continue
    t = re.search(r'20:\d{2}:\d{2}[.:]\d{3}', line)
    events.append({'file': name, 'line': number, 'time_jst': t.group(0) if t else None, 'fact': statement})

bytecode_path = RUN / 'main/main_arm64.jsc'
bytecode = bytecode_path.read_bytes()
terms = ['koffi', 'nativeLib', 'apiPointers', 'nativeCallback', 'ffiMap', 'ffiId',
         'getAllInterfaces', 'initializeCallback', 'uiOperationCallback', 'executeCallback',
         'QueryInterface', 'AbortExecute', 'SetUIOperationCallback', 'SetUIOperationResult',
         'libMediaConvert.dylib', 'executeTool']
static_terms = [{'term': t, 'first_byte_offset': bytecode.find(t.encode())} for t in terms]
library = RUN / 'native/libMediaConvert.dylib'
symbols = subprocess.run(['/usr/bin/nm', '-gUj', str(library)], check=True, capture_output=True, text=True).stdout.splitlines()
wanted = {'_Initialize', '_DeInitialize', '_QueryInterface', '_Execute',
          '_AbortExecute', '_SetUIOperationCallback', '_SetUIOperationResult'}
custom_library = RUN / 'native/libTranscodeDLL.dylib'
custom_symbols = subprocess.run(['/usr/bin/nm', '-gUj', str(custom_library)], check=True, capture_output=True, text=True).stdout.splitlines()
custom_wanted = {'_Initialize', '_Deinitialize', '_ReadHeader', '_ReadPacket'}
input_evidence = []
for name, names in {
    'libavformat.59.dylib': ['libTranscodeDLL.dylib', 'libTranscodeRecord.dylib', 'Initialize', 'Deinitialize', 'ReadHeader', 'ReadPacket'],
    'libavcodec.59.dylib': ['media_decoder'],
}.items():
    data = (RUN / 'native' / name).read_bytes()
    input_evidence.append({'file': 'native/' + name, 'sha256': hashlib.sha256(data).hexdigest(),
                           'strings': [{'term': t, 'present': t.encode() in data} for t in names]})

result = {
    'inspection_date_jst': '2026-10-10',
    'product': 'TunePat VideoGo All-In-One 3.1.0 ARM',
    'episode_id': 'Q4YVTYHXB2',
    'scope': 'Existing logs and static code metadata only. No additional download or application modification.',
    'privacy': 'Only predetermined facts exported. No raw log messages, clipboard text, credentials, license/session payloads, or device identifiers.',
    'sources': {n: {k: v for k, v in d.items() if k != 'lines'} for n, d in files.items()},
    'events': events,
    'static_evidence': {
        'file': 'main/main_arm64.jsc',
        'sha256': hashlib.sha256(bytecode).hexdigest(),
        'strings': static_terms,
        'library': 'native/libMediaConvert.dylib',
        'exports': [s for s in symbols if s in wanted],
        'limitation': 'Strings and exports do not recover the complete call graph or argument ABI.',
    },
    'custom_input_boundary': {
        'library': 'native/libTranscodeDLL.dylib',
        'sha256': hashlib.sha256(custom_library.read_bytes()).hexdigest(),
        'exports': [s for s in custom_symbols if s in custom_wanted],
        'references': input_evidence,
        'confidence': 'Strong static evidence, consistent with ReadHeader log events; individual dynamic symbol calls were not traced.',
    },
    'unmatched_checks': failures,
}
destination = OUT / 'tunepat-handoff-evidence.json'
destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'saved': str(destination), 'verified_events': len(events), 'unmatched_checks': failures}, ensure_ascii=False, indent=2))
