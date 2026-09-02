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

---

## A. 土台を固める（最優先）

**（2026-08-31 に解消）** ここは「手で 7 工程を繋いだ状態」だったが、
`tools/run_pipeline.py` で **4 枚 → 完成 glb が1コマンド**になった
（[実測](../measurements/2026-08-31-run-pipeline.md)。436 秒）。
工程の内訳は [通しの手順](../measurements/2026-08-30-final-pipeline.md#通しの手順更新)。

| ID | Issue | タスク | 由来 | 状態 | 前提 |
|---|---|---|---|---|---|
| A-1 | [PR #1](https://github.com/na2-dev/parts-studio/pull/1) | 作業の進め方とタスク一覧を用意する | — | 🔵 | — |
| A-2 | [#2](https://github.com/na2-dev/parts-studio/issues/2) | ~~形づくりを 1 コマンドにする（4枚 → 形の glb）~~ | ADR-0003/0005 | ✅ | — |
| A-3 | — | ~~リトポロジーを 1 コマンドにする~~ | ADR-0007 | ✅ | — |
| A-4 | [#3](https://github.com/na2-dev/parts-studio/issues/3) | 塗り工程を parts-studio から呼べるようにする | ADR-0008 | 🔵 | A-1 |
| A-5 | [#4](https://github.com/na2-dev/parts-studio/issues/4) | パーツ分割・投影・結合を 1 コマンドにする | ADR-0008 | 🔵 | A-4 |
| A-6 | [#5](https://github.com/na2-dev/parts-studio/issues/5) | 通しのパイプラインを 1 コマンドにする（4枚 → 完成 glb） | 全体 | 🔵 | A-2〜A-5 |
| A-7 | [#6](https://github.com/na2-dev/parts-studio/issues/6) | 別の題材で通して、過適合を確かめる | — | ⏸ | A-6 |

**A-3 は完了済み**: `experiments/remesh_blender.py` は QuadriFlow が通らず使えなかったが、
`tools/retopo_shrinkwrap.py` が「ボクセル化 → 元表面へスナップ」を 1 コマンドで行う。

**A-4 の決着（2026-08-31）**: **コードは parts-studio が持ち、環境は借りたままにする。**
塗りの実体は `tools/paint_backend.py`（parts-studio のもの）で、3d-studio の
`paint21_pipeline.py` は呼ばない。5.8GB の環境（`venv-21` / `Hunyuan3D-2.1` / 重み）だけを
`Z:\work\3d-studio` から借りており、場所は `--paint-root` と環境変数
`PARTS_STUDIO_PAINT_ROOT` で差し替えられる。借りていることは実行時に必ず表示する。
自前で作る手順は [paint-environment.md](../setup/paint-environment.md)（**まだ通していない**）。

**A-5 の状態について**: 🔵 は「前提が揃っている」だが、A-5 は**着手済みで PR 待ち**。
前提の A-4 も PR #20 が main 未マージなので、厳密には揃っていない。
A-5 は A-4 のブランチの上に積んでいる（[理由](../working-agreement.md#ブランチと-pr)）。

**A-5 で分かったこと（2026-08-31）**: 上方向の扱いが 3 か所で間違っていた。
どれもエラーを出さず、出来上がりが静かに悪くなるだけだった
（[実測](../measurements/2026-08-31-up-axis.md)）。とくに**色塗りは Y 上のメッシュを
前提にしており、Z 上のまま渡すと顔が頭の裏側に付く**。

**A-6 の結果（2026-08-31）**: `tools/run_pipeline.py` で 4 枚 → 完成 glb が
**436 秒**で通った（[実測](../measurements/2026-08-31-run-pipeline.md)）。
`--from` で途中から始められる。**リトポロジーは成功しても終了コードが 0 にならない**
（bpy が終了時に落ちる）ので、出力が新しくなったかで判定している。

**A-7 の注意**: 「首の高さの自動検出」「ならし 8 回」「首から上へ足す余白 `--margin` 0.01」などは、
すべて 1 体だけで調整した値。別の絵で通らない可能性がある。

---

## B. ブラウザツールにする（当初の目的）

| ID | Issue | タスク | 由来 | 状態 | 前提 |
|---|---|---|---|---|---|
| B-1 | [#7](https://github.com/na2-dev/parts-studio/issues/7) | ジョブサーバー（HTTP API・ジョブの投入と進捗） | ADR-0001 | 🔵 | A-6 |
| B-2 | [#8](https://github.com/na2-dev/parts-studio/issues/8) | ブラウザ UI（4枚アップロード → 進捗 → ダウンロード） | ADR-0001 | 🔵 | B-1 |
| B-3 | [#9](https://github.com/na2-dev/parts-studio/issues/9) | 3D ビューア（出来た glb をその場で回して見る） | ADR-0001 | ⏸ | B-2 |
| B-4 | [#10](https://github.com/na2-dev/parts-studio/issues/10) | Windows 機での起動手順（利用者向け） | ADR-0004 | ⏸ | B-2 |

---

**B-3 の状態（2026-09-03）**: 実装済み・PR 待ち。model-viewer を `web/vendor/` に
同梱（Apache-2.0）。実ブラウザで「done → 3D で見る → glb が表示（loaded=true）→
もう一度押すと閉じる」を検証済み。

**B-2 の状態（2026-09-03）**: 実装済み・PR 待ち。UI はジョブサーバーが配る
単一 HTML（`web/index.html`）。実ブラウザ（Chrome headless）で
「4枚そろうまで押せない → 投入 → 進捗 → glb 受け取り」を検証済み。

**B-1 の状態（2026-09-03）**: 実装済み・PR 待ち。API と作りは
[job-server.md](../setup/job-server.md)。本物の `run_pipeline` を載せた実機の通しも
済み（投入 → done 473 秒 → glb 10.3MB を API 経由で受け取り、描画して目視）。
**ssh 越しに立てたサーバーはセッションと共に死ぬ**ので、常駐は B-4 で扱う。


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

本線で使っていないコードは **[`experiments/`](../../experiments/README.md) に隔離した**。
なぜ使わなかったかと、当時の実測ドキュメントへのリンクをそこに置いてある。

| 対象 | 由来 | 場所 |
|---|---|---|
| `bake_to_uv.py` | ADR-0002 | [`experiments/`](../../experiments/README.md) |
| `crop_ref_for_part.py` | — | 同上 |
| `retopo_quad.py` / `vox_only.py` / `remesh_blender.py` | ADR-0007 | 同上 |
| ADR-0006 の視点別合成 | ADR-0006 | コードは Windows 機の `TRELLIS.2/gen_blend.py`（リポジトリ外）。ADR-0008 に置き換わった |
