# =========================================
# IMPORTS
# =========================================
import re
import html
import json
import time
import hashlib
import traceback
from io import BytesIO
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError
import warnings
from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from pandas.errors import EmptyDataError
import threading
from requests.adapters import HTTPAdapter
import base64

# =========================================
# APP SETUP
# =========================================
st.set_page_config(layout="wide")
st.title("PDP QA Tool ✅")

st.markdown(
    "<style>"
    "div[data-testid='stFileUploader'] > section {"
    "background:#232733;"
    "border:1px solid #2f3442;"
    "border-radius:10px;"
    "padding:10px;"
    "}"
    "div[data-testid='stDownloadButton'] > button {"
    "width:100%;"
    "min-height:56px;"
    "border-radius:10px;"
    "border:1px solid #2f3442;"
    "background:#232733;"
    "color:white;"
    "font-weight:700;"
    "}"
    "div[data-testid='stDownloadButton'] > button:hover {"
    "border-color:#4EA1FF;"
    "color:white;"
    "}"
    "</style>",
    unsafe_allow_html=True,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 6
IMAGE_TIMEOUT = 2.5
MAX_CACHE = 400
# Retailer-specific fetch tuning
WALGREENS_REQUEST_TIMEOUT = 18
WALGREENS_DEBUG_TIMEOUT = 25
WALGREENS_API_TIMEOUT = 10

# =========================================
# PERFORMANCE SETTINGS
# =========================================
# Lower these for Streamlit Cloud stability.
BATCH_SIZE = 16
MAX_WORKERS = 6
UI_UPDATE_EVERY = 2

# Faster image compare via tiny difference hash.
IMAGE_HASH_WIDTH = 9
IMAGE_HASH_HEIGHT = 8

# Keep caches smaller to prevent Streamlit Cloud memory pressure.
HTML_CACHE_MAX = 60
IMAGE_HASH_CACHE_MAX = 120

# Hard image safety limits.
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_SAFE_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_SLOTS_TO_COMPARE = 20
MAX_IMAGE_SLOTS_TO_SCORE = 5

html_cache = {}
image_hash_cache = {}
image_compare_cache = {}
IMAGE_COMPARE_CACHE_MAX = 600

thread_local = threading.local()

# =========================================
# VISUAL LAYOUT SETTINGS
# =========================================
SECTION_HEADER_SIZE = 24
COPY_TEXT_SIZE = 15
COPY_LINE_HEIGHT = 1.28
SECTION_VERTICAL_GAP = 8

# Use one shared spacing value so the image area feels mathematically even.
IMG_SPACE_PX = 4
IMG_BOX_HEIGHT = 104
IMG_SCORE_WIDTH_PX = 72

TITLE_TO_DESCRIPTION_GAP_PX = 28
DESCRIPTION_TO_FEATURES_GAP_PX = 28


def get_session():
    if not hasattr(thread_local, "session"):
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=40,
            pool_maxsize=40,
            max_retries=0,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(HEADERS)
        thread_local.session = session
    return thread_local.session

# =========================================
# GENERIC HELPERS
# =========================================
def normalize_space(text):
    text = str(text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_text(text):
    if not isinstance(text, str):
        return ""
    return re.sub(r"[^a-z0-9\s]", "", text.lower())


def dedupe_preserve_order(items):
    seen = set()
    out = []
    for item in items:
        item = normalize_space(item)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def keyword_score(a, b):
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)

    if not a_norm and not b_norm:
        return 0
    if not a_norm or not b_norm:
        return 0

    return int(SequenceMatcher(None, a_norm, b_norm).ratio() * 100)


def description_similarity_score(a, b):
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)

    if not a_norm and not b_norm:
        return 0
    if not a_norm or not b_norm:
        return 0
    if a_norm == b_norm:
        return 100

    return int(SequenceMatcher(None, a_norm, b_norm).ratio() * 100)

def html_escape_text(text):
    return html.escape(str(text or ""))


def equal_height_block(text, min_height=150):
    safe_text = html_escape_text(text or "Missing")
    return (
        f"<div style=\""
        f"width:100%;"
        f"min-height:{min_height}px;"
        f"padding:0;"
        f"margin:0;"
        f"background:transparent;"
        f"color:#FFFFFF;"
        f"white-space:pre-wrap;"
        f"line-height:{COPY_LINE_HEIGHT};"
        f"font-size:{COPY_TEXT_SIZE}px;"
        f"font-weight:500;"
        f"text-indent:0;"
        f"overflow-wrap:anywhere;"
        f"word-break:break-word;"
        f"\">{safe_text}</div>"
    )


def equal_feature_block(text, min_height=40):
    safe_text = html_escape_text(text or "Missing")
    return (
        f"<div style=\""
        f"width:100%;"
        f"min-height:{min_height}px;"
        f"padding:0;"
        f"margin:0;"
        f"background:transparent;"
        f"color:#FFFFFF;"
        f"white-space:pre-wrap;"
        f"line-height:{COPY_LINE_HEIGHT};"
        f"font-size:{COPY_TEXT_SIZE}px;"
        f"font-weight:500;"
        f"text-indent:0;"
        f"overflow-wrap:anywhere;"
        f"word-break:break-word;"
        f"\">{safe_text}</div>"
    )


def score_text_html(score):
    if score >= 80:
        color = "#4CAF50"
        label = "Strong"
    elif score >= 50:
        color = "#FFC107"
        label = "Review"
    else:
        color = "#F44336"
        label = "Poor"

    return f"<span style='color:{color}; font-weight:900; font-size:22px;'>{score}% ({label})</span>"


def section_header_html(label, score):
    safe_label = html_escape_text(label or "")
    return (
        f"<div style=\""
        f"display:flex;"
        f"justify-content:space-between;"
        f"align-items:flex-end;"
        f"gap:12px;"
        f"margin-top:{SECTION_VERTICAL_GAP}px;"
        f"margin-bottom:{SECTION_VERTICAL_GAP}px;"
        f"\">"
        f"<div style=\"font-size:{SECTION_HEADER_SIZE}px; font-weight:900; color:#FFFFFF; line-height:1.0;\">"
        f"{safe_label}"
        f"</div>"
        f"<div style=\"line-height:1.0;\">{score_text_html(score)}</div>"
        f"</div>"
    )


def avg_score_bar_html(label, score):
    if score >= 80:
        color = "#2E7D32"
    elif score >= 50:
        color = "#F9A825"
    else:
        color = "#C62828"

    safe_label = html_escape_text(label or "")
    return (
        f"<div style=\""
        f"background-color:{color};"
        f"padding:6px 10px;"
        f"border-radius:4px;"
        f"color:white;"
        f"font-weight:900;"
        f"font-size:19px;"
        f"margin-top:2px;"
        f"margin-bottom:{IMG_SPACE_PX}px;"
        f"display:flex;"
        f"justify-content:space-between;"
        f"align-items:center;"
        f"gap:10px;"
        f"\">"
        f"<span>{safe_label}</span>"
        f"<span style=\"color:#FFFFFF; font-weight:900; font-size:20px;\">{score}%</span>"
        f"</div>"
    )


def column_header_link_html(label, item_number, href):
    safe_label = html_escape_text(label or "")
    safe_item = html_escape_text(item_number or "")
    safe_href = html.escape(str(href or ""), quote=True)

    if safe_href and safe_item:
        item_html = (
            f"<a href=\"{safe_href}\" target=\"_blank\" "
            f"style=\"color:#3EA6FF; text-decoration:none; font-weight:900;\">"
            f"{safe_item}</a>"
        )
    else:
        item_html = f"<span style=\"color:#3EA6FF; font-weight:900;\">{safe_item or 'Missing'}</span>"

    return (
        f"<div style=\""
        f"text-align:left;"
        f"margin-top:0;"
        f"margin-bottom:2px;"
        f"font-size:28px;"
        f"font-weight:900;"
        f"color:#FFFFFF;"
        f"line-height:1.05;"
        f"\">"
        f"{safe_label}: {item_html}"
        f"</div>"
    )


def image_header_html(label):
    safe_label = html_escape_text(label or "")
    return (
        f"<div style=\""
        f"text-align:left;"
        f"margin-top:0;"
        f"margin-bottom:2px;"
        f"font-size:28px;"
        f"font-weight:900;"
        f"color:#FFFFFF;"
        f"line-height:1.05;"
        f"\">"
        f"{safe_label}"
        f"</div>"
    )


def image_compare_cell_html(url):
    if url:
        safe_url = html.escape(str(url), quote=True)
        return (
            f"<div style=\""
            f"width:100%;"
            f"margin:0;"
            f"padding:0;"
            f"display:flex;"
            f"align-items:flex-start;"
            f"justify-content:center;"
            f"overflow:hidden;"
            f"\">"
            f"<img src=\"{safe_url}\" style=\"display:block; width:100%; height:auto; object-fit:contain;\" />"
            f"</div>"
        )

    return (
        f"<div style=\""
        f"width:100%;"
        f"min-height:80px;"
        f"display:flex;"
        f"align-items:center;"
        f"justify-content:center;"
        f"margin:0;"
        f"padding:0;"
        f"color:#C62828;"
        f"font-size:16px;"
        f"font-weight:700;"
        f"\">"
        f"Missing"
        f"</div>"
    )

def image_compare_row_html(s_url, r_url, score):
    return (
        f"<div style=\""
        f"display:grid;"
        f"grid-template-columns:minmax(0,1fr) minmax(0,1fr) {IMG_SCORE_WIDTH_PX}px;"
        f"column-gap:8px;"
        f"align-items:start;"
        f"margin:0 0 {IMG_SPACE_PX}px 0;"
        f"padding:0;"
        f"\">"
        f"<div style=\"margin:0; padding:0;\">"
        f"{image_compare_cell_html(s_url)}"
        f"</div>"
        f"<div style=\"margin:0; padding:0;\">"
        f"{image_compare_cell_html(r_url)}"
        f"</div>"
        f"<div style=\""
        f"display:flex;"
        f"align-items:flex-start;"
        f"justify-content:flex-start;"
        f"text-align:left;"
        f"margin:0;"
        f"padding-top:4px;"
        f"\">"
        f"{score_text_html(score)}"
        f"</div>"
        f"</div>"
    )

def image_tile_html(label, url, box_height=170):
    safe_label = html.escape(label)

    if url:
        safe_url = html.escape(url, quote=True)
        return f'''<div style="border:1px solid #E0E0E0;border-radius:8px;background:#FFFFFF;padding:8px;">
<div style="font-size:45px;font-weight:600;margin-bottom:6px;">{safe_label}</div>
<div style="height:{box_height}px;display:flex;align-items:center;justify-content:center;background:#FAFAFA;border-radius:6px;overflow:hidden;">
<img src="{safe_url}" style="max-width:100%;max-height:{box_height}px;object-fit:contain;" />
</div>
</div>'''
    else:
        return f'''<div style="border:1px solid #E0E0E0;border-radius:8px;background:#FFFFFF;padding:8px;">
<div style="font-size:45px;font-weight:600;margin-bottom:6px;">{safe_label}</div>
<div style="height:{box_height}px;display:flex;align-items:center;justify-content:center;background:#FAFAFA;border-radius:6px;color:#C62828;font-size:14px;font-weight:600;">
❌ Missing
</div>
</div>'''


def image_slot_block_html(slot_num, s_url, r_url, score, retailer_name="CVS", box_height=170):
    if score >= 80:
        score_color = "#2E7D32"
    elif score >= 50:
        score_color = "#F9A825"
    else:
        score_color = "#C62828"

    return f'''<div style="border:1px solid #DADADA;border-radius:10px;padding:10px;margin-bottom:12px;background:#FCFCFC;">
<div style="font-weight:700;margin-bottom:10px;color:{score_color};">Image Slot {slot_num} — {score}%</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
{image_tile_html("Salsify", s_url, box_height=box_height)}
{image_tile_html(retailer_name, r_url, box_height=box_height)}
</div>
</div>'''


def build_image_panel_html(s_images, r_images, max_images, retailer_name="CVS", box_height=110):
    blocks = []

    for i in range(max_images):
        s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else ""
        r_url = r_images[i] if i < len(r_images) and isinstance(r_images[i], str) else ""
        score = compare_images_visually(s_url, r_url) if (s_url and r_url) else 0

        blocks.append(
            image_slot_block_html(
                slot_num=i + 1,
                s_url=s_url,
                r_url=r_url,
                score=score,
                retailer_name=retailer_name,
                box_height=box_height,
            )
        )

    return f'''<div style="padding-right:4px;">{"".join(blocks)}</div>'''


def read_uploaded_file_from_bytes(file_bytes, file_name):
    if not file_bytes:
        raise EmptyDataError("Uploaded file is empty.")
    if len(file_bytes.strip()) == 0:
        raise EmptyDataError("Uploaded file is empty.")

    file_name = str(file_name or "").lower().strip()

    if file_name.endswith(".xlsx"):
        xls = pd.ExcelFile(BytesIO(file_bytes), engine="openpyxl")
        frames = []

        for sheet_name in xls.sheet_names:
            sheet_df = pd.read_excel(
                BytesIO(file_bytes),
                sheet_name=sheet_name,
                engine="openpyxl",
            )

            if sheet_df is None or sheet_df.empty:
                continue

            sheet_df = sheet_df.copy()
            sheet_df["retailer"] = str(sheet_name).strip()
            frames.append(sheet_df)

        if not frames:
            raise EmptyDataError("No readable sheets found in uploaded Excel file.")

        return pd.concat(frames, ignore_index=True)

    last_error = None
    for encoding in ["utf-8-sig", "utf-8", "latin1"]:
        try:
            return pd.read_csv(BytesIO(file_bytes), encoding=encoding)
        except Exception as e:
            last_error = e

    raise last_error if last_error else EmptyDataError("Could not parse uploaded file.")

def infer_retailer_name_from_url(url):
    if pd.isna(url):
        return "Retailer"

    url = str(url or "").strip().lower()

    if not url:
        return "Retailer"

    if "cvs.com" in url:
        return "CVS"
    if "walmart.com" in url:
        return "Walmart"
    if "target.com" in url:
        return "Target"
    if "kroger.com" in url:
        return "Kroger"
    if "samsclub.com" in url or "sam's club" in url:
        return "Sam's Club"
    if "walgreens.com" in url:
        return "Walgreens"
    if "amazon.com" in url:
        return "Amazon"

    return "Retailer"


