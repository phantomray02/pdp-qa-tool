# =========================================
# IMPORTS
# =========================================
import re
import html
import json time
import json
import hashlib
import traceback
import base64
from io import BytesIO
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
from PIL import Image
from openpyxl.styles import PatternFill
from pandas.errors import EmptyDataError
import threading
from requests.adapters import HTTPAdapter

# =========================================
# APP SETUP
# =========================================
st.set_page_config(layout="wide")
st.title("PDP QA Tool ✅")

st.markdown(
    """
    <style>
    div[data-testid="stFileUploader"] > section {
        background: #232733;
        border: 1px solid #2f3442;
        border-radius: 10px;
        padding: 10px;
    }

    div[data-testid="stDownloadButton"] > button {
        width: 100%;
        min-height: 56px;
        border-radius: 10px;
        border: 1px solid #2f3442;
        background: #232733;
        color: white;
        font-weight: 700;
    }

    div[data-testid="stDownloadButton"] > button:hover {
        border-color: #4EA1FF;
        color: white;
    }
    </style>
    """,
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

# =========================================
# PERFORMANCE SETTINGS
# =========================================
BATCH_SIZE = 16
MAX_WORKERS = 6
UI_UPDATE_EVERY = 4

# Faster image compare via tiny difference hash.
IMAGE_HASH_WIDTH = 9
IMAGE_HASH_HEIGHT = 8

# Keep caches smaller to prevent Streamlit Cloud memory pressure.
HTML_CACHE_MAX = 80
IMAGE_HASH_CACHE_MAX = 300

# =========================================
# VISUAL LAYOUT SETTINGS
# =========================================
SECTION_HEADER_SIZE = 30
COPY_TEXT_SIZE = 24
COPY_LINE_HEIGHT = 1.18
SECTION_VERTICAL_GAP = 8

IMG_GAP = "small"
IMG_ROW_SPACER_PX = 6
IMG_BOX_HEIGHT = 125
IMG_SCORE_COL_RATIO = 0.34

html_cache = {}
image_hash_cache = {}

thread_local = threading.local()


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
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)


def description_similarity_score(a, b):
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)

    if not a_norm and not b_norm:
        return 100

    if a_norm == b_norm:
        return 100

    return int(SequenceMatcher(None, a_norm, b_norm).ratio() * 100)


def html_escape_text(text):
    return html.escape(str(text or ""))


def equal_height_block(text, min_height=210):
    safe_text = html_escape_text(text or "Missing")
    return f"""
    <div style="
        width:100%;
        min-height:{min_height}px;
        padding:0;
        margin:0;
        background:transparent;
        color:#FFFFFF;
        white-space:pre-wrap;
        line-height:{COPY_LINE_HEIGHT};
        font-size:{COPY_TEXT_SIZE}px;
        font-weight:500;
        text-indent:0;
        overflow-wrap:anywhere;
        word-break:break-word;
    ">
        {safe_text}
    </div>
    """


def equal_feature_block(text, min_height=90):
    safe_text = html_escape_text(text or "Missing")
    return f"""
    <div style="
        width:100%;
        min-height:{min_height}px;
        padding:0;
        margin:0;
        background:transparent;
        color:#FFFFFF;
        white-space:pre-wrap;
        line-height:{COPY_LINE_HEIGHT};
        font-size:{COPY_TEXT_SIZE}px;
        font-weight:500;
        text-indent:0;
        overflow-wrap:anywhere;
        word-break:break-word;
    ">
        {safe_text}
    </div>
    """


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
    return f"""
    <div style="
        display:flex;
        justify-content:space-between;
        align-items:flex-end;
        gap:12px;
        margin-top:{SECTION_VERTICAL_GAP}px;
        margin-bottom:{SECTION_VERTICAL_GAP}px;
    ">
        <div style="
            font-size:{SECTION_HEADER_SIZE}px;
            font-weight:900;
            color:#FFFFFF;
            line-height:1.0;
        ">
            {safe_label}
        </div>
        <div style="line-height:1.0;">
            {score_text_html(score)}
        </div>
    </div>
    """


def avg_score_bar_html(label, score):
    if score >= 80:
        color = "#2E7D32"
    elif score >= 50:
        color = "#F9A825"
    else:
        color = "#C62828"

    safe_label = html_escape_text(label or "")
    return f"""
    <div style="
        background-color:{color};
        padding:6px 10px;
        border-radius:4px;
        color:white;
        font-weight:900;
        font-size:19px;
        margin-top:2px;
        margin-bottom:{IMG_ROW_SPACER_PX}px;
        display:flex;
        justify-content:space-between;
        align-items:center;
        gap:10px;
    ">
        <span>{safe_label}</span>
        <span style="color:#FFFFFF; font-weight:900; font-size:20px;">{score}%</span>
    </div>
    """


