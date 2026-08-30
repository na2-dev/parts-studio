# テクスチャを Hunyuan3D-Paint 2.1 に差し替える（2026-08-30）

[元絵の投影](2026-08-30-project-detail.md)まで来ても顔が直らなかった。原因は色の**表現**
（ボクセル場）にあると切り分けたので、テクスチャ工程だけ差し替えた（[ADR-0008](../adr/0008-texture-by-hunyuan3d-paint.md)）。

## 環境は作らずに済んだ

Windows 機の `Z:\work\3d-studio` に 3d-studio の環境が丸ごと残っていた。

- `venv-21`（Python 3.12.10）… 色塗り 2.1 用
- `Hunyuan3D-2.1`（パッチ適用済み）
- `ckpt\RealESRGAN_x4plus.pth`（63.9MB）
- Hugging Face のキャッシュも共有（`models--tencent--Hunyuan3D-2.1` ほか）

**環境構築を丸ごと省けた。** 探す前に作り始めなくてよかった。

## 数字

我々のリトポロジー済みメッシュ（42,084 面）に対して:

| | 所要 | VRAM | 備考 |
|---|---|---|---|
| Hunyuan3D-Paint 2.1 | **60.5s** | 使用 12.99GB / 確保 19.23GB | PBR マップ付き（金属・ざらつき） |
| 元絵の投影 1段目（全身 detail） | 68.8s | GPU 不要 | 貼れた画素 46.53% |
| 元絵の投影 2段目（顔だけ color） | 42.0s | GPU 不要 | 16.28% |

VRAM は 3d-studio が同じ機体で測った 13.0GB と一致する。

## 顔

![face](images/2026-08-30-hy-paint-face.png)

左から 元の絵 / TRELLIS.2 の色 / ＋元絵の投影 / **Hunyuan3D-Paint 2.1**。

**Paint 2.1 に替えただけで、目に茶色い虹彩・眉毛・口が出た。** TRELLIS.2 の色では
暗い染みだった部分である。「顔が壊れているのは色の表現であって形ではない」という切り分けが
当たった。

![stages](images/2026-08-30-hy-paint-stages.png)

さらに元絵の投影を 2 段重ねると、虹彩の中の瞳孔とハイライト、眉の形、頬の赤みまで出る。
**今日の中で最良。**

## 全身

![final](images/2026-08-30-hy-paint-final.png)

正面は 兜の 3 段の突起・オレンジのフード・目玉・ベルトの黄色い三角バックル・胸元の黄色い粒・
ヒレの手足まで揃っている。背面は フードのオレンジ・**背中の鍵穴**・中央の縫い目・ベルトが出ている。

## 残っている差

- 顔が元の絵よりわずかに小さく、フードの開口に対する比率が違う
- テクスチャに筋状のノイズが残る（特に後頭部のフード）
- 目はまだ浅い窪みの中にあるので、元の絵の完全に平らな塗りにはならない

## 通しの手順（現時点の最良）

```powershell
# 1. 形（TRELLIS.2・多視点条件付け）
cd C:\work\parts-studio\TRELLIS.2
..\venv\Scripts\python.exe gen_shape.py 1024

# 2. リトポロジー（Blender・ボクセル化→元表面へスナップ）
cd C:\work\parts-studio
.\venv-bpy\Scripts\python.exe tools\retopo_shrinkwrap.py out\uv_C_60k.glb out\vox_snap_0.009.glb 0.009

# 3. 色塗り（Hunyuan3D-Paint 2.1・3d-studio の環境を借りる）
cd Z:\work\3d-studio
.\venv-21\Scripts\python.exe paint21_pipeline.py output\ps_shape.glb output\ps_front.png output\ps_painted.glb --texsize=2048

# 4. 元絵の投影（GPU不要・2段）
cd C:\work\parts-studio
.\venv\Scripts\python.exe tools\apply_reference_detail.py out\hy_painted.glb out\hy_d1.glb --front=... --mode=detail
.\venv\Scripts\python.exe tools\apply_reference_detail.py out\hy_d1.glb out\hy_d2.glb --front=... --mode=color --top=0.26 --bottom=0.48
```

合計 約4分（形づくりを除く）。
