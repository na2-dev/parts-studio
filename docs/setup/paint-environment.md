# 塗り環境を作る（Hunyuan3D-Paint 2.1）

色塗り（[ADR-0008](../adr/0008-texture-by-hunyuan3d-paint.md)）は
**形づくりとは別の Python 環境**で動く。この文書はその環境の作り方と、
いまそれを借りている事実を記録する。

## いまの状態: 環境だけを借りている

**塗りのコードは parts-studio が持っている**（`tools/paint_backend.py`）。
3d-studio の `paint21_pipeline.py` は呼ばない。

借りているのは**環境だけ**で、それがまだリポジトリの外（`Z:\work\3d-studio`）に
あるため、**parts-studio を clone しただけでは塗れない**。
`tools/make_texture.py` は借りているとき必ずこう表示する。

```
※ 塗り環境を借りています: Z:\work\3d-studio
   parts-studio 単体で動かすには docs/setup/paint-environment.md
```

| 借りているもの | 大きさ |
| :--- | ---: |
| `venv-21`（Python 3.12 + torch 2.6.0+cu126 + 上流の依存） | 5.6GB |
| `Hunyuan3D-2.1`（パッチ適用済み） | 0.2GB |
| `ckpt/RealESRGAN_x4plus.pth` | 64MB |

## なぜ別環境なのか

| | 形づくり | 塗り |
| :--- | :--- | :--- |
| Python | 3.11 | **3.12** |
| torch | 2.7.0+cu128 | **2.6.0+cu126** |
| 場所 | `venv/` | `venv-21/` |

1つの venv に同居できないので、`tools/make_texture.py`（形づくりと同じ環境で動く
薄いラッパ）が `tools/paint_backend.py` を**別プロセスとして**起動する。
ラッパ側は torch を import しない。

## 場所の決めかた

`tools/paintenv.py` が次の順で探す。

1. **人が指定した場所**（1 つだけ）
   - `--paint-root` があればそれ
   - 無ければ環境変数 `PARTS_STUDIO_PAINT_ROOT`
   - **`--paint-root` は環境変数を上書きする。** 両方見ると、有効な `--paint-root` を
     渡しても古い環境変数のせいで止まってしまうため
2. リポジトリ直下の `paint/`（下の手順で作った場合）
3. `Z:\work\3d-studio`（借り物・当面の既定）

**1 で指した場所が使えないときは、2・3 を探さずにその場で止まる。**
黙って別の環境へ落ちると、指したつもりの無い環境で塗った結果が返るため。
2 と 3 は「指定」ではないので、揃っていなければ次へ落ちる。

「使える」の判定は次の 3 つが揃っていること。

| | 無いとどうなるか |
| :--- | :--- |
| `venv-21\Scripts\python.exe` | 起動できない |
| `Hunyuan3D-2.1\` | 上流を import できない |
| `ckpt\RealESRGAN_x4plus.pth` | **モデルを読み込んだ後に生の `FileNotFoundError` で落ちる** |

重みを判定に入れているのは、上流が `Hunyuan3DPaintPipeline.__init__` の中で
これを読むため（`textureGenPipeline.py:88` → `image_super_utils.py:25-27`）。
**上流の既定 `"ckpt/RealESRGAN_x4plus.pth"` は cwd 相対**で、`make_texture.py` が
cwd を塗り環境にする以上まったく同じ場所を指すので、逃げ道は無い
（2026-08-31 に `RealESRGANer` へ存在しないパスを渡して `FileNotFoundError` を確認）。

## 自前で作る手順

以下は `C:\work\parts-studio\paint\` に作る場合。作り終えたら `--paint-root` も
環境変数も要らなくなる（候補 3 で見つかる）。

> **未検証**: 以下 1〜6 の手順は 3d-studio の構築記録から起こしたもので、
> parts-studio 側でまだ通していない。実行したら結果をここに追記すること。
> **末尾の「実測」は借り物の環境で測ったものなので、そちらは実測値である。**

### 1. 上流を取ってくる

```powershell
mkdir C:\work\parts-studio\paint
cd C:\work\parts-studio\paint
git clone --depth 1 https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git
```

### 2. パッチを当てる

`hy3dpaint/DifferentiableRenderer/mesh_utils.py` の `import bpy` を
try/except にする。**bpy は Python 3.11 用しか無く、この環境（3.12）には入らない。**

その代わり**上流の OBJ→GLB 変換は動かなくなる**（静かに失敗して `False` を返す）。
glb は `paint_backend.py` が `.obj` から作るので困らない。詳しくは
[patches/README.md](../../patches/README.md)。

```powershell
git -C Hunyuan3D-2.1 apply ..\..\patches\hunyuan3d-2.1_paint.patch
```

### 3. Python 環境を作る

```powershell
cd C:\work\parts-studio\paint
py -3.12 -m venv venv-21
.\venv-21\Scripts\pip.exe install torch==2.6.0 torchvision==0.21.0 `
  --index-url https://download.pytorch.org/whl/cu126
.\venv-21\Scripts\pip.exe install -r Hunyuan3D-2.1\requirements.txt
.\venv-21\Scripts\pip.exe install realesrgan basicsr
```

