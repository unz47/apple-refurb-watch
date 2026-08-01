#!/usr/bin/env python3
"""
Apple 整備済製品ウォッチャー

- Apple 整備済製品ページを定期取得し、在庫を記録する
- config.json のアラート条件に合う新着があれば通知内容を書き出す
- 抽出が壊れていないかを自己監視する

ローカル実行:
    python3 watch.py                # 取得して表示
    python3 watch.py --dry-run      # データを書き込まない
    python3 watch.py --dump-html    # 生HTMLを保存(構造調査用)

GitHub Actions からはそのまま `python3 watch.py` で呼ばれる。
依存: 標準ライブラリのみ
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"
LATEST_PATH = DATA / "latest.json"
HISTORY_PATH = DATA / "history.jsonl"
CSV_PATH = DATA / "prices.csv"
HEALTH_PATH = DATA / "health.json"
NOTIFY_PATH = ROOT / "notify_body.md"

BASE = "https://www.apple.com/jp/shop/refurbished/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
JST = timezone(timedelta(hours=9))


def now_jst():
    return datetime.now(JST)


def log(msg):
    print(f"[{now_jst():%H:%M:%S}] {msg}", flush=True)


# ----------------------------------------------------------------------------
# 取得
# ----------------------------------------------------------------------------
def fetch(url: str, retries: int = 3) -> str:
    last = None
    for i in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            if i < retries - 1:
                wait = 5 * (i + 1)
                log(f"  取得失敗 ({e}) — {wait}秒後に再試行")
                time.sleep(wait)
    raise RuntimeError(f"取得失敗: {url} ({last})")


# ----------------------------------------------------------------------------
# 抽出
# ----------------------------------------------------------------------------
def _norm_price(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, dict):
        for k in ("raw", "amount", "value", "currentPrice", "sellingPrice", "price"):
            if k in v:
                p = _norm_price(v[k])
                if p:
                    return p
        return None
    if isinstance(v, str):
        d = re.sub(r"[^\d]", "", v)
        return int(d) if d else None
    return None


def _walk(node, out):
    if isinstance(node, dict):
        k = set(node.keys())
        if "title" in k and k & {"price", "priceInfo", "currentPrice", "salePrice"}:
            out.append(node)
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def extract_from_json(page: str):
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, re.S)
    blobs = []
    for s in scripts:
        s = s.strip()
        for m in re.finditer(r"=\s*(\{.{200,}?\})\s*;?\s*$", s, re.S | re.M):
            blobs.append(m.group(1))
        if s.startswith("{") and s.endswith("}"):
            blobs.append(s)

    items = []
    for b in blobs:
        try:
            data = json.loads(b)
        except Exception:
            continue
        found = []
        _walk(data, found)
        for f in found:
            price = None
            for k in ("price", "currentPrice", "priceInfo", "salePrice"):
                if k in f:
                    price = _norm_price(f[k])
                    if price:
                        break
            title = f.get("title")
            if not isinstance(title, str) or not price:
                continue
            url = f.get("productDetailsUrl") or f.get("url") or f.get("link") or ""
            if isinstance(url, str) and url.startswith("/"):
                url = "https://www.apple.com" + url
            items.append({
                "title": unescape(title).strip(),
                "price": price,
                "part_number": str(f.get("partNumber") or f.get("sku") or ""),
                "url": url if isinstance(url, str) else "",
            })
    return dedupe(items)


def extract_from_html(page: str):
    items = []
    for m in re.finditer(r'<a[^>]+href="(/jp/shop/product/[^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href, inner = m.group(1), m.group(2)
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        if not text:
            continue
        tail = page[m.end(): m.end() + 1500]
        pm = re.search(r"[¥￥]\s?([\d,]{4,})", inner + " " + tail)
        if not pm:
            continue
        pn = re.search(r"/product/([A-Z0-9]+)", href)
        items.append({
            "title": re.split(r"[¥￥]", text)[0].strip() or text,
            "price": int(pm.group(1).replace(",", "")),
            "part_number": pn.group(1) if pn else "",
            "url": "https://www.apple.com" + href.split("?")[0],
        })
    return dedupe(items)


def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = (it["title"], it["price"])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return sorted(out, key=lambda x: x["price"])


# 商品名から機種を判定する。
# Apple の整備済ページは /mac/mac-studio を開いても中の JSON には Mac 全機種が
# 入っているため、「どの URL で取得したか」は機種の判別に使えない。
# 必ず商品名から判定すること。
TYPE_RULES = [
    ("mac-studio",   ["mac studio"]),
    ("mac-pro",      ["mac pro"]),
    ("mac-mini",     ["mac mini"]),
    ("macbook-pro",  ["macbook pro"]),
    ("macbook-air",  ["macbook air"]),
    ("macbook-neo",  ["macbook neo"]),
    ("imac",         ["imac"]),
    ("display",      ["studio display", "pro display"]),
]


def classify(title: str) -> str:
    t = title.lower()
    for name, keys in TYPE_RULES:
        if any(k in t for k in keys):
            return name
    return "other"


def scrape(source: str, dump_html: bool = False):
    """整備済 Mac ページを1回だけ取得し、商品名で機種を分類して返す"""
    url = BASE + source
    page = fetch(url)
    if dump_html:
        p = DATA / f"debug_{source}_{now_jst():%Y%m%d_%H%M%S}.html"
        p.write_text(page, encoding="utf-8")
        log(f"  HTML保存: {p.name} ({len(page):,} bytes)")

    items = extract_from_json(page)
    method = "json"
    if not items:
        items = extract_from_html(page)
        method = "html"
    for it in items:
        it["type"] = classify(it["title"])
    return items, method


# ----------------------------------------------------------------------------
# 突合
# ----------------------------------------------------------------------------
def item_key(it):
    return f"{it['type']}|{it['part_number']}|{it['title']}|{it['price']}"


def match(it, rule):
    types = rule.get("type") or rule.get("types")
    if types:
        if isinstance(types, str):
            types = [types]
        if it["type"] not in types:
            return False
    kw = rule.get("keyword", "")
    if kw and kw.lower() not in it["title"].lower():
        return False
    if rule.get("max_price") and it["price"] > rule["max_price"]:
        return False
    if rule.get("min_price") and it["price"] < rule["min_price"]:
        return False
    return True


def notify_macos(title, message):
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(message)} with title {json.dumps(title)} sound name "Glass"'],
            check=False,
        )
    except FileNotFoundError:
        pass


def set_output(key, value):
    """GitHub Actions の step output に値を渡す"""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Apple 整備済製品ウォッチャー")
    ap.add_argument("--dry-run", action="store_true", help="データを書き込まない")
    ap.add_argument("--dump-html", action="store_true", help="生HTMLを保存")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    source = cfg.get("source", "mac")
    rules = cfg.get("alerts", [])
    DATA.mkdir(exist_ok=True)

    # --- 取得 -----------------------------------------------------------
    # 1ページに Mac 全機種が入っているので、取得は1回で足りる
    all_items, methods, errors = [], {}, []
    log(f"取得中: {BASE + source}")
    try:
        all_items, method = scrape(source, args.dump_html)
        methods[source] = method
        counts = {}
        for it in all_items:
            counts[it["type"]] = counts.get(it["type"], 0) + 1
        log(f"  {len(all_items)}件 (抽出: {method})")
        for t, n in sorted(counts.items(), key=lambda x: -x[1]):
            log(f"    {t}: {n}件")
    except Exception as e:
        log(f"  !! {e}")
        errors.append(f"{source}: {e}")

    if errors and not all_items:
        log("ページの取得に失敗しました")
        set_output("has_new", "false")
        set_output("unhealthy", "true")
        sys.exit(1)

    # --- 前回との突合 ----------------------------------------------------
    prev = []
    # 「初回かどうか」はファイルの有無で判定する。
    # 在庫0件を初回扱いにしてしまうと、抽出が壊れて0件が続いた時に
    # 永遠に初回扱いのままヘルスチェックが発火しなくなる。
    first_run = not LATEST_PATH.exists()
    if not first_run:
        try:
            prev = json.loads(LATEST_PATH.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            prev = []
    prev_keys = {item_key(i) for i in prev}

    new_items = [i for i in all_items if item_key(i) not in prev_keys]

    # アラート条件に合う新着だけを通知対象にする。
    # 複数の条件に当たっても1商品1回しか通知しない。
    hits, hit_keys = [], set()
    for rule in rules:
        for it in new_items:
            k = item_key(it)
            if k in hit_keys or not match(it, rule):
                continue
            hit_keys.add(k)
            hits.append({**it, "rule": rule.get("name", "条件")})

    # --- 自己監視 --------------------------------------------------------
    health = {}
    if HEALTH_PATH.exists():
        try:
            health = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        except Exception:
            health = {}
    zero_streak = health.get("zero_streak", 0)
    zero_streak = zero_streak + 1 if not all_items else 0
    threshold = cfg.get("health", {}).get("zero_streak_threshold", 12)
    unhealthy = zero_streak >= threshold

    # --- 書き出し --------------------------------------------------------
    stamp = now_jst().isoformat(timespec="seconds")
    if not args.dry_run:
        LATEST_PATH.write_text(
            json.dumps({"checked_at": stamp, "methods": methods, "items": all_items},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            for it in all_items:
                f.write(json.dumps({"t": stamp, **it}, ensure_ascii=False) + "\n")
        new_csv = not CSV_PATH.exists()
        with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["checked_at", "type", "title", "price", "part_number", "url"])
            if new_csv:
                w.writeheader()
            for it in all_items:
                w.writerow({"checked_at": stamp, **{k: it.get(k, "") for k in
                            ["type", "title", "price", "part_number", "url"]}})
        HEALTH_PATH.write_text(
            json.dumps({"checked_at": stamp, "zero_streak": zero_streak,
                        "total_items": len(all_items), "errors": errors,
                        "methods": methods}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # --- 通知本文 --------------------------------------------------------
    log(f"合計 {len(all_items)}件 / 新着 {len(new_items)}件 / 条件一致 {len(hits)}件")

    if first_run:
        log("初回実行のため新着通知はスキップします（次回から差分を通知します）")
        set_output("has_new", "false")
        set_output("unhealthy", "true" if unhealthy else "false")
        return

    if hits:
        lines = [f"**{now_jst():%Y-%m-%d %H:%M} (JST) 時点で新着がありました。**", ""]
        for it in sorted(hits, key=lambda x: x["price"]):
            lines.append(f"### ¥{it['price']:,} — {it['title']}")
            lines.append(f"- 条件: {it['rule']}")
            lines.append(f"- 機種: `{it['type']}`")
            if it["url"]:
                lines.append(f"- [Apple のページで見る]({it['url']})")
            lines.append("")
        lines += ["---", "",
                  "整備済製品は人気構成だと数時間で売り切れます。買うなら早めに判断してください。",
                  "", f"確認したページ: {BASE + source}"]
        body = "\n".join(lines)
        NOTIFY_PATH.write_text(body, encoding="utf-8")
        set_output("has_new", "true")
        set_output("issue_title",
                   f"整備済に新着 {len(hits)}件 — 最安 ¥{min(h['price'] for h in hits):,}")
        head = min(hits, key=lambda x: x["price"])
        notify_macos(f"整備済に新着 {len(hits)}件", f"¥{head['price']:,} {head['title']}")
        print("\n" + body + "\n")
    else:
        set_output("has_new", "false")

    set_output("unhealthy", "true" if unhealthy else "false")
    if unhealthy:
        log(f"!! {zero_streak}回連続で0件です。抽出が壊れている可能性があります")


if __name__ == "__main__":
    main()
