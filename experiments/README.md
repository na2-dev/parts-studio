# 実験のアーカイブ

**本線のパイプラインでは使っていないもの。** 捨てずに残してあるのは、
同じ道をもう一度歩かないため（なぜ使わなかったかが分かるように）。

**ここに置いたものは動作を保証しない。** 本線が変わっても追随させない。
使うときは、当時の実測ドキュメントを併せて読むこと。

> **実測ドキュメント（`docs/measurements/`）の中では、これらは `tools/` 配下として
> 書かれている。** 当時の場所であり、記録なので書き換えていない。
> 読み替えること。

| ファイル | 何を試したか | なぜ本線でないか | 実測 |
|---|---|---|---|
| `bake_to_uv.py` | xatlas での UV 展開と、ボクセル場からの自前焼き込み | 本線では Hunyuan3D-Paint が UV を張るため出番が無い | [UV展開](../docs/measurements/2026-08-30-uv-unwrapping.md) / [リトポロジーと焼き込み](../docs/measurements/2026-08-30-retopology-and-bake.md) |
| `crop_ref_for_part.py` | パーツに対応する範囲で元絵を切り出す | `--partof`（全身の正規化を渡す方式）に置き換わった。**シルエットでマスクすると逆に悪化した**（一致 0.29〜0.48） | [通しのパイプライン](../docs/measurements/2026-08-30-final-pipeline.md) |
| `retopo_quad.py` | ボクセル化 → QuadriFlow で四角化 | **QuadriFlow が「多様体でなく法線も揃っていない」と拒否する。** ボクセルリメッシュ自体が四角100%を出すので不要だった | [リトポロジーと焼き込み](../docs/measurements/2026-08-30-retopology-and-bake.md) |
| `vox_only.py` | ボクセルリメッシュ単体 | `retopo_shrinkwrap.py` に取り込んだ | 同上 |
| `remesh_blender.py` | 3d-studio から移植した QuadriFlow リメッシュ | 上と同じ理由で通らない。**掃除しても おかしな辺が 720 本残る** | 同上 |

## 本線で使っているもの

`tools/` を参照。通しの手順は
[docs/measurements/2026-08-30-final-pipeline.md](../docs/measurements/2026-08-30-final-pipeline.md)。
