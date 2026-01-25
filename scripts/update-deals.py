#!/usr/bin/env python3
"""
PCゲームお得情報キュレーションサイト - 自動更新スクリプト

機能:
- 無料ゲーム取得 (Epic Games, Reddit r/FreeGameFindings, Reddit r/FreeGamesOnSteam, SteamDB)
- バンドル取得 (Humble Bundle, Fanatical, IndieGala, Itch.io, Reddit)
- セール情報取得 (Steam, GOG, IsThereAnyDeal, Reddit)
- レビュー記事取得 (RSS: AUTOMATON, doope!, インサイド, PC Gamer, RPS, Polygon)
- 翻訳・AI要約機能
- データクリーニング (重複排除、古いデータ削除)
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from dotenv import load_dotenv

# 環境変数読み込み
load_dotenv()

# 設定
BASE_DIR = Path(__file__).parent.parent
DATA_FILE = BASE_DIR / "games-data.json"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# User-Agent設定
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

# RSSフィードURL
RSS_FEEDS = {
    "AUTOMATON": "https://automaton-media.com/feed/",
    "doope!": "https://doope.jp/feed",
    "インサイド": "https://www.inside-games.jp/rss/index.rdf",
    "PC Gamer": "https://www.pcgamer.com/rss/",
    "Rock Paper Shotgun": "https://www.rockpapershotgun.com/feed",
    "Polygon": "https://www.polygon.com/rss/index.xml",
}

# 英語メディアリスト
ENGLISH_MEDIA = ["PC Gamer", "Rock Paper Shotgun", "Polygon"]


def retry_on_failure(max_retries=3, delay=2):
    """デコレータ: 失敗時にリトライ"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"❌ {func.__name__} failed after {max_retries} attempts: {e}")
                        return []
                    print(f"⚠️ Attempt {attempt + 1} failed, retrying in {delay}s...")
                    time.sleep(delay)
            return []
        return wrapper
    return decorator


# =============================================================================
# テキスト処理関数
# =============================================================================

def clean_reddit_meta(text):
    """Redditメタデータを完全削除"""
    if not text:
        return ""

    patterns = [
        r'submitted by /u/\w+',
        r'/u/\w+',
        r'\[link\]',
        r'\[comments\]',
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+',
        r'\d+ points?',
        r'\d+ comments?',
        r'&amp;',
        r'&nbsp;',
        r'&#\d+;',
    ]

    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

    # HTMLタグ削除
    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    # 余分な空白削除
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def translate_to_japanese(text):
    """Google翻訳（英→日）"""
    if not text:
        return ""

    try:
        translator = GoogleTranslator(source='en', target='ja')
        # 長いテキストは分割
        if len(text) > 4500:
            text = text[:4500]
        translated = translator.translate(text)
        return translated if translated else text
    except Exception as e:
        print(f"⚠️ Translation error: {e}")
        return text


def truncate_description(description, min_len=150, max_len=200):
    """説明文を150-200文字に調整（文の途中で切らない）"""
    if not description:
        return ""

    description = description.strip()

    if len(description) <= max_len:
        return description

    # max_len以内で最後の句点を探す
    truncated = description[:max_len]
    last_period = truncated.rfind('。')

    if last_period > min_len:
        return description[:last_period + 1]
    else:
        # 句点が見つからない場合は読点で切る
        last_comma = truncated.rfind('、')
        if last_comma > min_len:
            return description[:last_comma] + '。'
        # それでもダメならmax_lenで切って「…」を追加
        return description[:max_len - 1] + '…'


