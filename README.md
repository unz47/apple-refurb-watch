# Apple 整備済製品ウォッチャー

Apple の整備済製品ページ（Mac Studio / Mac mini）を定期的にチェックし、
条件に合う新着が出たら GitHub Issue で通知します。あわせて在庫と価格の履歴を蓄積します。

- **サーバー不要**（GitHub Actions のみ）
- **ランニングコスト0円**（パブリックリポジトリなら実行時間無制限）
- **依存パッケージなし**（Python 標準ライブラリのみ）
- **壊れたら自分で気づく**（抽出が効かなくなったら Issue が立つ）

---

## セットアップ（5分）

### 1. リポジトリを作る

GitHub で新規リポジトリを作成します。**Public を選んでください。**
Private でも動きますが、Free プランの無料枠（月2,000分）を消費します。
Public なら実行時間は無制限に無料です。

### 2. ファイルを置いて push

```bash
git clone https://github.com/<あなたのユーザー名>/apple-refurb-watch.git
cd apple-refurb-watch
# この zip の中身をここに展開する
git add -A
git commit -m "初期セットアップ"
git push
```

### 3. Actions の書き込み権限を許可する

リポジトリの **Settings → Actions → General → Workflow permissions** で
**「Read and write permissions」** を選んで Save してください。

これをやらないと、データのコミットと Issue 作成が権限エラーで失敗します。**最も忘れやすい手順です。**

### 4. 手動で1回動かす

**Actions タブ → 「Apple 整備済製品ウォッチ」→ Run workflow**

初回は「現在の在庫を記録するだけ」で通知は出ません。2回目以降から差分を通知します。
`data/latest.json` が出来ていれば成功です。

### 5. 通知が届くようにする

Issue が立つとメールが飛びます。届かない場合は
[通知設定](https://github.com/settings/notifications) で
「Participating and @mentions」の Email が有効か確認してください。

iPhone の GitHub アプリを入れておくと、プッシュ通知で受け取れます。

---

## 条件を変える

`config.json` を編集して push するだけです。

```json
{
  "source": "mac",
  "alerts": [
    { "name": "Mac Studio の新着", "type": "mac-studio" },
    { "name": "Mac mini の新着",   "type": "mac-mini" },
    { "name": "M4 Max だけ",       "type": "mac-studio", "keyword": "M4 Max", "max_price": 450000 }
  ]
}
```

| キー | 意味 |
|---|---|
| `source` | 取得するページ。`mac` のままでOK（後述） |
| `alerts[].name` | 通知に表示される条件名 |
| `alerts[].type` | 機種。配列で複数指定も可（省略で全機種） |
| `alerts[].keyword` | 商品名に含まれる文字列（省略で絞らない） |
| `alerts[].max_price` / `min_price` | 価格の上限・下限（円、省略で絞らない） |

`type` に指定できる値:

`mac-studio` / `mac-mini` / `mac-pro` / `macbook-pro` / `macbook-air` / `macbook-neo` / `imac` / `display` / `other`

### なぜ「取得ページ」と「機種」が別なのか

**Apple の整備済ページは、`/refurbished/mac/mac-studio` を開いても中のデータには Mac 全機種が入っています。**
URL のカテゴリはブラウザ上での見た目のフィルタでしかありません。

そのため、このツールは `/refurbished/mac` を1回だけ取得し、**商品名から機種を判定**しています
（`watch.py` の `TYPE_RULES`）。URL を信用すると、Mac Studio の条件に MacBook Air が引っかかります。

副次的な利点として、何機種を監視しても Apple へのアクセスは1回で済みます。

## 実行間隔を変える

`.github/workflows/watch.yml` の `cron` を編集します（**UTC 表記**です）。

```yaml
- cron: '17 * * * *'      # 1時間おき（デフォルト）
- cron: '17 */3 * * *'    # 3時間おき
- cron: '17 */6 * * *'    # 6時間おき
```

毎時00分は GitHub 全体が混雑して実行が遅延しやすいので、17分にずらしてあります。
GitHub の cron はもともと数分〜数十分の遅れが出ます。分単位の正確さは期待しないでください。

---

## 溜まるデータ

| ファイル | 中身 |
|---|---|
| `data/latest.json` | 最新の在庫スナップショット（差分検知に使う） |
| `data/history.jsonl` | 全チェックの記録を1行1件で追記。**これが資産になります** |
| `data/prices.csv` | 同じ内容の CSV。Excel でそのまま開けます |
| `data/health.json` | 直近の取得状況と連続0件回数 |

`type` 列が入っているので、後から「Mac Studio だけ」「M4 Max だけ」といった集計が簡単にできます。

1年回すと「M4 Max 64GB の整備済は年に何回出て、最安はいくらで、何時間で消えたか」が分かるようになります。

---

## ローカルで動かす

```bash
python3 watch.py              # 取得して表示（データも更新される）
python3 watch.py --dry-run    # 表示だけ、ファイルは書き換えない
python3 watch.py --dump-html  # 生HTMLを data/ に保存（構造調査用）
```

Mac なら新着時に通知センターにも出ます。

---

## メンテナンスについて

**このツールは放っておくと必ず壊れます。** Apple がページ構造を変えると抽出が0件になるためです。
そのため、連続で0件が続くと「⚠️ 要メンテ」Issue が自動で立つようにしてあります。

閾値は `config.json` の `health.zero_streak_threshold`（デフォルト12回 = 1時間おきなら半日）。
整備済 Mac Studio は本当に在庫ゼロの期間が長いことがあるので、
Issue が立ったらまず[実際のページ](https://www.apple.com/jp/shop/refurbished/mac/mac-studio)を目で確認してください。

抽出は「埋め込みJSON → HTMLパース」の2段構えで、片方が壊れてももう片方で拾える設計です。
それでもダメな時は `--dump-html` で保存した HTML を見て正規表現を直すことになります。

### 60日ルールについて

GitHub は60日間リポジトリに動きがないと、スケジュール実行を自動で無効化します。
このワークフローは毎回 `data/` をコミットするので、通常は自動的に回避されます。
ただし**在庫が全く変化しない状態が60日続くと止まる**可能性があるので、
長期間 Issue もコミットも見かけなくなったら Actions タブを確認してください。

---

## 注意

- Apple のサイトには常識的な頻度でアクセスしてください。1時間おきは1日24リクエスト程度で、人間がブラウザで見るのと変わらない負荷です
- GitHub Actions はデータセンターの IP から実行されるため、将来 Apple 側にブロックされる可能性はあります。その場合は自宅の Mac で launchd 実行に切り替えるのが確実です
- 表示される情報は参考です。購入前に必ず Apple 公式ページで確認してください
