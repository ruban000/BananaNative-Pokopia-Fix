"""The nn::codec AV1 decoder of the host (dm/host/hle_codec_video.c with NX_DAV1D) end to end through its SDK take-overs (dm/host/tests/t_codec_av1.c): both Initialize
overloads, the buffer and sync handle protocol, properties (3604 until a picture), NV12 reads compared bit for bit with ffmpeg's dav1d, flush, stop, refused packets, finalize.

The production codec source is compiled behind the POSIX shim of test_codec_poll.py, with AddressSanitizer and UBSan, against dav1d's C code (dm/third_party/dav1d) and the real
NV12 converter (hle_movie_yuv.c). Needs cmake, a C compiler and an ffmpeg with an AV1 encoder and libdav1d; skipped without them. The clips are made here (ffmpeg's testsrc2,
BT.709 limited range like Zelda: Ocarina of Time's movies): 320x180 with hidden frames and a second key frame, and a short 1920x1080 one.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def _module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


POLL = _module('test_codec_poll')
DAV1D = _module('test_av1_dav1d')
CLIPS = [  # width, height, frames at 30 fps, key frame interval
    (320, 180, 60, 24),
    (1920, 1080, 12, 8),
]


class CodecAv1(unittest.TestCase):
    def test_av1_decoder_end_to_end(self):
        src = DAV1D.dav1d_src()
        if not src:
            self.skipTest('dav1d sources unavailable (dm/third_party/dav1d)')
        if not (shutil.which('ffmpeg') and shutil.which('cmake')):
            self.skipTest('ffmpeg or cmake unavailable')
        enc = DAV1D.encoder()
        if not enc:
            self.skipTest('ffmpeg has no AV1 encoder')
        compiler = os.environ.get('CC') or shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler unavailable')
        run = lambda cmd: subprocess.run(cmd, capture_output=True, text=True)
        with tempfile.TemporaryDirectory(prefix='codec-av1-') as tmp:
            tmp = Path(tmp)
            opts = {'libaom-av1': ['-cpu-used', '8', '-crf', '36', '-b:v', '0', '-lag-in-frames', '8', '-row-mt', '1'],
                    'libsvtav1': ['-preset', '12', '-crf', '36'], 'librav1e': ['-speed', '10', '-qp', '140']}[enc]
            colour = ['-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv']
            clips = []
            for w, h, frames, gop in CLIPS:
                ivf, ref = tmp / f'clip{w}.ivf', tmp / f'ref{w}.yuv'
                made = run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={w}x{h}:rate=30', '-frames:v', str(frames), '-c:v', enc, '-pix_fmt', 'yuv420p',
                            '-g', str(gop), *opts, *colour, '-f', 'ivf', str(ivf)])
                self.assertEqual(made.returncode, 0, made.stderr)
                dec = run(['ffmpeg', '-v', 'error', '-c:v', 'libdav1d', '-i', str(ivf), '-pix_fmt', 'yuv420p', '-f', 'rawvideo', str(ref)])
                if dec.returncode:
                    self.skipTest('ffmpeg has no libdav1d reference decoder: ' + dec.stderr[:200])
                clips.append((ivf, ref, w, h))
            lib = tmp / 'lib'
            conf = run(['cmake', '-S', str(ROOT / 'dm/third_party/dav1d'), '-B', str(lib), '-DCMAKE_BUILD_TYPE=Release', f'-DDAV1D_SRC={src}'])
            self.assertEqual(conf.returncode, 0, conf.stderr)
            built = run(['cmake', '--build', str(lib), '-j8'])
            self.assertEqual(built.returncode, 0, built.stderr[-2000:])
            source = (ROOT / 'dm/host/hle_codec_video.c').read_text()
            self.assertEqual(source.count('#include "hle.h"'), 1)
            (tmp / 'codec.c').write_text(source.replace('#include "hle.h"', POLL.SHIM))
            binary = tmp / 'test'
            vpx = ROOT / 'dm/third_party/libvpx'
            cc = run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined',
                      '-pthread', '-DNX_DAV1D=1', '-I' + str(src / 'include'), '-I' + str(ROOT / 'dm/host'), '-I' + str(vpx), '-I' + str(vpx / 'config'),
                      '-DCODEC_SOURCE="' + str(tmp / 'codec.c') + '"', str(ROOT / 'dm/host/tests/t_codec_av1.c'), str(ROOT / 'dm/host/hle_av1_dav1d.c'),
                      str(ROOT / 'dm/host/hle_movie_yuv.c'), str(vpx / 'vpx/src/vpx_image.c'), str(lib / 'libdav1d_nx.a'), '-o', str(binary)])
            self.assertEqual(cc.returncode, 0, cc.stderr)
            for ivf, ref, w, h in clips:
                with self.subTest(clip=f'{w}x{h}'):
                    result = subprocess.run([str(binary), str(ivf), str(ref), str(w), str(h)], capture_output=True, text=True, timeout=900)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
                    self.assertIn('t_codec_av1: passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