- **`requirements.txt` に torch 本体は入っていない**ので先に入れる。
- `realesrgan` / `basicsr` は `requirements.txt` に無いが、
  高精細化に要る（入れないと `ModuleNotFoundError: No module named 'realesrgan'`）。

### 4. 描画カーネルを入れる

```powershell
.\venv-21\Scripts\pip.exe install https://github.com/kijai/ComfyUI-Hunyuan3DWrapper/raw/main/wheels/custom_rasterizer-0.1.0%2Btorch260.cuda126-cp312-cp312-win_amd64.whl
```

**GPU の世代に注意。** このカーネルは sm_86（RTX 3070 など）向けが無い。
sm_89（RTX 4070 Ti SUPER）では動く。VRAM の問題ではない。

### 5. 高精細化の重みを置く

Real-ESRGAN 公式リリースの `RealESRGAN_x4plus.pth`（64MB）を
`C:\work\parts-studio\paint\ckpt\RealESRGAN_x4plus.pth` に置く。

上流の既定の置き場所（`Hunyuan3D-2.1\hy3dpaint\ckpt\`）ではなく、
`paint/` 直下の `ckpt/` に置く。`paint_backend.py` が
`conf.realesrgan_ckpt_path` で差し替える。

### 6. 確かめる

```powershell
cd C:\work\parts-studio
.\venv\Scripts\python.exe tools\make_texture.py --mesh=out\shape_retopo.glb `
  --front=testimg\front.png --out=out\painted.glb
```

1 行目が `塗り環境: C:\work\parts-studio\paint` なら自前の環境。
`※ 塗り環境を借りています` なら借り物のまま。**どちらか必ず出る**ので、
何も出ない場合は実行そのものが失敗している。

## 渡すメッシュは Y 上でなければならない

**上流は Y 上を前提にしている。Z 上のまま渡すと、球に近い頭で前後を取り違え、
顔が頭の裏側に付く**（2026-08-31 実測。同じ頭を両方の向きで塗り分けて確認した）。

胴体は輪郭から前後が分かるので、間違えていても「なんとなく塗れている」ように見える。
**頭だけが壊れる**ので気づきにくい。

`tools/make_texture.py` は `--up=z`（既定）なら、渡す前に Y 上へ直し、
返ってきたものを Z 上へ戻す。このパイプラインの中身は Z 上なので、既定のまま使えばよい。
**既に glTF の向き（Y 上）の形を渡すときだけ `--up=y` にすること。**

## 上流をそのままでは動かせない箇所（2つ）

どちらも `tools/paint_backend.py` が実行時に差し込む。**上流のファイルは書き換えない。**

### 1. basicsr が消えた torchvision の場所を見に行く

`basicsr` は `torchvision.transforms.functional_tensor` を import するが、
torchvision 0.17 で削除されている（この環境は 0.21）。
`paint_backend.fix_torchvision()` が `rgb_to_grayscale` だけを持つ
ダミーモジュールを `sys.modules` に登録して橋渡しする。

適用されると起動時に `basicsr 用の回避を適用しました` と出る。

### 2. テクスチャの穴埋め `meshVerticeInpaint` が無い

`DifferentiableRenderer/mesh_inpaint_processor.cpp` の C++ 拡張が
Windows(MSVC) 向けにビルドされておらず、上流にフォールバックも無い。
そのままだと**生成が完走したあと最後で落ちる**。

