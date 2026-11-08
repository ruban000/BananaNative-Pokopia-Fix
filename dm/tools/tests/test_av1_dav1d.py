"""nx_av1 (dm/host/hle_av1_dav1d.c) decodes AV1 units like ffmpeg's dav1d does.

Needs the dav1d sources (dm/third_party/dav1d/vendor.sh, or DAV1D_SRC=<unpacked dav1d-1.5.4>), cmake, a C compiler and an ffmpeg with an AV1 encoder and libdav1d; skipped
without them. The clip is made here (a synthetic pattern, 320x180, hidden frames and a second key frame), nothing from a game.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
W, H = 320, 180


def dav1d_src():
    env = os.environ.get('DAV1D_SRC')
    if env and (Path(env) / 'src/lib.c').exists():
        return Path(env)
    if (ROOT / 'dm/third_party/dav1d/src/lib.c').exists():
        return ROOT / 'dm/third_party/dav1d'
    return None


def encoder():
    out = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
    for name in ('libaom-av1', 'libsvtav1', 'librav1e'):
        if name in out:
            return name
    return None


class Av1Dav1d(unittest.TestCase):
    def test_decode_matches_ffmpeg(self):
        src = dav1d_src()
        if not src:
            self.skipTest('dav1d sources unavailable (dm/third_party/dav1d/vendor.sh)')
        if not (shutil.which('ffmpeg') and shutil.which('cmake')):
            self.skipTest('ffmpeg or cmake unavailable')
        enc = encoder()
        if not enc:
            self.skipTest('ffmpeg has no AV1 encoder')
        compiler = os.environ.get('CC') or shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='av1-dav1d-') as tmp:
            tmp = Path(tmp)
            opts = {'libaom-av1': ['-cpu-used', '8', '-crf', '32', '-b:v', '0', '-lag-in-frames', '8'],
                    'libsvtav1': ['-preset', '12', '-crf', '32'], 'librav1e': ['-speed', '10', '-qp', '120']}[enc]
            run = lambda cmd: subprocess.run(cmd, capture_output=True, text=True)
            made = run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={W}x{H}:rate=30:duration=2', '-c:v', enc, '-pix_fmt', 'yuv420p', '-g', '24', *opts,
                        '-f', 'ivf', str(tmp / 'clip.ivf')])
            self.assertEqual(made.returncode, 0, made.stderr)
            dec = run(['ffmpeg', '-v', 'error', '-c:v', 'libdav1d', '-i', str(tmp / 'clip.ivf'), '-pix_fmt', 'yuv420p', '-f', 'rawvideo', str(tmp / 'ref.yuv')])
            if dec.returncode:
                self.skipTest('ffmpeg has no libdav1d reference decoder: ' + dec.stderr[:200])
            lib = tmp / 'lib'
            conf = subprocess.run(['cmake', '-S', str(ROOT / 'dm/third_party/dav1d'), '-B', str(lib), '-DCMAKE_BUILD_TYPE=Release', f'-DDAV1D_SRC={src}'], capture_output=True, text=True)
            self.assertEqual(conf.returncode, 0, conf.stderr)
            built = subprocess.run(['cmake', '--build', str(lib), '-j8'], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr[-2000:])
            binary = tmp / 'test'
            cc = subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Werror', '-fsanitize=address,undefined', '-DNX_DAV1D=1',
                                 '-I' + str(src / 'include'), '-I' + str(ROOT / 'dm/host'),
                                 str(ROOT / 'dm/host/tests/t_av1_dav1d.c'), str(ROOT / 'dm/host/hle_av1_dav1d.c'), str(lib / 'libdav1d_nx.a'), '-pthread', '-o', str(binary)],
                                capture_output=True, text=True)
            self.assertEqual(cc.returncode, 0, cc.stderr)
            result = subprocess.run([str(binary), str(tmp / 'clip.ivf'), str(tmp / 'ref.yuv'), str(W), str(H)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('t_av1_dav1d: passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
