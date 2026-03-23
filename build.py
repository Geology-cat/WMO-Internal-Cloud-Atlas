#!/usr/bin/env python3
"""
WMO 国際雲図帳 日本語訳 — MD → HTML ビルドスクリプト

使い方:
    python3 build.py          # 全mdファイルを変換
    python3 build.py --clean  # 生成したhtmlを削除
"""

import os
import re
import sys
import glob
import unicodedata
import markdown
import markdown.extensions.toc as _toc_mod
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

# toc 拡張の重複ID処理を GFM 互換に変更（_1 → -1）
_IDCOUNT_RE = re.compile(r'^(.+)-([\d]+)$')

def _unique_gfm(id: str, ids):
    """重複IDに -1, -2 ... のサフィックスを付与する (GFM 互換)"""
    while id in ids or not id:
        m = _IDCOUNT_RE.match(id)
        if m:
            id = '%s-%d' % (m.group(1), int(m.group(2)) + 1)
        else:
            id = '%s-%d' % (id, 1)
    ids.add(id)
    return id

_toc_mod.unique = _unique_gfm

# ── 設定 ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
TEMPLATE_NAME = "base.html"
CSS_FILENAME = "style.css"

# 変換対象から除外するファイル
EXCLUDE_FILES = {"変換プロンプト.txt", "README.md"}



# ── ユーティリティ ─────────────────────────────────────
def relative_path_from(source_dir: Path, target: str) -> str:
    """source_dir から見た target への相対パスを返す"""
    target_path = BASE_DIR / target
    try:
        return os.path.relpath(target_path, source_dir)
    except ValueError:
        return target


def parse_nav_from_index() -> list:
    """index.md のヘッダーメニュー部分(---で囲まれた領域)をパースし、
    ナビ項目のリストを返す。
    各項目: {"label": str, "href": str (html), "external": bool}
    """
    index_path = BASE_DIR / "index.md"
    md_text = index_path.read_text(encoding="utf-8")

    # --- で囲まれた領域を抽出
    lines = md_text.splitlines()
    in_menu = False
    menu_lines = []
    hr_count = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("---"):
            hr_count += 1
            if hr_count == 1:
                in_menu = True
                continue
            elif hr_count == 2:
                break
        if in_menu:
            menu_lines.append(stripped)

    # - [ラベル](URL) 形式をパース
    link_re = re.compile(r'^-\s*\[(.+?)\]\((.+?)\)$')
    nav_items = []
    for line in menu_lines:
        m = link_re.match(line)
        if not m:
            continue
        label = m.group(1)
        href = m.group(2)
        external = href.startswith("http://") or href.startswith("https://")
        if not external:
            # .md → .html 変換
            href = re.sub(r'\.md$', '.html', href)
        nav_items.append({"label": label, "href": href, "external": external})
    return nav_items


def compute_nav_items(nav_items_base: list, source_dir: Path, current_html_rel: str) -> list:
    """各ページ用にナビ項目の URL を相対パスに変換し、active フラグを付与する"""
    result = []
    current_normalized = current_html_rel.replace(os.sep, "/")
    for item in nav_items_base:
        entry = dict(item)  # コピー
        if not entry["external"]:
            # href の %20 をデコードして相対パス計算
            decoded_href = entry["href"].replace("%20", " ")
            entry["url"] = relative_path_from(source_dir, decoded_href)
            # active 判定: 現在のページと一致するか
            target_normalized = decoded_href.replace(os.sep, "/")
            entry["active"] = (current_normalized == target_normalized)
        else:
            entry["url"] = entry["href"]
            entry["active"] = False
        result.append(entry)
    return result


def compute_css_path(source_dir: Path) -> str:
    """source_dir から見た CSS ファイルの相対パスを返す"""
    return relative_path_from(source_dir, CSS_FILENAME)


def gfm_slugify(value: str, separator: str = "-") -> str:
    """GitHub Flavored Markdown 互換の slugify 関数。
    日本語・CJK 文字を保持しつつ、GFM と同じアンカーIDを生成する。

    GFM のアルゴリズム:
      1. 小文字化
      2. 文字・数字・スペース・ハイフン以外を除去
      3. 各スペースを個別にハイフンに変換（連続ハイフンは圧縮しない）
    Python の \\w は Unicode 文字（CJK漢字・ひらがな・カタカナ等）を含むため、
    明示的な Unicode 範囲指定は不要。
    """
    value = value.lower()
    # \w = Unicode文字+数字+_ 、\s = 空白、- = ハイフン 以外を除去
    value = re.sub(r'[^\w\s-]', '', value)
    # 各空白文字を個別にハイフンに変換（連続空白 → 連続ハイフン）
    value = re.sub(r'\s', separator, value)
    return value