```
NameError: name 'meshVerticeInpaint' is not defined
  MeshRender.py, line 1408, in uv_inpaint
```

`paint_backend.install_inpaint_fallback()` が、読み込み済みの `MeshRender`
モジュールへ Python 版（`scipy.ndimage.distance_transform_edt` で
最近傍の塗れている画素の色を引く）を差し込む。

適用されると `穴埋めの代替を差し込みました: DifferentiableRenderer.MeshRender` と出る。
起動時に上流が出す `InPaint Function CAN NOT BE Imported!!!` は**上流の警告**で、
この差し込みが効いていれば問題ない。

## ハマりどころ

| 症状 / 落とし穴 | 対処 |
| :--- | :--- |
| **`texture_size` の半分しか出ない** | 仕様。器 4096 → 実際に出る絵は 2048（実測）。既定を下げると黙って解像度が落ちるので `--texsize` は既定のまま使う |
| **顔が頭の裏側に付く** | 渡したメッシュが Z 上。上流は Y 上を前提にしている。`--up=z`（既定）なら自動で直す。`--up=y` を指定していないか確かめる |
| **2回目の実行で前回の形が出る** | 前回の `.glb` が残っていると「上流が書いた」と誤判定する。`paint_backend.clear_stale()` が実行前に中間ファイルを消して塞いでいる |
| **渡した形が作り直される**（`white_mesh_remesh.obj` が出る） | `use_remesh` の既定が True。`paint_backend.py` は必ず False で呼ぶ。True だとリトポロジー済みの形もパーツ分割した形も置き換わる |
| **金属・ざらつきが glb に入らない** | 2.1 は別々の `.jpg` で出し、同時に書き出す `.glb` には入れない。`attach_pbr()` が glTF の決まり（G=ざらつき / B=金属）で1枚にまとめて入れ直す |
| `ModuleNotFoundError: No module named 'realesrgan'` | 手順 3 の最終行を実行する |
| RTX 3070 で動かない | VRAM ではなく sm_86 向け `custom_rasterizer` カーネルの不在 |
| VRAM が足りない | 使用 13.41GB・確保 20.41GB（実測）。面数を減らしても器を下げてもほとんど変わらない。**8GB 機では通らない** |

## 実測（2026-08-31・RTX 4070 Ti SUPER 16GB）

借り物の環境（`Z:\work\3d-studio`）を parts-studio の `tools/make_texture.py` から
既定の設定（`--texsize=4096 --rendersize=1024 --views=6 --res=512`）で呼んだ結果。

対象は、この日 parts-studio 側で作り直した形
（`tools/make_shape.py` → `tools/retopo_shrinkwrap.py`）。

| 項目 | 値 |
| :--- | :--- |
| 対象 | リトポロジー済みの全身 42,088 面 |
| 時間 | **初回 61.5 秒 / 2 回目以降 49 秒**（初回はモデルの読み込みを含む） |
| VRAM | 使用 13.41GB・確保 20.41GB |
| 出たテクスチャ | 2048×2048（器 4096 の半分） |
| 穴埋め | 6,957,517 画素 |
| PBR | `metallicRoughnessTexture` 2048px / `metallicFactor=0.0` / `roughnessFactor=1.0` |

> **[2026-08-30 の実測](../measurements/2026-08-30-hunyuan-paint.md)との差について。**
> あちらは 42,084 面で 55.8 秒。面数が 4 つ違うのは**別のメッシュだから**で
> （あちらは 3d-studio が作った形、こちらは parts-studio が作り直した形）、
> 時間の差もそこから来ている。VRAM とテクスチャ寸法が一致するのは、
> どちらもモデル側で決まる値で面数にほとんど左右されないため
> （面数を 896,424 → 60,000 に減らしても変わらなかった、という実測がある）。
> **基準値として比べるならこちらを使う**（parts-studio の経路で測ったもの）。
> 詳しくは [2026-08-31 の実測](../measurements/2026-08-31-paint-from-parts-studio.md)。

HuggingFace の同意が要るモデルは**塗り側には無い**
（`tencent/Hunyuan3D-2.1` と `facebook/dinov2-giant` はトークン無しで取得できる）。
形づくり側の DINOv3 は gated なので、そちらは
[trellis2-windows.md](trellis2-windows.md) を参照。
