"""Verify the local CDM frames against an independent decoder and make an MP4."""
import argparse
import hashlib
import json
import subprocess
from run_probe import HERE
from prepare_fixture import FFMPEG, FFPROBE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='annexb')
    args = parser.parse_args()
    if not args.label.replace('-', '').isalnum():
        parser.error('Invalid label')
    raw = HERE / (args.label + '-frames.yuv')
    prior = json.loads((HERE / (args.label + '-result.json')).read_text())
    assert prior['exit_code'] == 0 and prior['input_is_encrypted'] is False
    assert any(e == {'event': 'decoded_frames', 'value': 48} for e in prior['events'])
    reference = HERE / 'reference-frames.yuv'
    if not reference.exists():
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                        '-i', str(HERE / 'synthetic-source.mp4'), '-pix_fmt', 'yuv420p',
                        '-f', 'rawvideo', str(reference)], check=True)
    raw_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
    reference_hash = hashlib.sha256(reference.read_bytes()).hexdigest()
    assert raw_hash == reference_hash, 'CDM frame pixels differ from independent FFmpeg decode'
    target = HERE / (args.label + '-output.mp4')
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                    '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-video_size', '320x180',
                    '-framerate', '24', '-i', str(raw), '-an', '-c:v', 'libx264',
                    '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart', str(target)], check=True)
    probe = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'v:0',
                                       '-count_frames', '-show_streams', '-show_format',
                                       '-of', 'json', str(target)], check=True, capture_output=True, text=True).stdout)
    stream = probe['streams'][0]
    assert stream['width'] == 320 and stream['height'] == 180 and int(stream['nb_read_frames']) == 48
    assert stream['codec_name'] == 'h264' and abs(float(probe['format']['duration']) - 2) < 0.01
    result = {'fixture': 'Locally generated unencrypted 2-second H264 test pattern',
              'complete': True, 'decoded_frames': 48, 'width': 320, 'height': 180,
              'pixels_match_independent_decoder': True, 'raw_frames_sha256': raw_hash,
              'output': target.name, 'output_bytes': target.stat().st_size,
              'duration_seconds': float(probe['format']['duration']), 'saved_video_codec': stream['codec_name'],
              'encrypted_media_verified': False, 'nhk_saving_verified': False}
    (HERE / (args.label + '-save-result.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
