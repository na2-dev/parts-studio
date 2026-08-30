# タスク一覧

grill で決めた ADR を、PR 1 つ分の大きさに割ったもの。
**1 タスク = 1 Issue = 1 ブランチ = 1 PR。** ブランチは必ず `main` から切る。

> **正は [GitHub Issues](https://github.com/na2-dev/parts-studio/issues)。この文書は索引。**
> 状態・担当・議論は Issue 側で管理する。ここは全体像と前提関係を見るためのもの。
> 二重管理を避けるため、**状態欄は Issue の open/close を正とする**（この表は目安）。

進め方は [working-agreement.md](../working-agreement.md) を参照。

## 状態の見かた

| 記号 | 意味 |
|---|---|
| ✅ | 完了（`main` に入っている） |
| 🔵 | 着手可能（前提が揃っている） |
| ⏸ | 前提待ち |
| 🧪 | 実験としては動いたが、リポジトリの成果物になっていない |

---

## A. 土台を固める（最優先）

いまパイプラインは**手で 7 工程を繋いだ状態**で、スクリプトになっていない
（工程の内訳は [通しの手順](../measurements/2026-08-30-final-pipeline.md#通しの手順更新) を参照）。
ここが最大の弱点なので先に潰す。

| ID | Issue | タスク | 由来 | 状態 | 前提 |
|---|---|---|---|---|---|
| A-1 | [PR #1](https://github.com/na2-dev/parts-studio/pull/1) | 作業の進め方とタスク一覧を用意する | — | 🔵 | — |
| A-2 | [#2](https://github.com/na2-dev/parts-studio/issues/2) | 形づくりを 1 コマンドにする（4枚 → 形の glb） | ADR-0003/0005 | 🔵 | A-1 |
| A-3 | — | ~~リトポロジーを 1 コマンドにする~~ | ADR-0007 | ✅ | — |
| A-4 | [#3](https://github.com/na2-dev/parts-studio/issues/3) | 塗り工程を parts-studio から呼べるようにする | ADR-0008 | 🔵 | A-1 |
| A-5 | [#4](https://github.com/na2-dev/parts-studio/issues/4) | パーツ分割・投影・結合を 1 コマンドにする | ADR-0008 | ⏸ | A-4 |
| A-6 | [#5](https://github.com/na2-dev/parts-studio/issues/5) | 通しのパイプラインを 1 コマンドにする（4枚 → 完成 glb） | 全体 | ⏸ | A-2〜A-5 |
| A-7 | [#6](https://github.com/na2-dev/parts-studio/issues/6) | 別の題材で通して、過適合を確かめる | — | ⏸ | A-6 |

**A-3 は完了済み**: `tools/remesh_blender.py` は QuadriFlow が通らず使えなかったが、
`tools/retopo_shrinkwrap.py` が「ボクセル化 → 元表面へスナップ」を 1 コマンドで行う。

**A-4 の注意**: いま塗りは `Z:\work\3d-studio` の `venv-21` を借りている。
parts-studio 単体で動く形にするか、借りることを正式な前提として文書化するかを決める必要がある。

**A-7 の注意**: 「首の高さの自動検出」「ならし 8 回」「切る高さ 0.033」などは、
すべて 1 体だけで調整した値。別の絵で通らない可能性がある。

---

## B. ブラウザツールにする（当初の目的）

| ID | Issue | タスク | 由来 | 状態 | 前提 |
|---|---|---|---|---|---|
| B-1 | [#7](https://github.com/na2-dev/parts-studio/issues/7) | ジョブサーバー（HTTP API・ジョブの投入と進捗） | ADR-0001 | ⏸ | A-6 |
| B-2 | [#8](https://github.com/na2-dev/parts-studio/issues/8) | ブラウザ UI（4枚アップロード → 進捗 → ダウンロード） | ADR-0001 | ⏸ | B-1 |
| B-3 | [#9](https://github.com/na2-dev/parts-studio/issues/9) | 3D ビューア（出来た glb をその場で回して見る） | ADR-0001 | ⏸ | B-2 |
| B-4 | [#10](https://github.com/na2-dev/parts-studio/issues/10) | Windows 機での起動手順（利用者向け） | ADR-0004 | ⏸ | B-2 |

---

## C. 品質を上げる

| ID | Issue | タスク | 由来 | 状態 | 前提 |
|---|---|---|---|---|---|
| C-1 | [#11](https://github.com/na2-dev/parts-studio/issues/11) | 顔をさらに細かく割る（顔だけ独立させる） | ADR-0008 | ⏸ | A-6 |
| C-2 | [#12](https://github.com/na2-dev/parts-studio/issues/12) | デライティング（元絵の陰影を剥がしてから焼く） | — | ⏸ | A-6 |
| C-3 | [#13](https://github.com/na2-dev/parts-studio/issues/13) | ならしの強さをパーツごとに変えられるようにする | — | 🔵 | A-3 |

**C-2 の根拠**: Meshy は「Remove Lighting（焼き付いた陰影と影を剥がして、どの光の下でも
正しく見えるようにする）」を機能として持つ
（[Meshy AI Texturing のドキュメント](https://docs.meshy.ai/en/webapp/guides/3d-model/ai-texturing)）。
我々は元絵の陰影を色ごと焼き込んでおり、二重に陰影がかかっている。
**これはリポジトリ内で実測していない。着手するときは、まず現状の二重陰影を測ること。**

---

## D. 最終ゴール（パーツの組み合わせ）

| ID | Issue | タスク | 由来 | 状態 | 前提 |
|---|---|---|---|---|---|
| D-1 | [#14](https://github.com/na2-dev/parts-studio/issues/14) | 意味でパーツを割る（P3-SAM を使う） | ADR-0007 | ⏸ | A-6 |
| D-2 | [#15](https://github.com/na2-dev/parts-studio/issues/15) | パーツの接合規格を決める（原点・スケール・向き） | CONTEXT.md | ⏸ | D-1 |
| D-3 | [#16](https://github.com/na2-dev/parts-studio/issues/16) | 骨入れの前にパーツを溶接する | — | ⏸ | D-2 |
| D-4 | [#17](https://github.com/na2-dev/parts-studio/issues/17) | 着せ替え（服・靴を体に沿わせる） | CONTEXT.md | ⏸ | D-2 |

**D-1 の注意**: 今日の実測で、**パーツ単位では視点の対応づけが壊れる**ことが分かっている
（頭で「正面の顔を後頭部に貼る」ところまで行った）。
全身の段階で対応づけを決めて持ち回ること。`--fixviews` と `--partof` が既にその形になっている。

---

## 実験のまま残っているもの

リポジトリにコードはあるが、本線では使っていないもの。捨てるか残すかを決める必要がある。

| 対象 | 由来 | 現状 |
|---|---|---|
| `tools/bake_to_uv.py` | ADR-0002 | 🧪 xatlas 展開＋自前焼き込み。本線では Hunyuan-Paint が UV を張るので使っていない |
| `tools/crop_ref_for_part.py` | — | 🧪 パーツ用の絵の切り出し。`--partof` の方式に置き換わり不要になった |
| `tools/retopo_quad.py` | ADR-0007 | 🧪 QuadriFlow を試したもの。多様体でないと拒否されるため使っていない |
| `tools/vox_only.py` | ADR-0007 | 🧪 ボクセルリメッシュ単体。`retopo_shrinkwrap.py` に取り込み済み |
| `tools/remesh_blender.py` | ADR-0007 | 🧪 3d-studio から移植。QuadriFlow が通らず使っていない |
| ADR-0006 の視点別合成 | ADR-0006 | 🧪 ADR-0008 に置き換わった。ボクセル場のプレビュー用としてのみ意味がある |
