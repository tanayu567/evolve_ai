# evolve_ai
shadowverse EVOLVE のAIを作成するためのリポジトリ

## Docker で `sve_scrape.py` を実行する

このリポジトリには Shadowverse EVOLVE のカード情報を取得するスクレイパー `sve_scrape.py` が含まれています。Docker を使って依存関係込みで実行できます。

### イメージのビルド

```
docker build -t sve-scraper .
```

### 使い方（例）

- 動作確認として10件だけ取得して標準の出力先（コンテナ内 `/app/cards.tsv`）に保存:

```
docker run --rm sve-scraper --limit 10
```

- ホスト側にファイルを出したい場合はボリュームをマウントして `--out` で保存先を指定:

```
docker run --rm -v "$PWD/data:/data" sve-scraper --limit 50 --out /data/cards.tsv
```

- 収録弾を絞って取得（例: BP16 と CP01）:

```
docker run --rm -v "$PWD/data:/data" sve-scraper \
  --only-expansion BP16 --only-expansion CP01 \
  --delay 1.0 --out /data/cards.tsv
```

- 任意の検索URL（カードリスト/カード検索）から取得（ページネーション対応）:

```
docker run --rm -v "$PWD/data:/data" sve-scraper \
  --search-url 'https://shadowverse-evolve.com/cardlist/?card_name=&class%5B0%5D=all&title=&expansion_name=BP01&cost%5B0%5D=all&card_kind%5B0%5D=all&rare%5B0%5D=all&power_from=&power_to=&hp_from=&hp_to=&type=&ability=&keyword=&view=image' \
  --out /data/cards.tsv
```

- cardsearch（無限スクロール）URLの例（ご提示のBP01, 273件相当）:

```
docker run --rm -v "$PWD/data:/data" sve-scraper \
  --search-url 'https://shadowverse-evolve.com/cardlist/cardsearch/?card_name=&class%5B%5D=all&title=&expansion_name=BP01&cost%5B%5D=all&card_kind%5B%5D=all&rare%5B%5D=all&power_from=&power_to=&hp_from=&hp_to=&type=&ability=&keyword=&view=image' \
  --out /data/cards.tsv
```

メモ: cardsearch のページは無限スクロール（`cardsearch_ex`）で次ページを読み込みます。本ツールはページ内の `max_page` を検出して、2ページ目以降も自動でクロールします。

複数の `--search-url` を並べることも可能です。`&` を含むURLはシェルの解釈を避けるため、クォートしてください。

### スクリプトのオプション

- `--limit N`: 取得枚数の上限（テスト用）
- `--only-expansion CODE`: 収録弾コードを指定（複数指定可, 例: `BP16`）
- `--delay SEC`: リクエスト間の遅延秒数
- `--out PATH`: 出力TSVのパス

依存関係は `requirements.txt` に記載されています。