def column_header_link_html(label, item_number, href):
    safe_label = html_escape_text(label or "")
    safe_item = html_escape_text(item_number or "")
    safe_href = html.escape(str(href or ""), quote=True)

    if safe_href and safe_item:
        item_html = f'<a href="{safe_href}" target="_blank" style="color:#3EA6FF; text-decoration:none; font-weight:900;">{safe_item}</a>'
    else:
        item_html = f'<span style="color:#3EA6FF; font-weight:900;">{safe_item or "Missing"}</span>'

    return f"""
    <div style="
        text-align:left;
        margin-top:0;
        margin-bottom:2px;
        font-size:28px;
        font-weight:900;
        color:#FFFFFF;
        line-height:1.05;
    ">
        {safe_label}: {item_html}
    </div>
    """


def image_header_html(label):
    safe_label = html_escape_text(label or "")
    return f"""
    <div style="
        text-align:left;
        margin-top:0;
        margin-bottom:2px;
        font-size:28px;
        font-weight:900;
        color:#FFFFFF;
        line-height:1.05;
    ">
        {safe_label}
    </div>
    """


def image_compare_box_html(url, box_height=IMG_BOX_HEIGHT):
    if url:
        safe_url = html.escape(str(url), quote=True)
        return f"""
        <div style="
            height:{box_height}px;
            width:100%;
            display:flex;
            align-items:center;
            justify-content:center;
            margin:0;
            padding:0;
            overflow:hidden;
        ">
            <img src="{safe_url}" style="max-width:100%; max-height:{box_height}px; object-fit:contain;" />
        </div>
        """
    else:
        return f"""
        <div style="
            height:{box_height}px;
            width:100%;
            display:flex;
            align-items:center;
            justify-content:center;
            margin:0;
            padding:0;
            color:#C62828;
            font-size:16px;
            font-weight:700;
        ">
            Missing
        </div>
        """


def read_uploaded_csv_from_bytes(file_bytes):
    if not file_bytes:
        raise EmptyDataError("Uploaded file is empty.")
    if len(file_bytes.strip()) == 0:
        raise EmptyDataError("Uploaded file is empty.")

    last_error = None
    for encoding in ["utf-8-sig", "utf-8", "latin1"]:
        try:
            return pd.read_csv(BytesIO(file_bytes), encoding=encoding)
        except Exception as e:
            last_error = e

    raise last_error if last_error else EmptyDataError("Could not parse uploaded CSV.")


