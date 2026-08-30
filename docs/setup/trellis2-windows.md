# TRELLIS.2 を Windows ネイティブで動かす

ADR-0004 の方針（Windows ネイティブ＋ビルド済みホイール）の実行手順。
上流の `setup.sh` は bash かつ conda 前提なので使わず、`py -3.11` の venv に手で組む。

前提は `docs/setup/windows-ssh.md`（Mac から `ssh gpu` で入れること）。

## 置き場所

```
C:\work\parts-studio\          ← このリポジトリの clone
  ├── TRELLIS.2\               ← 上流（clone、git管理外）
  ├── _wheels\                 ← visualbruno/ComfyUI-Trellis2 の clone。ホイールだけ借りる
  └── venv\                    ← Python 3.11（git管理外）
```

## 1. clone

```powershell
New-Item -ItemType Directory -Force -Path C:\work | Out-Null
cd C:\work
git clone https://github.com/na2-dev/parts-studio.git
cd C:\work\parts-studio
git clone -b main --recursive https://github.com/microsoft/TRELLIS.2.git
git clone --depth 1 https://github.com/visualbruno/ComfyUI-Trellis2.git _wheels
```

## 2. venv と torch

Python 3.11 を使う。ホイールが cp311 向けに揃っており、上流も
「Windows 11・Python 3.11・Torch 2.7.0+cu128 で動作確認」と書いているため。

```powershell
cd C:\work\parts-studio
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\pip.exe install torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cu128
```

確認:

```powershell
.\venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
# 2.7.0+cu128 12.8 True
```

## 3. CUDA 拡張（ビルド済みホイール）

CUDA Toolkit（`nvcc`）は入れない。ソースからビルドしないため不要。

```powershell
cd C:\work\parts-studio
$w = Get-ChildItem "_wheels\wheels\Windows\Torch270\*-cp311-*.whl" | ForEach-Object { $_.FullName }
.\venv\Scripts\pip.exe install $w
```

入るもの: `cumesh` / `o_voxel` / `flex_gemm` / `nvdiffrast` / `nvdiffrec_render` / `custom_rasterizer`。
このうち `custom_rasterizer` は Hunyuan3D 用で TRELLIS.2 は使わない（import できなくても問題ない）。

## 4. attention バックエンドは xformers にする

**flash-attn は入れなくてよい。** 上流の既定は `flash_attn` だが、Windows 向けの公式配布が無く、
第三者ビルドに頼ることになる。代わりに **xformers（PyPI 公式配布）** を使う。

```powershell
.\venv\Scripts\pip.exe install "xformers==0.0.30"
```

`0.0.30` は torch 2.7.0 向けで、cp311 の Windows ホイールがある。

実行時は環境変数で切り替える:

```powershell
$env:ATTN_BACKEND = "xformers"
```

**★`sdpa` は使えない。** dense 側（`trellis2/modules/attention/config.py`）は
`xformers / flash_attn / flash_attn_3 / sdpa / naive` を受け付けるが、**sparse 側**
（`trellis2/modules/sparse/config.py`）は `xformers / flash_attn / flash_attn_3` しか受け付けず、
`sdpa` を渡すと既定の `flash_attn` のまま残って import に失敗する。両方に効くのは `xformers` だけ。

## 5. 上流の基本依存

`setup.sh --basic` の中身を Windows 向けに置き換えたもの。`pillow-simd` は Linux 専用なので除く
（`pillow` は torch が入れる）。

```powershell
.\venv\Scripts\pip.exe install imageio imageio-ffmpeg tqdm easydict opencv-python-headless ninja `
  trimesh transformers "gradio==6.0.1" tensorboard pandas lpips zstandard kornia timm safetensors huggingface_hub
