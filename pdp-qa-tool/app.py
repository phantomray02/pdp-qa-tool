"""Brand Compliance Portal using the visual structure of the PDP QA tool."""
import html
import json
import os
import sys
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse, quote
from difflib import SequenceMatcher

import pandas as pd
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps
from io import BytesIO
import streamlit as st
import streamlit.components.v1 as components

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)


APP_TITLE = "Brand Compliance Portal"
APP_FAVICON_DATA_URI = "data:image/png;base64,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"

st.set_page_config(page_title=APP_TITLE, page_icon=APP_FAVICON_DATA_URI, layout="wide")

components.html(
    """
    <script>
    const title = __APP_TITLE__;
    const iconHref = __APP_ICON__;
    function updateBranding() {
      try {
        const doc = window.parent.document;
        if (doc.title !== title) doc.title = title;
        const titleNode = doc.querySelector('title');
        if (titleNode && titleNode.textContent !== title) titleNode.textContent = title;
        let icon = doc.querySelector("link[rel='icon']") || doc.querySelector("link[rel='shortcut icon']");
        if (!icon) {
          icon = doc.createElement('link');
          icon.setAttribute('rel', 'icon');
          doc.head.appendChild(icon);
        }
        icon.setAttribute('type', 'image/png');
        icon.setAttribute('href', iconHref);
      } catch (error) {}
    }
    updateBranding();
    let fastCount = 0;
    const fastTimer = window.setInterval(() => { updateBranding(); fastCount += 1; if (fastCount > 80) window.clearInterval(fastTimer); }, 125);
    window.setInterval(updateBranding, 1000);
    try {
      const observer = new MutationObserver(updateBranding);
      observer.observe(window.parent.document.head, { childList: true, subtree: true, characterData: true });
    } catch (error) {}
    </script>
    """.replace("__APP_TITLE__", json.dumps(APP_TITLE)).replace("__APP_ICON__", json.dumps(APP_FAVICON_DATA_URI)),
    height=0,
    width=0,
)


