# 形の候補を複数作る。★1つ作って終わりにしない。あとで見比べて選ぶ。
import os, subprocess, sys, time
ROOT = r'C:\work\parts-studio'
OUT = r'C:\work\handcraft\shapes'
IMG = os.path.join(ROOT, 'testimg')
os.makedirs(OUT, exist_ok=True)
combos = [
    ('multidiffusion', 1024, 1234),
    ('multidiffusion', 1024, 7),
    ('concat', 1024, 1234),
    ('multidiffusion', 1536, 1234),
]
for mode, res, seed in combos:
    name = f'{mode}_{res}_s{seed}'
    dst = os.path.join(OUT, name + '.glb')
    if os.path.isfile(dst):
        print('skip', name, flush=True); continue
    t = time.time()
    cmd = [os.path.join(ROOT, 'venv', 'Scripts', 'python.exe'),
           os.path.join(ROOT, 'tools', 'make_shape.py'),
           '--out', dst, '--mode', mode, '--res', str(res), '--seed', str(seed)]
    for v in ('front', 'left', 'right', 'back'):
        cmd += [f'--{v}', os.path.join(IMG, f'{v}.png')]
    r = subprocess.run(cmd, cwd=ROOT, env=dict(os.environ, PYTHONUTF8='1'),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    tail = [l for l in r.stdout.splitlines() if '生成' in l or '保存' in l or 'rror' in l][-3:]
    print(f'{name}: {time.time()-t:.0f}s rc={r.returncode} {" | ".join(tail)}', flush=True)
print('done', flush=True)