def rewrite_md_links(html_content: str) -> str:
    """HTML内の .md リンクを .html に置換する"""
    # href="...something.md" → href="...something.html"
    html_content = re.sub(
        r'(href="[^"]*?)\.md(")',
        r'\1.html\2',
        html_content,
    )
    # href='...something.md' → href='...something.html'
    html_content = re.sub(
        r"(href='[^']*?)\.md(')",
        r"\1.html\2",
        html_content,
    )
    return html_content


def extract_title(md_text: str) -> str:
    """md テキストから最初の # 見出しをタイトルとして抽出する"""
    for line in md_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.lstrip("# ").strip()
    return "国際雲図帳"


def strip_header_menu(md_text: str) -> str:
    """index.md 特有のヘッダーメニュー部分(---で囲まれた領域)を除去する"""
    result = []
    in_menu = False
    hr_count = 0
    for line in md_text.splitlines(True):
        stripped = line.strip()
        if stripped.startswith("---") or stripped.startswith("-----"):
            hr_count += 1
            if hr_count == 1:
                in_menu = True
                continue
            elif hr_count == 2:
                in_menu = False
                continue
        if not in_menu:
            result.append(line)
    return "".join(result)


# ── メイン処理 ──────────────────────────────────────
def build():
    # Jinja2 環境
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,
    )
    template = env.get_template(TEMPLATE_NAME)

    # Markdown 変換器
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "toc",
            "attr_list",
            "md_in_html",
        ],
        extension_configs={
            "toc": {
                "slugify": gfm_slugify,
                "separator": "-",
            },
        },
    )

    # index.md からナビゲーション項目をパース
    nav_items_base = parse_nav_from_index()
    print(f"ナビゲーション: {len(nav_items_base)} 項目を index.md から読み込み")

    # 全 .md ファイルを取得
    md_files = list(BASE_DIR.rglob("*.md"))

    converted = 0
    for md_file in md_files:
        # テンプレートディレクトリ内のファイルはスキップ
        try:
            md_file.relative_to(TEMPLATE_DIR)
            continue
        except ValueError:
            pass

        # .windsurf 等の隠しディレクトリ内はスキップ
        rel = md_file.relative_to(BASE_DIR)
        parts = rel.parts
        if any(p.startswith(".") for p in parts):
            continue

        # 除外ファイル
        if md_file.name in EXCLUDE_FILES:
            continue

        # md を読み込み
        md_text = md_file.read_text(encoding="utf-8")

        # index.md の場合、ヘッダーメニュー部分を除去
        rel_str = str(rel)
        is_index = rel_str == "index.md"
        if is_index:
            md_text = strip_header_menu(md_text)

        # タイトル抽出
        title = extract_title(md_text)

        # Markdown → HTML 変換
        md.reset()
        body_html = md.convert(md_text)

        # .md リンクを .html に置換
        body_html = rewrite_md_links(body_html)

        # 出力先
        html_rel = rel.with_suffix(".html")
        html_file = BASE_DIR / html_rel
        source_dir = html_file.parent

        # ナビゲーション項目と CSS パスを計算
        nav_items = compute_nav_items(nav_items_base, source_dir, str(html_rel))
        css_path = compute_css_path(source_dir)

        # テンプレートにレンダリング
        rendered = template.render(
            title=title,
            content=body_html,
            nav_items=nav_items,
            css_path=css_path,
            is_index=is_index,
        )

        # HTML を書き出し
        html_file.parent.mkdir(parents=True, exist_ok=True)
        html_file.write_text(rendered, encoding="utf-8")
        converted += 1
        print(f"  ✓ {rel} → {html_rel}")

    print(f"\n完了: {converted} ファイルを変換しました。")


def clean():
    """生成された HTML ファイルを削除する"""
    md_files = list(BASE_DIR.rglob("*.md"))
    removed = 0
    for md_file in md_files:
        rel = md_file.relative_to(BASE_DIR)
        html_file = BASE_DIR / rel.with_suffix(".html")
        if html_file.exists():
            html_file.unlink()
            removed += 1
            print(f"  ✗ {rel.with_suffix('.html')} 削除")
    print(f"\n完了: {removed} ファイルを削除しました。")


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    else:
        print("WMO 国際雲図帳 — MD → HTML ビルド開始\n")
        build()