def infer_retailer_name_from_url(url):
    url = (url or "").lower()

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
    df.columns = [c.strip().lower() for c in df.columns]

    df.rename(
        columns={
            "salsify url": "salsify_url",
            "retail url": "retail_url",
            "sku id": "sku",
            "product sku": "sku",
            "cvs rpc": "cvs_rpc",
            "retailer name": "retailer",
            "retailer_name": "retailer",
        },
        inplace=True,
    )

    if "brand" not in df.columns and len(df.columns) >= 5:
        df.rename(columns={df.columns[4]: "brand"}, inplace=True)

    required = ["sku", "salsify_url", "retail_url"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    if "retailer" not in df.columns:
        df["retailer"] = df["retail_url"].apply(infer_retailer_name_from_url)
    else:
        df["retailer"] = df["retailer"].fillna("").astype(str).str.strip()
        inferred = df["retail_url"].apply(infer_retailer_name_from_url)
        df["retailer"] = df["retailer"].where(df["retailer"] != "", inferred)

    if "cvs_rpc" not in df.columns:
        df["cvs_rpc"] = ""

    return df


def clear_in_memory_caches():
    html_cache.clear()
    image_hash_cache.clear()


# =========================================
# HTML FETCH
# =========================================
def get_html(url):
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
    if not url:
        return None

    if url in image_hash_cache:
        return image_hash_cache[url]

    try:
        session = get_session()
        r = session.get(url, timeout=IMAGE_TIMEOUT)
        if r.status_code != 200:
            return None
        if "image" not in r.headers.get("Content-Type", ""):
            return None

        img = Image.open(BytesIO(r.content))
        img = img.convert("L").resize((IMAGE_HASH_WIDTH, IMAGE_HASH_HEIGHT))

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

    except Exception:
        return None


def hamming_distance(a, b):
    return bin(a ^ b).count("1")


def compare_images_visually(s_url, r_url):
    if not s_url or not r_url:
        return 0

    s_hash = get_image_dhash(s_url)
    r_hash = get_image_dhash(r_url)

    if s_hash is None or r_hash is None:
        return 0

    dist = hamming_distance(s_hash, r_hash)

    if dist <= 2:
        return 100
    elif dist <= 6:
        return 90
    elif dist <= 10:
        return 75
    elif dist <= 16:
        return 60
    elif dist <= 22:
        return 45
    else:
        return 25


# =========================================
# PROCESS ROW
# =========================================
def process_row(row):
    try:
        retail_url = row.get("retail_url", "")
        salsify_url = row.get("salsify_url", "")
        cvs_rpc = row.get("cvs_rpc") or row.get("CVS RPC") or ""
        retailer_name = row.get("retailer", "") or infer_retailer_name_from_url(retail_url)

        target_sku = get_target_sku_from_inputs(
            retail_url=row.get("retail_url", ""),
            cvs_rpc=cvs_rpc,
        )

        # IMPORTANT:
        # No nested threadpool here. The outer batch executor already parallelizes rows.
        s_bundle = get_salsify_bundle(salsify_url)
        r_bundle = get_cvs_bundle(retail_url, target_sku)

        s_text = s_bundle["text"]
        s_images = s_bundle["images"]

        r_text = r_bundle["text"] or {}
        r_images = r_bundle["images"]

        debug_data = r_text.get("debug", {})

        r_text["description"] = clean_cvs_text(r_text.get("description", ""))
        r_text["features"] = normalize_cvs_features(r_text.get("features", []))

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
        max_img_positions = max(len(s_images), len(r_images))

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
    st.session_state.selected_retailer = "All"
if "auto_download_done" not in st.session_state:
    st.session_state.auto_download_done = False
if "report_bytes" not in st.session_state:
    st.session_state.report_bytes = None
if "report_filename" not in st.session_state:
    st.session_state.report_filename = None
if "run_context_key" not in st.session_state:
    st.session_state.run_context_key = None

# =========================================
# TOP UPLOAD + DOWNLOAD UI
# =========================================
top_upload_col, top_download_col = st.columns([2.4, 1.1], gap="small")

with top_upload_col:
    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

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

# =========================================
# VIEW + FILTER CONTROLS
# =========================================
st.markdown("## 🔎 QA Viewer Controls")

show_only_issues = st.checkbox("❌ Show ONLY Issues", key="show_issues")
hide_good = st.checkbox("✅ Hide Strong Matches (80%+)", key="hide_good")

# =========================================
# FILE + PROCESSING
# =========================================
if uploaded_file:
    try:
        file_bytes = uploaded_file.getvalue()
        st.session_state.uploaded_file_bytes = file_bytes
        file_hash = hashlib.md5(file_bytes).hexdigest()

        # Full reset when file changes.
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
            st.session_state.selected_retailer = "All"
            st.session_state.auto_download_done = False
            st.session_state.report_bytes = None
            st.session_state.report_filename = None
            st.session_state.run_context_key = None
            clear_in_memory_caches()

        df = read_uploaded_csv_from_bytes(file_bytes)
        df = prepare_input_df(df)

        all_retailers = sorted(df["retailer"].dropna().astype(str).unique().tolist()) if "retailer" in df.columns else ["CVS"]
        if not all_retailers:
            all_retailers = ["CVS"]

        multi_retailer = len(all_retailers) > 1

        # Force retailer selection only if multiple retailers exist.
        if multi_retailer:
            retailer_options = ["-- Select Retailer --"] + all_retailers

            if st.session_state.selected_retailer not in all_retailers:
                st.session_state.selected_retailer = "-- Select Retailer --"

            selected_retailer = st.selectbox(
                "🏪 Select Retailer",
                retailer_options,
                key="selected_retailer",
            )

            if selected_retailer == "-- Select Retailer --":
                st.info("Select a retailer to run the batch.")
                st.stop()
        else:
            st.session_state.selected_retailer = all_retailers[0]
            selected_retailer = st.selectbox(
                "🏪 Select Retailer",
                all_retailers,
                index=0,
                key="selected_retailer_single",
                disabled=True,
            )

        # Fresh batch only when retailer changes.
        current_run_context_key = (
            file_hash,
            selected_retailer,
        )

        if st.session_state.run_context_key != current_run_context_key:
            st.session_state.summary_rows = []
            st.session_state.export_rows = []
            st.session_state.debug_rows = []
            st.session_state.summary_skus = set()
            st.session_state.detail_skus = set()
            st.session_state.debug_skus = set()
            st.session_state.start_idx = 0
            st.session_state.processing_done = False
            st.session_state.progress_bar = None
            st.session_state.auto_download_done = False
            st.session_state.report_bytes = None
            st.session_state.report_filename = None
            st.session_state.run_context_key = current_run_context_key

        # ALL brands for the retailer run together.
        df = df[df["retailer"].astype(str) == selected_retailer]

        if df.empty:
            st.warning("No rows found for the selected retailer.")
            st.stop()

        start = st.session_state.start_idx
        end = start + BATCH_SIZE

        if start >= len(df):
            st.session_state.processing_done = True

        batch_df = df.iloc[start:end]

        if not st.session_state.processing_done:
            st.write(f"Processing SKUs {start + 1} to {min(end, len(df))} of {len(df)}")
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
                            f"**Processed:** {completed}/{total}  \n"
                            f"**Overall:** {start + completed}/{len(df)}"
                        )
                        overall_progress_bar.progress((start + completed) / max(len(df), 1))

            if start + BATCH_SIZE < len(df):
                st.session_state.start_idx += BATCH_SIZE
                time.sleep(0.05)
                st.rerun()
            else:
                st.session_state.processing_done = True
                st.rerun()

    except EmptyDataError:
        st.error("🔥 CRITICAL APP ERROR")
        st.text("The uploaded CSV is empty or could not be read.")
    except ValueError as e:
        st.error("❌ INPUT FILE ERROR")
        st.text(str(e))
    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())

