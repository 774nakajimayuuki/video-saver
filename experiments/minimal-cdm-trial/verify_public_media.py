"""Offline reconstruction, lossless remux, and independent decode of public test media."""
import hashlib
import json
import math
import struct
import subprocess
from public_cenc_fixture import restore
from run_probe import HERE

FFMPEG = '/opt/homebrew/bin/ffmpeg'
FFPROBE = '/opt/homebrew/bin/ffprobe'


def run(args):
    return subprocess.run(args, check=True, capture_output=True, timeout=30)


def packet_hashes(path, selector):
    result = json.loads(run([FFPROBE, '-v', 'error', '-select_streams', selector,
                            '-show_packets', '-show_data_hash', 'sha256',
                            '-show_entries', 'packet=data_hash,pts_time,duration_time',
                            '-of', 'json', str(path)]).stdout)
    return result['packets']


def main():
    prior = json.loads((HERE / 'public-media-license-result.json').read_text())
    assert prior['exit_code'] == 0 and prior['usable_license']
    assert len(prior['http_exchanges']) == 1 and prior['http_exchanges'][0]['http_status'] == 200
    changes = restore()
    target = HERE / 'public-test-decrypted.mp4'
    video = HERE / 'public-video-clear.mp4'
    audio = HERE / 'public-audio-clear.mp4'
    # One common timeline shift retains the original audio/video offset.
    run([FFMPEG, '-v', 'error', '-nostdin', '-n', '-copyts',
         '-itsoffset', '-12', '-i', str(video), '-itsoffset', '-12', '-i', str(audio),
         '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-avoid_negative_ts', 'disabled',
         '-movflags', '+faststart', str(target)])
    probe = json.loads(run([FFPROBE, '-v', 'error', '-count_frames', '-show_streams',
                            '-show_format', '-of', 'json', str(target)]).stdout)
    streams = {s['codec_type']: s for s in probe['streams']}
    v, a = streams['video'], streams['audio']
    assert (v['codec_name'], v['width'], v['height'], int(v['nb_read_frames'])) == ('h264', 640, 480, 100)
    assert a['codec_name'] == 'aac' and a['sample_rate'] == '48000' and a['channels'] == 2
    assert abs(float(probe['format']['duration']) - 4) < 0.05
    preserved = {}
    starts = {}
    for selector, source in (('v:0', video), ('a:0', audio)):
        original = packet_hashes(source, selector)
        saved = packet_hashes(target, selector)
        assert [p['data_hash'] for p in original] == [p['data_hash'] for p in saved]
        assert all(abs(float(p['pts_time']) - 12 - float(q['pts_time'])) < 0.002
                   for p, q in zip(original, saved))
        preserved[selector] = {'packets': len(saved), 'all_compressed_packet_hashes_identical': True}
        starts[selector] = float(saved[0]['pts_time'])
    # Strict complete decode must pass independently of the CDM.
    run([FFMPEG, '-v', 'error', '-xerror', '-err_detect', 'explode', '-nostdin',
         '-i', str(target), '-map', '0:v', '-map', '0:a', '-f', 'null', '-'])
    raw = run([FFMPEG, '-v', 'error', '-nostdin', '-i', str(target), '-map', '0:v:0',
               '-pix_fmt', 'gray', '-f', 'rawvideo', '-']).stdout
    frame_size = 640 * 480
    assert len(raw) == 100 * frame_size
    brightness = [sum(raw[i:i+frame_size]) / frame_size for i in range(0, len(raw), frame_size)]
    assert max(brightness) > 20 and len({hashlib.sha256(raw[i:i+frame_size]).digest()
                                      for i in range(0, len(raw), frame_size)}) > 80
    pcm = run([FFMPEG, '-v', 'error', '-nostdin', '-i', str(target), '-map', '0:a:0',
               '-f', 'f32le', '-']).stdout
    values = [x[0] for x in struct.iter_unpack('<f', pcm)]
    assert values and all(math.isfinite(x) for x in values)
    rms = math.sqrt(sum(x*x for x in values) / len(values))
    assert rms > 0.0001
    preview = HERE / 'public-test-decrypted-preview.jpg'
    run([FFMPEG, '-v', 'error', '-nostdin', '-n', '-ss', '2', '-i', str(target),
         '-frames:v', '1', '-update', '1', str(preview)])
    result = {'complete': True, 'scope': 'Official public Shaka Angel One fixture only',
              'license_http_attempts': len(prior['http_exchanges']),
              'actual_encrypted_samples_processed': changes,
              'output': target.name, 'output_bytes': target.stat().st_size,
              'duration_seconds': float(probe['format']['duration']),
              'video': {'codec': v['codec_name'], 'width': v['width'], 'height': v['height'],
                        'frame_rate': v['avg_frame_rate'], 'decoded_frames': int(v['nb_read_frames'])},
              'audio': {'codec': a['codec_name'], 'sample_rate': a['sample_rate'], 'channels': a['channels'],
                        'decoded_frames': int(a['nb_read_frames']), 'rms': rms},
              'reencoded': False, 'packet_preservation': preserved,
              'common_timestamp_shift_seconds': -12, 'first_packet_times': starts,
              'strict_full_decode_passed': True, 'all_black': False,
              'mean_frame_brightness_min_max': [min(brightness), max(brightness)],
              'nhk_saving_verified': False,
              'output_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    (HERE / 'public-media-save-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
