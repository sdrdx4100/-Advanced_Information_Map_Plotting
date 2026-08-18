# 道路勾配データパイプライン（試作）

東名・新東名（御殿場JCT〜浜松いなさJCT）の道路縦断勾配を計算し、Webマップ用の
GeoJSONを生成するオフラインパイプライン。`app/`側のWeb UIとは切り離して実行し、
出力した静的GeoJSONを `public/data/` にコピーしてフロントから読み込む構成。

## データソースと、要件書の想定からの変更点

要件書では道路中心線の一次ソースを国土地理院「電子国土基本図」/ 国交省「全国道路
基盤地図等データベース」としていたが、今回の試作では**OpenStreetMap (ODbL)** を
一次ソースに切り替えた。理由：

- GSIの「高さ付き道路中心線」は全国整備が確認できず、対象区間のカバレッジが不明。
- 国交省の全国道路基盤地図データベースはバルクAPIでの取得可否が未確認（要調査）。
- OSMは東名・新東名の本線について `ref=E1`/`E1A`、`oneway`、`tunnel`/`bridge`
  （トンネル名・橋梁名付き）、IC/JCT/SA/PA名称まで実データを持っており、即座に
  スクリプトから取得できることを確認済み。

本番運用では国交省データが使えるならそちらを優先すべきだが、試作としては
OSMの方が現実的だった。公開時はOSMのODbLライセンス表記が必要。

標高は国土地理院の標高タイル（`https://cyberjapandata.gsi.go.jp/xyz/{dataset}/{z}/{x}/{y}.txt`）
を使用。PNG形式ではなく**プレーンテキストの256x256数値グリッド**（`e`=欠測）。
`dem5a`（5mメッシュLiDAR、高精度だがカバレッジ限定）→ `dem`（旧統合10m/50m
メッシュ、ほぼ全国）の順にフォールバック。

## 処理フロー

```
fetch_osm.py       OSM Overpass API から東名/新東名の way（ジオメトリ+タグ）と
                    IC/JCT/SA/PAノードを取得し data/raw/ にキャッシュ
       ↓
build_pipeline.py
  1. 上り/下り判定      端点とGotemba JCTとの測地距離を比較するヒューリスティック
                        （oneway=yesの場合、way構築順=交通の流れというOSM標準に依拠）
  2. way同士のスティッチ  共有ノードIDでway群を最長の連続ポリラインに接続
  3. 25m等間隔リサンプル  測地距離（pyproj.Geod）で等間隔化、構造タグ(トンネル/
                        橋梁/normal)を各点に引き継ぐ
  4. 標高取得           "normal"点のみGSI DEMタイルをサンプリング（トンネル上の
                        山の標高を拾わないよう、tunnel/bridge点は最初からDEM
                        を参照しない）
  5. ノイズ除去         normal点の標高列に移動中央値(5点=125m窓)を適用
  6. トンネル/橋梁補間   前後のnormal点(平滑化後)から線形補間、quality="estimated"
                        でフラグ
  7. 100m単位で勾配計算  25m点4つ分(100m)の始点・終点標高差 ÷ 実測地距離
  8. IC/JCT紐付け        各セグメントの中間点に最も近い前後の施設名を付与
  9. 異常値チェック      |勾配|>7%のセグメントをqc_report.jsonにフラグ（削除は
                        しない。目視確認用）
       ↓
data/output/
  road-segments.geojson  100m区間ごとのLineString + grade/elevation/quality/structure
  facilities.geojson     IC/JCT/SA/PA名称+座標
  qc_report.json         ルート別最大勾配・平均勾配・異常値リスト
```

## 既知の制約（試作段階）

- **道路中心線自体の高さ情報は未使用**（未入手のため）。全区間がDEM由来
  （`quality: "measured"`）またはトンネル/橋梁補間（`quality: "estimated"`）。
  要件書の「道路中心線に高さがあれば優先」は本番で国交省/GSIデータが取得でき
  次第、対応する。
- 上り/下り判定はヒューリスティック。OSMの`oneway`の向きがまれに逆転している
  区間があれば誤判定しうる（QC推奨: 既知IC間の距離・順序との突合）。
- IC/JCT名の紐付けは最近傍スナップ（250m以内）。分岐・合流部やSA/PAが複数
  ノードに分かれている場所では取りこぼし/重複の可能性あり。
- 橋梁の標高補間は前後端点の線形補間のみ。橋の実際の縦断線形（構造上のわずかな
  凸曲線など）は反映していない。トンネルも同様。
- 国交省データとの精度比較（本要件の「精度チェック」項目）は未実施。まずは
  OSM+DEMベースで動くMVPを作り、後で国交省/GSIソースに差し替え可能な設計に
  してある（`build_pipeline.py`のway読み込み部分を差し替えるだけで済む構成）。

## 実行方法

```bash
cd pipeline
.venv/Scripts/python.exe fetch_osm.py      # 初回のみ（Overpass APIキャッシュ）
.venv/Scripts/python.exe build_pipeline.py # data/output/ に生成
```
