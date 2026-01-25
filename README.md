# PCゲームお得情報キュレーションサイト

PCゲームの無料配布、バンドル、セール、レビュー記事を自動収集・整理して提供するキュレーションサイトです。

## 主要機能

- **自動データ収集**: Epic Games、Steam、Reddit等から自動取得
- **AI翻訳・要約**: 英語記事を自動翻訳し、自然な日本語説明文を生成
- **カテゴリ分類**: 無料/バンドル/セール/レビューの4カテゴリ
- **レスポンシブUI**: PC/タブレット/スマホ対応
- **自動更新**: 毎日朝9時・夜9時に自動実行

## データソース

### 無料ゲーム
- Epic Games Store（公式API）
- Reddit - FreeGamesOnSteam

### バンドル
- Humble Bundle
- Fanatical
- IndieGala
- Itch.io
- Reddit - GameDeals

### セール
- Steam
- GOG
- IsThereAnyDeal
- Reddit - GameDeals

### レビュー記事
- AUTOMATON（日本語）
- doope!（日本語）
- インサイド（日本語）
- PC Gamer（英語）
- Rock Paper Shotgun（英語）
- Polygon（英語）

## セットアップ

### ローカル環境

1. 依存パッケージをインストール
```bash
pip install -r requirements.txt
```

2. 環境変数を設定（オプション）
```bash
cp .env.example .env
# .envファイルを編集してGROQ_API_KEYを設定
```

3. 更新スクリプトを実行
```bash
python scripts/update-deals.py
```

### GitHub Actions（自動更新）

1. リポジトリの Settings → Secrets and variables → Actions
2. `New repository secret` をクリック
3. `GROQ_API_KEY` を追加（オプション）

## 技術スタック

- **フロントエンド**: HTML5, CSS3, Vanilla JavaScript
- **バックエンド**: Python 3.11
- **ホスティング**: GitHub Pages
- **CI/CD**: GitHub Actions

## ディレクトリ構造

```
/
├── .github/
│   └── workflows/
│       ├── update-games.yml       # 自動更新ワークフロー
│       └── deploy-pages.yml       # GitHub Pages デプロイ
├── scripts/
│   └── update-deals.py            # メイン更新スクリプト
├── .env.example                   # 環境変数テンプレート
├── .gitignore
├── requirements.txt               # Python依存パッケージ
├── README.md                      # プロジェクト説明
├── index.html                     # フロントエンド
└── games-data.json                # ゲーム情報データベース
```

## ライセンス

MIT License
