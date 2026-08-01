連続して商品が0件でした。Apple 側のページ構造が変わって、抽出ロジックが効かなくなっている可能性があります。

### 確認手順

1. [Mac Studio](https://www.apple.com/jp/shop/refurbished/mac/mac-studio) と [Mac mini](https://www.apple.com/jp/shop/refurbished/mac/mac-mini) をブラウザで開き、実際に商品が載っているか確認する
2. 載っているのにこの Issue が立っているなら、抽出ロジックの修正が必要
3. ローカルで `python3 watch.py --dump-html` を実行し、`data/debug_*.html` を見て構造を確認する

直近の状態は `data/health.json` に記録されています。

なお、単に在庫が本当に無いだけの場合もあります（整備済 Mac Studio は品切れが続くことがあります）。その場合はこの Issue を閉じてください。閾値は `config.json` の `health.zero_streak_threshold` で調整できます。
