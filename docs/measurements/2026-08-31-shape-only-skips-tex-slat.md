# 形だけ作るならテクスチャ潜在は丸ごと要らない（2026-08-31）

形づくり CLI（[#2](https://github.com/na2-dev/parts-studio/issues/2)）の初版は、
「`decode_shape_slat` だけでは面が出ない」と考えて `sample_tex_slat` を通していた。
**これは誤りで、テクスチャ側の工程は出力に一切影響しない。**

## 上流を読んだ結果

`decode_shape_slat` は `(List[Mesh], List[SparseTensor])` を返し、`Mesh` は最初から
`vertices` と `faces` を持つ（`trellis2/representations/mesh/base.py:14-15`）。
`decode_latent` は次のように組み立てる（`trellis2/pipelines/trellis2_image_to_3d.py:470-484`）。

```python
meshes, subs = self.decode_shape_slat(shape_slat, resolution)
tex_voxels = self.decode_tex_slat(tex_slat, subs)
for m, v in zip(meshes, tex_voxels):
    m.fill_holes()
    out_mesh.append(MeshWithVoxel(
        m.vertices, m.faces,          # ← 形はここ。tex_slat は関与しない
        coords=v.coords[:, 1:], attrs=v.feats,   # ← tex_slat はここにしか入らない
        ...))
```

`make_shape.py` は `vertices` と `faces` しか書き出さない。したがって
`tex_slat` が入る `coords` / `attrs` は作った直後に捨てられていた。

## 実測（同一プロセス・同一 seed で A/B）

RTX 4070 Ti SUPER 16GB / `--mode multidiffusion` / `--res 1024` / `--seed 1234` /
4 View（`testimg/`）。

| | 生成時間 | VRAM ピーク | 頂点 | 面 |
| :--- | ---: | ---: | ---: | ---: |
| `tex_slat` あり（変更前） | 152s | 4.89GB | 3,460,150 | 7,013,392 |
| `tex_slat` なし（変更後） | **106s** | **4.54GB** | 3,460,150 | 7,013,392 |

- 頂点の最大差: **0.000e+00**
- 面: **完全一致**（`torch.equal` が True）

**同じ形が 30% 速く出る。** 捨てていたのは `tex_slat_flow_model_1024`（1.3B）の
12 ステップ × 4 View と `tex_slat_decoder` 一式。

`fill_holes()` は `decode_latent:474` が呼んでいたので、自前で呼び直している。

## 手順の再現

検証スクリプトは `ab_tex.py` として一時的に置いたもので、リポジトリには残していない。
上の表の値は、同一プロセス内で `with_tex` を切り替えて 2 回走らせた結果である。

## 併せて分かったこと（解像度ラダー）

`sample_shape_slat_cascade` の打ち切り条件は次のとおり
（`trellis2_image_to_3d.py:335-339`）。

```python
if num_tokens < max_num_tokens or hr_resolution == 1024:
    break
hr_resolution -= 128
```

ここから 2 つ言える。

1. **`--res 1024` では `--max-tokens` が一切効かない。** 初回で必ず break する。
   VRAM が足りなくて上限を下げても、解像度は下がらずそのまま HR サンプリングへ進む。
2. **`1024 + 128k` 以外の解像度を渡すと 1024 を跨いで下がり続ける。**
   `--max-tokens` を小さくすると `hr_resolution` が負まで進み、
   `hr_resolution // 16 == 0` で座標が 1 行に潰れても止まらない。

`make_shape.py` は `--res` を `1024 以上かつ 1024 + 128 の倍数`、
`--max-tokens` を `1 以上` に制限してこれを塞いだ。