def humanize_text_with_ai(text, game_data=None):
    """Groq APIで自然な日本語化"""
    if not GROQ_API_KEY:
        return text

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        game_type = game_data.get('type', 'free') if game_data else 'free'

        type_instructions = {
            'free': "ゲームのジャンル、特徴・魅力（2-3点）、配布プラットフォームを含めてください。",
            'bundle': "バンドル名、主要タイトル（2-3本）、ジャンル、大まかな価格帯を含めてください。",
            'sale': "ゲームのジャンル・特徴、割引率または価格、ゲームの評価・人気度を含めてください。",
            'review': "ゲームの評価ポイント、レビューの内容概要（2-3点）、対象読者を含めてください。"
        }

        instruction = type_instructions.get(game_type, type_instructions['free'])

        prompt = f"""以下のテキストを自然な日本語の説明文に書き直してください。

ルール:
- 150〜200文字で完結させる
- です・ます調で統一
- 改行なし（1段落）
- 文末まで完結させる（途中で切らない）
- {instruction}

テキスト:
{text}

説明文:"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=300
        )

        result = response.choices[0].message.content.strip()
        return truncate_description(result)
    except Exception as e:
        print(f"⚠️ AI humanization error: {e}")
        return truncate_description(text)


def generate_description_with_ai(game_data):
    """ゲームデータから説明文を生成"""
    title = game_data.get('title', '')
    platform = game_data.get('platform', '')
    game_type = game_data.get('type', 'free')
    original_desc = game_data.get('description', '')

    # 既に説明文があればそれを使用
    if original_desc and len(original_desc) >= 100:
        return humanize_text_with_ai(original_desc, game_data)

    # 基本的な説明文を生成
    if game_type == 'free':
        base_desc = f"{title}が{platform}で無料配布中です。"
    elif game_type == 'bundle':
        base_desc = f"{title}のバンドルが{platform}で販売中です。"
    elif game_type == 'sale':
        discount = game_data.get('discount', '')
        base_desc = f"{title}が{platform}で{discount}セール中です。"
    else:
        base_desc = f"{title}のレビュー記事です。"

    return humanize_text_with_ai(base_desc, game_data)


def simplify_title(title):
    """タイトルを簡潔化"""
    if not title:
        return ""

    # 余分な情報を削除
    patterns = [
        r'\s*\([^)]*\)',  # 括弧内を削除
        r'\s*\[[^\]]*\]',  # 角括弧内を削除
        r'\s*-\s*\d+%.*',  # 割引情報を削除
        r'\s*FREE.*',
        r'\s*無料.*',
    ]

    simplified = title
    for pattern in patterns:
        simplified = re.sub(pattern, '', simplified, flags=re.IGNORECASE)

    return simplified.strip()


def format_review_title(title, media_name=""):
    """レビュータイトルを【ゲーム名：レビュー】形式に整形"""
    if not title:
        return "【レビュー】"

    # 既に整形済みの場合はそのまま返す
    if title.startswith('【') and '：' in title:
        return title

    # "Review:" や "レビュー" などを削除
    game_name = re.sub(r'(review|レビュー|評価|感想)[：:]\s*', '', title, flags=re.IGNORECASE)
    game_name = re.sub(r'\s*(review|レビュー|評価|感想).*$', '', game_name, flags=re.IGNORECASE)
    game_name = game_name.strip()

    if len(game_name) > 30:
        game_name = game_name[:30] + '…'

    return f"【{game_name}：レビュー】"


def normalize_title(title):
    """タイトルを正規化（重複チェック用）"""
    if not title:
        return ""

    # 記号・数字を一部除去、小文字化
    cleaned = re.sub(r'[^\w\s]', '', title.lower())
    # 最初の5-6単語を取得
    words = cleaned.split()[:6]
    return ' '.join(words)


# =============================================================================
# データ取得関数
# =============================================================================

@retry_on_failure(max_retries=3, delay=2)
def fetch_epic_free_games():
    """Epic Games Store APIから無料ゲーム取得"""
    print("📥 Fetching Epic Games free games...")

    url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    params = {
        "locale": "ja",
        "country": "JP",
        "allowCountries": "JP"
    }

    response = requests.get(url, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()

    games = []
    elements = data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", [])

    for element in elements:
        # 現在無料のゲームをフィルタリング
        promotions = element.get("promotions")
        if not promotions:
            continue

        current_offers = promotions.get("promotionalOffers", [])
        if not current_offers:
            continue

        # 無料オファーを確認
        for offer_group in current_offers:
            for offer in offer_group.get("promotionalOffers", []):
                discount_percentage = offer.get("discountSetting", {}).get("discountPercentage", 0)
                if discount_percentage == 0:
                    title = element.get("title", "")
                    description = element.get("description", "")

                    # URLを構築
                    product_slug = element.get("productSlug") or element.get("urlSlug", "")
                    if product_slug:
                        game_url = f"https://store.epicgames.com/ja/p/{product_slug}"
                    else:
                        game_url = "https://store.epicgames.com/ja/free-games"

                    # 期限取得
                    end_date = offer.get("endDate", "")
                    deadline = ""
                    if end_date:
                        try:
                            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                            deadline = end_dt.strftime("%Y年%m月%d日まで")
                        except:
                            pass

                    # 元の価格取得
                    original_price = ""
                    price_info = element.get("price", {}).get("totalPrice", {})
                    if price_info:
                        original = price_info.get("originalPrice", 0)
                        if original > 0:
                            original_price = f"¥{original // 100:,}"

                    game_data = {
                        "title": title,
                        "platform": "Epic Games",
                        "type": "free",
                        "description": translate_to_japanese(description) if description else "",
                        "price": "無料",
                        "originalPrice": original_price,
                        "discount": "100% OFF",
                        "deadline": deadline,
                        "url": game_url,
                        "date": datetime.now().strftime("%Y-%m-%d")
                    }

                    # 説明文を調整
                    game_data["description"] = truncate_description(
                        generate_description_with_ai(game_data) if GROQ_API_KEY else game_data["description"]
                    )

                    games.append(game_data)

    print(f"✅ Found {len(games)} Epic Games free games")
    return games


@retry_on_failure(max_retries=3, delay=2)
def fetch_reddit_free_games():
    """Reddit r/FreeGamesOnSteam から無料ゲーム取得"""
    print("📥 Fetching Reddit free games...")

    url = "https://www.reddit.com/r/FreeGamesOnSteam/hot.json"
    headers = {**HEADERS, "Accept": "application/json"}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    games = []
    posts = data.get("data", {}).get("children", [])

    for post in posts[:20]:  # 最新20件
        post_data = post.get("data", {})

        # 固定投稿をスキップ
        if post_data.get("stickied"):
            continue

        title = post_data.get("title", "")
        url = post_data.get("url", "")
        selftext = post_data.get("selftext", "")

        # タイトルから不要な情報を削除
        title = simplify_title(title)

        # プラットフォーム判定
        platform = "Steam"
        if "epic" in url.lower() or "epic" in title.lower():
            platform = "Epic Games"
        elif "gog" in url.lower() or "gog" in title.lower():
            platform = "GOG"
        elif "humble" in url.lower():
            platform = "Humble Bundle"

        # 説明文を生成
        description = clean_reddit_meta(selftext) if selftext else ""
        if not description:
            description = f"{title}が{platform}で無料配布中です。"
        description = translate_to_japanese(description) if not any(ord(c) > 127 for c in description[:10]) else description

        game_data = {
            "title": title,
            "platform": platform,
            "type": "free",
            "description": "",
            "price": "無料",
            "originalPrice": "",
            "discount": "100% OFF",
            "deadline": "",
            "url": url,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        game_data["description"] = truncate_description(
            generate_description_with_ai(game_data) if GROQ_API_KEY else description
        )

        games.append(game_data)

    print(f"✅ Found {len(games)} Reddit free games")
    return games


@retry_on_failure(max_retries=3, delay=2)
def fetch_reddit_freegamefindings():
    """Reddit r/FreeGameFindings から無料ゲーム取得（世界で最も早い情報源）"""
    print("📥 Fetching Reddit r/FreeGameFindings...")

    url = "https://www.reddit.com/r/FreeGameFindings/hot.json"
    headers = {**HEADERS, "Accept": "application/json"}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    games = []
    posts = data.get("data", {}).get("children", [])

    for post in posts[:30]:  # 最新30件
        post_data = post.get("data", {})

        # 固定投稿をスキップ
        if post_data.get("stickied"):
            continue

        title = post_data.get("title", "")
        post_url = post_data.get("url", "")
        flair = post_data.get("link_flair_text", "")

        # PCゲームのみフィルタリング（Steam, Epic, GOG, Itch.io等）
        pc_keywords = ["steam", "epic", "gog", "itch", "humble", "indiegala", "fanatical", "pc", "drm-free"]
        is_pc = any(kw in title.lower() or kw in flair.lower() or kw in post_url.lower() for kw in pc_keywords)

        # 期限切れや無効なものをスキップ
        skip_keywords = ["expired", "ended", "psa", "other"]
        should_skip = any(kw in flair.lower() for kw in skip_keywords)

        if not is_pc or should_skip:
            continue

        # タイトルからプラットフォームとゲーム名を抽出
        # 形式: [Platform] Game Name (DLC/Free/100% off)
        platform = "Steam"
        game_title = title

        # プラットフォーム判定
        title_lower = title.lower()
        if "[epic" in title_lower or "epic games" in title_lower:
            platform = "Epic Games"
        elif "[gog" in title_lower or "gog.com" in title_lower:
            platform = "GOG"
        elif "[itch" in title_lower or "itch.io" in title_lower:
            platform = "Itch.io"
        elif "[humble" in title_lower:
            platform = "Humble Bundle"
        elif "[indiegala" in title_lower:
            platform = "IndieGala"

        # ゲームタイトルをクリーンアップ
        game_title = re.sub(r'\[.*?\]', '', game_title)  # [Platform] を削除
        game_title = re.sub(r'\(.*?\)', '', game_title)  # (Free/100% off) を削除
        game_title = game_title.strip()

        if not game_title:
            continue

        game_data = {
            "title": game_title,
            "platform": platform,
            "type": "free",
            "description": "",
            "price": "無料",
            "originalPrice": "",
            "discount": "100% OFF",
            "deadline": "",
            "url": post_url,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        game_data["description"] = truncate_description(
            generate_description_with_ai(game_data) if GROQ_API_KEY else f"{game_title}が{platform}で無料配布中です。r/FreeGameFindingsで発見された情報です。"
        )

        games.append(game_data)

    print(f"✅ Found {len(games)} r/FreeGameFindings free games")
    return games


@retry_on_failure(max_retries=3, delay=2)
def fetch_steamdb_free_games():
    """SteamDB から無料/一時無料ゲーム取得"""
    print("📥 Fetching SteamDB free games...")

    # SteamDB の無料プロモーションページ
    url = "https://steamdb.info/upcoming/free/"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        games = []

        # テーブルからゲーム情報を抽出
        table = soup.select_one('table.table')
        if not table:
            print("⚠️ SteamDB table not found")
            return []

        rows = table.select('tbody tr')

        for row in rows[:20]:
            cells = row.select('td')
            if len(cells) < 2:
                continue

            # ゲームタイトルとリンク
            title_cell = cells[1]
            link = title_cell.select_one('a')
            if not link:
                continue

            title = link.get_text(strip=True)
            href = link.get('href', '')

            # Steam URLを構築
            app_id_match = re.search(r'/app/(\d+)', href)
            if app_id_match:
                app_id = app_id_match.group(1)
                game_url = f"https://store.steampowered.com/app/{app_id}"
            else:
                game_url = f"https://steamdb.info{href}" if href.startswith('/') else href

            # 期間情報があれば取得
            deadline = ""
            if len(cells) >= 4:
                date_cell = cells[3]
                date_text = date_cell.get_text(strip=True)
                if date_text:
                    deadline = date_text

            game_data = {
                "title": title,
                "platform": "Steam",
                "type": "free",
                "description": "",
                "price": "無料",
                "originalPrice": "",
                "discount": "100% OFF",
                "deadline": deadline,
                "url": game_url,
                "date": datetime.now().strftime("%Y-%m-%d")
            }

            game_data["description"] = truncate_description(
                generate_description_with_ai(game_data) if GROQ_API_KEY else f"{title}がSteamで期間限定無料配布中です。SteamDBで検出された情報です。"
            )

            games.append(game_data)

        print(f"✅ Found {len(games)} SteamDB free games")
        return games

    except Exception as e:
        print(f"⚠️ SteamDB fetch error: {e}")
        return []


@retry_on_failure(max_retries=3, delay=2)
def fetch_humble_bundle_direct():
    """Humble Bundle から直接バンドル取得"""
    print("📥 Fetching Humble Bundle...")

    url = "https://www.humblebundle.com/bundles"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    bundles = []

    # バンドルカードを探す
    bundle_cards = soup.select('.content-wrap a[href*="/games/"]')

    for card in bundle_cards[:10]:
        href = card.get('href', '')
        if not href.startswith('http'):
            href = f"https://www.humblebundle.com{href}"

        title_elem = card.select_one('.content-title, .name, h2, .item-title')
        title = title_elem.get_text(strip=True) if title_elem else "Humble Bundle"

        price_elem = card.select_one('.price, .item-price')
        price = price_elem.get_text(strip=True) if price_elem else ""

        bundle_data = {
            "title": title,
            "platform": "Humble Bundle",
            "type": "bundle",
            "description": "",
            "price": price,
            "originalPrice": "",
            "discount": "",
            "deadline": "",
            "url": href,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        bundle_data["description"] = truncate_description(
            generate_description_with_ai(bundle_data) if GROQ_API_KEY else f"{title}のバンドルがHumble Bundleで販売中です。複数のゲームがお得な価格でまとめて購入できます。"
        )

        bundles.append(bundle_data)

    print(f"✅ Found {len(bundles)} Humble Bundle bundles")
    return bundles


@retry_on_failure(max_retries=3, delay=2)
def fetch_fanatical_bundles():
    """Fanatical からバンドル取得"""
    print("📥 Fetching Fanatical bundles...")

    url = "https://www.fanatical.com/en/bundle"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    bundles = []

    # バンドルカードを探す
    bundle_cards = soup.select('a[href*="/bundle/"]')
    seen_urls = set()

    for card in bundle_cards[:10]:
        href = card.get('href', '')
        if href in seen_urls:
            continue
        seen_urls.add(href)

        if not href.startswith('http'):
            href = f"https://www.fanatical.com{href}"

        title_elem = card.select_one('.bundle-card-title, h3, h4, .title')
        title = title_elem.get_text(strip=True) if title_elem else "Fanatical Bundle"

        if not title or len(title) < 3:
            continue

        bundle_data = {
            "title": title,
            "platform": "Fanatical",
            "type": "bundle",
            "description": "",
            "price": "",
            "originalPrice": "",
            "discount": "",
            "deadline": "",
            "url": href,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        bundle_data["description"] = truncate_description(
            generate_description_with_ai(bundle_data) if GROQ_API_KEY else f"{title}のバンドルがFanaticalで販売中です。Steamキーが含まれるお得なパッケージです。"
        )

        bundles.append(bundle_data)

    print(f"✅ Found {len(bundles)} Fanatical bundles")
    return bundles


@retry_on_failure(max_retries=3, delay=2)
def fetch_indiegala_bundles():
    """IndieGala からバンドル取得"""
    print("📥 Fetching IndieGala bundles...")

    url = "https://www.indiegala.com/bundles"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    bundles = []

    bundle_cards = soup.select('a[href*="/bundle"]')
    seen_urls = set()

    for card in bundle_cards[:10]:
        href = card.get('href', '')
        if href in seen_urls or '/bundles' == href:
            continue
        seen_urls.add(href)

        if not href.startswith('http'):
            href = f"https://www.indiegala.com{href}"

        title_elem = card.select_one('.bundle-title, h3, h4, .title, figcaption')
        title = title_elem.get_text(strip=True) if title_elem else ""

        if not title or len(title) < 3:
            # カード内のテキストを取得
            title = card.get_text(strip=True)[:50] or "IndieGala Bundle"

        bundle_data = {
            "title": title,
            "platform": "IndieGala",
            "type": "bundle",
            "description": "",
            "price": "",
            "originalPrice": "",
            "discount": "",
            "deadline": "",
            "url": href,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        bundle_data["description"] = truncate_description(
            generate_description_with_ai(bundle_data) if GROQ_API_KEY else f"{title}のバンドルがIndieGalaで販売中です。インディーゲームがお得な価格で手に入ります。"
        )

        bundles.append(bundle_data)

    print(f"✅ Found {len(bundles)} IndieGala bundles")
    return bundles


@retry_on_failure(max_retries=3, delay=2)
def fetch_itchio_bundles():
    """Itch.io からバンドル取得"""
    print("📥 Fetching Itch.io bundles...")

    url = "https://itch.io/bundles"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    bundles = []

    bundle_cards = soup.select('.game_cell, .bundle_cell')

    for card in bundle_cards[:10]:
        link = card.select_one('a.title, a.game_link')
        if not link:
            continue

        href = link.get('href', '')
        title = link.get_text(strip=True)

        if not title:
            continue

        price_elem = card.select_one('.price_value, .sale_price')
        price = price_elem.get_text(strip=True) if price_elem else ""

        bundle_data = {
            "title": title,
            "platform": "Itch.io",
            "type": "bundle",
            "description": "",
            "price": price,
            "originalPrice": "",
            "discount": "",
            "deadline": "",
            "url": href,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        bundle_data["description"] = truncate_description(
            generate_description_with_ai(bundle_data) if GROQ_API_KEY else f"{title}のバンドルがItch.ioで販売中です。独創的なインディーゲームが多数含まれています。"
        )

        bundles.append(bundle_data)

    print(f"✅ Found {len(bundles)} Itch.io bundles")
    return bundles


@retry_on_failure(max_retries=3, delay=2)
def fetch_reddit_bundles():
    """Reddit r/GameDeals からバンドル情報取得"""
    print("📥 Fetching Reddit bundles...")

    url = "https://www.reddit.com/r/GameDeals/search.json"
    params = {
        "q": "bundle",
        "restrict_sr": "on",
        "sort": "new",
        "t": "week",
        "limit": 20
    }
    headers = {**HEADERS, "Accept": "application/json"}

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    bundles = []
    posts = data.get("data", {}).get("children", [])

    for post in posts[:15]:
        post_data = post.get("data", {})

        if post_data.get("stickied"):
            continue

        title = post_data.get("title", "")
        url = post_data.get("url", "")

        # タイトルにbundleが含まれているか確認
        if "bundle" not in title.lower():
            continue

        title = simplify_title(title)

        # プラットフォーム判定
        platform = "Various"
        if "humble" in url.lower():
            platform = "Humble Bundle"
        elif "fanatical" in url.lower():
            platform = "Fanatical"
        elif "indiegala" in url.lower():
            platform = "IndieGala"

        bundle_data = {
            "title": title,
            "platform": platform,
            "type": "bundle",
            "description": "",
            "price": "",
            "originalPrice": "",
            "discount": "",
            "deadline": "",
            "url": url,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        bundle_data["description"] = truncate_description(
            generate_description_with_ai(bundle_data) if GROQ_API_KEY else f"{title}のバンドルが{platform}で販売中です。"
        )

        bundles.append(bundle_data)

    print(f"✅ Found {len(bundles)} Reddit bundles")
    return bundles


@retry_on_failure(max_retries=3, delay=2)
def fetch_steam_sales():
    """Steam からセール情報取得"""
    print("📥 Fetching Steam sales...")

    url = "https://store.steampowered.com/api/featuredcategories"
    params = {"cc": "jp", "l": "japanese"}

    response = requests.get(url, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()

    sales = []

    # スペシャルオファーを取得
    specials = data.get("specials", {}).get("items", [])

    for item in specials[:15]:
        title = item.get("name", "")
        app_id = item.get("id", "")

        discount_percent = item.get("discount_percent", 0)
        if discount_percent < 50:  # 50%以上のセールのみ
            continue

        final_price = item.get("final_price", 0) / 100
        original_price = item.get("original_price", 0) / 100

        sale_data = {
            "title": title,
            "platform": "Steam",
            "type": "sale",
            "description": "",
            "price": f"¥{int(final_price):,}",
            "originalPrice": f"¥{int(original_price):,}",
            "discount": f"{discount_percent}% OFF",
            "deadline": "",
            "url": f"https://store.steampowered.com/app/{app_id}",
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        sale_data["description"] = truncate_description(
            generate_description_with_ai(sale_data) if GROQ_API_KEY else f"{title}がSteamで{discount_percent}%オフのセール中です。"
        )

        sales.append(sale_data)

    print(f"✅ Found {len(sales)} Steam sales")
    return sales


@retry_on_failure(max_retries=3, delay=2)
def fetch_gog_sales():
    """GOG からセール情報取得"""
    print("📥 Fetching GOG sales...")

    url = "https://www.gog.com/games/ajax/filtered"
    params = {
        "mediaType": "game",
        "page": 1,
        "sort": "discount",
        "discounted": "true",
        "limit": 20
    }

    response = requests.get(url, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()

    sales = []
    products = data.get("products", [])

    for product in products[:15]:
        title = product.get("title", "")
        slug = product.get("slug", "")

        price_data = product.get("price", {})
        discount_percent = price_data.get("discountPercentage", 0)

        if discount_percent < 50:
            continue

        final_price = price_data.get("finalAmount", "")
        base_price = price_data.get("baseAmount", "")

        sale_data = {
            "title": title,
            "platform": "GOG",
            "type": "sale",
            "description": "",
            "price": f"${final_price}" if final_price else "",
            "originalPrice": f"${base_price}" if base_price else "",
            "discount": f"{discount_percent}% OFF",
            "deadline": "",
            "url": f"https://www.gog.com/game/{slug}",
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        sale_data["description"] = truncate_description(
            generate_description_with_ai(sale_data) if GROQ_API_KEY else f"{title}がGOGで{discount_percent}%オフのセール中です。DRMフリーで購入できます。"
        )

        sales.append(sale_data)

    print(f"✅ Found {len(sales)} GOG sales")
    return sales


@retry_on_failure(max_retries=3, delay=2)
def fetch_isthereanydeal_sales():
    """IsThereAnyDeal からセール情報取得（スクレイピング）"""
    print("📥 Fetching IsThereAnyDeal sales...")

    url = "https://isthereanydeal.com/"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    sales = []

    deal_cards = soup.select('.game, .deal')

    for card in deal_cards[:15]:
        link = card.select_one('a[href*="/game/"]')
        if not link:
            continue

        href = link.get('href', '')
        if not href.startswith('http'):
            href = f"https://isthereanydeal.com{href}"

        title_elem = card.select_one('.title, h3, h4')
        title = title_elem.get_text(strip=True) if title_elem else ""

        if not title:
            continue

        price_elem = card.select_one('.price-new, .price')
        price = price_elem.get_text(strip=True) if price_elem else ""

        discount_elem = card.select_one('.discount, .cut')
        discount = discount_elem.get_text(strip=True) if discount_elem else ""

        sale_data = {
            "title": title,
            "platform": "Various",
            "type": "sale",
            "description": "",
            "price": price,
            "originalPrice": "",
            "discount": discount,
            "deadline": "",
            "url": href,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        sale_data["description"] = truncate_description(
            generate_description_with_ai(sale_data) if GROQ_API_KEY else f"{title}が複数のストアでセール中です。"
        )

        sales.append(sale_data)

    print(f"✅ Found {len(sales)} IsThereAnyDeal sales")
    return sales


@retry_on_failure(max_retries=3, delay=2)
def fetch_reddit_sales():
    """Reddit r/GameDeals からセール情報取得"""
    print("📥 Fetching Reddit sales...")

    url = "https://www.reddit.com/r/GameDeals/hot.json"
    headers = {**HEADERS, "Accept": "application/json"}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    sales = []
    posts = data.get("data", {}).get("children", [])

    for post in posts[:20]:
        post_data = post.get("data", {})

        if post_data.get("stickied"):
            continue

        title = post_data.get("title", "")
        url = post_data.get("url", "")

        # バンドルはスキップ
        if "bundle" in title.lower():
            continue

        title = simplify_title(title)

        # プラットフォーム判定
        platform = "Various"
        if "steam" in url.lower():
            platform = "Steam"
        elif "gog" in url.lower():
            platform = "GOG"
        elif "humble" in url.lower():
            platform = "Humble Store"
        elif "epic" in url.lower():
            platform = "Epic Games"

        # 割引率を抽出
        discount_match = re.search(r'(\d+)%', title)
        discount = f"{discount_match.group(1)}% OFF" if discount_match else ""

        sale_data = {
            "title": title,
            "platform": platform,
            "type": "sale",
            "description": "",
            "price": "",
            "originalPrice": "",
            "discount": discount,
            "deadline": "",
            "url": url,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

        sale_data["description"] = truncate_description(
            generate_description_with_ai(sale_data) if GROQ_API_KEY else f"{title}が{platform}でセール中です。"
        )

        sales.append(sale_data)

    print(f"✅ Found {len(sales)} Reddit sales")
    return sales


@retry_on_failure(max_retries=3, delay=2)
def fetch_review_articles():
    """RSSフィードからレビュー記事取得"""
    print("📥 Fetching review articles...")

    articles = []

    for media_name, feed_url in RSS_FEEDS.items():
        try:
            print(f"  📰 Fetching {media_name}...")
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "") or entry.get("description", "")

                # レビュー記事をフィルタリング
                is_review = any(keyword in title.lower() or keyword in summary.lower()
                               for keyword in ["review", "レビュー", "評価", "感想", "プレイ"])

                if not is_review:
                    continue

                # 公開日を取得
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    pub_date = datetime(*published[:6]).strftime("%Y-%m-%d")
                else:
                    pub_date = datetime.now().strftime("%Y-%m-%d")

                # HTMLタグを削除
                summary = re.sub(r'<[^>]+>', '', summary)
                summary = clean_reddit_meta(summary)

                # 英語メディアの場合は翻訳
                is_translated = media_name in ENGLISH_MEDIA
                if is_translated:
                    title = translate_to_japanese(title)
                    summary = translate_to_japanese(summary)

                # タイトルを整形
                formatted_title = format_review_title(title, media_name)

                article_data = {
                    "title": formatted_title,
                    "platform": media_name,
                    "type": "review",
                    "description": "",
                    "price": "",
                    "originalPrice": "",
                    "discount": "",
                    "deadline": "",
                    "url": link,
                    "date": pub_date,
                    "is_translated": is_translated
                }

                article_data["description"] = truncate_description(
                    generate_description_with_ai(article_data) if GROQ_API_KEY else summary
                )

                articles.append(article_data)

            time.sleep(1)  # リクエスト間隔

        except Exception as e:
            print(f"  ⚠️ Error fetching {media_name}: {e}")

    print(f"✅ Found {len(articles)} review articles")
    return articles


# =============================================================================
# データ管理関数
# =============================================================================

def clean_old_data(games_list, days=7):
    """古いデータを削除"""
    if not games_list:
        return []

    cutoff_date = datetime.now() - timedelta(days=days)

    cleaned = []
    for game in games_list:
        date_str = game.get("date", "")
        if date_str:
            try:
                game_date = datetime.strptime(date_str, "%Y-%m-%d")
                if game_date >= cutoff_date:
                    cleaned.append(game)
            except:
                cleaned.append(game)
        else:
            cleaned.append(game)

    removed_count = len(games_list) - len(cleaned)
    if removed_count > 0:
        print(f"🗑️ Removed {removed_count} old entries")

    return cleaned


def remove_duplicates(games_list):
    """重複ゲームを削除"""
    if not games_list:
        return []

    seen = {}
    unique_games = []

    for game in games_list:
        norm_title = normalize_title(game.get('title', ''))
        if norm_title and norm_title not in seen:
            seen[norm_title] = True
            unique_games.append(game)

    removed_count = len(games_list) - len(unique_games)
    if removed_count > 0:
        print(f"🔄 Removed {removed_count} duplicates")

    return unique_games


def load_existing_data():
    """既存データを読み込み"""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error loading existing data: {e}")

    return {
        "pc": {
            "free": [],
            "bundle": [],
            "sale": [],
            "review": []
        },
        "last_updated": ""
    }


def save_data(data):
    """データを保存"""
    data["last_updated"] = datetime.now().isoformat()

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"💾 Data saved to {DATA_FILE}")


def merge_data(existing, new_items, category):
    """既存データと新規データをマージ"""
    existing_titles = {normalize_title(item.get('title', '')) for item in existing}

    merged = list(existing)
    added_count = 0

    for item in new_items:
        norm_title = normalize_title(item.get('title', ''))
        if norm_title and norm_title not in existing_titles:
            merged.append(item)
            existing_titles.add(norm_title)
            added_count += 1

    if added_count > 0:
        print(f"➕ Added {added_count} new {category} entries")

    return merged


# =============================================================================
# メイン処理
# =============================================================================

def update_games_data():
    """メイン更新処理"""
    print("=" * 60)
    print("🎮 PCゲームお得情報 自動更新開始")
    print("=" * 60)

    # 既存データ読み込み
    data = load_existing_data()

    # 無料ゲーム取得
    print("\n📦 Fetching Free Games...")
    free_games = []
    free_games.extend(fetch_epic_free_games())
    time.sleep(1)
    free_games.extend(fetch_reddit_freegamefindings())  # 最も早い情報源
    time.sleep(1)
    free_games.extend(fetch_reddit_free_games())
    time.sleep(1)
    free_games.extend(fetch_steamdb_free_games())  # SteamDB無料プロモーション

    # バンドル取得
    print("\n📦 Fetching Bundles...")
    bundles = []
    bundles.extend(fetch_humble_bundle_direct())
    time.sleep(1)
    bundles.extend(fetch_fanatical_bundles())
    time.sleep(1)
    bundles.extend(fetch_indiegala_bundles())
    time.sleep(1)
    bundles.extend(fetch_itchio_bundles())
    time.sleep(1)
    bundles.extend(fetch_reddit_bundles())

    # セール取得
    print("\n📦 Fetching Sales...")
    sales = []
    sales.extend(fetch_steam_sales())
    time.sleep(1)
    sales.extend(fetch_gog_sales())
    time.sleep(1)
    sales.extend(fetch_isthereanydeal_sales())
    time.sleep(1)
    sales.extend(fetch_reddit_sales())

    # レビュー記事取得
    print("\n📦 Fetching Reviews...")
    reviews = fetch_review_articles()

    # データマージ
    print("\n🔧 Processing data...")
    data["pc"]["free"] = merge_data(data["pc"].get("free", []), free_games, "free")
    data["pc"]["bundle"] = merge_data(data["pc"].get("bundle", []), bundles, "bundle")
    data["pc"]["sale"] = merge_data(data["pc"].get("sale", []), sales, "sale")
    data["pc"]["review"] = merge_data(data["pc"].get("review", []), reviews, "review")

    # 重複排除
    print("\n🔄 Removing duplicates...")
    data["pc"]["free"] = remove_duplicates(data["pc"]["free"])
    data["pc"]["bundle"] = remove_duplicates(data["pc"]["bundle"])
    data["pc"]["sale"] = remove_duplicates(data["pc"]["sale"])
    data["pc"]["review"] = remove_duplicates(data["pc"]["review"])

    # 古いデータ削除
    print("\n🗑️ Cleaning old data...")
    data["pc"]["free"] = clean_old_data(data["pc"]["free"], days=7)
    data["pc"]["bundle"] = clean_old_data(data["pc"]["bundle"], days=10)
    data["pc"]["sale"] = clean_old_data(data["pc"]["sale"], days=7)
    data["pc"]["review"] = clean_old_data(data["pc"]["review"], days=10)

    # 保存
    save_data(data)

    # サマリー表示
    print("\n" + "=" * 60)
    print("📊 Update Summary:")
    print(f"  🆓 Free Games: {len(data['pc']['free'])}")
    print(f"  📦 Bundles: {len(data['pc']['bundle'])}")
    print(f"  💰 Sales: {len(data['pc']['sale'])}")
    print(f"  📰 Reviews: {len(data['pc']['review'])}")
    print("=" * 60)
    print("✅ Update completed!")


if __name__ == "__main__":
    update_games_data()