st.markdown(
    """
    <style>
    .block-container {padding-top:6.25rem; padding-bottom:2rem; max-width:1920px; width:100%;}
    [data-testid="stSidebar"] {min-width: 235px; max-width: 235px;}
    [data-testid="stHeader"] {height:3.5rem;}
    [data-testid="stToolbar"] {top:0.35rem;}
    [data-bcp-hidden-old-ui="true"] {display:none !important;}
    h1 {font-size: 1.55rem !important; margin-bottom: .2rem !important;}
    h2 {font-size: 1.10rem !important; margin-top: .4rem !important;}
    h3 {font-size: .98rem !important;}
    .qa-panel {border: 1px solid #313844; border-radius: 6px; padding: 12px; background: #0e1117;}
    .qa-heading {font-size:19px; line-height:22px; height:22px; font-weight:800; margin:0; overflow:hidden; white-space:nowrap;}
    .qa-id {color: #27a6ff; font-weight: 800;}
    .qa-id-link {color:#27a6ff !important; font-weight:800; text-decoration:none;}
    .qa-id-link:hover {text-decoration:underline;}
    .locked-review-grid {display:grid; grid-template-columns:minmax(0, 1fr) 326px; gap:20px; align-items:start; width:100%;}
    .locked-copy-grid {display:grid; grid-template-columns:minmax(0, 1fr) minmax(0, 1fr) 104px; column-gap:12px; row-gap:0; align-items:start;}
    .locked-image-head, .locked-image-row {display:grid; grid-template-columns:104px 104px 94px; gap:6px; align-items:start;}
    .locked-image-row {margin-bottom:2px; min-height:106px;}
    .locked-image-box {width:104px; height:104px; background:#fff; border-radius:4px; display:flex; align-items:center; justify-content:center; overflow:hidden;}
    .locked-image-box img {display:block; width:100%; height:100%; object-fit:contain;}
    .locked-image-missing {width:104px; height:104px; border:1px dashed #4b5563; border-radius:4px; display:flex; align-items:center; justify-content:center; color:#9ca3af; font-size:12px;}
    .locked-score {padding-top:2px; line-height:16px; word-break:normal; text-align:right; align-self:start; white-space:nowrap;}
    .locked-copy-cell {font-size:12px; line-height:1.24; white-space:pre-wrap; overflow-wrap:anywhere;}
    .locked-feature-cell {padding:4px 0 2px 0; border-bottom:0; min-height:30px;}
    .feature-score-under {grid-column:1; padding:2px 0 5px 0; border-bottom:1px solid #242a34; text-align:left; line-height:16px; white-space:nowrap;}
    .feature-score-spacer {grid-column:2 / -1; min-height:20px; border-bottom:1px solid #242a34;}
    .locked-item-card {width:100%; overflow:hidden;}
    .locked-copy-grid > .qa-heading, .locked-image-head > .qa-heading {height:22px; line-height:22px;}
    .locked-image-head {height:22px; margin:0;}
    .locked-copy-grid > .score-strip {grid-column:1/-1; align-self:start;}
    .locked-review-grid > div {min-width:0;}
    @media (max-width:1050px) {.locked-review-grid {grid-template-columns:1fr;} .locked-copy-grid {grid-template-columns:1fr 1fr 96px;}}
    .section-title {font-size:15px; line-height:18px; font-weight:800; margin:6px 0 2px 0;}
    .copy-text {font-size:12px; line-height:1.24; white-space:pre-wrap;}
    .score-strong {color:#20c56b; font-weight:800; white-space:nowrap;}
    .score-review {color:#ffad2f; font-weight:800; white-space:nowrap;}
    .score-poor {color:#ff3b49; font-weight:800; white-space:nowrap;}
    .score-na {color:#9ca3af; font-weight:800; white-space:nowrap;}
    .score-strip {background:#f5a623; color:white; font-weight:800; line-height:18px; height:28px; box-sizing:border-box; padding:5px 9px; border-radius:4px; margin:4px 0 4px 0;}
    .image-strip {background:#d92332; color:white; font-weight:800; line-height:18px; height:28px; box-sizing:border-box; padding:5px 9px; border-radius:4px; margin:4px 0 4px 0;}
    .feature-row {padding:4px 0; border-bottom:1px solid #242a34; min-height:34px;}
    .tiny-label {display:none;}
    .capture-note {font-size:11px; color:#aab2bf;}
    .stImage img {border-radius:4px; background:white; object-fit:contain;}
    div[data-testid="stMetricValue"] {font-size:1.25rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

RETAILERS = ["All Retailers", "Sam's Club", "CVS", "Walgreens", "Kroger", "HEB"]

RETAILER_CONFIG = {
    "sams_club": {
        "display": "Sam's Club",
        "domains": ["samsclub.com"],
        "queue_files": ["skus_samsclub.csv", "skus_samsclub.xlsx"],
        "salsify_mode": "sams_club",
        "capture_mode": "sams_club",
    },
    "cvs": {
        "display": "CVS",
        "domains": ["cvs.com"],
        "queue_files": ["skus_cvs.csv", "skus_cvs.xlsx"],
        "salsify_mode": "generic",
        "capture_mode": "generic",
    },
    "walgreens": {
        "display": "Walgreens",
        "domains": ["walgreens.com"],
        "queue_files": ["skus_walgreens.csv", "skus_walgreens.xlsx"],
        "salsify_mode": "generic",
        "capture_mode": "generic",
    },
    "kroger": {
        "display": "Kroger",
        "domains": ["kroger.com"],
        "queue_files": ["skus_kroger.csv", "skus_kroger.xlsx"],
        "salsify_mode": "generic",
        "capture_mode": "generic",
    },
    "heb": {
        "display": "HEB",
        "domains": ["heb.com"],
        "queue_files": ["skus_heb.csv", "skus_heb.xlsx"],
        "salsify_mode": "generic",
        "capture_mode": "generic",
    },
}
MASTER_QUEUE_FILES = []
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov")


def retailer_key_from_display(value):
    raw = " ".join(str(value or "").lower().replace("&", " and ").split())
    for key, cfg in RETAILER_CONFIG.items():
        if raw == " ".join(cfg["display"].lower().split()):
            return key
    compact = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return {"sam_s_club": "sams_club", "sams_club": "sams_club", "samsclub": "sams_club"}.get(compact, compact)


def retailer_display_from_key(retailer_key):
    return RETAILER_CONFIG.get(retailer_key, {}).get("display", str(retailer_key or "Retailer").replace("_", " ").title())


def clean_retail_host(value):
    host = urlparse(str(value or "").strip()).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def retailer_key_from_url(url):
    host = clean_retail_host(url)
    if not host:
        return ""
    for key, cfg in RETAILER_CONFIG.items():
        for domain in cfg.get("domains", []):
            if host == domain or host.endswith("." + domain):
                return key
    return re.sub(r"[^a-z0-9]+", "_", host.split(":", 1)[0]).strip("_")


def normalize_retail_url(url):
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunparse(((parsed.scheme or "https").lower(), host, path, "", "", ""))


def capture_key(retailer_key, rpc, retail_url=""):
    return "||".join([str(retailer_key or "").strip().lower(), str(rpc or "").strip().lower(), normalize_retail_url(retail_url).lower()])


def candidate_capture_keys(retailer_key, rpc, retail_url=""):
    keys = []
    retailer_key = retailer_key or retailer_key_from_url(retail_url)
    if retailer_key and rpc and retail_url:
        keys.append(capture_key(retailer_key, rpc, retail_url))
    if retailer_key and rpc:
        keys.append(capture_key(retailer_key, rpc, ""))
    return keys


def _row_value(row, names):
    columns = {str(col).strip().lower(): col for col in row.index}
    for name in names:
        col = columns.get(str(name).lower())
        if col is not None:
            value = str(row.get(col, "") or "").strip()
            if value:
                return value
    return ""



def safe(row, key, default=""):
    try:
        value = row.get(key, default)
    except Exception:
        return default
    if value is None:
        return default
    try:
        return default if pd.isna(value) else value
    except Exception:
        return value


def as_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def normalize(value):
    return " ".join(str(value or "").lower().split())


def similarity(left, right):
    left_n, right_n = normalize(left), normalize(right)
    if not left_n or not right_n:
        return None
    if left_n == right_n:
        return 100
    return int(round(SequenceMatcher(None, left_n, right_n).ratio() * 100))


def score_text(value):
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
        return f"{int(round(float(value)))}%"
    except Exception:
        return "—"


def score_class(value):
    if value is None:
        return "score-na"
    try:
        value = float(value)
    except Exception:
        return "score-na"
    if value >= 85:
        return "score-strong"
    if value >= 60:
        return "score-review"
    return "score-poor"


def score_label(value):
    if value is None:
        return "Unavailable"
    try:
        value = float(value)
    except Exception:
        return "Unavailable"
    if value >= 85:
        return "Strong"
    if value >= 60:
        return "Review"
    return "Poor"


def _read_queue_file(path):
    """Read a current queue file from /data.

    Supported formats: .csv and .xlsx. Excel is fine for users, but CSV is still
    the safest option for GitHub diffs and simple uploads.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if suffix == ".xlsx":
        return pd.read_excel(path, dtype=str, keep_default_na=False, engine="openpyxl")
    return None


def load_master_queue_rows():
    data_dir = Path(ROOT) / "data"
    frames = []
    # Read only retailer-specific queue files from /data.
    for retailer_key, cfg in RETAILER_CONFIG.items():
        for filename in cfg.get("queue_files", []):
            path = data_dir / filename
            if path.exists():
                try:
                    queue_df = _read_queue_file(path)
                    if queue_df is not None:
                        frames.append((queue_df, retailer_key))
                except Exception:
                    pass

    items, seen = [], set()
    for queue_df, forced_retailer_key in frames:
        for _, queue_row in queue_df.iterrows():
            url = str(_row_value(queue_row, ["retail_url", "retailer_url", "url", "pdp_url", "product_url", "requested_url"]) or "").strip()
            if not url:
                continue
            retailer_raw = _row_value(queue_row, ["retailer", "retailer_name", "site"])
            retailer_key = retailer_key_from_display(retailer_raw) if retailer_raw else ""
            retailer_key = retailer_key or forced_retailer_key or retailer_key_from_url(url) or "unknown"
            rpc = _row_value(queue_row, ["rpc", "retailer_rpc", "item_code", "item_id", "product_id", "sams_club_rpc", "samsclub_rpc", "samsClubRpc", "kroger_rpc", "cvs_rpc", "walgreens_rpc", "heb_rpc"])
            item = {
                "sku": _row_value(queue_row, ["sku", "salsify_sku", "product_sku"]),
                "rpc": rpc,
                "brand": _row_value(queue_row, ["brand", "brand_name"]),
                "retailer_key": retailer_key,
                "retailer": retailer_display_from_key(retailer_key),
                "salsify_url": str(_row_value(queue_row, ["salsify_url", "source_url"]) or "").strip(),
                "url": url,
                "retail_url": url,
            }
            if retailer_key == "sams_club":
                item["sams_club_rpc"] = rpc
            dedupe = capture_key(retailer_key, rpc, url) or f"{retailer_key}|{url}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            items.append(item)
    return items


def expose_queue():
    items = load_master_queue_rows()
    single_retailer = len({item.get("retailer_key") for item in items}) == 1
    payload = {
        "retailer": items[0].get("retailer", "Retailer") if items and single_retailer else "Mixed Retailers",
        "items": items,
        "rows": items,
    }
    raw = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    escaped = html.escape(raw, quote=True)
    components.html(
        f'<script id="brand-compliance-batch" type="application/json" '
        f'data-pdp-extension-batch="{escaped}">{raw}</script>'
        f'<textarea id="pdp-extension-batch" data-pdp-extension-batch="{escaped}" '
        f'style="display:none">{raw}</textarea>'
        f'<script>window.__BRAND_COMPLIANCE_BATCH__={raw};'
        f'window.__PDP_EXTENSION_BATCH__={raw};window.PDP_EXTENSION_BATCH={raw};</script>',
        height=1,
        scrolling=False,
    )
    return len(items)


def render_score(value, include_label=True):
    text = score_text(value)
    label = f" ({score_label(value)})" if include_label and value is not None else ""
    st.markdown(f'<span class="{score_class(value)}">{text}{label}</span>', unsafe_allow_html=True)


def render_feature_pairs(source_features, retailer_features):
    row_count = max(len(source_features), len(retailer_features), 1)
    for index in range(row_count):
        source_value = source_features[index] if index < len(source_features) else ""
        retailer_value = retailer_features[index] if index < len(retailer_features) else ""
        pair_score = similarity(source_value, retailer_value)
        left, middle, right = st.columns([1.08, 1.08, .28], gap="medium")
        with left:
            st.markdown('<div class="feature-row">', unsafe_allow_html=True)
            st.markdown(f'<div class="copy-text">{html.escape(source_value or "Missing")}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with middle:
            st.markdown('<div class="feature-row">', unsafe_allow_html=True)
            st.markdown(f'<div class="copy-text">{html.escape(retailer_value or "Missing")}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with right:
            st.markdown('<div class="feature-row">', unsafe_allow_html=True)
            render_score(pair_score)
            st.markdown('</div>', unsafe_allow_html=True)


def render_image_pairs(source_images, retailer_images):
    row_count = max(len(source_images), len(retailer_images), 1)
    for index in range(row_count):
        source_url = source_images[index] if index < len(source_images) else ""
        retailer_url = retailer_images[index] if index < len(retailer_images) else ""
        # URL equality is intentionally conservative. Visual similarity belongs in the QA image engine.
        image_score = 100 if source_url and retailer_url and source_url.split("?", 1)[0] == retailer_url.split("?", 1)[0] else (0 if source_url or retailer_url else None)
        left, middle, right = st.columns([1, 1, .32], gap="small")
        with left:
            if source_url:
                st.image(source_url, width="stretch")
            else:
                st.error("Missing")
            st.caption(f"Salsify image {index + 1}")
        with middle:
            if retailer_url:
                st.image(retailer_url, width="stretch")
            else:
                st.error("Missing")
            st.caption(f"Retailer image {index + 1}")
        with right:
            render_score(image_score)



def _clean_visible_text(value):
    value = html.unescape(str(value or ""))
    if "<" in value and ">" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def _norm_label(value):
    value = html.unescape(str(value or "")).lower()
    value = value.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _asset_url_from_cell(cell):
    if cell is None:
        return ""
    link = cell.find("a", href=True)
    if link:
        return str(link.get("href", "") or "").strip()
    image = cell.find("img")
    if image:
        return str(image.get("src", "") or image.get("data-src", "") or "").strip()
    match = re.search(r"https?://[^\s\"'<>]+", str(cell))
    return match.group(0) if match else ""


@st.cache_data(show_spinner=False, ttl=1800)
def load_sams_salsify_fields(salsify_url):
    """Read Sam's Club fields from Salsify's product payload.

    The visible Salsify link can be valid in a browser but fail from the portal if
    the server request receives an error shell, an auth shell, or a slightly
    different payload shape. This loader now retries with a path-safe URL where
    plus signs remain literal (%2B) and searches the page payload more flexibly.
    """
    result = {"title": "", "description": "", "features": [], "images": [], "image_labels": [], "error": ""}
    if not salsify_url:
        result["error"] = "Salsify URL missing"
        return result

    def _salsify_url_candidates(url):
        candidates = []
        raw = str(url or "").strip()
        if raw:
            candidates.append(raw)
        try:
            parsed = urlparse(raw)
            encoded_path = quote(parsed.path or "", safe="/-._~%")
            encoded = urlunparse((parsed.scheme, parsed.netloc, encoded_path, "", parsed.query, parsed.fragment))
            if encoded and encoded not in candidates:
                candidates.append(encoded)
        except Exception:
            pass
        return candidates

    def _find_salsify_product(payload):
        if isinstance(payload, dict):
            if isinstance(payload.get("propertySets"), list) or isinstance(payload.get("digitalAssets"), dict):
                return payload
            for key in ["product", "item", "data", "pageProps", "props"]:
                found = _find_salsify_product(payload.get(key))
                if found:
                    return found
            for value in payload.values():
                found = _find_salsify_product(value)
                if found:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = _find_salsify_product(value)
                if found:
                    return found
        return None

    response_text = ""
    last_error = ""
    for candidate_url in _salsify_url_candidates(salsify_url):
        try:
            response = requests.get(
                candidate_url,
                timeout=25,
                allow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Cache-Control": "no-cache",
                },
            )
            response.raise_for_status()
            response_text = response.text or ""
            if "Something went wrong" in response_text and "__NEXT_DATA__" not in response_text:
                last_error = "Salsify returned an error shell"
                continue
            break
        except Exception as exc:
            last_error = f"Salsify request failed: {exc}"
            response_text = ""

    if not response_text:
        result["error"] = last_error or "Salsify response empty"
        return result

    soup = BeautifulSoup(response_text, "html.parser")
    product = None
    next_data_node = soup.find("script", id="__NEXT_DATA__")
    if next_data_node and next_data_node.string:
        try:
            next_data = json.loads(next_data_node.string)
            product = _find_salsify_product(next_data)
        except Exception as exc:
            result["error"] = f"Salsify __NEXT_DATA__ parse failed: {exc}"

    if not product:
        for script in soup.find_all("script"):
            script_text = script.string or script.get_text("", strip=True)
            if not script_text or "propertySets" not in script_text:
                continue
            try:
                product = _find_salsify_product(json.loads(script_text))
            except Exception:
                continue
            if product:
                break

    if not product:
        result["error"] = result.get("error") or "Salsify product payload missing"
        return result

    # Copy comes only from the Sam's Club property set and exact property IDs.
    property_values = {}
    for prop_set in product.get("propertySets", []) or []:
        if _norm_label(prop_set.get("label")) != "sams club":
            continue
        for prop in prop_set.get("properties", []) or []:
            prop_id = str(prop.get("property", "") or "").strip()
            values = prop.get("values", []) or []
            if prop_id and values:
                property_values[prop_id] = _clean_visible_text(values[0])

    result["title"] = property_values.get("SAMSCLUB_PRODUCT_TITLE", "")
    result["description"] = property_values.get("SAMSCLUB_DESCRIPTION", "")
    feature_map = {}
    for prop_id, value in property_values.items():
        match = re.fullmatch(r"SAMSCLUB_FEATURE_(\d+)", prop_id)
        if match and value:
            feature_map[int(match.group(1))] = value
    result["features"] = [feature_map[index] for index in sorted(feature_map)]

    # Exact Sam's asset order:
    # 1. Main Variant Image-Sams Club first when available.
    # 2. Legacy Main Variant Image-Club is accepted as a fallback.
    # 3. Then video, OOI, and numbered ATF assets.
    # If no main variant exists, OOI stays first so the live Sam's carousel stays aligned.
    asset_map = {}
    for asset_prop in (product.get("digitalAssets", {}) or {}).get("properties", []) or []:
        label = str(asset_prop.get("property", "") or "").strip()
        values = asset_prop.get("values", []) or []
        if label and values:
            url = str(values[0].get("value", "") or "").strip()
            if url:
                asset_map[label] = url

    def _asset_by_label(*labels):
        for label in labels:
            if label in asset_map:
                return label, asset_map[label]
        wanted = {_norm_label(label) for label in labels}
        for label, url in asset_map.items():
            if _norm_label(label) in wanted:
                return label, url
        return "", ""

    chosen = []
    main_variant_label, main_variant = _asset_by_label(
        "Main Variant Image-Sams Club",
        "Main Variant Image-Club",
    )
    video_label, video_asset = _asset_by_label("ATF Video-Sams Club")
    ooi_label, ooi_asset = _asset_by_label("Online Optimized Image-")

    if main_variant:
        chosen.append((main_variant_label or "Main Variant Image-Sams Club", main_variant))
        if video_asset:
            chosen.append((video_label or "ATF Video-Sams Club", video_asset))
        if ooi_asset:
            chosen.append((ooi_label or "Online Optimized Image-", ooi_asset))
    else:
        # When Main Variant Image-Sams Club is unavailable, OOI becomes the first
        # Salsify image and stays aligned to Sam's Club carousel image 1.
        if ooi_asset:
            chosen.append((ooi_label or "Online Optimized Image-", ooi_asset))
        if video_asset:
            chosen.append((video_label or "ATF Video-Sams Club", video_asset))

    numbered = []
    for label, url in asset_map.items():
        match = re.fullmatch(r"ATF\s+(\d+)-Sams Club", label, flags=re.IGNORECASE)
        if match and int(match.group(1)) >= 2:
            numbered.append((int(match.group(1)), label, url))
    for _, label, url in sorted(numbered):
        chosen.append((label, url))

    result["image_labels"] = [label for label, _ in chosen]
    result["images"] = [url for _, url in chosen]
    return result


def _capture_file_candidates():
    capture_dir = Path(ROOT) / "data" / "captures"
    if not capture_dir.exists():
        return []
    return sorted(capture_dir.glob("*.txt"), key=lambda path: path.stat().st_mtime, reverse=True)


def _generic_capture_from_parsed(parsed):
    return {
        "title": _clean_visible_text(parsed.get("title", "")),
        "description": _clean_visible_text(parsed.get("description", "")),
        "features": [_clean_visible_text(item) for item in parsed.get("features", []) or [] if _clean_visible_text(item)],
        "images": [clean_url(url) for url in parsed.get("images", []) or [] if clean_url(url)],
    }


def _parse_capture_block_json(block):
    parsed_match = re.search(
        r"-----BEGIN PARSED JSON-----\s*(.*?)\s*-----END PARSED JSON-----",
        block,
        flags=re.DOTALL,
    )
    if not parsed_match:
        return None
    try:
        parsed = json.loads(parsed_match.group(1))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _capture_meta(block, label):
    match = re.search(rf"^{re.escape(label)}:\s*(.*)$", block, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _capture_html(block):
    html_match = re.search(
        r"-----BEGIN HTML-----\s*(.*?)\s*-----END HTML-----",
        block,
        flags=re.DOTALL,
    )
    return html_match.group(1) if html_match else ""


def _parse_sams_capture(parsed, raw_html):
    # This is the original Sam's Club-specific parser behavior. Do not use
    # generic image/feature fallback for Sam's because that pulls logos, pins,
    # fulfillment icons, and shipping/pickup navigation into the comparison.
    decoded_html = raw_html
    for _ in range(2):
        next_decoded = html.unescape(decoded_html)
        if next_decoded == decoded_html:
            break
        decoded_html = next_decoded
    page_soup = BeautifulSoup(decoded_html, "html.parser")

    highlights = []
    product_json_descriptions = []
    for script in page_soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw_json = script.string or script.get_text("", strip=True)
        if not raw_json:
            continue
        try:
            payload = json.loads(raw_json)
        except Exception:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph", [])
            graph_items = graph if isinstance(graph, list) else []
            for item in [candidate] + graph_items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type", "")
                types = item_type if isinstance(item_type, list) else [item_type]
                if "Product" in types and item.get("description"):
                    product_json_descriptions.append(str(item["description"]))
    if not product_json_descriptions:
        flattened_match = re.search(
            r'"description"\s*:\s*("(?:\\.|[^"\\])*")\s*,\s*"model"',
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if flattened_match:
            try:
                product_json_descriptions.append(json.loads(flattened_match.group(1)))
            except Exception:
                pass
    for description_html in product_json_descriptions:
        feature_soup = BeautifulSoup(html.unescape(description_html), "html.parser")
        feature_items = [_clean_visible_text(item.get_text(" ", strip=True)) for item in feature_soup.find_all("li")]
        feature_items = [item for item in feature_items if item]
        if feature_items:
            highlights = feature_items
            break
    if not highlights:
        highlights_match = re.search(
            r"###\s+Highlights\s*(.*?)(?:\s*Read more|\n#{3,4}\s+)",
            decoded_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if highlights_match:
            highlights = [_clean_visible_text(item) for item in re.findall(r"(?m)^-\s+(.+)$", highlights_match.group(1)) if _clean_visible_text(item)]

    details = ""
    description_container = page_soup.find(attrs={"data-testid": "product-description-content"})
    if description_container:
        content = description_container.find(class_=lambda value: value and "dangerous-html" in value)
        content = content or description_container
        paragraph_parts = []
        for node in content.find_all(["h1", "h2", "h3", "h4", "p"]):
            text = _clean_visible_text(node.get_text(" ", strip=True))
            if not text:
                continue
            if node.name in {"h1", "h2", "h3", "h4"} and text[-1:] not in ".!?:":
                text += "."
            if text not in paragraph_parts:
                paragraph_parts.append(text)
        details = " ".join(paragraph_parts)
    if not details:
        details_match = re.search(
            r"###\s+About this item\s*\n+####\s+Product details\s*(.*?)(?:\n\s*info:|\n###\s+Specifications)",
            decoded_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if details_match:
            lines = []
            for line in details_match.group(1).splitlines():
                raw_line = line.strip()
                if not raw_line:
                    continue
                heading_match = re.match(r"^#{1,6}\s+(.+)$", raw_line)
                line = _clean_visible_text(heading_match.group(1) if heading_match else raw_line)
                if heading_match and line and line[-1:] not in ".!?:":
                    line += "."
                if line:
                    lines.append(line)
            details = " ".join(lines)

    parsed_description = str(parsed.get("description", "") or "").strip()
    parsed_description_is_list = bool(re.search(r"<(?:ul|ol|li)\b", html.unescape(parsed_description), flags=re.IGNORECASE))
    retailer_description = details or (_clean_visible_text(parsed_description) if not parsed_description_is_list else "")

    def _sams_image_urls_from_text(text):
        """Extract Sam's product ASR image URLs from a scoped PDP hero fragment.

        Important: pass only the current item PDP hero/carousel markup into this
        helper. Do not pass the full page HTML, because Sam's pages also contain
        recommender shelves such as "Members also considered" that use the same
        i5.samsclubimages.com/asr CDN pattern.
        """
        candidates = []
        seen_exact = set()
        text = html.unescape(str(text or ""))
        pattern = r"https?://i\d+\.samsclubimages\.com/asr/[^\s\"'<>),]+"
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            url = html.unescape(match.group(0)).strip()
            url = re.sub(r"[;]+$", "", url)
            context = text[max(0, match.start() - 1400):match.end() + 1400]
            is_video_thumb = bool(re.search(
                r"ui-video-play-button-testid|thumbnail\s+video\s+image|video\s+image|play-button|hero-carousel-video",
                context,
                flags=re.IGNORECASE,
            ))
            if is_video_thumb and "#bc_video_thumbnail" not in url:
                url = url + "#bc_video_thumbnail"
            if not url or url in seen_exact:
                continue
            if not re.search(r"/asr/", url, flags=re.IGNORECASE):
                continue
            if re.search(r"logo|icon|sprite|placeholder|pin|truck|delivery|pickup|reorder", url, flags=re.IGNORECASE):
                continue
            seen_exact.add(url)
            candidates.append(url)
        return candidates

    def _sams_video_urls_from_text(text):
        """Extract actual Sam's rich-media video URLs from PDP JSON/HTML.

        Sam's stores product videos in product JSON under videos[].versions.small
        and videos[].versions.large. These are better for the comparison row than
        the thumbnail image because they represent the real PDP video asset.
        """
        text = html.unescape(str(text or ""))
        text = text.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
        urls = []
        seen = set()
        patterns = [
            r"https?://i5-richmedia\.samsclubimages\.com/asr-rm/[^\s\"'<>]+?\.mp4",
            r"https?://[^\s\"'<>]+?video_samsmigrationforwardsync\.mp4",
            r"https?://[^\s\"'<>]+?\.mp4",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                url = html.unescape(match.group(0)).replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/").strip()
                url = url.rstrip("\\\"'<>),;")
                if "samsclubimages.com" not in url.lower():
                    continue
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
        return urls

    def _sams_pdp_hero_fragments(soup, decoded_text):
        """Return only HTML fragments that belong to the current PDP hero carousel."""
        fragments = []
        seen_fragments = set()

        selectors = [
            'section[data-module-name="ItemHeroVerticalCarouselModule"]',
            '[data-module-name="ItemHeroVerticalCarouselModule"]',
            '[data-testid="vertical-carousel"]',
            '[data-testid*="vertical-carousel"]',
            '[data-testid*="hero-carousel"]',
            '[class*="ItemHeroVerticalCarousel"]',
            '[class*="vertical-carousel"]',
            '[class*="hero-carousel"]',
        ]
        for selector in selectors:
            for node in soup.select(selector):
                fragment = str(node)
                if fragment and fragment not in seen_fragments:
                    seen_fragments.add(fragment)
                    fragments.append(fragment)

        # Fallback for TXT captures where BeautifulSoup cannot rebuild the module
        # cleanly. Keep only the page area before recommendation shelves start.
        if not fragments:
            limited = re.split(
                r"Members also considered|Sponsored products|Related products|Customers also|Recommended for you|Similar items",
                decoded_text,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            start_match = re.search(
                r"ItemHeroVerticalCarouselModule|vertical-carousel|hero-carousel|hero-carousel-image",
                limited,
                flags=re.IGNORECASE,
            )
            fragments.append(limited[start_match.start():] if start_match else limited[:250000])
        return fragments

    actual_video_urls = []
    for video_source_text in [parsed_description, raw_html, decoded_html]:
        for video_url in _sams_video_urls_from_text(video_source_text):
            if video_url not in actual_video_urls:
                actual_video_urls.append(video_url)

    raw_images = [str(url or "").strip() for url in parsed.get("images", [])]

    # Sam's-only rule: scan only the current PDP hero carousel/module, not the
    # whole page. This prevents pulling images from "Members also considered"
    # or other lower-page recommendation modules.
    for fragment in _sams_pdp_hero_fragments(page_soup, decoded_html):
        raw_images.extend(_sams_image_urls_from_text(fragment))

    raw_images = [
        html.unescape(url).strip()
        for url in raw_images
        if "samsclubimages.com/asr/" in html.unescape(str(url or "")).lower()
        and not re.search(r"logo|icon|sprite|placeholder|pin|truck|delivery|pickup|reorder", str(url or ""), flags=re.I)
    ]

    base_counts = {}
    max_sizes = {}
    first_position = {}
    video_bases = set()
    occurrence_position = 0
    for url in raw_images:
        is_video_thumb = "#bc_video_thumbnail" in url
        clean_image_url = url.split("#", 1)[0]
        base = clean_image_url.split("?", 1)[0]
        if not base:
            continue
        if is_video_thumb:
            video_bases.add(base)
        base_counts[base] = base_counts.get(base, 0) + 1
        first_position.setdefault(base, occurrence_position)
        occurrence_position += 1
        sizes = [int(value) for value in re.findall(r"odn(?:Height|Width)=(\d+)", clean_image_url, flags=re.IGNORECASE)]
        max_sizes[base] = max([max_sizes.get(base, 0), *sizes] if sizes else [max_sizes.get(base, 0)])

    hero_candidates = [base for base, size in max_sizes.items() if size >= 640]
    hero = min(hero_candidates, key=lambda base: first_position[base]) if hero_candidates else ""

    # Keep carousel entries in PDP order, but preserve the video thumbnail as a
    # separate slot. Sam's can reuse the same ASR asset for the hero image and
    # the video tile, so de-duping only by base URL would make the video row show
    # as Missing.
    gallery_entries = []
    if hero:
        gallery_entries.append((hero, False))

    inserted_video = False
    for video_base in sorted(video_bases, key=lambda base: first_position.get(base, 999999)):
        if (video_base, True) not in gallery_entries:
            insert_at = 1 if gallery_entries else 0
            gallery_entries.insert(insert_at, (video_base, True))
            inserted_video = True
            break
    if actual_video_urls and not inserted_video:
        insert_at = 1 if gallery_entries else 0
        gallery_entries.insert(insert_at, ("__sams_actual_video__", True))

    for base in sorted(first_position, key=first_position.get):
        if (base, False) in gallery_entries:
            continue
        if base_counts.get(base, 0) >= 2 and max_sizes.get(base, 0) <= 160:
            gallery_entries.append((base, False))

    def _gallery_url(base, is_video):
        if is_video and actual_video_urls:
            return actual_video_urls[0]
        if base == "__sams_actual_video__":
            return actual_video_urls[0] if actual_video_urls else ""
        suffix = "#bc_video_thumbnail" if is_video else ""
        return f"{base}?odnHeight=640&odnWidth=640&odnBg=FFFFFF{suffix}"

    return {
        "title": _clean_visible_text(parsed.get("title", "")),
        "description": retailer_description,
        "features": highlights,
        "images": [_gallery_url(base, is_video) for base, is_video in gallery_entries],
    }


@st.cache_data(show_spinner=False, ttl=120)
def load_extension_captures(cache_token=""):
    captures = {}
    for path in _capture_file_candidates():
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = re.split(r"(?=^=+\s*PDP CAPTURE\s+\d+\s*=+)", text, flags=re.MULTILINE)
        for block in blocks:
            parsed = _parse_capture_block_json(block)
            if not parsed:
                continue
            requested_url = str(parsed.get("requestedUrl") or parsed.get("requested_url") or _capture_meta(block, "Requested URL") or "").strip()
            final_url = str(parsed.get("finalUrl") or parsed.get("final_url") or _capture_meta(block, "Final URL") or "").strip()
            retail_url = requested_url or final_url
            retailer_key = retailer_key_from_display(parsed.get("retailer_key") or parsed.get("retailer") or "") or retailer_key_from_url(retail_url)
            rpc = str(parsed.get("rpc", "") or parsed.get("retailer_rpc", "") or "").strip()
            if not retailer_key or not rpc:
                continue
            if RETAILER_CONFIG.get(retailer_key, {}).get("capture_mode") == "sams_club":
                capture = _parse_sams_capture(parsed, _capture_html(block))
            else:
                capture = _generic_capture_from_parsed(parsed)
            capture.update({"retailer_key": retailer_key, "rpc": rpc, "retail_url": retail_url})
            for key in candidate_capture_keys(retailer_key, rpc, retail_url):
                captures.setdefault(key, capture)
    return captures


def find_extension_capture(captures, retailer_key, rpc, retail_url):
    for key in candidate_capture_keys(retailer_key, rpc, retail_url):
        if key in captures:
            return captures[key]
    return {}

header_area = st.container()
# CLEAN_UI_FIXED_2026_07_24

# Source of truth for item list: current retailer file in /data.
# Source of truth for retailer parsing: TXT capture files in /data/captures.
current_queue_items_all = load_master_queue_rows()
if not current_queue_items_all:
    st.info("No current retailer queue file found in /data. Add a file like data/skus_samsclub.csv.")
    st.stop()

capture_files = _capture_file_candidates()
capture_token = str(max((path.stat().st_mtime for path in capture_files), default=0))
extension_captures = load_extension_captures(capture_token)

with st.sidebar:
    st.header("Retailers")
    selected_retailer = st.radio("Choose retailer", RETAILERS, index=1)
    st.divider()
    display_filter = st.radio("Show", ["All Items", "Issues Only", "Blocked / Unavailable"])
    brand_filter_placeholder = st.empty()
    search = st.text_input("Search SKU, RPC, or brand")
    collapse_all_items = st.checkbox("Collapse all items", value=False)

selected_retailer_key = "" if selected_retailer == "All Retailers" else retailer_key_from_display(selected_retailer)
current_queue_items = [item for item in current_queue_items_all if not selected_retailer_key or item.get("retailer_key") == selected_retailer_key]
rows = []
for item in current_queue_items:
    retail_url = item.get("retail_url") or item.get("url") or ""
    capture = find_extension_capture(extension_captures, item.get("retailer_key"), str(item.get("rpc") or item.get("sams_club_rpc") or "").strip(), retail_url)
    rows.append({
        "run_date": "Current TXT capture",
        "retailer": item.get("retailer") or retailer_display_from_key(item.get("retailer_key")),
        "sku": item.get("sku", ""),
        "rpc": item.get("rpc") or item.get("sams_club_rpc") or "",
        "brand": item.get("brand", ""),
        "salsify_url": item.get("salsify_url", ""),
        "retail_url": retail_url,
        "url": retail_url,
        "status": "Green" if capture else "Capture blocked",
        "overall_score": None,
        "retailer_title": capture.get("title", "") if capture else "",
        "retailer_description": capture.get("description", "") if capture else "",
        "retailer_features": json.dumps(capture.get("features", []), ensure_ascii=False) if capture else "[]",
        "retailer_images": json.dumps(capture.get("images", []), ensure_ascii=False) if capture else "[]",
        "salsify_title": "",
        "salsify_description": "",
        "salsify_features": "[]",
        "salsify_images": "[]",
    })

df = pd.DataFrame(rows)
if df.empty:
    st.warning(f"No current file items for {selected_retailer}.")
    st.stop()

for column, default in {"status": "Unavailable", "overall_score": None, "brand": "", "sku": "", "rpc": ""}.items():
    if column not in df.columns:
        df[column] = default

brand_options = ["All Brands"]
if "brand" in df.columns:
    brand_options.extend(sorted({str(value).strip() for value in df["brand"].tolist() if str(value).strip()}))
with brand_filter_placeholder:
    selected_brand = st.selectbox("Brand", brand_options)

if selected_brand != "All Brands":
    df = df[df["brand"].astype(str) == selected_brand]
if display_filter == "Issues Only":
    df = df[~df["status"].isin(["Green"])]
elif display_filter == "Blocked / Unavailable":
    df = df[df["status"].isin(["Capture blocked", "Source unavailable", "Retailer URL missing", "Unavailable"])]
if search:
    query = search.lower().strip()
    df = df[df.apply(lambda row: any(query in str(safe(row, key)).lower() for key in ["sku", "rpc", "brand"]), axis=1)]

def build_current_view_excel(current_df):
    export_rows = []
    for _, row in current_df.iterrows():
        features = as_list(safe(row, "retailer_features", "[]"))
        images = as_list(safe(row, "retailer_images", "[]"))
        export_row = {
            "Retailer": safe(row, "retailer"),
            "Brand": safe(row, "brand"),
            "SKU": safe(row, "sku"),
            "RPC": safe(row, "rpc"),
            "Status": safe(row, "status"),
            "Salsify URL": safe(row, "salsify_url"),
            "Retailer URL": safe(row, "retail_url") or safe(row, "url"),
            "Retailer Title": safe(row, "retailer_title"),
            "Retailer Description": safe(row, "retailer_description"),
        }
        for index in range(10): export_row[f"Retailer Feature {index + 1}"] = features[index] if index < len(features) else ""
        for index in range(12): export_row[f"Retailer Image {index + 1}"] = images[index] if index < len(images) else ""
        export_rows.append(export_row)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(export_rows).to_excel(writer, sheet_name="Current View", index=False)
        pd.DataFrame([
            {"Metric": "Retailer", "Value": selected_retailer},
            {"Metric": "Rows", "Value": len(current_df)},
            {"Metric": "Capture files found", "Value": len(capture_files)},
            {"Metric": "TXT capture matches", "Value": len(extension_captures)},
        ]).to_excel(writer, sheet_name="Summary", index=False)
    output.seek(0)
    return output.getvalue()

if df.empty:
    st.info("No items match the selected filters.")
    st.stop()

export_file_label = re.sub(r"[^A-Za-z0-9]+", "_", str(selected_retailer or "all")).strip("_").lower() or "all"
with header_area:
    title_col, download_col = st.columns([0.78, 0.22], vertical_alignment="center")
    with title_col:
        st.markdown(f"""
        <div style="display:flex; align-items:center; gap:10px; margin:0 0 18px 0; padding-top:20px;">
          <img src="{APP_FAVICON_DATA_URI}" alt="KC logo" style="width:36px; height:36px; object-fit:contain; border-radius:4px; flex:0 0 auto;" />
          <h1 style="font-size:1.55rem; line-height:1.35; margin:0; padding:0;">Brand Compliance Portal</h1>
        </div>
        """, unsafe_allow_html=True)
    with download_col:
        st.download_button("Download Excel", data=build_current_view_excel(df), file_name=f"brand_compliance_{export_file_label}_current_view.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

st.markdown("<div style=\"height:6px\"></div>", unsafe_allow_html=True)

def clean_url(value):
    value = str(value or "").strip()
    return value if value.lower().startswith(("http://", "https://")) else ""


def linked_item_code(label, code, url):
    safe_label = html.escape(str(label or ""))
    safe_code = html.escape(str(code or "Missing"))
    href = clean_url(url)
    if href:
        code_html = (
            f'<a class="qa-id-link" href="{html.escape(href, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">{safe_code}</a>'
        )
    else:
        code_html = f'<span class="qa-id">{safe_code}</span>'
    return f'<div class="qa-heading">{safe_label}: {code_html}</div>'


def score_html(value, include_label=True):
    label = f" ({score_label(value)})" if include_label and value is not None else ""
    return f'<span class="{score_class(value)}">{score_text(value)}{label}</span>'


def image_cell(url, label):
    href = clean_url(url)
    safe_label = html.escape(label)
    if href and href.lower().split("?", 1)[0].endswith((".mp4", ".webm", ".mov")):
        safe_src = html.escape(href, quote=True)
        return (
            f'<div class="locked-image-box">'
            f'<video src="{safe_src}" controls preload="metadata" '
            f'aria-label="{safe_label}" title="{safe_label}" '
            f'style="width:100%;height:100%;object-fit:contain;background:#000"></video>'
            f'</div>'
        )
    if href:
        safe_src = html.escape(href, quote=True)
        return (
            f'<div class="locked-image-box">'
            f'<img src="{safe_src}" alt="{safe_label}" title="{safe_label}" loading="lazy">'
            f'</div>'
        )
    return '<div class="locked-image-missing">Missing</div>'


@st.cache_data(show_spinner=False, ttl=3600)
def visual_image_score(left_url, right_url):
    """Compare the actual image pixels rather than comparing different CDN URLs."""
    if not left_url and not right_url:
        return None
    if not left_url or not right_url:
        return 0

    def _is_video_asset(url):
        url_l = str(url or "").lower()
        return "#bc_video_thumbnail" in url_l or url_l.split("?", 1)[0].endswith((".mp4", ".webm", ".mov"))

    def _image_base(url):
        return str(url or "").split("#", 1)[0].split("?", 1)[0]

    left_is_video = _is_video_asset(left_url)
    right_is_video = _is_video_asset(right_url)
    if left_is_video or right_is_video:
        return 100
    if _image_base(left_url) == _image_base(right_url):
        return 100
    try:
        hashes=[]
        for url in [left_url, right_url]:
            response=requests.get(url, timeout=12, headers={"User-Agent":"Mozilla/5.0"})
            response.raise_for_status()
            image=Image.open(BytesIO(response.content)).convert("RGB")
            image=ImageOps.fit(image, (17, 16), method=Image.Resampling.LANCZOS)
            gray=ImageOps.grayscale(image)
            pixels=list(gray.getdata())
            bits=[]
            for y in range(16):
                row=pixels[y*17:(y+1)*17]
                bits.extend(row[x] > row[x+1] for x in range(16))
            hashes.append(bits)
        distance=sum(a != b for a,b in zip(hashes[0], hashes[1]))
        # 256-bit dHash. Identical/near-identical assets remain high; unrelated
        # carousel assets fall rapidly. Treat near-identical PDP/Salsify image
        # renders as 100 so resized CDN thumbnails do not show as false issues.
        score = max(0, min(100, int(round(100 - (distance / 128) * 100))))
        return 100 if score >= 95 else score
    except Exception:
        return 100 if _image_base(left_url) == _image_base(right_url) else 0


def render_item_detail(row):
    source_title = safe(row, "salsify_title")
    retailer_title = safe(row, "retailer_title")
    source_description = safe(row, "salsify_description")
    retailer_description = safe(row, "retailer_description")
    source_features = as_list(safe(row, "salsify_features", "[]"))
    retailer_features = as_list(safe(row, "retailer_features", "[]"))
    source_images = as_list(safe(row, "salsify_images", "[]"))
    source_image_labels = [f"Salsify image {index + 1}" for index in range(len(source_images))]
    retailer_images = as_list(safe(row, "retailer_images", "[]"))

    # Retailer isolation rule. Sam's keeps its special Salsify and capture rules.
    # Other retailers use generic extension JSON only. Matching is never RPC-only.
    retailer_name = str(safe(row, "retailer") or "Retailer")
    retail_url = clean_url(safe(row, "retail_url") or safe(row, "url") or safe(row, "requested_url"))
    retailer_config_key = retailer_key_from_display(retailer_name) or retailer_key_from_url(retail_url)
    retailer_key = retailer_name.strip().lower()
    if RETAILER_CONFIG.get(retailer_config_key, {}).get("salsify_mode") == "sams_club":
        salsify_live = load_sams_salsify_fields(str(safe(row, "salsify_url") or ""))
        source_title = salsify_live.get("title") or ("" if normalize(source_title) == "something went wrong" else source_title)
        source_description = salsify_live.get("description") or ("" if normalize(source_description) in {"missing", "something went wrong"} else source_description)
        source_features = salsify_live.get("features") or [feature for feature in source_features if normalize(feature) not in {"missing", "something went wrong"}]
        source_images = salsify_live.get("images") or source_images
        if salsify_live.get("image_labels"):
            source_image_labels = salsify_live["image_labels"]

    captured = find_extension_capture(extension_captures, retailer_config_key, str(safe(row, "rpc") or "").strip(), retail_url)
    retailer_title = captured.get("title") or retailer_title
    retailer_description = captured.get("description") or retailer_description
    retailer_features = captured.get("features") or retailer_features
    retailer_images = captured.get("images") or retailer_images

    source_images = [clean_url(url) for url in source_images if clean_url(url)]
    retailer_images = [clean_url(url) for url in retailer_images if clean_url(url)]

    # Sam's Club alignment rule: when Salsify includes ATF Video-Sams Club but
    # the live Sam's carousel has no video asset, preserve the video row and
    # insert a blank retailer slot. This shifts Sam's image 2 and later images
    # down instead of comparing Sam's image 2 against the Salsify video.
    if retailer_key in {"sam's club", "sams club", "samsclub"}:
        video_index = next((
            index for index, label in enumerate(source_image_labels)
            if _norm_label(label) == "atf video sams club"
        ), None)
        retailer_has_video = any(
            "#bc_video_thumbnail" in str(url).lower()
            or str(url).lower().split("?", 1)[0].endswith((".mp4", ".webm", ".mov"))
            for url in retailer_images
        )
        if video_index is not None and not retailer_has_video:
            retailer_images.insert(video_index, "")

        # Sam's raw HTML can contain extra ASR assets from duplicated carousel
        # markup and lower-page content. Keep only the retailer slots needed to
        # compare against the Salsify image set so the UI does not add extra
        # Sam's-only rows at the bottom.
        if source_images:
            retailer_images = retailer_images[:len(source_images)]

    # Recalculate scores from the content actually displayed. Database scores
    # may reflect older parsing and should not drive the current review UI.
    live_title_score = similarity(source_title, retailer_title)
    live_description_score = similarity(source_description, retailer_description)
    live_feature_scores = []
    for index in range(max(len(source_features), len(retailer_features), 1)):
        left_feature = str(source_features[index]) if index < len(source_features) else ""
        right_feature = str(retailer_features[index]) if index < len(retailer_features) else ""
        live_feature_scores.append(similarity(left_feature, right_feature) if left_feature and right_feature else 0)
    valid_feature_scores = [value for value in live_feature_scores if value is not None]
    live_feature_average = (
        int(round(sum(valid_feature_scores) / len(valid_feature_scores)))
        if valid_feature_scores else None
    )
    live_copy_values = [value for value in [live_title_score, live_description_score, *live_feature_scores] if value is not None]
    live_copy_average = int(round(sum(live_copy_values) / len(live_copy_values))) if live_copy_values else None

    source_header = linked_item_code("Salsify", safe(row, "sku"), safe(row, "salsify_url"))
    retailer_header = linked_item_code(retailer_name, safe(row, "rpc"), retail_url or safe(row, "retail_url"))
    reason_html = ""

    copy_parts = [
        '<div class="locked-copy-grid">',
        source_header, retailer_header, '<div></div>',
        '<div class="score-strip" style="grid-column:1/-1;">Copy — Avg '
        f'<span style="float:right">{score_text(live_copy_average)}</span></div>',
        '<div class="section-title" style="grid-column:1/-1;">Title</div>',
        f'<div class="locked-copy-cell">{html.escape(str(source_title or "Missing"))}</div>',
        f'<div class="locked-copy-cell">{html.escape(str(retailer_title or "Missing"))}</div>',
        f'<div class="locked-score">{score_html(live_title_score)}</div>',
        '<div class="section-title" style="grid-column:1/-1;">Description</div>',
        f'<div class="locked-copy-cell">{html.escape(str(source_description or "Missing"))}</div>',
        f'<div class="locked-copy-cell">{html.escape(str(retailer_description or "Missing"))}</div>',
        f'<div class="locked-score">{score_html(live_description_score)}</div>',
        '<div class="section-title" style="grid-column:1/-1;">Features '
        f'<span style="float:right">{score_html(live_feature_average)}</span></div>',
    ]
    feature_count = max(len(source_features), len(retailer_features), 1)
    for index in range(feature_count):
        left = str(source_features[index]) if index < len(source_features) else ""
        right = str(retailer_features[index]) if index < len(retailer_features) else ""
        copy_parts.extend([
            f'<div class="locked-copy-cell locked-feature-cell">{html.escape(left or "Missing")}</div>',
            f'<div class="locked-copy-cell locked-feature-cell">{html.escape(right or "Missing")}</div>',
            '<div class="locked-feature-cell"></div>',
            f'<div class="feature-score-under">{score_html(live_feature_scores[index])}</div>',
            '<div class="feature-score-spacer"></div>',
        ])
    copy_parts.append('</div>')

    image_count = max(len(source_images), len(retailer_images), 1)
    live_image_scores = []
    for index in range(image_count):
        left_url = source_images[index] if index < len(source_images) else ""
        right_url = retailer_images[index] if index < len(retailer_images) else ""
        live_image_scores.append(visual_image_score(left_url, right_url))
    valid_image_scores = [value for value in live_image_scores if value is not None]
    live_image_average = int(round(sum(valid_image_scores) / len(valid_image_scores))) if valid_image_scores else None

    image_parts = [
        '<div class="locked-image-head">',
        '<div class="qa-heading">Salsify</div>',
        f'<div class="qa-heading">{html.escape(retailer_name)}</div>',
        '<div></div></div>',
        '<div class="image-strip">Images — Avg '
        f'<span style="float:right">{score_text(live_image_average)}</span></div>',
    ]
    for index in range(image_count):
        left = source_images[index] if index < len(source_images) else ""
        right = retailer_images[index] if index < len(retailer_images) else ""
        image_score = live_image_scores[index]
        image_parts.extend([
            '<div class="locked-image-row">',
            f'<div>{image_cell(left, source_image_labels[index] if index < len(source_image_labels) else f"Salsify image {index + 1}")}</div>',
            f'<div>{image_cell(right, f"{retailer_name} image {index + 1}")}</div>',
            f'<div class="locked-score">{score_html(image_score)}</div>',
            '</div>',
        ])

    st.markdown(
        '<div class="locked-item-card">'
        f'{reason_html}<div class="locked-review-grid">'
        f'<div>{"".join(copy_parts)}</div><div>{"".join(image_parts)}</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )



for _, row in df.iterrows():
    item_status = safe(row, "status", "Unavailable")
    item_label = (
        f"{safe(row, 'brand')} | SKU {safe(row, 'sku')} | "
        f"RPC {safe(row, 'rpc')} | {item_status} | "
        f"{score_text(safe(row, 'overall_score', None))}"
    )
    with st.expander(item_label, expanded=not collapse_all_items):
        render_item_detail(row)