def prepare_input_df(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Rename only safe one-to-one columns first.
    df.rename(
        columns={
            "salsify url": "salsify_url",
            "retail url": "retail_url",
            "sku id": "sku",
            "product sku": "sku",
            "retailer name": "retailer",
            "retailer_name": "retailer",
        },
        inplace=True,
    )

    # Build one normalized retailer_rpc column without creating duplicate column names.
    rpc_candidates = []
    for rpc_col in ["retailer_rpc", "cvs rpc", "walgreens rpc"]:
        if rpc_col in df.columns:
            rpc_candidates.append(
                df[rpc_col]
                .replace("#N/A", "")
                .fillna("")
                .astype(str)
                .str.strip()
            )

    if rpc_candidates:
        retailer_rpc = rpc_candidates[0].copy()
        for series in rpc_candidates[1:]:
            retailer_rpc = retailer_rpc.where(retailer_rpc != "", series)
        df["retailer_rpc"] = retailer_rpc
    else:
        df["retailer_rpc"] = ""

    # Remove original retailer-specific rpc columns after combining.
    for rpc_col in ["cvs rpc", "walgreens rpc"]:
        if rpc_col in df.columns:
            df.drop(columns=[rpc_col], inplace=True)

    # Ensure required working columns exist.
    for col in ["sku", "salsify_url", "retail_url", "brand", "retailer_rpc"]:
        if col not in df.columns:
            df[col] = ""

    # Clean standard text columns safely.
    for col in ["sku", "salsify_url", "retail_url", "brand", "retailer_rpc"]:
        df[col] = (
            df[col]
            .replace("#N/A", "")
            .fillna("")
            .astype(str)
            .str.strip()
        )

    # Normalize retailer column.
    if "retailer" not in df.columns:
        df["retailer"] = df["retail_url"].apply(infer_retailer_name_from_url)
    else:
        df["retailer"] = (
            df["retailer"]
            .replace("#N/A", "")
            .fillna("")
            .astype(str)
            .str.strip()
        )

    required = ["sku", "salsify_url", "retail_url"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    return df
    
def clear_in_memory_caches():
    global html_cache, image_hash_cache, image_compare_cache, walgreens_api_cache

    if "html_cache" not in globals() or not isinstance(globals().get("html_cache"), dict):
        html_cache = {}
    if "image_hash_cache" not in globals() or not isinstance(globals().get("image_hash_cache"), dict):
        image_hash_cache = {}
    if "image_compare_cache" not in globals() or not isinstance(globals().get("image_compare_cache"), dict):
        image_compare_cache = {}

    html_cache.clear()
    image_hash_cache.clear()
    image_compare_cache.clear()
    if "walgreens_api_cache" not in globals() or not isinstance(globals().get("walgreens_api_cache"), dict):
        walgreens_api_cache = {}
    walgreens_api_cache.clear()

# =========================================
# HTML FETCH
# =========================================
def get_html(url):
    global html_cache

    if "html_cache" not in globals() or not isinstance(globals().get("html_cache"), dict):
        html_cache = {}

    if not url:
        return ""

    if url in html_cache:
        html_cache[url] = html_cache.pop(url)
        return html_cache[url]

    try:
        session = get_session()
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200 and r.text:
            html_cache[url] = r.text
            while len(html_cache) > HTML_CACHE_MAX:
                html_cache.pop(next(iter(html_cache)))
            return r.text
    except Exception:
        pass

    return ""

def fetch_html_with_timeout(url, timeout_seconds):
    if not url:
        return ""

    try:
        session = get_session()
        r = session.get(url, timeout=timeout_seconds, allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass

    return ""


def get_walgreens_html(url):
    """
    Walgreens-specific fetch path with a longer timeout and shared HTML cache.
    This avoids refetching the same PDP across batch processing + visual review.
    """
    global html_cache
    if "html_cache" not in globals() or not isinstance(globals().get("html_cache"), dict):
        html_cache = {}

    url = str(url or "").strip()
    if not url:
        return ""

    cache_key = f"walgreens::{url}"
    if cache_key in html_cache:
        html_cache[cache_key] = html_cache.pop(cache_key)
        return html_cache[cache_key]

    html_text = fetch_html_with_timeout(url, WALGREENS_REQUEST_TIMEOUT)
    if html_text:
        html_cache[cache_key] = html_text
        while len(html_cache) > HTML_CACHE_MAX:
            html_cache.pop(next(iter(html_cache)))
    return html_text

def get_walgreens_product_id_from_url(retail_url):
    """
    Supports Walgreens product URLs like:
    - /ID=300432791-product
    - /ID=prod6153586-product
    - ?productId=300432791
    - ?productId=prod6153586
    """
    if not retail_url:
        return ""

    retail_url = str(retail_url or "").strip()

    patterns = [
        r"/ID=([A-Za-z0-9]+)-product",
        r"[?&]productId=([A-Za-z0-9]+)",
        r'"productId"\s*:\s*"([A-Za-z0-9]+)"',
    ]

    for pattern in patterns:
        m = re.search(pattern, retail_url, flags=re.IGNORECASE)
        if m:
            return m.group(1)

    return ""


def get_walgreens_sku_id_from_url(retail_url):
    """
    Extracts selected skuId from Walgreens variant/querystring PDPs such as:
    .../ID=300447053-product?skuId=400632972
    .../ID=300465880-product?skuId=sku6275345
    """
    if not retail_url:
        return ""

    retail_url = str(retail_url or "").strip()

    m = re.search(r"[?&]skuId=([A-Za-z0-9_-]+)", retail_url, flags=re.IGNORECASE)
    if m:
        return m.group(1)

    return ""

def fetch_json_with_timeout(url, timeout_seconds):
    if not url:
        return None

    try:
        session = get_session()
        r = session.get(url, timeout=timeout_seconds, allow_redirects=True)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass

    return None


def get_walgreens_product_api_payload(product_id):
    """
    Walgreens source exposes a lighter product endpoint:
    /productapi/v1/products?productId={prodId}

    Cache API payloads so repeated runs and visual mode are faster.
    """
    global walgreens_api_cache
    if "walgreens_api_cache" not in globals() or not isinstance(globals().get("walgreens_api_cache"), dict):
        walgreens_api_cache = {}

    product_id = str(product_id or "").strip()
    if not product_id:
        return None

    cache_key = f"productapi::{product_id}"
    if cache_key in walgreens_api_cache:
        walgreens_api_cache[cache_key] = walgreens_api_cache.pop(cache_key)
        return walgreens_api_cache[cache_key]

    api_url = f"https://www.walgreens.com/productapi/v1/products?productId={product_id}"
    payload = fetch_json_with_timeout(api_url, WALGREENS_API_TIMEOUT)
    if payload is not None:
        walgreens_api_cache[cache_key] = payload
        while len(walgreens_api_cache) > HTML_CACHE_MAX:
            walgreens_api_cache.pop(next(iter(walgreens_api_cache)))
    return payload

def walk_json(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_json(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_json(item)


def find_first_dict_with_keys(obj, required_keys):
    required_keys = set(required_keys)

    for node in walk_json(obj):
        if isinstance(node, dict) and required_keys.issubset(set(node.keys())):
            return node

    return {}

# =========================================
# HTML / DOM DEBUG HELPERS
# =========================================
def html_to_debug_textblob(html_text):
    if not html_text:
        return ""

    raw = html.unescape(html_text or "")
    text = BeautifulSoup(raw, "html.parser").get_text("\n", strip=True)
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def html_to_prettified_dom(html_text):
    if not html_text:
        return ""

    try:
        raw = html.unescape(html_text or "")
        soup = BeautifulSoup(raw, "html.parser")
        return soup.prettify()
    except Exception:
        return html_text or ""


def preview_between_markers(text, start_marker="", end_marker=""):
    """
    Returns a preview slice between start_marker and end_marker.
    Uses case-insensitive literal matching.
    """
    source = str(text or "")

    result = {
        "preview": "",
        "start_found": False,
        "end_found": False,
        "start_index": -1,
        "end_index": -1,
    }

    if not source:
        return result

    working = source
    start_idx_global = 0

    if start_marker:
        start_match = re.search(re.escape(start_marker), source, flags=re.IGNORECASE)
        if not start_match:
            return result

        result["start_found"] = True
        result["start_index"] = start_match.start()
        start_idx_global = start_match.start()
        working = source[start_idx_global:]
    else:
        result["start_found"] = True
        result["start_index"] = 0
        working = source

    if end_marker:
        end_match = re.search(re.escape(end_marker), working, flags=re.IGNORECASE)
        if end_match:
            result["end_found"] = True
            result["end_index"] = start_idx_global + end_match.start()
            result["preview"] = working[:end_match.start()].strip()
            return result

    result["preview"] = working.strip()
    return result


def fetch_url_debug(url, retailer_name=""):
    """
    Fresh non-cached fetch debugger so we can see exactly what the app receives.
    Uses retailer-specific timeout tuning when needed.
    """
    result = {
        "requested_url": str(url or ""),
        "final_url": "",
        "status_code": None,
        "reason": "",
        "content_type": "",
        "content_length_header": "",
        "text_length": 0,
        "history": [],
        "error": "",
        "raw_html": "",
        "dom_text": "",
        "prettified_dom": "",
        "response_headers": {},
    }

    url = str(url or "").strip()
    retailer_name = str(retailer_name or "").strip().lower()

    if not url:
        result["error"] = "No URL provided."
        return result

    timeout_seconds = REQUEST_TIMEOUT
    if retailer_name == "walgreens":
        timeout_seconds = WALGREENS_DEBUG_TIMEOUT

    try:
        session = get_session()
        r = session.get(url, timeout=timeout_seconds, allow_redirects=True)

        result["final_url"] = str(r.url or "")
        result["status_code"] = int(r.status_code)
        result["reason"] = str(getattr(r, "reason", "") or "")
        result["content_type"] = str(r.headers.get("Content-Type", "") or "")
        result["content_length_header"] = str(r.headers.get("Content-Length", "") or "")
        result["history"] = [
            {
                "status_code": int(h.status_code),
                "url": str(h.url or ""),
            }
            for h in r.history
        ]

        interesting_headers = [
            "Content-Type",
            "Content-Length",
            "Server",
            "Cache-Control",
            "Set-Cookie",
            "Location",
            "X-Cache",
            "X-Served-By",
            "CF-Cache-Status",
            "CF-Ray",
        ]
        result["response_headers"] = {
            k: v for k, v in r.headers.items() if k in interesting_headers
        }

        raw_html = r.text or ""
        result["raw_html"] = raw_html
        result["text_length"] = len(raw_html)
        result["dom_text"] = html_to_debug_textblob(raw_html)
        result["prettified_dom"] = html_to_prettified_dom(raw_html)

    except Exception as e:
        result["error"] = repr(e)

    return result
    
    
@st.cache_data(show_spinner=False)
def get_debug_views_for_url(url):
    html_text = get_html(url)

    return {
        "raw_html": html_text or "",
        "dom_text": html_to_debug_textblob(html_text),
        "prettified_dom": html_to_prettified_dom(html_text),
    }

def build_debug_views_from_html(html_text):
    html_text = str(html_text or "")
    return {
        "raw_html": html_text,
        "dom_text": html_to_debug_textblob(html_text),
        "prettified_dom": html_to_prettified_dom(html_text),
    }


def get_uploaded_text_file_bytes(uploaded_text_file):
    if uploaded_text_file is None:
        return ""

    try:
        raw = uploaded_text_file.getvalue()
        if isinstance(raw, bytes):
            for encoding in ["utf-8", "utf-8-sig", "latin1"]:
                try:
                    return raw.decode(encoding)
                except Exception:
                    pass
        return str(raw)
    except Exception:
        return ""


def resolve_debug_views(
    debug_url,
    retailer_name="",
    use_manual_html_override=False,
    manual_html_text="",
    manual_html_file=None,
):
    """
    If manual override is provided, use that instead of live fetch.
    Otherwise use the live fetch debugger.
    """
    manual_text = str(manual_html_text or "").strip()
    uploaded_text = get_uploaded_text_file_bytes(manual_html_file).strip()

    if use_manual_html_override:
        chosen_html = manual_text or uploaded_text
        if chosen_html:
            views = build_debug_views_from_html(chosen_html)
            return {
                "mode": "manual_html_override",
                "requested_url": str(debug_url or ""),
                "final_url": "manual_html_override",
                "status_code": "MANUAL",
                "reason": "Manual HTML override",
                "content_type": "text/html",
                "content_length_header": str(len(chosen_html)),
                "text_length": len(chosen_html),
                "history": [],
                "error": "",
                "response_headers": {},
                **views,
            }

    # Fallback to live fetch.
    return fetch_url_debug(debug_url, retailer_name=retailer_name)
    
# =========================================
# SALSIFY PARSERS
# =========================================
def _parse_salsify_page(html_text):
    empty = {
        "text": {
            "title": "",
            "description": "",
            "feature1": "",
            "feature2": "",
            "feature3": "",
            "feature4": "",
            "feature5": "",
        },
        "images": [],
    }

    if not html_text:
        return empty

    soup = BeautifulSoup(html_text, "html.parser")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return empty

    try:
        data = json.loads(script.string)
    except Exception:
        return empty

    text_map = {}
    try:
        props = data["props"]["pageProps"]["product"]["propertySets"][0]["properties"]
        for p in props:
            key = p.get("property")
            values = p.get("values", [])
            if values:
                text_map[key] = values[0]
    except Exception:
        pass

    text = {
        "title": text_map.get("PRODUCT_TITLE", ""),
        "description": text_map.get("DESCRIPTION", ""),
        "feature1": text_map.get("FEATURE_1", ""),
        "feature2": text_map.get("FEATURE_2", ""),
        "feature3": text_map.get("FEATURE_3", ""),
        "feature4": text_map.get("FEATURE_4", ""),
        "feature5": text_map.get("FEATURE_5", ""),
    }

    asset_map = {}
    try:
        properties = data["props"]["pageProps"]["product"]["digitalAssets"]["properties"]
        for prop in properties:
            name = prop.get("property", "").lower()
            values = prop.get("values", [])
            if values:
                val = values[0].get("value", "")
                if val:
                    asset_map[name] = val.split("?")[0]
    except Exception:
        pass

    def find(keyword):
        for k, v in asset_map.items():
            if keyword in k:
                return v
        return None

    ordered = [find("online"), find("back"), find("left")]
    atf_io = find("atf io")

    if atf_io:
        ordered.append(atf_io)
        for k in ["atf 2", "atf 3", "atf 4", "atf 5", "atf 6"]:
            ordered.append(find(k))
    else:
        for k in ["atf 2", "atf 3", "atf 4", "atf 5", "atf 6"]:
            ordered.append(find(k))

    images = [{"url": x or ""} for x in ordered[:8]]

    return {
        "text": text,
        "images": images,
    }

@st.cache_data(show_spinner=False)
def get_salsify_bundle(url):
    html_text = get_html(url)
    return _parse_salsify_page(html_text)


def get_salsify_text(url):
    return get_salsify_bundle(url)["text"]


def get_salsify_images(url):
    return get_salsify_bundle(url)["images"]


# =========================================
# CVS / RETAILER PARSERS
# =========================================
def clean_cvs_text(text):
    if not text:
        return ""

    text = str(text)

    text = text.replace("\\u0026", "&amp;")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')
    text = html.unescape(text)

    wrapper_patterns = [
        r'"\]\)\s*</script>\s*<script>\s*self\.__next_f\.push\(\[1,\s*"',
        r'"\]\)\s*self\.__next_f\.push\(\[1,\s*"',
        r'</script>\s*<script>\s*self\.__next_f\.push\(\[1,\s*"',
    ]
    for pattern in wrapper_patterns:
        text = re.sub(pattern, "", text, flags=re.DOTALL)

    text = re.sub(r'^(?:T[0-9A-Za-z]+,)+', "", text)
    text = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:\{.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:\[.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:$', "", text, flags=re.DOTALL)
    text = re.sub(r'"\]\s*[0-9A-Za-z]{1,3}:T[0-9A-Za-z]+,.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:T[0-9A-Za-z]+,.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'self\.__next_f\.push\(\[1,.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?script[^>]*>', " ", text, flags=re.IGNORECASE)
    text = text.replace("\\*", "*")
    text = re.sub(r"\binconti\s+nence\b", "incontinence", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def clean_cvs_feature_text(text):
    if not text:
        return ""

    text = str(text)

    text = text.replace("\\u0026", "&amp;")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')
    text = html.unescape(text)

    text = re.sub(r'"\]\s*[0-9A-Za-z]{1,3}:T[0-9A-Za-z]+,.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'\]\)\s*</script>\s*<script>\s*self\.__next_f\.push\(\[1,\s*".*$', "", text, flags=re.DOTALL)
    text = re.sub(r'</script>\s*<script>\s*self\.__next_f\.push\(\[1,\s*".*$', "", text, flags=re.DOTALL)
    text = re.sub(r'self\.__next_f\.push\(\[1,.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:\{.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:\[.*$', "", text, flags=re.DOTALL)
    text = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:$', "", text, flags=re.DOTALL)
    text = text.replace("\\*", "*")
    text = re.sub(r"\s+", " ", text).strip()

    return text


def get_target_sku_from_inputs(retail_url="", cvs_rpc=""):
    retail_url = html.unescape(str(retail_url or ""))
    cvs_rpc = str(cvs_rpc or "").strip()

    m = re.search(r'[?&]skuId=([0-9A-Za-z_-]+)', retail_url)
    if m:
        return m.group(1).strip()

    return cvs_rpc


def clean_cvs_text_refined(text):
    return clean_cvs_text(text)


def get_nextjs_chunks(html_text):
    if not html_text:
        return ""

    source = html.unescape(html_text)
    pattern = r'self\.__next_f\.push\(\[1,\s*"((?:\\.|[^"\\])*)"\s*\]\)'
    chunks = []

    for m in re.finditer(pattern, source, re.DOTALL):
        payload = m.group(1)
        try:
            decoded = json.loads(f'"{payload}"')
        except Exception:
            decoded = payload
            decoded = decoded.replace("\\n", "\n")
            decoded = decoded.replace("\\/", "/")
            decoded = decoded.replace('\\"', '"')
        chunks.append(decoded)

    return "\n".join(chunks)


def extract_balanced_bracket_block(source, start_index):
    if start_index < 0 or start_index >= len(source) or source[start_index] != "[":
        return ""

    depth = 0
    in_str = False
    escape = False

    for i in range(start_index, len(source)):
        ch = source[i]

        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return source[start_index:i + 1]

    return ""


def extract_array_block_for_key(source, key):
    if not source:
        return ""

    source = str(source)
    key = str(key)

    patterns = [
        rf'(?:^|\n){re.escape(key)}:\[',
        rf'(?<![0-9A-Za-z]){re.escape(key)}:\[',
    ]

    for pattern in patterns:
        m = re.search(pattern, source)
        if not m:
            continue

        bracket_start = source.find("[", m.start())
        if bracket_start == -1:
            continue

        block = extract_balanced_bracket_block(source, bracket_start)
        if block:
            return block

    return ""


def extract_balanced_brace_block(source, start_index):
    if start_index < 0 or start_index >= len(source) or source[start_index] != "{":
        return ""

    depth = 0
    in_str = False
    escape = False

    for i in range(start_index, len(source)):
        ch = source[i]

        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[start_index:i + 1]

    return ""


def find_newline_anchored_key(source, key, for_array=False):
    key = str(key)

    if for_array:
        strict_pattern = rf'(?:^|\n){re.escape(key)}:\['
    else:
        strict_pattern = rf'(?:^|\n){re.escape(key)}:'

    m = re.search(strict_pattern, source)
    if m:
        return m

    if for_array:
        fallback_pattern = rf'(?:^|[\s,{{]){re.escape(key)}:\['
    else:
        fallback_pattern = rf'(?:^|[\s,{{]){re.escape(key)}:'

    return re.search(fallback_pattern, source)


def looks_like_next_newline_key(source, idx):
    if idx < 0 or idx >= len(source):
        return False

    return bool(
        re.match(
            r'(?:\s|,)([0-9A-Za-z]{1,3}):(?=[\[{"]|T[0-9A-Za-z]+,|null|true|false|\d)',
            source[idx:],
        )
    )


def extract_newline_anchored_value_block(source, key):
    m = find_newline_anchored_key(source, key, for_array=False)
    if not m:
        return ""

    start = m.end()
    i = start

    in_str = False
    escape = False
    bracket_depth = 0
    brace_depth = 0
    paren_depth = 0

    while i < len(source):
        ch = source[i]

        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue

        if ch == '"':
            in_str = True
            i += 1
            continue

        if ch == "[":
            bracket_depth += 1
            i += 1
            continue
        if ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
            i += 1
            continue

        if ch == "{":
            brace_depth += 1
            i += 1
            continue
        if ch == "}":
            brace_depth = max(0, brace_depth - 1)
            i += 1
            continue

        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue

        if bracket_depth == 0 and brace_depth == 0 and paren_depth == 0:
            if re.match(
                r'(?:\s|,|^)([0-9A-Za-z]{1,3}):(?=[\[{"]|T[0-9A-Za-z]+,|null|true|false|\d)',
                source[i:],
            ):
                break

        i += 1

    block = source[start:i].strip()
    block = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:\{.*$', "", block, flags=re.DOTALL)
    block = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:\[.*$', "", block, flags=re.DOTALL)
    block = re.sub(r'(?<=[\.\)\]"\'])\s*[0-9A-Za-z]{1,3}:$', "", block, flags=re.DOTALL)

    return block.strip()


def extract_object_block_for_key(source, key):
    m = find_newline_anchored_key(source, key, for_array=False)
    if not m:
        return ""

    start = m.end()
    while start < len(source) and source[start].isspace():
        start += 1

    if start < len(source) and source[start] == "{":
        return extract_balanced_brace_block(source, start)

    return extract_newline_anchored_value_block(source, key)


def find_direct_vendor_details_block_near_sku(source, target_rpc="", search_after=30000):
    if not source or not target_rpc:
        return ""

    source = str(source)
    rpc = re.escape(str(target_rpc))

    anchor_patterns = [
        rf'skuId={rpc}\b',
        rf'/{rpc}(?:_[0-9]+)?\.jpg',
    ]

    anchors = []
    for pattern in anchor_patterns:
        anchors.extend(list(re.finditer(pattern, source, flags=re.IGNORECASE)))

    if not anchors:
        return ""

    for anchor in anchors:
        segment_start = anchor.start()
        segment_end = min(len(source), anchor.start() + search_after)
        segment = source[segment_start:segment_end]

        direct_match = re.search(
            r'"vendorContent"\s*:\s*\{\s*"vendorDetails"\s*:\s*\{',
            segment,
            flags=re.DOTALL,
        )
        if direct_match:
            brace_start = segment_start + direct_match.end() - 1
            block = extract_balanced_brace_block(source, brace_start)
            if block:
                return block

        ref_match = re.search(
            r'"vendorContent"\s*:\s*\{\s*"vendorDetails"\s*:\s*"(\$[0-9A-Za-z]{1,3})"',
            segment,
            flags=re.DOTALL,
        )
        if ref_match:
            ref_key = ref_match.group(1).replace("$", "")
            resolved = extract_object_block_for_key(source, ref_key)
            if resolved:
                return resolved

    return ""


def extract_rpc_anchor_windows(source, target_rpc="", context_before=3500, context_after=12000):
    if not source or not target_rpc:
        return []

    rpc = re.escape(str(target_rpc))
    patterns = [
        rf'skuId={rpc}\b',
        rf'/{rpc}(?:_[0-9]+)?\.jpg',
        rf'"dynamicMediaUrl"\s*:\s*"[^"]*?/{rpc}(?:_[0-9]+)?\.jpg[^"]*"',
    ]

    hits = []
    seen = set()

    for pattern in patterns:
        for m in re.finditer(pattern, source, flags=re.IGNORECASE):
            start = max(0, m.start() - context_before)
            end = min(len(source), m.end() + context_after)
            key = (start, end)
            if key in seen:
                continue
            seen.add(key)
            hits.append({
                "start": start,
                "end": end,
                "anchor_start": m.start(),
                "anchor_end": m.end(),
                "window": source[start:end],
                "anchor_text": source[m.start():m.end()],
            })

    hits.sort(key=lambda x: x["start"])
    return hits


def merge_feature_continuations(items, max_features=5):
    merged = []

    continuation_patterns = [
        r'^packaging and product may vary\.?$',
        r'^product may vary\.?$',
        r'^packaging may vary\.?$',
    ]

    hard_stop_patterns = [
        r'^[0-9A-Za-z]{1,3}:T[0-9A-Za-z]+,',
        r'^self\.__next_f\.push',
        r'^</script>',
        r'^<script>',
        r'^vendorDetailsParagraph',
        r'^\]\)\s*</script>',
    ]

    for raw_item in items:
        item = clean_cvs_feature_text(raw_item)
        if not item:
            continue

        if any(re.search(p, item, flags=re.IGNORECASE) for p in hard_stop_patterns):
            break

        is_continuation = any(
            re.match(pattern, item, flags=re.IGNORECASE)
            for pattern in continuation_patterns
        )

        if not is_continuation:
            if (
                merged
                and len(item) <= 60
                and "—" not in item
                and not re.match(r'^[A-Z0-9][A-Z0-9\s\'"&/\-\(\)\*:]+(?:\s—|\s-|\s:)', item)
                and item[:1].islower()
            ):
                is_continuation = True

        if is_continuation and merged:
            if len(merged) >= max_features:
                continue

            prev = merged.pop()
            if prev.endswith(";"):
                merged.append(f"{prev} {item}")
            else:
                merged.append(f"{prev}; {item}")
        else:
            if len(merged) >= max_features:
                break
            merged.append(item)

    return dedupe_preserve_order(merged[:max_features])


def normalize_cvs_features(items):
    cleaned = [clean_cvs_feature_text(x) for x in items if isinstance(x, str)]
    cleaned = [x for x in cleaned if x]
    cleaned = merge_feature_continuations(cleaned, max_features=5)
    return dedupe_preserve_order(cleaned[:5])


def split_feature_blob_preserve_semicolons(text):
    text = clean_cvs_feature_text(text)
    if not text:
        return []

    if " | " in text:
        parts = [x.strip() for x in text.split(" | ")]
    elif "•" in text:
        parts = [x.strip() for x in text.split("•")]
    else:
        parts = [text.strip()]

    return [p for p in parts if p]


def parse_jsonish_array_text(array_text):
    array_text = normalize_space(array_text)
    if not array_text:
        return []

    candidates = [
        array_text,
        array_text.replace('\\"', '"'),
        html.unescape(array_text).replace('\\"', '"'),
    ]

    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, list):
                return normalize_cvs_features(value)
        except Exception:
            pass

    inner = array_text[1:-1] if array_text.startswith("[") and array_text.endswith("]") else array_text
    inner = clean_cvs_feature_text(inner)

    parts = re.split(r'"\s*,\s*"', inner)

    cleaned = []
    for part in parts:
        val = clean_cvs_feature_text(part.strip().strip('"'))
        if val:
            cleaned.extend(split_feature_blob_preserve_semicolons(val))

    return normalize_cvs_features(cleaned)


def extract_candidate_variant_windows(source, context_before=4500, context_after=22000):
    windows = []

    patterns = [
        r'"vendorContent"\s*:\s*\{\s*"vendorDetails"\s*:\s*\{',
        r'"vendorContent"\s*:\s*\{\s*"vendorDetails"\s*:\s*"(\$[0-9A-Za-z]{1,3})"',
    ]

    seen = set()

    for pattern in patterns:
        for m in re.finditer(pattern, source, flags=re.DOTALL):
            start = max(0, m.start() - context_before)
            end = min(len(source), m.start() + context_after)
            key = (start, end)
            if key in seen:
                continue
            seen.add(key)

            windows.append({
                "start": start,
                "end": end,
                "vendor_start": m.start(),
                "window": source[start:end],
            })

    return windows


def score_variant_window(window_text, vendor_start, window_start, target_rpc="", retail_url=""):
    score = 0
    reason = []
    matched_dynamic_media = ""
    matched_variant_url = ""
    matched_nearby_image = ""

    if not target_rpc:
        return {
            "score": 0,
            "reason": [],
            "matched_dynamic_media": "",
            "matched_variant_url": "",
            "matched_nearby_image": "",
        }

    rpc = re.escape(str(target_rpc))

    sku_match = re.search(rf'skuId={rpc}\b', window_text)
    if sku_match:
        score += 100
        reason.append("skuId_match")

    dm_match = re.search(
        rf'"dynamicMediaUrl"\s*:\s*"([^"]*?/({rpc})(?:_[0-9]+)?\.jpg[^"]*)"',
        window_text,
        flags=re.IGNORECASE,
    )
    if dm_match:
        score += 90
        matched_dynamic_media = dm_match.group(1)
        reason.append("dynamicMediaUrl_rpc_match")

    img_match = re.search(
        rf'"(?:imageUrl|image|src|url)"\s*:\s*"([^"]*?/({rpc})(?:_[0-9]+)?\.jpg[^"]*)"',
        window_text,
        flags=re.IGNORECASE,
    )
    if img_match:
        score += 60
        matched_nearby_image = img_match.group(1)
        reason.append("nearby_image_rpc_match")

    if retail_url:
        path_match = re.search(r'https?://www\.cvs\.com(/shop/[^?\s"]+)', retail_url)
        if path_match:
            retail_path = path_match.group(1)
            if retail_path and retail_path in window_text:
                score += 10
                matched_variant_url = retail_path
                reason.append("retail_path_match")

    local_vendor_start = vendor_start - window_start
    rpc_positions = []

    for m in re.finditer(rf'(?:skuId=|/as/|/high_res/){rpc}\b', window_text):
        rpc_positions.append(m.start())

    prior_positions = [p for p in rpc_positions if p <= local_vendor_start]

    if prior_positions:
        nearest_prior = max(prior_positions)
        distance = local_vendor_start - nearest_prior

        if distance <= 1200:
            score += 50
            reason.append("rpc_near_vendorcontent_before_1200")
        elif distance <= 2500:
            score += 35
            reason.append("rpc_near_vendorcontent_before_2500")
        elif distance <= 4500:
            score += 20
            reason.append("rpc_near_vendorcontent_before_4500")

    if '"vendorDetailsBullets"' in window_text:
        score += 5
        reason.append("has_bullets_marker")

    if '"vendorDetailsParagraph"' in window_text:
        score += 5
        reason.append("has_paragraph_marker")

    return {
        "score": score,
        "reason": reason,
        "matched_dynamic_media": matched_dynamic_media,
        "matched_variant_url": matched_variant_url,
        "matched_nearby_image": matched_nearby_image,
    }


def get_sorted_variant_windows(source, target_rpc="", retail_url=""):
    candidates = extract_candidate_variant_windows(source)

    for candidate in candidates:
        scored = score_variant_window(
            candidate["window"],
            vendor_start=candidate["vendor_start"],
            window_start=candidate["start"],
            target_rpc=target_rpc,
            retail_url=retail_url,
        )
        candidate["match_score"] = scored["score"]
        candidate["match_reason"] = scored["reason"]
        candidate["matched_dynamic_media"] = scored["matched_dynamic_media"]
        candidate["matched_variant_url"] = scored["matched_variant_url"]
        candidate["matched_nearby_image"] = scored["matched_nearby_image"]

    candidates.sort(key=lambda x: (x.get("match_score", 0), -x.get("start", 0)), reverse=True)
    return candidates


def parse_vendor_details_from_block(local_block, working_source, debug):
    if not local_block:
        return {"features": [], "description": "", "debug": debug}

    local_debug = debug.copy()
    local_debug["directVendorContentFound"] = True
    local_debug["directVendorDetailsFound"] = True
    local_debug["directVendorContentExcerpt"] = normalize_space(local_block[:2500])

    working_block = local_block
    seen_vendor_detail_refs = set()
    max_ref_hops = 6

    for _ in range(max_ref_hops):
        if (
            '"vendorDetailsBullets"' in working_block
            or '"vendorDetailsParagraph"' in working_block
        ):
            break

        vendor_details_ref_match = re.search(
            r'"vendorDetails"\s*:\s*"(\$[0-9A-Za-z]{1,3})"',
            working_block,
            flags=re.DOTALL,
        )

        if not vendor_details_ref_match:
            break

        ref_token = vendor_details_ref_match.group(1)
        ref_key = ref_token.replace("$", "")

        if ref_key in seen_vendor_detail_refs:
            local_debug["vendorDetailsRefLoopDetected"] = True
            local_debug["vendorDetailsResolvedKey"] = ref_key
            break

        seen_vendor_detail_refs.add(ref_key)

        resolved_block = extract_object_block_for_key(working_source, ref_key)
        if not resolved_block:
            local_debug["vendorDetailsResolvedKey"] = ref_key
            local_debug["vendorDetailsResolveFailed"] = True
            break

        if normalize_space(resolved_block) == normalize_space(working_block):
            local_debug["vendorDetailsResolvedKey"] = ref_key
            local_debug["vendorDetailsResolveSameBlock"] = True
            break

        local_debug["vendorDetailsRef"] = ref_token
        local_debug["vendorDetailsResolvedKey"] = ref_key
        working_block = resolved_block

    features = []
    description = ""

    bullets_ref_match = re.search(
        r'"vendorDetailsBullets"\s*:\s*"(\$[0-9A-Za-z]{1,3})"',
        working_block,
        flags=re.DOTALL,
    )

    bullets_array_text = ""
    bullets_array_marker = re.search(
        r'"vendorDetailsBullets"\s*:\s*\[',
        working_block,
        flags=re.DOTALL,
    )
    if bullets_array_marker:
        array_start = bullets_array_marker.end() - 1
        bullets_array_text = extract_balanced_bracket_block(working_block, array_start)

    if bullets_ref_match:
        ref_token = bullets_ref_match.group(1)
        ref_key = ref_token.replace("$", "")

        local_debug["vendorPatternFound"] = True
        local_debug["vendorDetailsBulletsRef"] = ref_token
        local_debug["featuresKey"] = ref_key

        array_text = extract_array_block_for_key(working_source, ref_key)
        local_debug["featuresArrayFound"] = bool(array_text)
        local_debug["featuresArrayExcerpt"] = normalize_space(array_text)[:2000] if array_text else ""

        if array_text:
            features = parse_jsonish_array_text(array_text)

    elif bullets_array_text:
        local_debug["featuresArrayFound"] = True
        local_debug["featuresArrayExcerpt"] = normalize_space(bullets_array_text)[:2000]
        features = parse_jsonish_array_text(bullets_array_text)

    paragraph_ref_match = re.search(
        r'"vendorDetailsParagraph"\s*:\s*"(\$[0-9A-Za-z]{1,3})"',
        working_block,
        flags=re.DOTALL,
    )
    paragraph_direct_match = re.search(
        r'"vendorDetailsParagraph"\s*:\s*"((?:\\.|[^"\\])*)"',
        working_block,
        flags=re.DOTALL,
    )

    if paragraph_ref_match:
        ref_token = paragraph_ref_match.group(1)
        ref_key = ref_token.replace("$", "")

        local_debug["vendorPatternFound"] = True
        local_debug["vendorDetailsParagraphRef"] = ref_token
        local_debug["descriptionKey"] = ref_key

        desc_block = extract_newline_anchored_value_block(working_source, ref_key)
        desc_block = clean_cvs_text(desc_block)

        local_debug["descriptionBlockFound"] = bool(desc_block)
        local_debug["descriptionBlockExcerpt"] = normalize_space(desc_block)[:2000]
        description = desc_block

    elif paragraph_direct_match:
        raw_para = paragraph_direct_match.group(1)

        if not re.fullmatch(r'\$[0-9A-Za-z]{1,3}', raw_para or ""):
            try:
                description = json.loads(f'"{raw_para}"')
            except Exception:
                description = raw_para

            description = clean_cvs_text(description)
            local_debug["descriptionBlockFound"] = bool(description)
            local_debug["descriptionBlockExcerpt"] = normalize_space(description)[:2000]

    cleaned_features = normalize_cvs_features(features)
    cleaned_description = clean_cvs_text(description)

    return {
        "features": cleaned_features,
        "description": cleaned_description,
        "debug": local_debug,
    }


def extract_vendor_copy_from_source(source, source_name="", target_rpc="", retail_url=""):
    debug = {
        "vendorPatternFound": False,
        "vendorDetailsBulletsRef": "",
        "vendorDetailsParagraphRef": "",
        "featuresKey": "",
        "descriptionKey": "",
        "featuresArrayFound": False,
        "descriptionBlockFound": False,
        "directVendorContentFound": False,
        "directVendorDetailsFound": False,
        "variantWindowMatched": False,
        "variantMatchScore": 0,
        "variantMatchReason": "",
        "matchedDynamicMediaUrl": "",
        "matchedVariantUrl": "",
        "matchedNearbyImage": "",
        "Source Used": source_name,
        "vendorPatternExcerpt": "",
        "featuresArrayExcerpt": "",
        "descriptionBlockExcerpt": "",
        "directVendorContentExcerpt": "",
    }

    if not source:
        return {"features": [], "description": "", "debug": debug}

    working_source = html.unescape(source)
    working_source = working_source.replace("\\u0026", "&amp;")

    direct_fastpath_result = None

    direct_variant_block_parts = find_direct_vendor_details_block_near_sku(
        working_source,
        target_rpc=target_rpc,
        search_after=30000,
    )

    if direct_variant_block_parts:
        direct_debug = debug.copy()
        direct_debug["variantWindowMatched"] = True
        direct_debug["variantMatchScore"] = 1000
        direct_debug["variantMatchReason"] = "direct_sku_vendorDetails_fastpath"
        direct_debug["directVendorContentExcerpt"] = normalize_space(direct_variant_block_parts[:2500])

        parsed = parse_vendor_details_from_block(
            direct_variant_block_parts,
            working_source,
            direct_debug,
        )

        direct_features = parsed.get("features", []) or []
        direct_description = parsed.get("description", "") or ""
        parsed_debug = parsed.get("debug", direct_debug)

        if direct_features or direct_description:
            direct_fastpath_result = {
                "features": normalize_cvs_features(direct_features[:5]),
                "description": clean_cvs_text(direct_description),
                "debug": parsed_debug,
            }

    anchor_windows = extract_rpc_anchor_windows(
        working_source,
        target_rpc=target_rpc,
        context_before=3500,
        context_after=12000,
    )

    for candidate in anchor_windows:
        candidate_debug = debug.copy()
        candidate_debug["variantWindowMatched"] = True
        candidate_debug["variantMatchScore"] = 999
        candidate_debug["variantMatchReason"] = "rpc_anchor_window"
        candidate_debug["matchedDynamicMediaUrl"] = candidate.get("anchor_text", "")
        candidate_debug["matchedNearbyImage"] = candidate.get("anchor_text", "")

        parsed = parse_vendor_details_from_block(
            candidate.get("window", ""),
            working_source,
            candidate_debug,
        )

        variant_features = parsed.get("features", []) or []
        variant_description = parsed.get("description", "") or ""

        if variant_features or variant_description:
            return {
                "features": normalize_cvs_features(variant_features[:5]),
                "description": clean_cvs_text(variant_description),
                "debug": parsed.get("debug", candidate_debug),
            }

    sorted_candidates = get_sorted_variant_windows(
        working_source,
        target_rpc=target_rpc,
        retail_url=retail_url,
    )

    for candidate in sorted_candidates:
        candidate_debug = debug.copy()
        candidate_debug["variantWindowMatched"] = candidate.get("match_score", 0) > 0
        candidate_debug["variantMatchScore"] = candidate.get("match_score", 0)
        candidate_debug["variantMatchReason"] = " | ".join(candidate.get("match_reason", []))
        candidate_debug["matchedDynamicMediaUrl"] = candidate.get("matched_dynamic_media", "")
        candidate_debug["matchedVariantUrl"] = candidate.get("matched_variant_url", "")
        candidate_debug["matchedNearbyImage"] = candidate.get("matched_nearby_image", "")

        parsed = parse_vendor_details_from_block(
            candidate.get("window", ""),
            working_source,
            candidate_debug,
        )

        variant_features = parsed.get("features", []) or []
        variant_description = parsed.get("description", "") or ""

        if variant_features or variant_description:
            return {
                "features": normalize_cvs_features(variant_features[:5]),
                "description": clean_cvs_text(variant_description),
                "debug": parsed.get("debug", candidate_debug),
            }

    if direct_fastpath_result:
        return direct_fastpath_result

    shared_features = []
    shared_description = ""

    global_bullets_ref_match = re.search(
        r'"vendorDetailsBullets"\s*:\s*"(\$[0-9A-Za-z]{1,3})"',
        working_source,
        flags=re.DOTALL,
    )
    global_paragraph_ref_match = re.search(
        r'"vendorDetailsParagraph"\s*:\s*"(\$[0-9A-Za-z]{1,3})"',
        working_source,
        flags=re.DOTALL,
    )

    global_bullets_array_text = ""
    global_bullets_array_marker = re.search(
        r'"vendorDetailsBullets"\s*:\s*\[',
        working_source,
        flags=re.DOTALL,
    )
    if global_bullets_array_marker:
        array_start = global_bullets_array_marker.end() - 1
        global_bullets_array_text = extract_balanced_bracket_block(working_source, array_start)

    global_paragraph_direct_match = re.search(
        r'"vendorDetailsParagraph"\s*:\s*"((?:\\.|[^"\\])*)"',
        working_source,
        flags=re.DOTALL,
    )

    if global_bullets_ref_match:
        ref_token = global_bullets_ref_match.group(1)
        ref_key = ref_token.replace("$", "")

        debug["vendorPatternFound"] = True
        debug["vendorDetailsBulletsRef"] = ref_token
        debug["featuresKey"] = ref_key

        array_text = extract_array_block_for_key(working_source, ref_key)
        debug["featuresArrayFound"] = bool(array_text)
        debug["featuresArrayExcerpt"] = normalize_space(array_text)[:2000] if array_text else ""

        if array_text:
            shared_features = parse_jsonish_array_text(array_text)

    elif global_bullets_array_text:
        debug["featuresArrayFound"] = True
        debug["featuresArrayExcerpt"] = normalize_space(global_bullets_array_text)[:2000]
        shared_features = parse_jsonish_array_text(global_bullets_array_text)

    if global_paragraph_ref_match:
        ref_token = global_paragraph_ref_match.group(1)
        ref_key = ref_token.replace("$", "")

        debug["vendorPatternFound"] = True
        debug["vendorDetailsParagraphRef"] = ref_token
        debug["descriptionKey"] = ref_key

        desc_block = extract_newline_anchored_value_block(working_source, ref_key)
        desc_block = clean_cvs_text(desc_block)

        debug["descriptionBlockFound"] = bool(desc_block)
        debug["descriptionBlockExcerpt"] = normalize_space(desc_block)[:2000]
        shared_description = desc_block

    elif global_paragraph_direct_match:
        raw_para = global_paragraph_direct_match.group(1)

        if not re.fullmatch(r'\$[0-9A-Za-z]{1,3}', raw_para or ""):
            try:
                shared_description = json.loads(f'"{raw_para}"')
            except Exception:
                shared_description = raw_para

            shared_description = clean_cvs_text(shared_description)
            debug["descriptionBlockFound"] = bool(shared_description)
            debug["descriptionBlockExcerpt"] = normalize_space(shared_description)[:2000]

    return {
        "features": normalize_cvs_features(shared_features[:5]),
        "description": clean_cvs_text(shared_description),
        "debug": debug,
    }


def extract_vendor_copy_from_nextjs(html_text, target_rpc="", retail_url=""):
    raw_text = get_nextjs_chunks(html_text)
    raw_html = html.unescape(html_text or "")

    debug = {
        "rawHtmlLength": len(raw_html or ""),
        "rawTextLength": len(raw_text or ""),
        "nextjsChunkFound": bool(raw_text),
        "rawHtmlHasSelfNextF": "self.__next_f.push([1," in (raw_html or ""),
        "rawHtmlHasVendorDetailsBullets": "vendorDetailsBullets" in (raw_html or ""),
        "rawHtmlHasVendorDetailsParagraph": "vendorDetailsParagraph" in (raw_html or ""),
        "rawTextHasVendorDetailsBullets": "vendorDetailsBullets" in (raw_text or ""),
        "rawTextHasVendorDetailsParagraph": "vendorDetailsParagraph" in (raw_text or ""),
        "vendorPatternFound": False,
        "vendorDetailsBulletsRef": "",
        "vendorDetailsParagraphRef": "",
        "featuresKey": "",
        "descriptionKey": "",
        "featuresArrayFound": False,
        "descriptionBlockFound": False,
        "directVendorContentFound": False,
        "directVendorDetailsFound": False,
        "variantWindowMatched": False,
        "variantMatchScore": 0,
        "variantMatchReason": "",
        "matchedDynamicMediaUrl": "",
        "matchedVariantUrl": "",
        "matchedNearbyImage": "",
        "Source Used": "",
        "vendorPatternExcerpt": "",
        "featuresArrayExcerpt": "",
        "descriptionBlockExcerpt": "",
        "directVendorContentExcerpt": "",
        "rawHtmlVendorExcerpt": "",
        "rawTextVendorExcerpt": "",
    }

    if "vendorDetailsBullets" in raw_html:
        idx = raw_html.find("vendorDetailsBullets")
        debug["rawHtmlVendorExcerpt"] = normalize_space(
            raw_html[max(0, idx - 250): idx + 1500]
        )[:2000]

    if "vendorDetailsBullets" in raw_text:
        idx = raw_text.find("vendorDetailsBullets")
        debug["rawTextVendorExcerpt"] = normalize_space(
            raw_text[max(0, idx - 250): idx + 1500]
        )[:2000]

    text_result = extract_vendor_copy_from_source(
        raw_text,
        source_name="raw_text",
        target_rpc=target_rpc,
        retail_url=retail_url,
    )

    text_features = text_result.get("features", []) or []
    text_description = text_result.get("description", "") or ""

    html_result = {"features": [], "description": "", "debug": {}}

    if not text_features or not text_description:
        html_result = extract_vendor_copy_from_source(
            raw_html,
            source_name="raw_html",
            target_rpc=target_rpc,
            retail_url=retail_url,
        )

    html_features = html_result.get("features", []) or []
    html_description = html_result.get("description", "") or ""

    final_features = text_features or html_features
    final_description = text_description or html_description

    chosen_debug = text_result.get("debug", {}) if (text_features or text_description) else {}
    fallback_debug = html_result.get("debug", {}) if (html_features or html_description) else {}

    if not text_features and html_features:
        chosen_debug = fallback_debug.copy()
        chosen_debug["variantMatchReason"] = (
            str(chosen_debug.get("variantMatchReason", "")) + " | raw_html_features_fallback"
        ).strip(" |")
    else:
        merged_debug = chosen_debug.copy()
        for k, v in fallback_debug.items():
            if not merged_debug.get(k) and v:
                merged_debug[k] = v
        chosen_debug = merged_debug

    debug.update(chosen_debug)

    return {
        "features": final_features,
        "description": final_description,
        "debug": debug,
    }


def extract_cvs_images_from_html(html_text):
    matches = re.findall(r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^\"]*', html_text or "")

    best_images = {}
    order = []

    for m in matches:
        full = "https://www.cvs.com" + m
        base = full.split("?")[0]
        name = base.split("/")[-1]
        size_match = re.search(r"Resize=\((\d+)", m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in best_images:
            order.append(name)
            best_images[name] = {"url": base, "size": size}
        elif size > best_images[name]["size"]:
            best_images[name] = {"url": base, "size": size}

    return [best_images[name]["url"] for name in order]


def _extract_cvs_text_from_html(html_text, retail_url="", target_rpc=""):
    debug = {"Title Path": "", "Description Path": "", "Features Path": ""}

    if not html_text:
        return {"title": "", "description": "", "features": [], "debug": debug}

    soup = BeautifulSoup(html_text, "html.parser")
    title = ""

    h1 = soup.find("h1")
    if h1:
        title = normalize_space(h1.get_text(" ", strip=True))
        debug["Title Path"] = "h1"
    elif soup.title:
        title = normalize_space(soup.title.get_text(" ", strip=True))
        debug["Title Path"] = "html_title"

    vendor_copy = extract_vendor_copy_from_nextjs(
        html_text,
        target_rpc=target_rpc,
        retail_url=retail_url,
    )

    description = clean_cvs_text(vendor_copy.get("description", ""))
    features = normalize_cvs_features(vendor_copy.get("features", []))

    debug.update(vendor_copy.get("debug", {}))
    debug["Description Path"] = debug.get("Source Used", "") if description else "description_empty"
    debug["Features Path"] = debug.get("Source Used", "") if features else "features_empty"

    return {
        "title": title,
        "description": description,
        "features": features[:5],
        "debug": debug,
    }


@st.cache_data(show_spinner=False)
def get_cvs_bundle(retail_url, target_rpc=""):
    html_text = get_html(retail_url)
    return {
        "text": _extract_cvs_text_from_html(
            html_text,
            retail_url=retail_url,
            target_rpc=target_rpc,
        ),
        "images": extract_cvs_images_from_html(html_text),
    }

# =========================================
# WALGREENS PARSERS
# =========================================
def _safe_json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def _decode_walgreens_json_string(raw_value):
    """
    Decodes Walgreens JSON-like fragments such as:
    \\u003cp\\u003eHello\\u003c/p\\u003e
    """
    if not raw_value:
        return ""

    raw_value = str(raw_value)

    try:
        decoded = json.loads(f'"{raw_value}"')
    except Exception:
        decoded = raw_value
        decoded = decoded.replace('\\"', '"')
        decoded = decoded.replace("\\/", "/")

    decoded = html.unescape(decoded)
    return decoded.strip()


def _normalize_walgreens_text(value):
    if not value:
        return ""

    value = str(value)
    value = html.unescape(value)

    value = value.replace("\\u003c", "<")
    value = value.replace("\\u003e", ">")
    value = value.replace("\\u0026", "&")
    value = value.replace("\\n", " ")
    value = value.replace("\\/", "/")
    value = value.replace('\\"', '"')

    value = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if "<" in value and ">" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)

    return normalize_space(value)


def get_walgreens_product_id_from_url(retail_url):
    """
    Supports Walgreens product URLs like:
    - /ID=300432791-product
    - /ID=prod6153586-product
    - ?productId=300432791
    - ?productId=prod6153586
    """
    if not retail_url:
        return ""

    retail_url = str(retail_url or "").strip()

    patterns = [
        r"/ID=([A-Za-z0-9]+)-product",
        r"[?&]productId=([A-Za-z0-9]+)",
        r'"productId"\s*:\s*"([A-Za-z0-9]+)"',
    ]

    for pattern in patterns:
        m = re.search(pattern, retail_url, flags=re.IGNORECASE)
        if m:
            return m.group(1)

    return ""


def fetch_json_with_timeout(url, timeout_seconds):
    if not url:
        return None

    try:
        session = get_session()
        r = session.get(url, timeout=timeout_seconds, allow_redirects=True)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass

    return None


def get_walgreens_product_api_payload(product_id):
    """
    Walgreens source exposes a lighter product endpoint:
    /productapi/v1/products?productId={prodId}
    """
    if not product_id:
        return None

    api_url = f"https://www.walgreens.com/productapi/v1/products?productId={product_id}"
    return fetch_json_with_timeout(api_url, WALGREENS_API_TIMEOUT)


def walk_json(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_json(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_json(item)


def find_first_dict_with_keys(obj, required_keys):
    required_keys = set(required_keys)

    for node in walk_json(obj):
        if isinstance(node, dict) and required_keys.issubset(set(node.keys())):
            return node

    return {}


def format_walgreens_title_from_parts(raw_title="", size_count="", primary_attr=""):
    raw_title = _normalize_walgreens_text(raw_title)
    size_count = _normalize_walgreens_text(size_count)
    primary_attr = _normalize_walgreens_text(primary_attr)

    title_clean = raw_title.strip()

    if primary_attr and title_clean.lower().endswith((" " + primary_attr).lower()):
        title_clean = title_clean[: -(len(primary_attr) + 1)].rstrip(" ,-/")
    else:
        trailing_colors = [
            " Grey",
            " Gray",
            " Black",
            " White",
            " Pink",
            " Blue",
            " Green",
            " Red",
            " Brown",
            " Beige",
            " Purple",
            " Yellow",
            " Orange",
        ]
        for color in trailing_colors:
            if title_clean.lower().endswith(color.lower()):
                title_clean = title_clean[: -len(color)].rstrip(" ,-/")
                break

    if size_count:
        return f"{title_clean}, {size_count}"

    return title_clean


def _find_walgreens_feature_block_start(desc_html):
    """
    Walgreens live productDesc generally switches from description to features at:
    <br/>
<UL>
<LI>

    Return the start index of the whole feature block and the end index of the
    first <LI> opening tag so callers can either trim at the block start or begin
    parsing right after the first LI marker.
    """
    if not desc_html:
        return None

    working = str(desc_html)
    patterns = [
        r"<br\s*/?>\s*<ul[^>]*>\s*<li[^>]*>",
        r"<ul[^>]*>\s*<li[^>]*>",
        r"<li[^>]*>",
    ]

    for pattern in patterns:
        m = re.search(pattern, working, flags=re.IGNORECASE | re.DOTALL)
        if m:
            li_open = re.search(r"<li[^>]*>", m.group(0), flags=re.IGNORECASE)
            li_end = m.start() + li_open.end() if li_open else m.end()
            return m.start(), li_end

    return None
def _truncate_walgreens_copy_at_hard_stop(text):
    """
    Hard-stop Walgreens copy before utility / legal / disposal sections.
    This keeps only the real description + bullets and drops anything after,
    such as Made in USA, Do not flush, or Walgreens disclaimer text.
    """
    if not text:
        return ""

    working = str(text)
    lower = working.lower()
    stop_markers = [
        "Made in USA",
        "Made In USA",
        "Do not flush",
        "Do Not Flush",
        "Directions for Use:",
        "Direction for Use:",
        "To Use:",
        "To Dispose:",
        "How to Use:",
        "How To Use:",
        "Walgreens does not represent or warrant",
        "We recommend that you not rely solely on the information presented",
        "On occasion, manufacturers may improve or change their product formulas",
        "The food and drug administration has not intended to diagnose, treat, cure, or prevent any disease",
    ]

    cut_index = len(working)
    for marker in stop_markers:
        idx = lower.find(marker.lower())
        if idx != -1:
            cut_index = min(cut_index, idx)

    return working[:cut_index].strip()
    
def _truncate_walgreens_copy_at_hard_stop(text):
    """
    Hard-stop Walgreens copy before utility / legal / disposal sections.
    This keeps only the real description + bullets and drops anything after,
    such as Made in USA, Do not flush, or Walgreens disclaimer text.
    """
    if not text:
        return ""

    working = str(text)
    lower = working.lower()

    stop_markers = [
        "Made in USA",
        "Made In USA",
        "Do not flush",
        "Do Not Flush",
        "Directions for Use:",
        "Direction for Use:",
        "To Use:",
        "To Dispose:",
        "How to Use:",
        "How To Use:",
        "Walgreens does not represent or warrant",
        "We recommend that you not rely solely on the information presented",
        "On occasion, manufacturers may improve or change their product formulas",
        "The food and drug administration has not intended to diagnose, treat, cure, or prevent any disease",
    ]

    cut_index = len(working)
    for marker in stop_markers:
        idx = lower.find(marker.lower())
        if idx != -1:
            cut_index = min(cut_index, idx)

    return working[:cut_index].strip()
    
def _is_walgreens_450_image(url):
    url = str(url or '').lower().split('?', 1)[0]
    return url.endswith('/450.jpg') or url.endswith('_450.jpg')


def _extract_walgreens_feature_items_from_raw_product_desc(desc_html):
    """
    Walgreens live features start at a block like:
    <UL><LI>...
    Separation points are repeated <LI> markers.
    The last bullet may be the only one with a closing </LI>, so split on raw <LI>
    markers and stop each chunk at the next <LI>, a closing </UL>, or the end of the string.
    IMPORTANT: include the very first feature immediately after the first <LI>.
    """
    if not desc_html:
        return []

    working = str(desc_html)
    working = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        working,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # NEW: hard-stop before utility / legal / disposal text.
    working = _truncate_walgreens_copy_at_hard_stop(working)

    start_info = _find_walgreens_feature_block_start(working)
    if not start_info:
        return []

    feature_block_start, _first_li_end = start_info
    feature_html = working[feature_block_start:]

    li_markers = list(re.finditer(r"<li[^>]*>", feature_html, flags=re.IGNORECASE))
    if not li_markers:
        return []

    features = []
    for i, marker in enumerate(li_markers):
        start = marker.end()
        end_candidates = []

        if i + 1 < len(li_markers):
            end_candidates.append(li_markers[i + 1].start())

        ul_close = re.search(r"</ul>", feature_html[start:], flags=re.IGNORECASE)
        if ul_close:
            end_candidates.append(start + ul_close.start())

        li_close = re.search(r"</li>", feature_html[start:], flags=re.IGNORECASE)
        if li_close:
            end_candidates.append(start + li_close.start())

        end = min(end_candidates) if end_candidates else len(feature_html)
        chunk = feature_html[start:end]
        chunk = re.sub(r"<br[^>]*>", " ", chunk, flags=re.IGNORECASE)
        chunk = re.sub(r"</p>", " ", chunk, flags=re.IGNORECASE)

        feature_text = BeautifulSoup(chunk, "html.parser").get_text(" ", strip=True)
        feature_text = _normalize_walgreens_text(feature_text)
        feature_text = strip_walgreens_utility_tail(feature_text)

        if not feature_text:
            continue
        if is_walgreens_utility_feature(feature_text):
            continue

        features.append(feature_text)

    return dedupe_preserve_order(features)[:5]

def extract_walgreens_copy_from_product_desc_html(product_desc_html):
    """
    Walgreens live description starts inside productDesc paragraph HTML and ends right before the feature block that looks like:
    <UL><LI>...
    Improvement:
    - join all useful text nodes before the UL instead of trusting only the first <br> tag,
      because some Walgreens rows place a short label paragraph first.
    - hard-stop before utility / legal / disposal blocks such as Made in USA, Do not flush,
      or Walgreens disclaimer text.
    """
    if not product_desc_html:
        return "", []

    working = str(product_desc_html)
    working = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        working,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # NEW: hard-stop before utility / legal / disposal text.
    working = _truncate_walgreens_copy_at_hard_stop(working)

    start_info = _find_walgreens_feature_block_start(working)
    desc_html = working[:start_info[0]] if start_info else working
    desc_html = re.sub(r"(?:\s*)+$", " ", desc_html, flags=re.IGNORECASE)

    soup = BeautifulSoup(desc_html, "html.parser")
    desc_parts = []

    candidates = list(soup.body.children) if soup.body else list(soup.children)
    for node in candidates:
        node_name = getattr(node, "name", None)
        if node_name in {"ul", "ol", "script", "style"}:
            continue

        if hasattr(node, "get_text"):
            node_text = node.get_text(" ", strip=True)
        else:
            node_text = str(node).strip()

        node_text = _normalize_walgreens_text(node_text)
        node_text = strip_walgreens_utility_tail(node_text)

        if not node_text:
            continue
        if node_text.lower() in {"description", "details", "overview", "features"}:
            continue

        desc_parts.append(node_text)

    description_text = normalize_space(" ".join(desc_parts))
    description_text = re.sub(r"\)\)$", ")", description_text).strip()

    feature_items = _extract_walgreens_feature_items_from_raw_product_desc(working)
    return description_text, feature_items

def extract_walgreens_copy_from_product_sections(section_list):
    description = ""
    features = []

    if not isinstance(section_list, list):
        return description, features

    for section in section_list:
        if not isinstance(section, dict):
            continue

        desc_obj = section.get("description", {})
        if isinstance(desc_obj, dict) and desc_obj.get("productDesc"):
            desc_html = _decode_walgreens_json_string(desc_obj.get("productDesc", ""))
            description, features = extract_walgreens_copy_from_product_desc_html(desc_html)
            if description or features:
                break

    return description, features

def _walgreens_text_richness_tuple(value):
    value = clean_walgreens_text(value)
    useful = 0
    if len(value) >= 120:
        useful += 2
    elif len(value) >= 60:
        useful += 1
    if re.search(r"\b(count|ct|pack|roll|pads?|wipes?|diapers?|underwear|absorb|protection|odor|soft|dry|hours?)\b", value, flags=re.IGNORECASE):
        useful += 1
    return (useful, len(value), value)


def _walgreens_choose_richer_description(primary_value, secondary_value):
    primary_value = clean_walgreens_text(primary_value)
    secondary_value = clean_walgreens_text(secondary_value)
    if _walgreens_text_richness_tuple(secondary_value) > _walgreens_text_richness_tuple(primary_value):
        return secondary_value
    return primary_value


def _collect_jsonld_description_candidates(soup):
    candidates = []
    for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
        raw = (script.string or script.get_text(' ', strip=True) or '').strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        while nodes:
            node = nodes.pop(0)
            if isinstance(node, dict):
                desc = node.get('description', '')
                if isinstance(desc, str) and desc.strip():
                    candidates.append(desc)
                for value in node.values():
                    if isinstance(value, (dict, list)):
                        nodes.append(value)
            elif isinstance(node, list):
                nodes.extend(node)
    return candidates


def _collect_heading_following_copy(soup):
    desc_candidates = []
    feature_lists = []
    heading_pattern = re.compile(r'(description|details|overview|about this product|about the product|product details|why we love|benefits|features)', re.IGNORECASE)
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'strong', 'b', 'span', 'div', 'p']):
        heading_text = normalize_space(tag.get_text(' ', strip=True))
        if not heading_text or len(heading_text) > 80:
            continue
        if not heading_pattern.search(heading_text):
            continue
        collected_desc = []
        collected_features = []
        sibling = tag.next_sibling
        sibling_steps = 0
        while sibling is not None and sibling_steps < 10:
            sibling_steps += 1
            sibling_name = getattr(sibling, 'name', None)
            if sibling_name in {'h1', 'h2', 'h3', 'h4'}:
                break
            sibling_text = ''
            if hasattr(sibling, 'get_text'):
                sibling_text = normalize_space(sibling.get_text(' ', strip=True))
            else:
                sibling_text = normalize_space(str(sibling))
            sibling_text = clean_walgreens_text(sibling_text)
            if sibling_name in {'ul', 'ol'}:
                li_values = [clean_walgreens_text(li.get_text(' ', strip=True)) for li in sibling.find_all('li')]
                li_values = [v for v in li_values if v and not is_walgreens_utility_feature(v)]
                if li_values:
                    collected_features.extend(li_values)
            elif sibling_name == 'li':
                if sibling_text and not is_walgreens_utility_feature(sibling_text):
                    collected_features.append(sibling_text)
            else:
                if sibling_text and sibling_text.lower() not in {'description', 'details', 'overview', 'features'}:
                    collected_desc.append(sibling_text)
            sibling = getattr(sibling, 'next_sibling', None)
        if collected_desc:
            desc_candidates.append(' '.join(collected_desc))
        if collected_features:
            feature_lists.append(collected_features)
    return desc_candidates, feature_lists


def extract_walgreens_copy_from_meta_and_jsonld(html_text):
    if not html_text:
        return '', []
    soup = BeautifulSoup(html_text, 'html.parser')

    desc_candidates = []
    meta_selectors = [
        {'name': 'description'},
        {'property': 'og:description'},
        {'name': 'twitter:description'},
        {'itemprop': 'description'},
    ]
    for attrs in meta_selectors:
        meta = soup.find('meta', attrs=attrs)
        if meta and meta.get('content'):
            desc_candidates.append(meta.get('content', ''))

    desc_candidates.extend(_collect_jsonld_description_candidates(soup))
    heading_desc_candidates, heading_feature_lists = _collect_heading_following_copy(soup)
    desc_candidates.extend(heading_desc_candidates)

    best_description = ''
    for candidate in desc_candidates:
        best_description = _walgreens_choose_richer_description(best_description, candidate)

    feature_lists = []
    all_li = []
    for li in soup.find_all('li'):
        li_text = clean_walgreens_text(li.get_text(' ', strip=True))
        if li_text and not is_walgreens_utility_feature(li_text):
            all_li.append(li_text)
    if all_li:
        feature_lists.append(all_li)
    feature_lists.extend(heading_feature_lists)

    best_features = []
    for feature_values in feature_lists:
        cleaned = normalize_walgreens_features_final(feature_values, max_features=5)
        if _walgreens_feature_richness_tuple(cleaned) > _walgreens_feature_richness_tuple(best_features):
            best_features = cleaned

    return best_description, best_features

def _extract_walgreens_slot_num_from_key(key):
    key = str(key or "")
    m = re.search(r"(\d+)$", key)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def _choose_best_walgreens_slot_image_from_dict(item):
    """
    Pull only Walgreens images that contain 450 in the live URL.
    Prefer /450.jpg and then /n_450.jpg style URLs in the exact site order.
    """
    if not isinstance(item, dict):
        return ""

    # Prefer explicit 450 image keys first.
    for k, v in item.items():
        if 'largeimageurl' in str(k).lower():
            url = _absolutize_walgreens_image_url(v)
            if url and _is_walgreens_450_image(url):
                return url

    # Do not fall back to 900 / 100. User only wants 450 images.
    return ""

def extract_walgreens_images_from_product_info(product_info):
    """
    Pull all live Walgreens images in Walgreens site order, but only keep image URLs
    that contain 450 in the path (e.g. /450.jpg, /2_450.jpg, /3_450.jpg).

    Do NOT append 900 / 100 / meta fallbacks.
    """
    if not isinstance(product_info, dict):
        return []

    ordered_urls = []
    seen = set()

    filmstrip = product_info.get("filmStripUrl", [])
    if isinstance(filmstrip, list):
        for item in filmstrip:
            chosen_url = _choose_best_walgreens_slot_image_from_dict(item)
            if chosen_url and chosen_url not in seen:
                ordered_urls.append(chosen_url)
                seen.add(chosen_url)

    return ordered_urls[:MAX_IMAGE_SLOTS_TO_COMPARE]

def _absolutize_walgreens_image_url(url):
    if not url:
        return ""

    url = html.unescape(str(url).strip())

    if url.startswith("//"):
        url = "https:" + url

    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        return ""

    lowered = url.lower()
    if not any(ext in lowered for ext in [".jpg", ".jpeg", ".png", ".webp", ".avif"]):
        return ""

    bad_tokens = [
        "sprite",
        "icon",
        "logo",
        "placeholder",
        "spacer",
        "data:image",
        ".svg",
    ]
    if any(tok in lowered for tok in bad_tokens):
        return ""

    return url


def _walgreens_image_family_key(url):
    """
    Normalize Walgreens image URLs so the same asset family collapses to one key,
    regardless of 100 / 220 / 450 / 900 size or image slot suffix.
    """
    url = _absolutize_walgreens_image_url(url)
    if not url:
        return ""

    lowered = url.lower().split("?", 1)[0]
    m = re.search(r"/prodimg/([^/]+)/([^/]+)$", lowered)
    if not m:
        return lowered

    product_folder = m.group(1)
    filename = m.group(2)
    filename = re.sub(r"\.(jpg|jpeg|png|webp|avif)$", "", filename, flags=re.IGNORECASE)
    filename = re.sub(r"_(100|220|450|900)$", "", filename, flags=re.IGNORECASE)
    filename = re.sub(r"^(100|220|450|900)$", "main", filename, flags=re.IGNORECASE)

    return f"{product_folder}/{filename}"


def _walgreens_image_size_rank(url):
    """
    Prefer 450px for visual QA, then 900px, then 220px, then 100px.
    """
    url = str(url or "").lower()

    if "/450." in url or "_450." in url:
        return 1
    if "/900." in url or "_900." in url:
        return 2
    if "/220." in url or "_220." in url:
        return 3
    if "/100." in url or "_100." in url:
        return 4

    return 99


def _add_walgreens_image_candidate(candidates, url, source_priority=99):
    """
    Keep only one best URL per image family.
    Lower source_priority wins.
    Lower image size rank wins.
    """
    url = _absolutize_walgreens_image_url(url)
    if not url:
        return

    family_key = _walgreens_image_family_key(url)
    if not family_key:
        return

    candidate = {
        "url": url,
        "source_priority": source_priority,
        "size_rank": _walgreens_image_size_rank(url),
    }

    current = candidates.get(family_key)
    if current is None:
        candidates[family_key] = candidate
        return

    current_tuple = (current["source_priority"], current["size_rank"], current["url"])
    new_tuple = (candidate["source_priority"], candidate["size_rank"], candidate["url"])

    if new_tuple < current_tuple:
        candidates[family_key] = candidate


def build_walgreens_bundle_from_api_payload(payload):
    empty = {
        "text": {
            "title": "",
            "description": "",
            "features": [],
            "debug": {
                "Title Path": "walgreens_api_missing",
                "Description Path": "walgreens_api_missing",
                "Features Path": "walgreens_api_missing",
                "Source Used": "walgreens_api",
            },
        },
        "images": [],
    }

    if not payload:
        return empty

    root = payload
    if isinstance(payload, dict) and "productData" in payload and isinstance(payload["productData"], dict):
        root = payload["productData"]

    product_info = {}
    prod_details = {}

    if isinstance(root, dict):
        product_info = root.get("productInfo", {}) if isinstance(root.get("productInfo", {}), dict) else {}
        prod_details = root.get("prodDetails", {}) if isinstance(root.get("prodDetails", {}), dict) else {}

    if not product_info:
        product_info = find_first_dict_with_keys(root, {"title", "sizeCount"})
    if not prod_details:
        prod_details = find_first_dict_with_keys(root, {"section"})

    raw_title = product_info.get("title", "")
    size_count = product_info.get("sizeCount", "")
    primary_attr = product_info.get("primaryAttribute", "")

    final_title = format_walgreens_title_from_parts(
        raw_title=raw_title,
        size_count=size_count,
        primary_attr=primary_attr,
    )

    section_list = prod_details.get("section", []) if isinstance(prod_details, dict) else []
    description, features = extract_walgreens_copy_from_product_sections(section_list)
    images = extract_walgreens_images_from_product_info(product_info)

    return {
        "text": {
            "title": final_title,
            "description": description,
            "features": features[:5],
            "debug": {
                "Title Path": "walgreens_api_productInfo_title_plus_sizeCount",
                "Description Path": "walgreens_api_prodDetails_section_description_productDesc_preUL" if description else "walgreens_api_description_missing",
                "Features Path": "walgreens_api_prodDetails_section_description_productDesc_rawLI" if features else "walgreens_api_features_missing",
                "Source Used": "walgreens_api",
            },
        },
        "images": images,
    }


def get_walgreens_prod_desc_url(product_id):
    if not product_id:
        return ""
    return f"https://www.walgreens.com/store/store/prodDesc.jsp?id={product_id}"


def get_walgreens_prod_desc_html(product_id):
    """
    Lightweight Walgreens copy endpoint.
    Often more reliable than the full PDP HTML for prod... items.
    """
    url = get_walgreens_prod_desc_url(product_id)
    return fetch_html_with_timeout(url, WALGREENS_REQUEST_TIMEOUT)


def build_walgreens_title_from_url_slug(retail_url, description_html=""):
    """
    Fallback title builder for prodDesc.jsp route.
    """
    retail_url = str(retail_url or "").strip()

    slug_match = re.search(
        r"/store/c/([^/]+)/ID=[A-Za-z0-9]+-product",
        retail_url,
        flags=re.IGNORECASE,
    )

    if not slug_match:
        return ""

    slug = slug_match.group(1)
    title = slug.replace("-", " ")
    title = title.replace(" ,", ",")
    title = html.unescape(title)
    title = normalize_space(title)

    title = " ".join(word.capitalize() if word.islower() else word for word in title.split())

    desc_text = BeautifulSoup(str(description_html or ""), "html.parser").get_text(" ", strip=True)
    desc_text = normalize_space(desc_text)

    count_match = re.search(r"(\d+)\s+count", desc_text, flags=re.IGNORECASE)
    if count_match:
        count_val = count_match.group(1)
        title = f"{title}, {count_val}.0 ea"

    return normalize_space(title)


def build_walgreens_bundle_from_prod_desc_fragment(product_id, retail_url=""):
    """
    Fallback path for prod... Walgreens items.
    Uses the lightweight prodDesc.jsp endpoint for description/features.
    Also runs the same meta/jsonld fallback extractor against the fragment HTML.
    """
    empty = {
        "text": {
            "title": "",
            "description": "",
            "features": [],
            "debug": {
                "Title Path": "walgreens_prodDesc_missing",
                "Description Path": "walgreens_prodDesc_missing",
                "Features Path": "walgreens_prodDesc_missing",
                "Source Used": "walgreens_prodDesc_fragment",
            },
        },
        "images": [],
    }

    if not product_id:
        return empty

    fragment_html = get_walgreens_prod_desc_html(product_id)
    if not fragment_html:
        return empty

    description, features = extract_walgreens_copy_from_product_desc_html(fragment_html)
    fallback_description, fallback_features = extract_walgreens_copy_from_meta_and_jsonld(fragment_html)
    description = _walgreens_choose_richer_description(description, fallback_description)
    features = normalize_walgreens_features_final(features, max_features=5)
    fallback_features = normalize_walgreens_features_final(fallback_features, max_features=5)
    if _walgreens_feature_richness_tuple(fallback_features) > _walgreens_feature_richness_tuple(features):
        features = fallback_features

    title = build_walgreens_title_from_url_slug(
        retail_url=retail_url,
        description_html=fragment_html,
    )

    return {
        "text": {
            "title": title,
            "description": description,
            "features": features[:5],
            "debug": {
                "Title Path": "walgreens_prodDesc_url_slug_fallback" if title else "walgreens_prodDesc_title_missing",
                "Description Path": "walgreens_prodDesc_fragment" if description else "walgreens_prodDesc_description_missing",
                "Features Path": "walgreens_prodDesc_fragment" if features else "walgreens_prodDesc_features_missing",
                "Source Used": "walgreens_prodDesc_fragment",
            },
        },
        "images": [],
    }

def _extract_walgreens_title_from_source(html_text):
    if not html_text:
        return "", "walgreens_title_missing"

    title_match = re.search(
        r'"productInfo"\s*:\s*\{.*?"title"\s*:\s*"((?:\\.|[^"\\])*)"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    size_match = re.search(
        r'"sizeCount"\s*:\s*"((?:\\.|[^"\\])*)"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    primary_attr_match = re.search(
        r'"primaryAttribute"\s*:\s*"((?:\\.|[^"\\])*)"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not title_match:
        return "", "walgreens_productInfo_title_missing"

    raw_title = _decode_walgreens_json_string(title_match.group(1))
    size_count = _decode_walgreens_json_string(size_match.group(1)) if size_match else ""
    primary_attr = _decode_walgreens_json_string(primary_attr_match.group(1)) if primary_attr_match else ""

    final_title = format_walgreens_title_from_parts(
        raw_title=raw_title,
        size_count=size_count,
        primary_attr=primary_attr,
    )

    return final_title, "walgreens_productInfo_title_plus_sizeCount"


def _extract_walgreens_product_desc_block(html_text):
    if not html_text:
        return "", "walgreens_productDesc_missing"

    desc_match = re.search(
        r'"productDesc"\s*:\s*"((?:\\.|[^"\\])*)"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not desc_match:
        return "", "walgreens_productDesc_missing"

    raw_desc = desc_match.group(1)
    desc_html = _decode_walgreens_json_string(raw_desc)
    return desc_html, "walgreens_productDesc_found"


def _extract_walgreens_description_and_features_from_product_desc(html_text):
    desc_html, source_path = _extract_walgreens_product_desc_block(html_text)

    if not desc_html:
        return "", [], source_path

    description_text, feature_items = extract_walgreens_copy_from_product_desc_html(desc_html)

    return (
        description_text,
        feature_items,
        "walgreens_productDesc_preUL_description_and_rawLI_features",
    )


def extract_walgreens_text_from_html(html_text, retail_url="", target_rpc=""):
    debug = {
        "Title Path": "",
        "Description Path": "",
        "Features Path": "",
        "Source Used": "walgreens_html",
    }

    if not html_text:
        return {
            "title": "",
            "description": "",
            "features": [],
            "debug": debug,
        }

    title, title_path = _extract_walgreens_title_from_source(html_text)
    description, features, copy_path = _extract_walgreens_description_and_features_from_product_desc(html_text)
    fallback_description, fallback_features = extract_walgreens_copy_from_meta_and_jsonld(html_text)

    chosen_description = _walgreens_choose_richer_description(description, fallback_description)
    chosen_features = normalize_walgreens_features_final(features, max_features=5)
    fallback_features = normalize_walgreens_features_final(fallback_features, max_features=5)
    if _walgreens_feature_richness_tuple(fallback_features) > _walgreens_feature_richness_tuple(chosen_features):
        chosen_features = fallback_features

    debug["Title Path"] = title_path
    if chosen_description == description and description:
        debug["Description Path"] = copy_path
    elif chosen_description:
        debug["Description Path"] = "walgreens_meta_jsonld_fallback"
        debug["Source Used"] = "walgreens_html | walgreens_meta_jsonld_fallback"
    else:
        debug["Description Path"] = "walgreens_description_missing"

    if chosen_features == normalize_walgreens_features_final(features, max_features=5) and chosen_features:
        debug["Features Path"] = copy_path
    elif chosen_features:
        debug["Features Path"] = "walgreens_meta_jsonld_fallback"
        if "walgreens_meta_jsonld_fallback" not in str(debug.get("Source Used", "")):
            debug["Source Used"] = "walgreens_html | walgreens_meta_jsonld_fallback"
    else:
        debug["Features Path"] = "walgreens_features_missing"

    return {
        "title": title,
        "description": chosen_description,
        "features": chosen_features[:5],
        "debug": debug,
    }

def extract_walgreens_images_from_html(html_text):
    if not html_text:
        return []

    slot_candidates = {}
    seen = set()

    def maybe_store(slot_num, url):
        url = _absolutize_walgreens_image_url(url)
        if not url or not _is_walgreens_450_image(url):
            return
        slot_candidates[slot_num] = url

    # Preserve Walgreens slot order from numbered 450 image keys only.
    for m in re.finditer(
        r'"largeImageUrl(\d+)"\s*:\s*"((?:\\.|[^"\\])*)"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        maybe_store(int(m.group(1)), _decode_walgreens_json_string(m.group(2)))

    ordered_urls = []
    for slot_num in sorted(slot_candidates.keys()):
        url = slot_candidates[slot_num]
        if url and url not in seen:
            ordered_urls.append(url)
            seen.add(url)

    return ordered_urls[:MAX_IMAGE_SLOTS_TO_COMPARE]

def _walgreens_has_copy_or_images(bundle):
    if not isinstance(bundle, dict):
        return False

    bundle_text = bundle.get("text", {}) or {}
    bundle_images = bundle.get("images", []) or []

    return bool(
        bundle_text.get("title")
        or bundle_text.get("description")
        or bundle_text.get("features")
        or bundle_images
    )


def _walgreens_clean_feature_list(values, max_features=5):
    if not values:
        return []

    if isinstance(values, str):
        values = [values]

    cleaned = []
    for value in values:
        value = clean_walgreens_text(value)
        if not value:
            continue
        if is_walgreens_utility_feature(value):
            continue
        cleaned.append(value)

    return dedupe_preserve_order(cleaned)[:max_features]


def _walgreens_feature_richness_tuple(values):
    cleaned = _walgreens_clean_feature_list(values, max_features=5)
    return (len(cleaned), sum(len(x) for x in cleaned))


def _prefer_richer_walgreens_text_value(primary_value, secondary_value):
    primary_value = clean_walgreens_text(primary_value)
    secondary_value = clean_walgreens_text(secondary_value)

    primary_tuple = (len(primary_value), primary_value)
    secondary_tuple = (len(secondary_value), secondary_value)

    return secondary_value if secondary_tuple > primary_tuple else primary_value


def merge_walgreens_bundles_prefer_richer_copy(*bundles):
    """
    Merge multiple Walgreens bundle candidates and keep the richest live copy.

    Why this exists:
    - Walgreens HTML often reflects what is actually live on the PDP.
    - Walgreens API / prodDesc fragment can still be useful fallbacks.
    - Some rows were under-pulling copy because only one source path was trusted.

    Rules:
    - Prefer the longest useful title.
    - Prefer the longest useful description.
    - Prefer the feature set with the most non-empty bullets; tie-break by total text length.
    - Prefer the first non-empty image set in the order passed in.
    - Carry forward merged debug/source notes.
    """
    merged = {
        "text": {
            "title": "",
            "description": "",
            "features": [],
            "debug": {},
        },
        "images": [],
    }

    source_used_parts = []

    for bundle in bundles:
        if not isinstance(bundle, dict):
            continue

        bundle_text = bundle.get("text", {}) or {}
        bundle_images = bundle.get("images", []) or []
        bundle_debug = bundle_text.get("debug", {}) or {}

        merged["text"]["title"] = _prefer_richer_walgreens_text_value(
            merged["text"].get("title", ""),
            bundle_text.get("title", ""),
        )
        merged["text"]["description"] = _prefer_richer_walgreens_text_value(
            merged["text"].get("description", ""),
            bundle_text.get("description", ""),
        )

        current_features = merged["text"].get("features", []) or []
        new_features = bundle_text.get("features", []) or []
        if _walgreens_feature_richness_tuple(new_features) > _walgreens_feature_richness_tuple(current_features):
            merged["text"]["features"] = _walgreens_clean_feature_list(new_features, max_features=5)

        if not merged.get("images") and bundle_images:
            merged["images"] = [x for x in bundle_images if x]

        for k, v in bundle_debug.items():
            if v and not merged["text"]["debug"].get(k):
                merged["text"]["debug"][k] = v

        source_used = str(bundle_debug.get("Source Used", "") or "").strip()
        if source_used and source_used not in source_used_parts:
            source_used_parts.append(source_used)

    if source_used_parts:
        merged["text"]["debug"]["Source Used"] = " | ".join(source_used_parts)

    merged["text"]["features"] = _walgreens_clean_feature_list(
        merged["text"].get("features", []),
        max_features=5,
    )

    return merged


def _walgreens_description_richness_tuple(value):
    value = clean_walgreens_text(value)
    return (len(value), value)


def _walgreens_bundle_is_rich_enough(bundle):
    if not isinstance(bundle, dict):
        return False

    bundle_text = bundle.get("text", {}) or {}
    title = clean_walgreens_title(bundle_text.get("title", ""))
    description = bundle_text.get("description", "")
    features = bundle_text.get("features", []) or []

    return bool(
        title
        and _walgreens_description_is_rich_enough(description)
        and _walgreens_features_are_rich_enough(features)
    )

def _walgreens_description_is_rich_enough(value):
    value = clean_walgreens_text(value)
    return len(value) >= 140


def _walgreens_features_are_rich_enough(values):
    cleaned = _walgreens_clean_feature_list(values, max_features=5)
    feature_count = len(cleaned)
    feature_chars = sum(len(x) for x in cleaned)
    return bool(
        feature_count >= 4
        or (feature_count >= 3 and feature_chars >= 220)
    )


@st.cache_data(show_spinner=False)
@st.cache_data(show_spinner=False)
def get_walgreens_bundle(retail_url, target_rpc="", sku=""):
    """
    Walgreens strategy:
    1) Build live HTML bundle first.
    2) If HTML already looks rich enough on BOTH description and features, return it.
    3) If HTML is weak, merge in API + prodDesc fallback for any valid productId.
    4) If the merged result is still weak, prefer the richer field-level combination.
    """
    retail_url = str(retail_url or "").strip()
    retail_url_lc = retail_url.lower()
    product_id = get_walgreens_product_id_from_url(retail_url)
    selected_sku_id = get_walgreens_sku_id_from_url(retail_url)

    def build_html_bundle():
        html_text = get_walgreens_html(retail_url)
        return {
            "text": extract_walgreens_text_from_html(
                html_text,
                retail_url=retail_url,
                target_rpc=target_rpc,
            ),
            "images": extract_walgreens_images_from_html(html_text),
        }

    if "/search/results.jsp" in retail_url_lc:
        html_bundle = build_html_bundle()
        if _walgreens_has_copy_or_images(html_bundle):
            return html_bundle
        return {
            "text": {
                "title": "",
                "description": "",
                "features": [],
                "debug": {
                    "Title Path": "walgreens_search_results_url_not_pdp",
                    "Description Path": "walgreens_search_results_url_not_pdp",
                    "Features Path": "walgreens_search_results_url_not_pdp",
                    "Source Used": "walgreens_search_results_url_not_pdp",
                },
            },
            "images": [],
        }

    html_bundle = build_html_bundle()
    if _walgreens_bundle_is_rich_enough(html_bundle):
        return html_bundle

    candidate_bundles = [html_bundle]
    if product_id:
        api_payload = get_walgreens_product_api_payload(product_id)
        api_bundle = build_walgreens_bundle_from_api_payload(api_payload)
        if _walgreens_has_copy_or_images(api_bundle):
            candidate_bundles.append(api_bundle)
        fragment_bundle = build_walgreens_bundle_from_prod_desc_fragment(product_id, retail_url=retail_url)
        if _walgreens_has_copy_or_images(fragment_bundle):
            candidate_bundles.append(fragment_bundle)

    merged_bundle = merge_walgreens_bundles_prefer_richer_copy(*candidate_bundles)
    if _walgreens_bundle_is_rich_enough(merged_bundle):
        return merged_bundle

    # If still weak, force the richest field-by-field merge one more time.
    if len(candidate_bundles) > 1:
        html_text = html_bundle.get("text", {}) or {}
        merged_text = merged_bundle.get("text", {}) or {}
        merged_text["description"] = _walgreens_choose_richer_description(
            html_text.get("description", ""),
            merged_text.get("description", ""),
        )
        html_features = normalize_walgreens_features_final(html_text.get("features", []), max_features=5)
        merged_features = normalize_walgreens_features_final(merged_text.get("features", []), max_features=5)
        if _walgreens_feature_richness_tuple(html_features) > _walgreens_feature_richness_tuple(merged_features):
            merged_text["features"] = html_features
        else:
            merged_text["features"] = merged_features
        merged_bundle["text"] = merged_text

    if _walgreens_has_copy_or_images(merged_bundle):
        return merged_bundle

    if selected_sku_id and len(candidate_bundles) > 1:
        return candidate_bundles[1]
    for bundle in candidate_bundles:
        if _walgreens_has_copy_or_images(bundle):
            return bundle
    return html_bundle

def get_retailer_bundle(retailer_name, retail_url, target_rpc="", sku=""):
    retailer = str(retailer_name or "").strip().lower()

    if retailer == "walgreens":
        return get_walgreens_bundle(retail_url, target_rpc, sku=sku)

    # Default path stays CVS.
    return get_cvs_bundle(retail_url, target_rpc)

# =========================================
# RETAILER-SPECIFIC FINAL COPY CLEANUP
# =========================================
def strip_walgreens_utility_tail(text):
    """
    Removes non-marketing utility/footer copy that should not live in the final description/features.
    """
    text = str(text or "")

    stop_markers = [
        "Directions for Use:",
        "Direction for Use:",
        "To Use:",
        "To Dispose:",
        "How to Use:",
        "How To Use:",
        "Directions:",
        "Do not flush.",
        "Do Not Flush.",
        "Made in USA",
        "Made In USA",
        "©",
        "Walgreens does not represent or warrant",
        "We recommend that you not rely solely on the information presented",
        "On occasion, manufacturers may improve or change their product formulas",
        "the food and drug administration has not intended to diagnose, treat, cure, or prevent any disease",
    ]

    cut_index = len(text)
    lowered = text.lower()
    for marker in stop_markers:
        idx = lowered.find(marker.lower())
        if idx != -1:
            cut_index = min(cut_index, idx)

    text = text[:cut_index].strip()
    return normalize_space(text)

def is_walgreens_utility_feature(text):
    text = normalize_space(text)

    if not text:
        return True

    bad_starts = [
        "Directions for Use",
        "Direction for Use",
        "To Use",
        "To Dispose",
        "How to Use",
        "How To Use",
        "Directions",
        "Made in USA",
        "Made In USA",
        "Do not flush",
        "Do Not Flush",
        "©",
    ]

    bad_contains = [
        "Walgreens does not represent or warrant",
        "consult your doctor",
        "keep this plastic bag away",
        "do not use this bag",
    ]

    for prefix in bad_starts:
        if text.lower().startswith(prefix.lower()):
            return True

    for token in bad_contains:
        if token.lower() in text.lower():
            return True

    return False


def split_walgreens_feature_fallback_text(text):
    """
    If Walgreens gives weak/malformed LI tags, fall back to splitting on strong
    uppercase lead-ins used across Depend, Huggies, Poise, Kotex, Pull-Ups, etc.
    """
    text = normalize_space(text)
    if not text:
        return []

    if " | " in text:
        parts = [x.strip() for x in text.split(" | ")]
    elif "•" in text:
        parts = [x.strip() for x in text.split("•")]
    else:
        heading_pattern = (
            r"(?=(?:"
            r"WHAT'S INCLUDED|ALL DAY PROTECTION|UNDERWEAR-LIKE COMFORT|UP TO ZERO ODOR|UNCOMPROMISED COMFORT|"
            r"UNBEATABLE PROTECTION|ODOR CONTROL|DRYNESS|ACTIVE FIT|INSTANT ABSORPTION|FRESHSENSE|GUSHPROTECT ZONE|"
            r"GRAVITY CORE|NIGHTDEFENSE|LEAKSHIELD|DESIGNED FOR MEN|SECURE FIT|FOR LARGE BLADDER LEAKS|"
            r"DEPEND SHIELDS|DEPEND FRESH PROTECTION|OUTSTANDING ABSORBENCY|FRONT AND BACK BLOWOUT BLOCKER|"
            r"UP TO 100% LEAKPROOF|LUXURY SOFTNESS|FASTABSORB SYSTEM|99% WATER|DERMATOLOGIST TESTED|"
            r"NATIONAL ECZEMA ASSOCIATION SEAL OF ACCEPTANCE|THICK AND ABSORBENT|COMPACT COMFORT, POWERFUL PROTECTION|"
            r"#1 COMPACT TAMPON BRAND|GYNECOLOGIST-TESTED|THIN AND SOFT|ALL-NIGHT DRYNESS|NEW! 60% WIDER BACK|"
            r"CLEAN SHIELD|DOUBLE GRIP STRIPS|GENTLEABSORB|QUICKSORB PROTECTION|HYPOALLERGENIC)"
            r"[\s:\-])"
        )
        parts = re.split(heading_pattern, text, flags=re.IGNORECASE)

    cleaned = []
    for part in parts:
        part = strip_walgreens_utility_tail(part)
        part = normalize_space(part)
        if not part:
            continue
        if is_walgreens_utility_feature(part):
            continue
        cleaned.append(part)
    return dedupe_preserve_order(cleaned)

def clean_walgreens_text(text):
    if not text:
        return ""

    text = str(text)
    text = html.unescape(text)
    text = text.replace("\u003c", "<")
    text = text.replace("\u003e", ">")
    text = text.replace("\u0026", "&")
    text = text.replace("\n", " ")
    text = text.replace("\/", "/")
    text = text.replace('\"', '"')

    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)

    text = normalize_space(text)
    text = strip_walgreens_utility_tail(text)

    stop_patterns = [
        r"\bMade in USA\b.*$",
        r"\bMade In USA\b.*$",
        r"\bDirections for Use:.*$",
        r"\bDirection for Use:.*$",
        r"\bTo Use:.*$",
        r"\bTo Dispose:.*$",
        r"\bHow to Use:.*$",
        r"\bDo not flush\b.*$",
        r"\bDo Not Flush\b.*$",
        r"\bWalgreens does not represent or warrant.*$",
    ]

    for pattern in stop_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL).strip()

    return normalize_space(text)

def normalize_walgreens_features_final(items, max_features=5):
    cleaned = []

    if not items:
        return []

    if isinstance(items, str):
        items = [items]

    for item in items:
        if not item:
            continue

        text = clean_walgreens_text(item)
        text = strip_walgreens_utility_tail(text)

        if not text:
            continue
        if is_walgreens_utility_feature(text):
            continue

        cleaned.append(text)

    cleaned = dedupe_preserve_order(cleaned)

    expanded = []
    for item in cleaned:
        parts = split_walgreens_feature_fallback_text(item)
        if parts:
            expanded.extend(parts)
        else:
            expanded.append(item)

    expanded = dedupe_preserve_order([normalize_space(x) for x in expanded if normalize_space(x)])

    out = []
    for item in expanded:
        if not item:
            continue
        if is_walgreens_utility_feature(item):
            continue
        out.append(item)

    return dedupe_preserve_order(out)[:max_features]

def clean_walgreens_title(text):
    if not text:
        return ""

    text = clean_walgreens_text(text)
    text = normalize_space(text)

    # The title builder should already have constructed:
    # "Depend Adult Incontinence Underwear for Men Extra-Large, 15.0 ea"
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ", ", text)
    text = normalize_space(text)

    return text


def finalize_retailer_copy(retailer_name, r_text):
    retailer = str(retailer_name or "").strip().lower()
    out = dict(r_text or {})

    if retailer == "walgreens":
        out["title"] = clean_walgreens_title(out.get("title", ""))
        out["description"] = clean_walgreens_text(out.get("description", ""))
        out["features"] = normalize_walgreens_features_final(out.get("features", []), max_features=5)
        return out

    out["title"] = normalize_space(out.get("title", ""))
    out["description"] = clean_cvs_text(out.get("description", ""))
    out["features"] = normalize_cvs_features(out.get("features", []))
    return out

# =========================================
# QUALITY HELPERS
# =========================================
def debug_description(desc):
    if not desc:
        return {"length": 0, "quality_score": 0, "issues": ["Missing description"]}

    desc_clean = normalize_text(desc)
    length = len(desc_clean)

    absorbency_keywords = ["absorb", "leak", "fluid", "protection", "flushable", "soft", "care"]
    size_keywords = ["count", "ct", "pack", "roll", "sheets", "wipes", "mega", "tissues", "cube", "box"]
    benefit_keywords = ["soft", "comfort", "odor", "dry", "safe", "clean", "trusted", "aloe", "lotion"]

    has_absorbency = any(k in desc_clean for k in absorbency_keywords)
    has_size = any(k in desc_clean for k in size_keywords)
    has_benefits = any(k in desc_clean for k in benefit_keywords)

    is_truncated = not desc.strip().endswith((".", "!", "?")) or length < 80

    words = desc_clean.split()
    unique_ratio = len(set(words)) / len(words) if words else 0

    issues = []
    if length < 80:
        issues.append("Too short")
    if not has_absorbency:
        issues.append("Missing absorbency info")
    if not has_size:
        issues.append("Missing size/count")
    if not has_benefits:
        issues.append("Missing benefits")
    if is_truncated:
        issues.append("Possible truncation")
    if unique_ratio < 0.5:
        issues.append("Repetitive content")

    quality_score = 100
    if length < 80:
        quality_score -= 20
    if not has_absorbency:
        quality_score -= 15
    if not has_size:
        quality_score -= 15
    if not has_benefits:
        quality_score -= 15
    if is_truncated:
        quality_score -= 20
    if unique_ratio < 0.5:
        quality_score -= 15

    quality_score = max(0, quality_score)

    return {
        "length": length,
        "quality_score": quality_score,
        "issues": issues,
    }


# =========================================
# IMAGE HASHING (FAST IMAGE COMPARE)
# =========================================
def get_image_dhash(url):
    global image_hash_cache

    if "image_hash_cache" not in globals() or not isinstance(globals().get("image_hash_cache"), dict):
        image_hash_cache = {}

    if not url:
        return None

    if url in image_hash_cache:
        return image_hash_cache[url]

    try:
        session = get_session()

        # Stream the response so we can stop early if the file is too large.
        r = session.get(url, timeout=IMAGE_TIMEOUT, stream=True)
        if r.status_code != 200:
            return None

        content_type = str(r.headers.get("Content-Type", "") or "")
        if "image" not in content_type.lower():
            return None

        content_length = str(r.headers.get("Content-Length", "") or "").strip()
        if content_length.isdigit() and int(content_length) > MAX_IMAGE_BYTES:
            return None

        image_bytes = bytearray()

        for chunk in r.iter_content(chunk_size=65536):
            if not chunk:
                continue

            image_bytes.extend(chunk)

            if len(image_bytes) > MAX_IMAGE_BYTES:
                return None

        if not image_bytes:
            return None

        bio = BytesIO(image_bytes)

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)

            img = Image.open(bio)

            width, height = img.size
            if width * height > MAX_SAFE_IMAGE_PIXELS:
                return None

            img.load()

        img = img.convert("L")
        img.thumbnail((256, 256))
        img = img.resize((IMAGE_HASH_WIDTH, IMAGE_HASH_HEIGHT))

        bits = []
        for y in range(IMAGE_HASH_HEIGHT):
            for x in range(IMAGE_HASH_WIDTH - 1):
                left_pixel = img.getpixel((x, y))
                right_pixel = img.getpixel((x + 1, y))
                bits.append(1 if left_pixel > right_pixel else 0)

        h = 0
        for bit in bits:
            h = (h << 1) | bit

        image_hash_cache[url] = h
        while len(image_hash_cache) > IMAGE_HASH_CACHE_MAX:
            image_hash_cache.pop(next(iter(image_hash_cache)))

        return h

    except (Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError):
        return None
    except Exception:
        return None

def hamming_distance(a, b):
    return bin(a ^ b).count("1")


def compare_images_visually(s_url, r_url):
    global image_compare_cache

    if "image_compare_cache" not in globals() or not isinstance(globals().get("image_compare_cache"), dict):
        image_compare_cache = {}

    if not s_url or not r_url:
        return 0

    cache_key = (str(s_url), str(r_url))
    if cache_key in image_compare_cache:
        return image_compare_cache[cache_key]

    s_hash = get_image_dhash(s_url)
    r_hash = get_image_dhash(r_url)

    if s_hash is None or r_hash is None:
        score = 0
    else:
        dist = hamming_distance(s_hash, r_hash)

        if dist <= 2:
            score = 100
        elif dist <= 6:
            score = 90
        elif dist <= 10:
            score = 75
        elif dist <= 16:
            score = 60
        elif dist <= 22:
            score = 45
        else:
            score = 25

    image_compare_cache[cache_key] = score
    while len(image_compare_cache) > IMAGE_COMPARE_CACHE_MAX:
        image_compare_cache.pop(next(iter(image_compare_cache)))

    return score


@st.cache_data(show_spinner=False)
def get_visual_row_payload(salsify_url, retailer_name, retail_url, current_target_sku="", sku=""):
    s_bundle = get_salsify_bundle(salsify_url)
    r_bundle = get_retailer_bundle(
        retailer_name,
        retail_url,
        current_target_sku,
        sku=sku,
    )
    return {
        "s_text": s_bundle["text"],
        "s_images": s_bundle["images"],
        "r_text": finalize_retailer_copy(retailer_name, r_bundle["text"] or {}),
        "r_images": r_bundle["images"],
    }

# =========================================
# PROCESS ROW
# =========================================
def process_row(row):
    try:
        retail_url = row.get("retail_url", "")
        salsify_url = row.get("salsify_url", "")
        cvs_rpc = row.get("retailer_rpc", "")
        retailer_name = row.get("retailer", "") or infer_retailer_name_from_url(retail_url)

        salsify_url = str(salsify_url or "").strip()
        retail_url = str(retail_url or "").strip()

        status_notes = []

        if not salsify_url:
            status_notes.append("Missing Salsify URL")
        if not retail_url:
            status_notes.append("Missing Retail URL")

        if status_notes:
            note = " | ".join(status_notes)

            return {
                "summary": {
                    "SKU": row.get("sku", ""),
                    "Retailer": retailer_name,
                    "CVS RPC": cvs_rpc,
                    "Brand": row.get("brand", ""),
                    "Salsify URL": salsify_url,
                    "Retail URL": retail_url,
                    "Title %": 0,
                    "Description %": 0,
                    "Feature %": 0,
                    "Image Match %": 0,
                    "Overall %": 0,
                    "Status": note,
                },
                "detail": {
                    "SKU": row.get("sku", ""),
                    "Retailer": retailer_name,
                    "CVS RPC": cvs_rpc,
                    "Brand": row.get("brand", ""),
                    "Salsify URL": salsify_url,
                    "Retail URL": retail_url,
                    "Title %": 0,
                    "Description %": 0,
                    "Feature %": 0,
                    "Image Match %": 0,
                    "Overall %": 0,
                    "Status": note,
                    "Salsify Title": "",
                    "CVS Title": "",
                    "Salsify Description": "",
                    "CVS Description": "",
                    "Salsify Feature 1": "",
                    "Salsify Feature 2": "",
                    "Salsify Feature 3": "",
                    "Salsify Feature 4": "",
                    "Salsify Feature 5": "",
                    "CVS Features": "",
                    "Salsify Images": "",
                    "CVS Images": "",
                },
                "debug": {
                    "SKU": row.get("sku", ""),
                    "Retailer": retailer_name,
                    "CVS RPC": cvs_rpc,
                    "Brand": row.get("brand", ""),
                    "Retail URL": retail_url,
                    "Salsify URL": salsify_url,
                    "Status": note,
                },
            }

        target_sku = get_target_sku_from_inputs(
            retail_url=retail_url,
            cvs_rpc=cvs_rpc,
        )


        # IMPORTANT:
        # Do NOT create a nested thread pool here.
        # The outer batch executor already parallelizes rows.
        s_bundle = get_salsify_bundle(salsify_url)
        r_bundle = get_retailer_bundle(
            retailer_name,
            retail_url,
            target_sku,
            sku=row.get("sku", ""),
        )

        s_text = s_bundle["text"]
        s_images = s_bundle["images"]

        r_text = r_bundle["text"] or {}
        r_images = r_bundle["images"]

        r_text = finalize_retailer_copy(retailer_name, r_text)
        debug_data = r_text.get("debug", {})

        title_score = keyword_score(s_text.get("title", ""), r_text.get("title", ""))

        s_desc_debug = debug_description(s_text.get("description", ""))
        r_desc_debug = debug_description(r_text.get("description", ""))

        desc_score = description_similarity_score(
            s_text.get("description", ""),
            r_text.get("description", ""),
        )

        retailer_features = r_text.get("features", []) if isinstance(r_text, dict) else []
        feature_fields = ["feature1", "feature2", "feature3", "feature4", "feature5"]

        feature_scores = []
        feature_score_fields = {}

        for i, f_key in enumerate(feature_fields, start=1):
            s_val = s_text.get(f_key, "")
            r_val = retailer_features[i - 1] if i - 1 < len(retailer_features) else ""

            score = keyword_score(s_val, r_val) if r_val else 0
            feature_scores.append(score)
            feature_score_fields[f"Feature {i} %"] = score

        avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0

        img_scores = []
        image_position_scores = {}
        max_img_positions = min(max(len(s_images), len(r_images)), MAX_IMAGE_SLOTS_TO_SCORE)

        for i in range(max_img_positions):
            s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else None
            r_url = r_images[i] if i < len(r_images) else None

            sc = 0
            if s_url and r_url:
                sc = compare_images_visually(s_url, r_url)
                if sc > 0:
                    img_scores.append(sc)

            image_position_scores[f"Image {i + 1} %"] = sc

        avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0
        overall = int((title_score + desc_score + avg_feature_score + avg_img_score) / 4)

        return {
            "summary": {
                "SKU": row.get("sku", ""),
                "Retailer": retailer_name,
                "CVS RPC": cvs_rpc,
                "Brand": row.get("brand", ""),
                "Salsify URL": salsify_url,
                "Retail URL": retail_url,
                "Title %": title_score,
                "Description %": desc_score,
                "Feature %": avg_feature_score,
                "Image Match %": avg_img_score,
                "Overall %": overall,
                "Status": "",
                **feature_score_fields,
                **image_position_scores,
            },
            "detail": {
                "SKU": row.get("sku", ""),
                "Retailer": retailer_name,
                "CVS RPC": cvs_rpc,
                "Brand": row.get("brand", ""),
                "Salsify URL": salsify_url,
                "Retail URL": retail_url,
                "Title %": title_score,
                "Description %": desc_score,
                "Feature %": avg_feature_score,
                "Image Match %": avg_img_score,
                "Overall %": overall,
                "Status": "",
                "Salsify Title": s_text.get("title", ""),
                "CVS Title": r_text.get("title", ""),
                "Salsify Description": s_text.get("description", ""),
                "CVS Description": r_text.get("description", ""),
                "Salsify Feature 1": s_text.get("feature1", ""),
                "Salsify Feature 2": s_text.get("feature2", ""),
                "Salsify Feature 3": s_text.get("feature3", ""),
                "Salsify Feature 4": s_text.get("feature4", ""),
                "Salsify Feature 5": s_text.get("feature5", ""),
                "CVS Features": " | ".join(r_text.get("features", [])),
                "Salsify Images": " | ".join([img.get("url", "") for img in s_images if isinstance(img, dict)]),
                "CVS Images": " | ".join(r_images),
                "Title Path": debug_data.get("Title Path", ""),
                "Description Path": debug_data.get("Description Path", ""),
                "Features Path": debug_data.get("Features Path", ""),
                "vendorDetailsBulletsRef": debug_data.get("vendorDetailsBulletsRef", ""),
                "vendorDetailsParagraphRef": debug_data.get("vendorDetailsParagraphRef", ""),
                "featuresKey": debug_data.get("featuresKey", ""),
                "descriptionKey": debug_data.get("descriptionKey", ""),
                "directVendorContentFound": debug_data.get("directVendorContentFound", False),
                "directVendorDetailsFound": debug_data.get("directVendorDetailsFound", False),
                "variantWindowMatched": debug_data.get("variantWindowMatched", False),
                "variantMatchScore": debug_data.get("variantMatchScore", 0),
                "variantMatchReason": debug_data.get("variantMatchReason", ""),
                "matchedDynamicMediaUrl": debug_data.get("matchedDynamicMediaUrl", ""),
                "matchedVariantUrl": debug_data.get("matchedVariantUrl", ""),
                "matchedNearbyImage": debug_data.get("matchedNearbyImage", ""),
            },
            "debug": {
                "SKU": row.get("sku", ""),
                "Retailer": retailer_name,
                "CVS RPC": cvs_rpc,
                "Brand": row.get("brand", ""),
                "Retail URL": retail_url,
                "Salsify URL": salsify_url,
                "Desc Final": r_text.get("description", ""),
                "Desc Quality Score": r_desc_debug["quality_score"],
                "Desc Length": r_desc_debug["length"],
                "Desc Issues": ", ".join(r_desc_debug["issues"]),
                "Salsify Desc Quality Score": s_desc_debug["quality_score"],
                "Final Features": " | ".join(r_text.get("features", [])),
                "Title Path": debug_data.get("Title Path", ""),
                "Description Path": debug_data.get("Description Path", ""),
                "Features Path": debug_data.get("Features Path", ""),
                "vendorPatternFound": debug_data.get("vendorPatternFound", False),
                "vendorDetailsBulletsRef": debug_data.get("vendorDetailsBulletsRef", ""),
                "vendorDetailsParagraphRef": debug_data.get("vendorDetailsParagraphRef", ""),
                "featuresKey": debug_data.get("featuresKey", ""),
                "descriptionKey": debug_data.get("descriptionKey", ""),
                "featuresArrayFound": debug_data.get("featuresArrayFound", False),
                "descriptionBlockFound": debug_data.get("descriptionBlockFound", False),
                "directVendorContentFound": debug_data.get("directVendorContentFound", False),
                "directVendorDetailsFound": debug_data.get("directVendorDetailsFound", False),
                "variantWindowMatched": debug_data.get("variantWindowMatched", False),
                "variantMatchScore": debug_data.get("variantMatchScore", 0),
                "variantMatchReason": debug_data.get("variantMatchReason", ""),
                "matchedDynamicMediaUrl": debug_data.get("matchedDynamicMediaUrl", ""),
                "matchedVariantUrl": debug_data.get("matchedVariantUrl", ""),
                "matchedNearbyImage": debug_data.get("matchedNearbyImage", ""),
                "Source Used": debug_data.get("Source Used", ""),
                "vendorPatternExcerpt": debug_data.get("vendorPatternExcerpt", ""),
                "featuresArrayExcerpt": debug_data.get("featuresArrayExcerpt", ""),
                "descriptionBlockExcerpt": debug_data.get("descriptionBlockExcerpt", ""),
                "directVendorContentExcerpt": debug_data.get("directVendorContentExcerpt", ""),
                "rawHtmlLength": debug_data.get("rawHtmlLength", 0),
                "rawTextLength": debug_data.get("rawTextLength", 0),
                "nextjsChunkFound": debug_data.get("nextjsChunkFound", False),
                "rawHtmlHasSelfNextF": debug_data.get("rawHtmlHasSelfNextF", False),
                "rawHtmlHasVendorDetailsBullets": debug_data.get("rawHtmlHasVendorDetailsBullets", False),
                "rawHtmlHasVendorDetailsParagraph": debug_data.get("rawHtmlHasVendorDetailsParagraph", False),
                "rawTextHasVendorDetailsBullets": debug_data.get("rawTextHasVendorDetailsBullets", False),
                "rawTextHasVendorDetailsParagraph": debug_data.get("rawTextHasVendorDetailsParagraph", False),
                "rawHtmlVendorExcerpt": debug_data.get("rawHtmlVendorExcerpt", ""),
                "rawTextVendorExcerpt": debug_data.get("rawTextVendorExcerpt", ""),
            },
        }

    except Exception:
        return None
# =========================================
# SESSION STATE
# =========================================
if "start_idx" not in st.session_state:
    st.session_state.start_idx = 0
if "summary_rows" not in st.session_state:
    st.session_state.summary_rows = []
if "export_rows" not in st.session_state:
    st.session_state.export_rows = []
if "debug_rows" not in st.session_state:
    st.session_state.debug_rows = []
if "summary_skus" not in st.session_state:
    st.session_state.summary_skus = set()
if "detail_skus" not in st.session_state:
    st.session_state.detail_skus = set()
if "debug_skus" not in st.session_state:
    st.session_state.debug_skus = set()
if "processing_done" not in st.session_state:
    st.session_state.processing_done = False
if "progress_bar" not in st.session_state:
    st.session_state.progress_bar = None
if "last_file_hash" not in st.session_state:
    st.session_state.last_file_hash = None
if "uploaded_file_bytes" not in st.session_state:
    st.session_state.uploaded_file_bytes = None
if "selected_retailer" not in st.session_state:
    st.session_state.selected_retailer = "-- Select Retailer --"
if "selected_brand_visual" not in st.session_state:
    st.session_state.selected_brand_visual = "All"
if "active_batch_key" not in st.session_state:
    st.session_state.active_batch_key = ""
if "completed_batch_key" not in st.session_state:
    st.session_state.completed_batch_key = ""
if "auto_download_done" not in st.session_state:
    st.session_state.auto_download_done = False
if "report_bytes" not in st.session_state:
    st.session_state.report_bytes = None
if "report_filename" not in st.session_state:
    st.session_state.report_filename = None
if "report_batch_key" not in st.session_state:
    st.session_state.report_batch_key = ""

# =========================================
# TOP UPLOAD + DOWNLOAD UI
# =========================================
top_upload_col, top_download_col = st.columns([2.4, 1.1], gap="small")

with top_upload_col:
    uploaded_file = st.file_uploader("Upload Master File", type=["xlsx", "csv"])

with top_download_col:
    st.markdown("<div style='height: 30px;'></div>", unsafe_allow_html=True)
    if st.session_state.report_bytes is not None and st.session_state.report_filename:
        st.download_button(
            label="📥 Download Excel Report",
            data=st.session_state.report_bytes,
            file_name=st.session_state.report_filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_excel_report_top_inline",
        )

master_df = None
retailer_df = None
all_retailers = []
multi_retailer = False
selected_retailer = ""
current_batch_key = ""
file_hash = ""
file_ready_for_batch = False

if uploaded_file:
    try:
        file_bytes = uploaded_file.getvalue()
        st.session_state.uploaded_file_bytes = file_bytes
        file_hash = hashlib.md5(file_bytes).hexdigest()

        if st.session_state.last_file_hash != file_hash:
            st.session_state.summary_rows = []
            st.session_state.export_rows = []
            st.session_state.debug_rows = []
            st.session_state.summary_skus = set()
            st.session_state.detail_skus = set()
            st.session_state.debug_skus = set()
            st.session_state.start_idx = 0
            st.session_state.processing_done = False
            st.session_state.progress_bar = None
            st.session_state.last_file_hash = file_hash
            st.session_state.selected_retailer = "-- Select Retailer --"
            st.session_state.selected_brand_visual = "All"
            st.session_state.active_batch_key = ""
            st.session_state.completed_batch_key = ""
            st.session_state.auto_download_done = False
            st.session_state.report_bytes = None
            st.session_state.report_filename = None
            st.session_state.report_batch_key = ""
            clear_in_memory_caches()
            st.cache_data.clear()

        master_df = read_uploaded_file_from_bytes(file_bytes, uploaded_file.name)
        master_df = prepare_input_df(master_df)
        all_retailers = sorted(master_df["retailer"].dropna().astype(str).unique().tolist()) if "retailer" in master_df.columns else ["CVS"]
        if not all_retailers:
            all_retailers = ["CVS"]
        multi_retailer = len(all_retailers) > 1

        with top_upload_col:
            if multi_retailer:
                retailer_options = ["-- Select Retailer --"] + all_retailers
                if st.session_state.selected_retailer not in retailer_options:
                    st.session_state.selected_retailer = "-- Select Retailer --"
                selected_retailer = st.selectbox(
                    "🏪 Select Retailer",
                    retailer_options,
                    key="selected_retailer",
                    help="Select retailer to run batch.",
                )
                if selected_retailer == "-- Select Retailer --":
                    st.info("Select retailer to run batch.")
                else:
                    file_ready_for_batch = True
            else:
                st.session_state.selected_retailer = all_retailers[0]
                selected_retailer = st.selectbox(
                    "🏪 Select Retailer",
                    all_retailers,
                    index=0,
                    key="selected_retailer_single",
                    disabled=True,
                )
                file_ready_for_batch = True

        if file_ready_for_batch:
            retailer_df = master_df[master_df["retailer"].astype(str) == selected_retailer].copy()
            current_batch_key = f"{file_hash}::{selected_retailer}"

            if st.session_state.active_batch_key != current_batch_key:
                st.session_state.summary_rows = []
                st.session_state.export_rows = []
                st.session_state.debug_rows = []
                st.session_state.summary_skus = set()
                st.session_state.detail_skus = set()
                st.session_state.debug_skus = set()
                st.session_state.start_idx = 0
                st.session_state.processing_done = False
                st.session_state.progress_bar = None
                st.session_state.active_batch_key = current_batch_key
                st.session_state.completed_batch_key = ""
                st.session_state.selected_brand_visual = "All"
                st.session_state.auto_download_done = False
                st.session_state.report_batch_key = ""

    except EmptyDataError:
        st.error("🔥 CRITICAL APP ERROR")
        st.text("The uploaded file is empty or could not be read.")
    except ValueError as e:
        st.error("❌ INPUT FILE ERROR")
        st.text(str(e))
    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())

# =========================================
# VIEW + FILTER CONTROLS
# =========================================
st.markdown("## 🔎 QA Viewer Controls")
show_only_issues = st.checkbox("❌ Show ONLY Issues", key="show_issues")
hide_good = st.checkbox("✅ Hide Strong Matches (80%+)", key="hide_good")

st.markdown("### 🧪 Debug Controls")
show_html_debugger = st.checkbox(
    "Show Raw HTML / DOM Debugger in Full Visual QA",
    key="show_html_debugger",
)
debugger_source = st.selectbox(
    "Debugger Source",
    ["Retailer page", "Salsify page"],
    key="debugger_source",
)
debug_only_sku = st.text_input(
    "Debug only this SKU. Leave blank to show debugger for all visible rows.",
    key="debug_only_sku",
).strip()
use_manual_html_override = st.checkbox(
    "Use manual HTML override for debugger",
    key="use_manual_html_override",
)
manual_html_file = None
manual_html_text = ""
if use_manual_html_override:
    manual_html_file = st.file_uploader(
        "Upload HTML file for debugger only",
        type=["html", "txt"],
        key="manual_html_file",
    )
    manual_html_text = st.text_area(
        "Or paste raw HTML / copied DOM here for debugger only",
        height=180,
        key="manual_html_text",
    )

if retailer_df is not None and st.session_state.processing_done and st.session_state.completed_batch_key == current_batch_key:
    visual_brands = sorted(retailer_df["brand"].dropna().astype(str).unique().tolist()) if "brand" in retailer_df.columns else []
    visual_brand_options = ["All"] + visual_brands
    if st.session_state.selected_brand_visual not in visual_brand_options:
        st.session_state.selected_brand_visual = "All"
    st.markdown("### 🏷️ Select Brand")
    st.selectbox(
        "",
        visual_brand_options,
        key="selected_brand_visual",
        label_visibility="collapsed",
    )

# =========================================
# FILE + PROCESSING
# =========================================
if retailer_df is not None and file_ready_for_batch:
    try:
        if retailer_df.empty:
            st.warning("No rows found for the selected retailer.")
            st.stop()

        start = st.session_state.start_idx
        end = start + BATCH_SIZE

        if start >= len(retailer_df):
            st.session_state.processing_done = True
            st.session_state.completed_batch_key = current_batch_key

        batch_df = retailer_df.iloc[start:end]

        if not st.session_state.processing_done:
            st.write(f"Processing SKUs {start + 1} to {min(end, len(retailer_df))} of {len(retailer_df)}")
            st.caption(f"Batch Size: {BATCH_SIZE} | Workers: {MAX_WORKERS}")

            if st.session_state.progress_bar is None:
                st.session_state.progress_bar = st.progress(0)
            progress_bar = st.session_state.progress_bar
            status_text = st.empty()
            st.write("### Overall Progress")
            overall_progress_bar = st.progress(0)

            total = len(batch_df)
            completed = 0
            batch_records = batch_df.to_dict("records")

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(process_row, row_dict) for row_dict in batch_records]
                for future in as_completed(futures):
                    completed += 1
                    result = future.result()
                    if result:
                        summary = result.get("summary")
                        detail = result.get("detail")
                        debug = result.get("debug")

                        if summary and summary["SKU"] not in st.session_state.summary_skus:
                            st.session_state.summary_rows.append(summary)
                            st.session_state.summary_skus.add(summary["SKU"])
                        if detail and detail["SKU"] not in st.session_state.detail_skus:
                            st.session_state.export_rows.append(detail)
                            st.session_state.detail_skus.add(detail["SKU"])
                        if debug and debug["SKU"] not in st.session_state.debug_skus:
                            st.session_state.debug_rows.append(debug)
                            st.session_state.debug_skus.add(debug["SKU"])

                    if completed % UI_UPDATE_EVERY == 0 or completed == total:
                        progress_bar.progress(completed / max(total, 1))
                        status_text.markdown(
                            f"**Processed:** {completed}/{total}  \n**Overall:** {start + completed}/{len(retailer_df)}"
                        )
                        overall_progress_bar.progress((start + completed) / max(len(retailer_df), 1))

            if start + BATCH_SIZE < len(retailer_df):
                st.session_state.start_idx += BATCH_SIZE
                time.sleep(0.05)
                st.rerun()
            else:
                st.session_state.processing_done = True
                st.session_state.completed_batch_key = current_batch_key
                st.rerun()
    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())

# =========================================
# TOP EXPORT SECTION
# =========================================
if (
    retailer_df is not None
    and st.session_state.processing_done
    and st.session_state.completed_batch_key == current_batch_key
    and st.session_state.summary_rows
):
    if st.session_state.report_batch_key != current_batch_key:
        summary_df = pd.DataFrame(st.session_state.summary_rows)
        detail_df = pd.DataFrame(st.session_state.export_rows)
        debug_df = pd.DataFrame(st.session_state.debug_rows)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary_df.to_excel(writer, index=False, sheet_name="Summary")
            detail_df.to_excel(writer, index=False, sheet_name="Details")
            debug_df.to_excel(writer, index=False, sheet_name="Debug")

            wb = writer.book
            ws = wb["Summary"]
            green = PatternFill(start_color="C6EFCE", fill_type="solid")
            yellow = PatternFill(start_color="FFEB9C", fill_type="solid")
            red = PatternFill(start_color="FFC7CE", fill_type="solid")

            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, (int, float)):
                        if cell.value >= 80:
                            cell.fill = green
                        elif cell.value >= 50:
                            cell.fill = yellow
                        else:
                            cell.fill = red

        output.seek(0)
        safe_retailer = re.sub(r"[^A-Za-z0-9_-]+", "_", str(selected_retailer or "retailer"))
        report_filename = f"pdp_qa_results_{safe_retailer}_all_brands.xlsx"
        report_bytes = output.getvalue()

        st.session_state.report_bytes = report_bytes
        st.session_state.report_filename = report_filename
        st.session_state.report_batch_key = current_batch_key

    if (
        not st.session_state.auto_download_done
        and st.session_state.report_bytes
        and st.session_state.report_filename
    ):
        b64 = base64.b64encode(st.session_state.report_bytes).decode()
        components.html(
            "<script>"
            "const link = document.createElement('a');"
            f"link.href = 'data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64}';"
            f"link.download = '{st.session_state.report_filename}';"
            "document.body.appendChild(link);"
            "link.click();"
            "document.body.removeChild(link);"
            "</script>",
            height=0,
            width=0,
        )
        st.session_state.auto_download_done = True

# =========================================
# FULL VISUAL MODE
# =========================================
if (
    retailer_df is not None
    and st.session_state.processing_done
    and st.session_state.completed_batch_key == current_batch_key
):
    try:
        visual_df = retailer_df.copy()

        selected_visual_brand = st.session_state.selected_brand_visual
        if selected_visual_brand != "All" and "brand" in visual_df.columns:
            visual_df = visual_df[visual_df["brand"].astype(str) == selected_visual_brand].copy()

        if visual_df.empty:
            st.warning("No rows found for the selected retailer / brand.")
            st.stop()

        invalid_retail_values = {"", "n/a", "#n/a", "na", "nan", "none"}
        visual_df["retail_url_clean"] = (
            visual_df["retail_url"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        hidden_count = int(visual_df["retail_url_clean"].isin(invalid_retail_values).sum())
        visual_df = visual_df[
            ~visual_df["retail_url_clean"].isin(invalid_retail_values)
        ].copy()
        visual_df.drop(columns=["retail_url_clean"], inplace=True, errors="ignore")

        st.markdown(
            "<style>.block-container{max-width:1700px;padding-top:1rem;padding-bottom:1rem;} img{max-width:100%;height:auto;}</style>",
            unsafe_allow_html=True,
        )
        st.markdown("## 👁️ Full Visual QA Review")

        if hidden_count > 0:
            st.caption(
                f"Excluded from Full Visual QA only: {hidden_count} item(s) with missing retailer URLs."
            )
        if visual_df.empty:
            st.info(
                "No visually reviewable items found. Products without retailer URLs are still included in the extract."
            )
            st.stop()

        for _, row in visual_df.iterrows():
            sku = row.get("sku", "Missing SKU")
            retail_url = row.get("retail_url", "")
            salsify_url = row.get("salsify_url", "")
            retailer_name = row.get("retailer", "") or infer_retailer_name_from_url(retail_url)

            current_rpc = row.get("retailer_rpc", "")
            current_target_sku = get_target_sku_from_inputs(
                retail_url=retail_url,
                cvs_rpc=current_rpc,
            )
            visual_payload = get_visual_row_payload(
                salsify_url,
                retailer_name,
                retail_url,
                current_target_sku,
                sku=sku,
            )
            s_text = visual_payload["s_text"]
            s_images = visual_payload["s_images"]
            r_text = visual_payload["r_text"]
            r_images = visual_payload["r_images"]

            s_title = s_text.get("title") or ""
            r_title = r_text.get("title") or ""
            s_desc = s_text.get("description") or ""
            r_desc = r_text.get("description") or ""
            retailer_features = r_text.get("features") or []
            feature_fields = ["feature1", "feature2", "feature3", "feature4", "feature5"]

            title_score = keyword_score(s_title, r_title)
            desc_score = description_similarity_score(s_desc, r_desc)

            max_features = max(len(feature_fields), len(retailer_features))
            feature_scores = []
            feature_rows = []
            for i in range(max_features):
                s_val = s_text.get(feature_fields[i], "") if i < len(feature_fields) else ""
                r_val = retailer_features[i] if i < len(retailer_features) else ""
                row_score = keyword_score(s_val, r_val)
                feature_scores.append(row_score)
                feature_rows.append((s_val, r_val, row_score))

            avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0
            copy_avg_score = int((title_score + desc_score + avg_feature_score) / 3)

            img_scores = []
            max_images = min(max(len(s_images), len(r_images)), MAX_IMAGE_SLOTS_TO_COMPARE)
            max_images_to_score = min(max(len(s_images), len(r_images)), MAX_IMAGE_SLOTS_TO_SCORE)
            for i in range(max_images_to_score):
                s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else None
                r_url = r_images[i] if i < len(r_images) else None
                if s_url and r_url:
                    sc = compare_images_visually(s_url, r_url)
                    if sc > 0:
                        img_scores.append(sc)

            avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0
            overall_score = int((title_score + desc_score + avg_feature_score + avg_img_score) / 4)

            if show_only_issues and overall_score >= 80:
                continue
            if hide_good and overall_score >= 80:
                continue

            left, right = st.columns([2.72, 0.95], gap="small")

            with left:
                top_l, top_r = st.columns(2, gap="small")
                top_l.markdown(
                    column_header_link_html("Salsify", sku, salsify_url),
                    unsafe_allow_html=True,
                )
                top_r.markdown(
                    column_header_link_html(retailer_name, current_target_sku or current_rpc, retail_url),
                    unsafe_allow_html=True,
                )

                st.markdown(avg_score_bar_html("Copy — Avg", copy_avg_score), unsafe_allow_html=True)

                st.markdown(section_header_html("Title", title_score), unsafe_allow_html=True)
                t1, t2 = st.columns(2, gap="small")
                with t1:
                    st.markdown(
                        "<div style='margin-bottom:4px'>" + equal_height_block(s_title or "Missing", min_height=56) + "</div>",
                        unsafe_allow_html=True,
                    )
                with t2:
                    st.markdown(
                        "<div style='margin-bottom:4px'>" + equal_height_block(r_title or "Missing", min_height=56) + "</div>",
                        unsafe_allow_html=True,
                    )

                st.markdown(f"<div style='height:{TITLE_TO_DESCRIPTION_GAP_PX}px'></div>", unsafe_allow_html=True)

                st.markdown(section_header_html("Description", desc_score), unsafe_allow_html=True)
                d1, d2 = st.columns(2, gap="small")
                with d1:
                    st.markdown(
                        "<div style='margin-bottom:4px'>" + equal_height_block(s_desc or "Missing", min_height=150) + "</div>",
                        unsafe_allow_html=True,
                    )
                with d2:
                    st.markdown(
                        "<div style='margin-bottom:4px'>" + equal_height_block(r_desc or "Missing", min_height=150) + "</div>",
                        unsafe_allow_html=True,
                    )

                st.markdown(f"<div style='height:{DESCRIPTION_TO_FEATURES_GAP_PX}px'></div>", unsafe_allow_html=True)

                st.markdown(section_header_html("Features", avg_feature_score), unsafe_allow_html=True)
                for s_val, r_val, row_score in feature_rows:
                    f1, f2 = st.columns(2, gap="small")
                    with f1:
                        st.markdown(
                            "<div style='margin-bottom:4px'>" + equal_feature_block(s_val or "Missing", min_height=40) + "</div>",
                            unsafe_allow_html=True,
                        )
                    with f2:
                        st.markdown(
                            "<div style='margin-bottom:4px'>" + equal_feature_block(r_val or "Missing", min_height=40) + "</div>",
                            unsafe_allow_html=True,
                        )
                    st.markdown(
                        f"<div style='margin:0 0 {SECTION_VERTICAL_GAP}px 0; font-size:14px; font-weight:700;'>{score_text_html(row_score)}</div>",
                        unsafe_allow_html=True,
                    )

            with right:
                head_i1, head_i2 = st.columns(2, gap="small")
                head_i1.markdown(image_header_html("Salsify"), unsafe_allow_html=True)
                head_i2.markdown(image_header_html(retailer_name), unsafe_allow_html=True)
                st.markdown(
                    avg_score_bar_html("Images — Avg", avg_img_score),
                    unsafe_allow_html=True,
                )
                for i in range(max_images):
                    s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else ""
                    r_url = r_images[i] if i < len(r_images) and isinstance(r_images[i], str) else ""
                    slot_score = compare_images_visually(s_url, r_url) if (s_url and r_url and i < MAX_IMAGE_SLOTS_TO_SCORE) else 0
                    st.markdown(
                        image_compare_row_html(s_url, r_url, slot_score),
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"<div style='height:{IMG_SPACE_PX}px'></div>", unsafe_allow_html=True)

            if show_html_debugger:
                should_render_debugger = (not debug_only_sku) or (str(sku).strip() == str(debug_only_sku).strip())
                if should_render_debugger:
                    debug_url = retail_url if debugger_source == "Retailer page" else salsify_url
                    debug_retailer_name = retailer_name if debugger_source == "Retailer page" else "salsify"
                    debug_views = resolve_debug_views(
                        debug_url,
                        retailer_name=debug_retailer_name,
                        use_manual_html_override=use_manual_html_override,
                        manual_html_text=manual_html_text,
                        manual_html_file=manual_html_file,
                    )
                    with st.expander(f"Debug HTML / DOM — {sku}"):
                        st.text_input("Requested URL", value=str(debug_views.get("requested_url", "")), key=f"debug_requested_url_{sku}")
                        st.text_input("Final URL", value=str(debug_views.get("final_url", "")), key=f"debug_final_url_{sku}")
                        st.text_area("DOM Text", value=str(debug_views.get("dom_text", "")), height=220, key=f"debug_dom_text_{sku}")
                        st.text_area("Raw HTML", value=str(debug_views.get("raw_html", "")), height=280, key=f"debug_raw_html_{sku}")

            st.divider()
    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())