.\venv\Scripts\pip.exe install "git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8"
```

`utils3d` のコミットは上流 `setup.sh` の指定どおりに固定する。

## 6. 重み

3 つ要るが、**必要なのは 2 つだけにできる**。

| 重み | ライセンス | gated | 必要か |
|---|---|---|---|
| `microsoft/TRELLIS.2-4B` | MIT | なし | **必須**。16.24GB |
| `facebook/dinov3-vitl16-pretrain-lvd1689m` | dinov3-license | **manual** | **必須**。差し替え不可 |
| `briaai/RMBG-2.0` | bria-rmbg-2.0 | auto | **不要にできる**（下記） |

### RMBG-2.0 を不要にする

`preprocess_image` は、入力が RGBA でアルファが全て 255 でなければ（＝切り抜き済みなら）
背景ぬきを呼ばない（`trellis2/pipelines/trellis2_image_to_3d.py:131-147`）。

```python
if has_alpha:
    output = input                      # rembg を呼ばない
else:
    output = self.rembg_model(input)    # ここでだけ RMBG-2.0 が要る
```

したがって**透過 PNG を渡す限り RMBG-2.0 は一度も読まれない**。背景ぬきは自前の工程として持ち、
重みは `ZhengPeng7/BiRefNet`（**MIT**・gated なし・匿名取得可）を使う。TRELLIS.2 の rembg 実装
ファイル名が `BiRefNet.py` であることからも分かるとおり、RMBG-2.0 は BiRefNet の重み違いである。

### DINOv3 は差し替えられない

DINOv3 は前処理ではなく画像エンコーダで、`get_cond()` が呼ぶ `image_cond_model` の実体。
4B の DiT は DINOv3 の特徴空間に対して訓練されているため、CLIP や SigLIP に差し替えると
cross-attention が意味を失う。**規約に同意して承認を待つ以外に道はない**（同意自体は無料）。

### トークンの渡し方

トークンは会話やリポジトリに残さない。Windows 側でユーザー環境変数にする。

```powershell
setx HF_TOKEN "hf_..."
```

`setx` は**新しいセッションから**有効になる。設定後に SSH を張り直すこと。

### ダウンロード

```powershell
cd C:\work\parts-studio
.\venv\Scripts\python.exe -c @"
import os
from huggingface_hub import snapshot_download
print(snapshot_download('microsoft/TRELLIS.2-4B', token=os.environ['HF_TOKEN'], max_workers=4))
"@
```

## 動作確認

```powershell
cd C:\work\parts-studio\TRELLIS.2
$env:ATTN_BACKEND = "xformers"
$env:PYTHONIOENCODING = "utf-8"
..\venv\Scripts\python.exe -c "from trellis2.pipelines import Trellis2ImageTo3DPipeline; print('OK')"
```

期待される出力:

```
[ATTENTION] Using backend: xformers
[SPARSE] Conv backend: flex_gemm; Attention backend: xformers
OK
```

---

## 現状

- 1〜5: 完了。`trellis2` の両パイプラインが import できるところまで確認済み
- 6: `microsoft/TRELLIS.2-4B`（15.12GB・22ファイル）取得済み。**`facebook/dinov3-vitl16` は承認待ち（403）**
- DINOv2-large を代役にした実測で、`1024_cascade` が 46 秒 / VRAM 2.9GB / RAM 22.2GB で通ることを確認済み（[実測](../measurements/2026-08-30-trellis2-memory.md)）。**形の品質は DINOv3 の承認後にやり直す**

## ハマったところ

| 症状 | 原因 |
|---|---|
| `ATTN_BACKEND=sdpa` にしたのに `[SPARSE] Attention backend: flash_attn` と出る | sparse 側は `sdpa` を受け付けない。`xformers` にする |
| `import torch` が `DLL load failed` | pip install が終わっていなかっただけ |
| SSH 越しの日本語が化ける | `windows-ssh.md` の手順8（UTF-8 プロファイル）。Python の出力は別途 `$env:PYTHONIOENCODING="utf-8"` |
| `ssh gpu 'python -c "..."'` が構文エラー | 引用符が bash と PowerShell の二重解釈で壊れる。スクリプトを `scp` してから実行する |
