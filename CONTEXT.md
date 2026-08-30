# parts-studio

複数枚の絵から 3D モデルを作るブラウザツール。最終的には顔や体を「パーツ」として個別に作り、
Blender で組み合わせて 1 体にすることを目指す。

## Language

### 生成の材料と成果物

**View（向き）**:
1 つの Part を見る 4 方向のいずれか（front / left / right / back）。1 View につき絵を 1 枚受け取り、
Job は 4 View すべてが揃ってはじめて成立する。
_Avoid_: アングル、視点、カメラ、面

**Reference View（基準の向き）**:
形づくりの条件として実際にモデルへ渡す View。現在は 4 View すべてが Reference View である。
_Avoid_: 入力画像、条件画像、プロンプト画像

**Check View（検算の向き）**:
条件には使わず、出来上がった Part を同じ向きから撮って絵と見比べるために使う View。
**現在は存在しない。** 4 View すべてを条件に使うようになったため、条件に使っていない向きが
無くなった。検算は同じ絵との見比べで行う。
_Avoid_: 参考画像、検証画像、テスト画像

**Shape（形）**:
色もテクスチャ座標も持たない三角形メッシュ。輪郭と凹凸だけを表す。
_Avoid_: メッシュ、ジオメトリ、素体、モデル

**Layout（展開）**:
Shape の各三角形を 1 枚の平面に配置したテクスチャ座標。Shape と Texture をつなぐ唯一の接点。
_Avoid_: UV、UVマップ、アトラス、パラメータ化

**Texture（テクスチャ）**:
Layout に従って Shape へ貼られる画像の組（色・法線・金属・粗さ）。
_Avoid_: 色、マテリアル、スキン、見た目

**Unwrapped Shape（展開済みの形）**:
Shape に Layout を焼き付けたもの。

**現在この状態は保存していない。** 色塗り工程（Hunyuan3D-Paint）が Layout を自分で張るため、
同じ Shape に塗り直すたびに Layout が変わり、Texture どうしに互換性が無い。
「同じ Layout のまま Texture だけ差し替える」ことは今はできない。
経緯は [ADR-0002](docs/adr/0002-unwrap-is-its-own-stage-using-xatlas.md) と
[ADR-0008](docs/adr/0008-texture-by-hunyuan3d-paint.md) を参照。
_Avoid_: UV付きメッシュ、下地、ベースモデル

**Part（パーツ）**:
単体で完結した Unwrapped Shape。自分の原点とスケール基準を持ち、他のパーツを前提としない。
顔・髪・体・衣装のように、入れ替えて組み合わせられる単位を指す。
_Avoid_: 部位、断片、オブジェクト、要素

### 処理の単位

**Job（ジョブ）**:
利用者が 1 回投入する生成処理。形づくり・展開・色塗りはそれぞれ別の Job として走る。
_Avoid_: タスク、リクエスト、処理、実行

**Job Server（ジョブサーバー）**:
利用者の PC 上で GPU を使って Job を実行するサーバー。ブラウザはここに Job を投げるだけで、
自身は推論しない。
_Avoid_: バックエンド、API サーバー、ワーカー
