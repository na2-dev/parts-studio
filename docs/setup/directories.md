# 作業ディレクトリの構成

**3 箇所ある。役割が違うので混同しないこと。**

| | 場所 | 役割 |
|---|---|---|
| **Mac** | `/Users/snatsuya/work/parts-studio` | **git の正。** ドキュメントと道具を書いて push する |
| **Windows** | `C:\work\parts-studio` | GPU 実行。Mac から `ssh gpu` で操作する（[SSH の手順](windows-ssh.md)） |
| Windows | `Z:\work\3d-studio` | **借りている。** Hunyuan3D-Paint の環境（`venv-21`）と重み。別プロジェクト |

`Z:` を借りていることは [ADR-0008](../adr/0008-texture-by-hunyuan3d-paint.md) の前提。
parts-studio 単体で動く形にするかは [#3](https://github.com/na2-dev/parts-studio/issues/3) で判断する。

## リポジトリの中身

```
parts-studio/
├── CONTEXT.md          用語集（Shape / Layout / Texture / Part / View …）
├── docs/
│   ├── working-agreement.md  作業の進め方（ブランチ・レビュー・測り方）
│   ├── adr/            決定の記録（8本）
│   ├── measurements/   実測の記録。★履歴なので書き換えない（注記を足すだけ）
│   ├── setup/          環境構築とこの文書
│   └── tasks/          タスクの索引（正は GitHub Issues）
├── tools/              ★本線で使う道具だけを置く
├── tests/              道具のテスト（GPU 不要・pytest）
├── experiments/        本線で使っていないもの。動作を保証しない
│
│  ── 以下は git 管理外 ──
├── TRELLIS.2/          上流（clone）
├── _wheels/            ビルド済みホイールを借りている先（clone）
├── venv/  venv-bpy/    Python 環境（3.11 / bpy 用）
├── out/                生成物
├── scratch/            実行時の一時スクリプトとログ
└── testimg*/           入力の絵（題材ごとに置き換わる）
```

※ `.gitignore` のパターンと一致させること（`/testimg*/`）。

## 守ること

- **`tools/` には本線で使うものだけ置く。** 試したものは `experiments/` へ
- **一時スクリプトとログは `scratch/` へ。** リポジトリ直下に置かない
  （実際に直下へ 46 件溜めてしまい、何が本線か分からなくなった）
- **`docs/measurements/` は書き換えない。** 当時の記録なので、変わったことは注記で足す
- **入力の絵はリポジトリに入れない。** 題材ごとに置き換わるため

## Windows 側で踏んだ文字コードの罠

- SSH 越しの PowerShell 出力は既定で Shift-JIS。UTF-8 のプロファイルを置いて解決した
  （[手順8](windows-ssh.md)）
- **`.ps1` ファイルを UTF-8（BOM なし）で置くと、`powershell -File` が化ける。**
  スクリプトファイルに日本語を書かないか、BOM 付きで保存すること
- Python の出力は別枠。`$env:PYTHONIOENCODING="utf-8"` が要る
