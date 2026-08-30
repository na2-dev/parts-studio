# 塗り工程を parts-studio から呼ぶ（2026-08-31）

[#3](https://github.com/na2-dev/parts-studio/issues/3) で `tools/make_texture.py` を作り、
借りている環境（`Z:\work\3d-studio`）に対して parts-studio 側のコードで塗った。
RTX 4070 Ti SUPER 16GB / 既定の設定（`--texsize=4096 --rendersize=1024 --views=6 --res=512`）。

## 注記（2026-08-31・同日あとから）

この後で **上流が Y 上のメッシュを前提にしている**ことが分かり、
`paint_backend` に「渡す前に Y 上へ直し、返ってきたら Z 上へ戻す」を足した。
**下の時間・VRAM はその変更が入る前の経路で測ったもの**である
（工程の重さは変わらないが、出来上がるテクスチャの中身は変わる）。
詳しくは [2026-08-31-up-axis.md](2026-08-31-up-axis.md) の「2. 色塗りは Y 上の
メッシュを前提にしている」。

## 通した結果

対象は同日 parts-studio で作り直した形（`make_shape.py` → `retopo_shrinkwrap.py`、42,088 面）。

| 回 | 時間 | VRAM | 出たテクスチャ | 前回の中間ファイル |
| ---: | ---: | :--- | :--- | :--- |
| 1 | 61.5s | 使用 13.41GB・確保 20.41GB | 2048×2048 | （無し） |
| 2 | 49.1s | 同上 | 2048×2048 | （無し） |
| 3 | 48.8s | 同上 | 2048×2048 | **7 個を削除** |

1 回目が長いのは初回のモデル読み込みを含むため。

出来上がったアトラスを目視した。白目と暗い虹彩、オレンジのフード、水色の差し色が
はっきり出ており、塗りは意図どおり効いている。

`metallicRoughnessTexture` は 2048px、`metallicFactor=0.0` / `roughnessFactor=1.0`。

## 見つけた欠陥: 2回目の実行で前回の形が出る

**エラーを一切出さずに「古い形＋新しい絵」を完成品として返す。**
`docs/setup/paint-environment.md` のハマりどころにも入れた。

### 何が起きるか

塗りの実体は、上流が `.glb` を書いていればそれを使い、無ければ `.obj` から作る。
`--out=out\painted.glb` のとき、この「上流が書いた `.glb`」の判定対象は
**前回の成果物そのもの**になる。

1. 1 回目。`out\painted.glb` ができる。
2. 形を作り直して 2 回目。上流は `.obj` と `.jpg` を**新しい内容で上書き**する。
3. `out\painted.glb` は 1 回目のものが残っているので「上流が書いた」と誤判定し、
   `.obj` からの変換を飛ばす。
4. **1 回目の形**に**2 回目のテクスチャ**を貼って書き出す。
5. `保存: ...` と表示して終了コード 0。呼び出し側は出力の存在しか見ないので成功扱い。

UV レイアウトが噛み合っていないのに、どこにも異常が出ない。

### 上流の glb 変換は必ず静かに失敗する（この欠陥の前提）

上流 `convert_obj_to_glb` は bpy を使うが、中身は
`try: ... except Exception: return False`（`mesh_utils.py:274-290`）。
呼び出し側は戻り値を見ていない（`textureGenPipeline.py:188-190`）。

**bpy は Python 3.11 用しか無く、venv-21（3.12）には入っていない**
（`import bpy` → `ModuleNotFoundError` を実測）。パッチで `bpy = None` にしてあるので
`bpy.ops.wm.obj_import` が `AttributeError` になり、そのまま握り潰されて False が返る。

つまり **`.glb` は上流からは一度も出ない。** 毎回こちら側が `.obj` から作っている。
だから「上流が書いた `.glb`」の判定は、実質「前回の成果物が残っているか」だけを見ていた。

### 直しかた

`clear_stale()` が塗りの前に中間ファイル（`.glb` `.obj` `.mtl` `.jpg`
`_metallic.jpg` `_roughness.jpg` `_in.obj`）を消す。3 回目の実行で 7 個の削除を確認した。

あわせて、成果物は作業用の名前で組み立ててから `--out` へ置くようにした。
途中で落ちたときに中途半端な glb を掴ませないため。

## 重みを置き忘れると、モデルを載せたあとに落ちる

当初は「高精細化の重み（`ckpt/RealESRGAN_x4plus.pth`）は無くても上流の既定で塗れる」と
判断して必須から外していた。**これは誤り。**

- 上流は `Hunyuan3DPaintPipeline.__init__` の中で `imageSuperNet` を無条件に組み立てる
  （`textureGenPipeline.py:88` → `image_super_utils.py:25-27`）。任意にする分岐は無い。
- 上流の既定 `"ckpt/RealESRGAN_x4plus.pth"` は **cwd 相対**で、`make_texture.py` が
  cwd を塗り環境にする以上まったく同じ場所を指す。逃げ道が無い。
- `RealESRGANer` に存在しないパスを渡して **`FileNotFoundError` を実測**した。

いまは `paintenv` の判定に入れ、`paint_backend` も塗りに入る前に弾く。

## 検証

- テスト **79 件**（`tests/test_paintenv.py`）。GPU も torch も要らない
- 変異テスト **22 種すべて捕捉**。うち 6 種は、レビューが「壊しても緑だった」と
  指摘したものをそのまま入れたもの
  - `use_remesh=False` → `True`
  - `texture_size` を半分にする
  - 穴埋めの差し込みを呼ばない
  - 終了コードの検査を消す
  - 金属とざらつきの入る色（G/B）を入れ替える
  - `--paint-root` を絶対化しない
