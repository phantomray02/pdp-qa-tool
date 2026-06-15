# =========================================
# PDP QA TOOL (FULL REFACTORED VERSION)
# Retailer-Isolated Architecture
# =========================================

import re
import html
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

# GENERIC HELPERS

def normalize_space(text):
    text = str(text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_text(text):
    return re.sub(r"[^a-z0-9\s]", "", str(text).lower())


def dedupe_preserve_order(items):
    seen = set()
    out = []
    for x in items:
        x = normalize_space(x)
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def similarity(a, b):
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)

# HTTP

HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_html(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""

# SALSIFY

def get_salsify_data(url):
    html_text = get_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return {"title": "", "description": "", "features": []}

    try:
        data = json.loads(script.string)
        props = data["props"]["pageProps"]["product"]["propertySets"][0]["properties"]
    except Exception:
        return {"title": "", "description": "", "features": []}

    mapping = {}
    for p in props:
        mapping[p.get("property")] = p.get("values", [""])[0]

    features = [
        mapping.get("FEATURE_1", ""),
        mapping.get("FEATURE_2", ""),
        mapping.get("FEATURE_3", ""),
        mapping.get("FEATURE_4", ""),
        mapping.get("FEATURE_5", ""),
    ]

    return {
        "title": normalize_space(mapping.get("PRODUCT_TITLE", "")),
        "description": normalize_space(mapping.get("DESCRIPTION", "")),
        "features": dedupe_preserve_order(features)
    }

# CVS

def get_cvs_data(url):
    html_text = get_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    bullets = [li.get_text(strip=True) for li in soup.select("ul li")]

    return {
        "title": normalize_space(title),
        "description": "",
        "features": dedupe_preserve_order(bullets[:5])
    }

# WALGREENS

def get_walgreens_product_id(url):
    m = re.search(r"/ID=([A-Za-z0-9]+)", str(url))
    return m.group(1) if m else ""


def get_walgreens_sku(url):
    m = re.search(r"skuId=([A-Za-z0-9_-]+)", str(url))
    return m.group(1) if m else ""


def fetch_walgreens_api(product_id):
    if not product_id:
        return None
    url = f"https://www.walgreens.com/productapi/v1/products?productId={product_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def walk_json(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_json(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from walk_json(x)


def get_walgreens_data(url):
    product_id = get_walgreens_product_id(url)
    sku_id = get_walgreens_sku(url)

    payload = fetch_walgreens_api(product_id)

    features, description, title = [], "", ""

    if payload:
        for node in walk_json(payload):
            if not isinstance(node, dict):
                continue
            if sku_id and str(node.get("skuId", "")) != sku_id:
                continue

            title = title or node.get("name", "")
            description = description or node.get("vendorDetailsParagraph", "")

            if not features:
                bullets = node.get("vendorDetailsBullets")
                if isinstance(bullets, list):
                    features = bullets

    if not features and payload:
        for node in walk_json(payload):
            bullets = node.get("vendorDetailsBullets")
            if isinstance(bullets, list) and bullets:
                features = bullets
                break

    if not features:
        soup = BeautifulSoup(get_html(url), "html.parser")
        features = [li.get_text(strip=True) for li in soup.select("li")]

    features = dedupe_preserve_order([normalize_space(x) for x in features if x][:5])

    return {
        "title": normalize_space(title),
        "description": normalize_space(description),
        "features": features
    }

# DISPATCHER

def get_retailer_data(url, retailer):
    r = str(retailer).lower()
    if "cvs" in r:
        return get_cvs_data(url)
    if "walgreens" in r:
        return get_walgreens_data(url)
    return {"title": "", "description": "", "features": []}

# COMPARE

def compare_features(salsify_features, retailer_features):
    scores = []
    for i in range(5):
        s = salsify_features[i] if i < len(salsify_features) else ""
        r = retailer_features[i] if i < len(retailer_features) else ""
        scores.append(similarity(s, r))
    return int(sum(scores)/len(scores)) if scores else 0, scores

# MAIN

def run_row(row):
    salsify = get_salsify_data(row["salsify_url"])
    retailer = get_retailer_data(row["retail_url"], row.get("retailer", ""))
    score, breakdown = compare_features(salsify["features"], retailer["features"])
    return {"sku": row.get("sku"), "feature_score": score, "feature_breakdown": breakdown}