# =========================================
# TOP EXPORT SECTION
# =========================================
if st.session_state.processing_done and st.session_state.summary_rows:
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

    current_retailer_for_file = (
        st.session_state.selected_retailer
        if st.session_state.selected_retailer not in ["All", "-- Select Retailer --"]
        else "retailer"
    )

    safe_retailer = re.sub(r"[^A-Za-z0-9_-]+", "_", str(current_retailer_for_file))

    report_filename = f"pdp_qa_results_{safe_retailer}.xlsx"
    report_bytes = output.getvalue()

    st.session_state.report_bytes = report_bytes
    st.session_state.report_filename = report_filename

    if (
        not st.session_state.auto_download_done
        and st.session_state.report_bytes
        and st.session_state.report_filename
    ):
        b64 = base64.b64encode(st.session_state.report_bytes).decode()

        components.html(
            f"""
            <script>
            const link = document.createElement("a");
            link.href = "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64}";
            link.download = "{st.session_state.report_filename}";
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            </script>
            """,
            height=0,
            width=0,
        )

        st.session_state.auto_download_done = True

# =========================================
# FULL VISUAL MODE
# =========================================
if uploaded_file and st.session_state.processing_done:
    try:
        if not st.session_state.uploaded_file_bytes:
            st.error("Uploaded CSV data is missing from session state.")
            st.stop()

        df = read_uploaded_csv_from_bytes(st.session_state.uploaded_file_bytes)
        df = prepare_input_df(df)

        if st.session_state.selected_retailer not in ["All", "-- Select Retailer --"] and "retailer" in df.columns:
            df = df[df["retailer"].astype(str) == st.session_state.selected_retailer]

        if df.empty:
            st.warning("No rows found for the selected retailer.")
            st.stop()

        st.markdown("## 👁️ Full Visual QA Review")

        for _, row in df.iterrows():
            sku = row.get("sku", "Missing SKU")
            retail_url = row.get("retail_url", "")
            salsify_url = row.get("salsify_url", "")
            retailer_name = row.get("retailer", "") or infer_retailer_name_from_url(retail_url)

            s_bundle = get_salsify_bundle(salsify_url)
            s_text = s_bundle["text"]
            s_images = s_bundle["images"]

            current_rpc = row.get("cvs_rpc") or row.get("CVS RPC") or ""
            current_target_sku = get_target_sku_from_inputs(
                retail_url=retail_url,
                cvs_rpc=current_rpc,
            )

            r_bundle = get_cvs_bundle(retail_url, target_rpc=current_target_sku)

            r_text = r_bundle["text"] or {}
            r_images = r_bundle["images"]

            r_text["description"] = clean_cvs_text(r_text.get("description", ""))
            r_text["features"] = normalize_cvs_features(r_text.get("features", []))

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
            max_images = max(len(s_images), len(r_images))

            for i in range(max_images):
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

            left, right = st.columns([2.72, 0.78], gap="small")

            with left:
                # Locked compare columns so one side does not push the other.
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

                # TITLE.
                st.markdown(section_header_html("Title", title_score), unsafe_allow_html=True)
                t1, t2 = st.columns(2, gap="small")

                with t1:
                    st.markdown(
                        "<div style='width:100%; overflow:hidden; padding-left:0; margin-left:0;'>"
                        + equal_height_block(s_title or "Missing", min_height=76)
                        + "</div>",
                        unsafe_allow_html=True,
                    )

                with t2:
                    st.markdown(
                        "<div style='width:100%; overflow:hidden; padding-left:0; margin-left:0;'>"
                        + equal_height_block(r_title or "Missing", min_height=76)
                        + "</div>",
                        unsafe_allow_html=True,
                    )

                # DESCRIPTION.
                st.markdown(section_header_html("Description", desc_score), unsafe_allow_html=True)
                d1, d2 = st.columns(2, gap="small")

                with d1:
                    st.markdown(
                        "<div style='width:100%; overflow:hidden; padding-left:0; margin-left:0;'>"
                        + equal_height_block(s_desc or "Missing", min_height=220)
                        + "</div>",
                        unsafe_allow_html=True,
                    )

                with d2:
                    st.markdown(
                        "<div style='width:100%; overflow:hidden; padding-left:0; margin-left:0;'>"
                        + equal_height_block(r_desc or "Missing", min_height=220)
                        + "</div>",
                        unsafe_allow_html=True,
                    )

                # FEATURES.
                st.markdown(section_header_html("Features", avg_feature_score), unsafe_allow_html=True)

                for s_val, r_val, row_score in feature_rows:
                    f1, f2 = st.columns(2, gap="small")

                    with f1:
                        st.markdown(
                            "<div style='width:100%; overflow:hidden; padding-left:0; margin-left:0;'>"
                            + equal_feature_block(s_val or "Missing", min_height=46)
                            + "</div>",
                            unsafe_allow_html=True,
                        )

                    with f2:
                        st.markdown(
                            "<div style='width:100%; overflow:hidden; padding-left:0; margin-left:0;'>"
                            + equal_feature_block(r_val or "Missing", min_height=46)
                            + "</div>",
                            unsafe_allow_html=True,
                        )

                    st.markdown(
                        f"""
                        <div style="
                            text-align:right;
                            margin-top:0;
                            margin-bottom:2px;
                        ">
                            {score_text_html(row_score)}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    st.markdown("<div style='height:2px;'></div>", unsafe_allow_html=True)

            with right:
                head_i1, head_i2 = st.columns(2, gap="small")
                head_i1.markdown(image_header_html("Salsify"), unsafe_allow_html=True)
                head_i2.markdown(image_header_html(retailer_name), unsafe_allow_html=True)

                st.markdown(avg_score_bar_html("Images — Avg", avg_img_score), unsafe_allow_html=True)

                for i in range(max_images):
                    s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else ""
                    r_url = r_images[i] if i < len(r_images) and isinstance(r_images[i], str) else ""
                    slot_score = compare_images_visually(s_url, r_url) if (s_url and r_url) else 0

                    # Equal spacing system: same row height, same gaps, same centered score.
                    img1, img2, score_col = st.columns([1, 1, IMG_SCORE_COL_RATIO], gap=IMG_GAP)

                    with img1:
                        st.markdown(
                            image_compare_box_html(s_url, box_height=IMG_BOX_HEIGHT),
                            unsafe_allow_html=True,
                        )

                    with img2:
                        st.markdown(
                            image_compare_box_html(r_url, box_height=IMG_BOX_HEIGHT),
                            unsafe_allow_html=True,
                        )

                    with score_col:
                        st.markdown(
                            f"""
                            <div style="
                                height:{IMG_BOX_HEIGHT}px;
                                display:flex;
                                align-items:center;
                                justify-content:flex-end;
                                text-align:right;
                                margin:0;
                                padding:0;
                            ">
                                {score_text_html(slot_score)}
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                    st.markdown(
                        f"<div style='height:{IMG_ROW_SPACER_PX}px;'></div>",
                        unsafe_allow_html=True,
                    )

            st.divider()

    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())
