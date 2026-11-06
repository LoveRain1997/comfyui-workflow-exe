# -*- coding: utf-8 -*-
"""S5 验收：最低分辨率跑 + R6 显存前后采样 + PNG 落盘检查。"""
import argparse, json, os, subprocess, sys, glob, time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ap = argparse.ArgumentParser()
ap.add_argument('--program', required=True)
ap.add_argument('--venv', default=r'E:/comfycache/ComfyUI/ComfyUI/.venv')
ap.add_argument('--params', required=True, help='验收参数 JSON（AI 按 schema 写最低分辨率值）')
ap.add_argument('--timeout', type=int, default=1800)
a = ap.parse_args()

py = os.path.join(a.venv, 'Scripts', 'python.exe')
env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')

def vram():
    r = subprocess.run(['nvidia-smi', '--query-gpu=memory.free,memory.used',
                        '--format=csv,noheader'], capture_output=True, text=True)
    return r.stdout.strip()

before = vram()
t0 = time.time()
r = subprocess.run([py, '-m', 'mossapp', 'run', '--params', a.params],
                   cwd=a.program, capture_output=True, text=True, encoding='utf-8',
                   errors='replace', timeout=a.timeout, env=env)
elapsed = round(time.time() - t0, 1)
after = vram()

rc = r.returncode
ok = rc == 0
pngs = []
if ok:
    try:
        out_dir = json.load(open(os.path.join(a.program, 'config.json'), encoding='utf-8'))['output_dir']
        t = t0 - 1
        pngs = [p for p in glob.glob(os.path.join(out_dir, '**', '*.png'), recursive=True)
                if os.path.getmtime(p) >= t]
    except Exception as e:
        ok = False
        print('output check fail:', e)

audit = subprocess.run([sys.executable, '-c',
                        'import glob,sys;'
                        'bad=[f for f in glob.glob(sys.argv[1]+"/mossapp/*.py")'
                        ' if any(x in open(f,encoding="utf-8").read() for x in ("socket",".bind(",".listen("))];'
                        'print("CLEAN" if not bad else bad)'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
print(json.dumps({'rc': rc, 'ok': ok and bool(pngs) and 'CLEAN' in audit.stdout,
                  'seconds': elapsed, 'png': pngs, 'vram_before': before, 'vram_after': after,
                  'listen_audit': audit.stdout.strip()[:80],
                  'tail': (r.stdout or r.stderr)[-1200:]}, ensure_ascii=False, indent=1))
