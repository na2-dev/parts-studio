# 実験のアーカイブ

**本線のパイプラインでは使っていないもの。** 捨てずに残してあるのは、
同じ道をもう一度歩かないため（なぜ使わなかったかが分かるように）。

**ここに置いたものは動作を保証しない。** 本線が変わっても追随させない。

> **実測ドキュメント（`docs/measurements/`）のパスについて**
> 記録なので書き換えていない。読み替えが要るのは次の **2 件だけ**。
>
> | 実測ドキュメント中の表記 | 実際の場所 |
> |---|---|
> | `tools/bake_to_uv.py` | `experiments/bake_to_uv.py` |
> | `tools/crop_ref_for_part.py` | `experiments/crop_ref_for_part.py` |
>
> **それ以外の `tools/xxx` はそのまま有効。** `retopo_shrinkwrap.py` /
> `split_parts.py` / `extract_part.py` / `combine_parts.py` /
> `apply_reference_detail.py` は本線として `tools/` に実在する。
>
> **`tools/remesh_blender.py` と書かれている箇所は 3d-studio 側のファイルを指す。**
> 読み替えてはいけない。

| ファイル | 何を試したか | なぜ本線でないか |
|---|---|---|
| `bake_to_uv.py` | xatlas での UV 展開と、ボクセル場からの自前焼き込み | **[ADR-0008](../docs/adr/0008-texture-by-hunyuan3d-paint.md) でテクスチャを Hunyuan3D-Paint に任せる方式へ移り、UV は色塗りAI が張るようになった。** [当時の実測](../docs/measurements/2026-08-30-retopology-and-bake.md)では最良の手順だったが、本線が変わって出番が無くなった |
| `crop_ref_for_part.py` | パーツに対応する範囲で元絵を切り出す | `--partof`（全身の正規化を渡す方式）に置き換わった。**シルエットでマスクすると逆に悪化した**（一致 0.29〜0.48 / [実測](../docs/measurements/2026-08-30-final-pipeline.md)） |
| `retopo_quad.py` | ボクセル化 → QuadriFlow で四角化（2 段） | **拒否は回避済み。** ボクセルリメッシュを前に入れれば QuadriFlow は通る。ただし**ボクセルリメッシュ自体が四角 100% を出すので、QuadriFlow が不要だった**（[実測](../docs/measurements/2026-08-30-retopology-and-bake.md)） |
| `vox_only.py` | ボクセルリメッシュ単体 | `tools/retopo_shrinkwrap.py` に取り込んだ（貼り付け直しを足したもの） |
| `remesh_blender.py` | 3d-studio から移植した QuadriFlow リメッシュ（1 段） | **QuadriFlow が「多様体でなく法線も揃っていない」と拒否する。** 掃除しても**おかしな辺が 720 本残る**（[実測](../docs/measurements/2026-08-30-retopology-and-bake.md)） |

## 本線で使っているもの

`tools/` を参照。通しの手順は
[docs/measurements/2026-08-30-final-pipeline.md](../docs/measurements/2026-08-30-final-pipeline.md)。
