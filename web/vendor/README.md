# 同梱している外部ファイル

| ファイル | 出どころ | 版 | ライセンス |
| :--- | :--- | :--- | :--- |
| `model-viewer.min.js` | [@google/model-viewer](https://www.npmjs.com/package/@google/model-viewer)（`dist/model-viewer.min.js` をそのまま） | 4.3.1 | Apache-2.0（`model-viewer.LICENSE`） |

## なぜ同梱するのか

- **実行時に CDN を引かない。** オフラインでも動き、供給元の変化にも左右されない
- ジョブサーバーが `/vendor/...` で配る（UI と同一オリジン）

## 更新のしかた

```
curl -sO https://registry.npmjs.org/@google/model-viewer/-/model-viewer-<版>.tgz
tar xzf model-viewer-<版>.tgz package/dist/model-viewer.min.js package/LICENSE
```

中身は書き換えないこと（そのまま差し替える）。
