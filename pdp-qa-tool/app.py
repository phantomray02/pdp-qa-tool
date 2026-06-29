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
st.title("PDP QA Tool 🎉")

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

REQUEST_TIMEOUT = 8
IMAGE_TIMEOUT = 6
IMAGE_RETRY_TIMEOUT = 10
IMAGE_RETRY_COUNT = 1
IMAGE_FETCH_WORKERS = 6
IMAGE_FETCH_CHUNK_SIZE = 65536

# UI-only thumbnail compression. These values affect only what is rendered in the
# Streamlit visual QA HTML. Scoring still uses the original full image URLs.
DISPLAY_IMAGE_TIMEOUT = 5
DISPLAY_IMAGE_MAX_WIDTH = 360
DISPLAY_IMAGE_MAX_HEIGHT = 360
DISPLAY_IMAGE_QUALITY = 68
DISPLAY_IMAGE_CACHE_MAX = 1000
MAX_CACHE = 400
# Retailer-specific fetch tuning
WALGREENS_REQUEST_TIMEOUT = 18
WALGREENS_DEBUG_TIMEOUT = 25
WALGREENS_API_TIMEOUT = 10

# =========================================
# PERFORMANCE SETTINGS
# =========================================
# Balanced parallelism for image-heavy retailers.
# Too many workers can cause Salsify/CVS image requests to timeout or show as broken.
BATCH_SIZE = 32
MAX_WORKERS = 8
UI_UPDATE_EVERY = 5

# Faster image compare via tiny difference hash.
IMAGE_HASH_WIDTH = 9
IMAGE_HASH_HEIGHT = 8

# Larger caches to reduce repeat image fetches during batch + visual QA.
HTML_CACHE_MAX = 200
IMAGE_HASH_CACHE_MAX = 600

# Hard image safety limits.
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_SAFE_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_SLOTS_TO_COMPARE = 20
MAX_IMAGE_SLOTS_TO_SCORE = 12
STRICT_LIVE_RETAILER_ONLY = True
STRICT_CVS_VARIANT_MATCH = True
CVS_VARIANT_MIN_MATCH_SCORE = 35

CAPTURE_MODE_USE_EXTENSION = "Use extension + TXT upload"
CAPTURE_MODE_SKIP_EXTENSION = "Skip extension and go straight to batch"
AUTO_SKIP_EXTENSION_RETAILERS = {"CVS", "Walgreens"}
# Retailer-specific Salsify isolation controls.
# Copy can stay retailer-locked while images still fall back to generic locked Salsify slots if
# retailer-labeled image assets do not exist yet.
EXCLUSIVE_SALSIFY_COPY_RETAILERS = {"walgreens"}
EXCLUSIVE_SALSIFY_IMAGE_RETAILERS = set()

html_cache = {}
image_hash_cache = {}
image_compare_cache = {}
display_image_cache = {}
IMAGE_COMPARE_CACHE_MAX = 1200

# Image downloads are throttled separately from row workers so image-heavy rows
# do not overload Salsify/CVS/retailer image hosts and create false timeouts.
image_fetch_semaphore = threading.BoundedSemaphore(IMAGE_FETCH_WORKERS)

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
            pool_connections=100,
            pool_maxsize=100,
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

def clean_item_number(value):
    if not value:
        return ""
    return str(value).replace(".0", "").strip()


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
        f'<div style="background-color:{color};padding:6px 10px;border-radius:4px;color:white;'
        f'font-weight:900;font-size:19px;margin-top:2px;margin-bottom:{IMG_SPACE_PX}px;'
        f'display:flex;justify-content:space-between;align-items:center;gap:10px;">'
        f'<span>{safe_label}</span>'
        f'<span style="color:#FFFFFF; font-weight:900; font-size:20px;">{score}%</span>'
        f'</div>'
    )


def rating_stars_html(rating, review_count=None, font_size_px=18):
    try:
        rating = float(rating or 0)
    except Exception:
        rating = 0.0

    rating = max(0.0, min(rating, 5.0))
    fill_pct = (rating / 5.0) * 100

    review_html = ""
    if review_count is not None and str(review_count).strip() != "":
        review_html = (
            f'<span style="margin-left:8px;font-size:28px;font-weight:900;color:#FFFFFF;line-height:1;white-space:nowrap;">'
            f'{html_escape_text(review_count)}</span>'
        )

    return (
        f'<div style="display:inline-flex;align-items:center;justify-content:flex-end;gap:8px;white-space:nowrap;margin:0;line-height:1;overflow:visible;">'
        f'<span style="font-size:28px;font-weight:900;color:#FFFFFF;line-height:1;">{rating:.1f}</span>'
        f'<div style="position:relative;display:inline-block;line-height:1;font-size:{font_size_px}px;letter-spacing:0.6px;">'
        f'<div style="color:rgba(255,255,255,0.35);">★★★★★</div>'
        f'<div style="position:absolute;top:0;left:0;width:{fill_pct}%;overflow:hidden;color:#FFFFFF;white-space:nowrap;">★★★★★</div>'
        f'</div>'
        f'{review_html}'
        f'</div>'
    )


def locked_visual_header_row_html(
    salsify_header_html,
    retailer_header_html,
    rating_html="",
    retailer_name="",
):
    retailer_name = str(retailer_name or "").strip().lower()

    if retailer_name in {"walgreens", "kroger"}:
        return (
            '<div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:18px;align-items:start;margin:0 0 6px 0;">'
            f'<div style="min-width:0;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible;">{salsify_header_html}</div>'
            '<div style="min-width:0;display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:12px;align-items:start;overflow:visible;">'
            f'<div style="min-width:0;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible;">{retailer_header_html}</div>'
            f'<div style="min-width:0;display:flex;align-items:flex-start;justify-content:flex-end;overflow:visible;">{rating_html or "&nbsp;"}</div>'
            '</div>'
            '</div>'
        )

    return (
        '<div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:18px;align-items:start;margin:0 0 6px 0;">'
        f'<div style="min-width:0;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible;">{salsify_header_html}</div>'
        f'<div style="min-width:0;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible;">{retailer_header_html}</div>'
        '</div>'
    )

def column_header_link_html(label, item_number, href):
    safe_label = html_escape_text(label or "")
    safe_item = html_escape_text(item_number or "")
    safe_href = html.escape(str(href or ""), quote=True)

    if safe_href and safe_item:
        item_html = (
            f'<a href="{safe_href}" target="_blank" '
            f'style="color:#3EA6FF; text-decoration:none; font-weight:900; white-space:nowrap;">{safe_item}</a>'
        )
    else:
        item_html = f'<span style="color:#3EA6FF; font-weight:900; white-space:nowrap;">{safe_item or "Missing"}</span>'

    return (
        f'<div style="text-align:left;margin-top:0;margin-bottom:2px;font-size:28px;'
        f'font-weight:900;color:#FFFFFF;line-height:1.05;white-space:nowrap;display:inline-block;">{safe_label}: {item_html}</div>'
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



def is_video_like_url(url):
    url = html.unescape(str(url or "").strip())
    if not url:
        return False
    lowered = url.lower().split('?', 1)[0]
    return bool(
        lowered.endswith('.mp4')
        or lowered.endswith('.m3u8')
        or lowered.endswith('.webm')
        or '/video/' in lowered
        or 'salsify.com/video/' in lowered
        or 'asr-rm/' in lowered
    )



def _cache_display_image_src(url, value):
    global display_image_cache
    if "display_image_cache" not in globals() or not isinstance(globals().get("display_image_cache"), dict):
        display_image_cache = {}
    display_image_cache[url] = value
    while len(display_image_cache) > DISPLAY_IMAGE_CACHE_MAX:
        display_image_cache.pop(next(iter(display_image_cache)))


def get_display_image_src(url):
    """
    UI-only thumbnail source builder.

    This returns a compressed data URI for image rendering in Streamlit, while all
    scoring still uses the original full image URL. CSS dimensions are unchanged;
    only the downloaded/rendered payload is smaller.
    """
    url = str(url or "").strip()
    if not url:
        return ""
    if is_video_like_url(url):
        return url

    global display_image_cache
    if "display_image_cache" not in globals() or not isinstance(globals().get("display_image_cache"), dict):
        display_image_cache = {}

    if url in display_image_cache:
        return display_image_cache[url]

    try:
        # Reuse the existing controlled image fetch path so display thumbnails do
        # not create an unbounded second wave of browser/network requests.
        image_bytes = _download_image_bytes_once(url, DISPLAY_IMAGE_TIMEOUT)
        if not image_bytes:
            _cache_display_image_src(url, url)
            return url

        bio = BytesIO(image_bytes)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(bio)
            width, height = img.size
            if width * height > MAX_SAFE_IMAGE_PIXELS:
                _cache_display_image_src(url, url)
                return url
            img.load()

        # Preserve transparency against a white background so PNG/WebP assets look
        # normal after JPEG compression.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.thumbnail((DISPLAY_IMAGE_MAX_WIDTH, DISPLAY_IMAGE_MAX_HEIGHT), Image.LANCZOS)

        out = BytesIO()
        img.save(out, format="JPEG", quality=DISPLAY_IMAGE_QUALITY, optimize=True)
        encoded = base64.b64encode(out.getvalue()).decode("ascii")
        data_uri = f"data:image/jpeg;base64,{encoded}"
        _cache_display_image_src(url, data_uri)
        return data_uri

    except Exception:
        _cache_display_image_src(url, url)
        return url


def image_compare_cell_html(url):
    if not url:
        return '<div style="width:100%;min-height:80px;display:flex;align-items:center;justify-content:center;margin:0;padding:0;color:#C62828;font-size:16px;font-weight:700;">Missing</div>'

    display_url = get_display_image_src(url)
    safe_url = html.escape(str(display_url), quote=True)

    if not is_video_like_url(url):
        return (
            f"<div style='width:100%;margin:0;padding:0;display:flex;align-items:flex-start;justify-content:center;overflow:hidden;'>"
            f"<img src='{safe_url}' loading='lazy' decoding='async' referrerpolicy='no-referrer' style='display:block;width:100%;height:auto;object-fit:contain;' />"
            f"</div>"
        )

    media_id = hashlib.md5(str(url).encode("utf-8")).hexdigest()[:12]
    modal_id = f"video_modal_{media_id}"
    open_js = (
        f"(function(){{"
        f"var modal=document.getElementById('{modal_id}');"
        f"if(modal){{modal.style.display='flex';}}"
        f"var thumb=document.getElementById('thumb_media_{media_id}');"
        f"var full=document.getElementById('full_media_{media_id}');"
        f"if(full){{"
        f"try{{if(thumb&&thumb.currentTime>=0){{full.currentTime=thumb.currentTime||0;}}}}catch(e){{}}"
        f"try{{full.play();}}catch(e){{}}"
        f"}}"
        f"}})()"
    )
    close_js = (
        f"(function(){{"
        f"var modal=document.getElementById('{modal_id}');"
        f"if(modal){{modal.style.display='none';}}"
        f"var full=document.getElementById('full_media_{media_id}');"
        f"if(full){{try{{full.pause();}}catch(e){{}}}}"
        f"}})()"
    )

    return (
        f'<div style="position:relative;width:100%;margin:0;padding:0;display:flex;align-items:flex-start;justify-content:center;overflow:hidden;">'
        f'<video id="thumb_media_{media_id}" controls playsinline preload="metadata" onplay="{open_js}" style="display:block;width:100%;height:auto;max-height:320px;object-fit:contain;background:#000;">'
        f'<source src="{safe_url}" />'
        f'</video>'
        f'</div>'
        f'<div id="{modal_id}" onclick="if(event.target===this){{{close_js}}}" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.28);z-index:999999;align-items:center;justify-content:center;padding:18px;">'
        f'<div style="position:relative;width:min(74vw,760px);max-height:76vh;border-radius:12px;overflow:hidden;box-shadow:0 20px 56px rgba(0,0,0,0.38);background:#111;">'
        f'<button type="button" onclick="{close_js}" style="position:absolute;top:8px;right:8px;width:30px;height:30px;border:0;border-radius:15px;background:rgba(255,255,255,0.95);color:#111;font-size:18px;font-weight:900;cursor:pointer;z-index:2;">×</button>'
        f'<video id="full_media_{media_id}" controls playsinline preload="metadata" style="display:block;width:100%;max-height:76vh;height:auto;object-fit:contain;background:#000;">'
        f'<source src="{safe_url}" />'
        f'</video>'
        f'</div></div>'
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
        display_url = get_display_image_src(url)
        safe_url = html.escape(display_url, quote=True)
        return f'''<div style="border:1px solid #E0E0E0;border-radius:8px;background:#FFFFFF;padding:8px;">
<div style="font-size:45px;font-weight:600;margin-bottom:6px;">{safe_label}</div>
<div style="height:{box_height}px;display:flex;align-items:center;justify-content:center;background:#FAFAFA;border-radius:6px;overflow:hidden;">
<img src="{safe_url}" loading="lazy" decoding="async" referrerpolicy="no-referrer" style="max-width:100%;max-height:{box_height}px;object-fit:contain;" />
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
    


def normalize_retailer_name(value):
    value = normalize_space(value)
    if not value:
        return "Retailer"
    lowered = value.lower()
    mapping = {
        "cvs": "CVS",
        "walgreens": "Walgreens",
        "wags": "Walgreens",
        "sam's club": "Sam's Club",
        "sams club": "Sam's Club",
        "samsclub": "Sam's Club",
        "walmart": "Walmart",
        "target": "Target",
        "kroger": "Kroger",
        "amazon": "Amazon",
        "retailer": "Retailer",
    }
    return mapping.get(lowered, value)


def build_empty_retailer_bundle(retailer_name="Retailer", reason=""):
    retailer_name = normalize_retailer_name(retailer_name)
    reason = normalize_space(reason) or "retailer_not_supported"
    return {
        "text": {
            "title": "",
            "description": "",
            "features": [],
            "rating": "",
            "review_count": "",
            "debug": {
                "Title Path": reason,
                "Description Path": reason,
                "Features Path": reason,
                "Source Used": reason,
                "Retailer": retailer_name,
            },
        },
        "images": [],
    }


def normalize_salsify_asset_name(value):
    value = html.unescape(str(value or "")).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[/_|-]+", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def make_blank_salsify_image_slot(name="missing"):
    return {"name": str(name or "missing"), "url": ""}


def build_locked_salsify_slots(s_images, lock_top_three=True, max_slots=MAX_IMAGE_SLOTS_TO_COMPARE):
    """
    Salsify-only slot builder.

    Rules:
    1. Slot 1 is reserved for Online Optimized Image / online image.
    2. Slot 2 is reserved for Flat Back_2D / back image.
    3. Slot 3 is reserved for Flat Left_2D / left image.
    4. If one of those top-three images is missing, keep the slot blank so later
       lifestyle / ATF images shift down and do not move up into slots 1-3.
    5. Retailer images must not be modified by this rule.
    """
    s_images = [img for img in (s_images or []) if isinstance(img, dict)]

    by_name = {}
    remainder = []
    locked_name_map = {
        "online": [
            "online optimized image",
            "online image",
            "online",
            "front",
        ],
        "back": [
            "flat back 2d",
            "flat back",
            "back 2d",
            "back",
        ],
        "left": [
            "flat left 2d",
            "flat left",
            "left 2d",
            "left",
        ],
    }

    def classify(img):
        name = normalize_salsify_asset_name(img.get("name", ""))
        for canonical, options in locked_name_map.items():
            for option in options:
                if normalize_salsify_asset_name(option) in name:
                    return canonical
        return ""

    for img in s_images:
        canonical = classify(img)
        if canonical and canonical not in by_name:
            by_name[canonical] = img
        else:
            remainder.append(img)

    if not lock_top_three:
        ordered = []
        for key in ["online", "back", "left"]:
            if key in by_name:
                ordered.append(by_name[key])
        ordered.extend(remainder)
        return ordered[:max_slots]

    ordered = [
        by_name.get("online") or make_blank_salsify_image_slot("online"),
        by_name.get("back") or make_blank_salsify_image_slot("back"),
        by_name.get("left") or make_blank_salsify_image_slot("left"),
    ]
    ordered.extend(remainder)
    return ordered[:max_slots]


# =========================================
# RETAILER Salsify REQUIREMENTS
# =========================================
# Centralized retailer-specific Salsify limits so copy/image rules are easy to update in one place.
RETAILER_SALSIFY_REQUIREMENTS = {
    "default": {"max_features": 5, "max_images": 6},
    "cvs": {"max_features": 5, "max_images": 8},
    "walgreens": {"max_features": 5, "max_images": 6},
    "kroger": {"max_features": 7, "max_images": 7},
    "sam's club": {"max_features": 10, "max_images": 10},
    "sams club": {"max_features": 10, "max_images": 10},
    "samsclub": {"max_features": 10, "max_images": 10},
}


def get_retailer_salsify_requirements(retailer_name):
    retailer = str(retailer_name or "").strip().lower()
    return dict(RETAILER_SALSIFY_REQUIREMENTS.get(retailer, RETAILER_SALSIFY_REQUIREMENTS["default"]))


def get_retailer_salsify_feature_fields(retailer_name):
    max_features = int(get_retailer_salsify_requirements(retailer_name).get("max_features", 5) or 5)
    max_features = max(0, min(max_features, 10))
    return [f"feature{i}" for i in range(1, max_features + 1)]


def classify_cvs_generic_asset_name(value):
    name = normalize_salsify_asset_name(value or "")
    if any(token in name for token in [
        "atf i/o generic",
        "atf i o generic",
        "atf io generic",
        "atf i/o-generic",
        "atf io-generic",
    ]):
        return "io_generic"
    if any(token in name for token in [
        "atf 6 generic",
        "atf 6-generic",
        "atf6 generic",
        "atf6-generic",
    ]):
        return "atf6_generic"
    return ""


def apply_retailer_salsify_copy_limits(retailer_name, text_bundle):
    out = dict(text_bundle or {})
    limits = get_retailer_salsify_requirements(retailer_name)
    max_features = int(limits.get("max_features", 5) or 5)
    max_features = max(0, min(max_features, 10))

    gathered = []
    for value in out.get("features", []) or []:
        clean_value = normalize_space(value)
        if clean_value:
            gathered.append(clean_value)
    for i in range(1, 11):
        clean_value = normalize_space(out.get(f"feature{i}", ""))
        if clean_value:
            gathered.append(clean_value)

    gathered = dedupe_preserve_order(gathered)[:max_features]
    out["features"] = gathered
    for i in range(1, 11):
        out[f"feature{i}"] = gathered[i - 1] if i <= max_features and i - 1 < len(gathered) else ""
    return out


def infer_cvs_image_slot_from_url(url):
    url = str(url or "").strip().split("?", 1)[0]
    if not url:
        return None
    name = url.rsplit("/", 1)[-1]
    stem = re.sub(r'\.(jpg|jpeg|png|webp|avif)$', '', name, flags=re.IGNORECASE)
    match = re.search(r'(?:[_\-])(\d{1,2})$', stem)
    if not match:
        match = re.search(r'\((\d{1,2})\)$', stem)
    if match:
        try:
            slot_num = int(match.group(1))
            if 1 <= slot_num <= MAX_IMAGE_SLOTS_TO_COMPARE:
                return slot_num
        except Exception:
            pass
    if re.search(r'\d', stem):
        return 1
    return None

def reorder_cvs_salsify_images_for_visual(images, max_slots=MAX_IMAGE_SLOTS_TO_COMPARE):
    """
    CVS Salsify order for visual QA:
    1. Online/Main image.
    2. Flat Back_2D / back.
    3. Flat Left_2D / side.
    4. ATF I/O-Generic when present.
       - If ATF I/O-Generic is missing, ATF 2 moves up into this slot.
    5+. Continue remaining ATF 2-5 Generic images in order.
    Last. ATF 6-Generic is always kept last and is never used as the ATF I/O fallback.

    Missing slots 1-3 stay blank so later ATF images do not shift up.
    """
    imgs = [img for img in (images or []) if isinstance(img, dict)]

    def norm_name(img):
        return normalize_salsify_asset_name((img or {}).get("name", "")) if isinstance(img, dict) else ""

    def find_first(*queries):
        query_tokens = [normalize_salsify_asset_name(q) for q in queries if normalize_salsify_asset_name(q)]
        for q in query_tokens:
            for img in imgs:
                name = norm_name(img)
                if name and (q == name or q in name):
                    return img
        return None

    def image_key(img):
        return str(img.get("url", "") or "").strip() if isinstance(img, dict) else ""

    ordered = []
    used = set()

    def add(img, blank_name=""):
        if not isinstance(img, dict):
            if blank_name:
                ordered.append(make_blank_salsify_image_slot(blank_name))
            return False
        key = image_key(img)
        if not key or key in used:
            if blank_name:
                ordered.append(make_blank_salsify_image_slot(blank_name))
            return False
        ordered.append(img)
        used.add(key)
        return True

    def add_first_available(query_groups, blank_name=""):
        for query_group in query_groups:
            if add(find_first(*query_group)):
                return True
        if blank_name:
            ordered.append(make_blank_salsify_image_slot(blank_name))
        return False

    add(find_first(
        "online optimized image", "online image", "main variant image", "main image", "hero", "primary", "front", "product image 1", "image 1",
    ), "cvs_slot_1")
    add(find_first(
        "flat back 2d", "flat back", "back 2d", "back", "rear", "product image 2", "image 2",
    ), "cvs_slot_2")
    add(find_first(
        "flat left 2d", "flat left", "left 2d", "left", "flat right 2d", "flat right", "right 2d", "right", "side", "product image 3", "image 3",
    ), "cvs_slot_3")

    io_queries = (("atf i/o generic", "atf i o generic", "atf io generic", "atf i/o-generic", "atf io-generic"),)
    atf2_queries = (("atf 2 generic", "atf 2-generic", "atf2 generic", "atf2-generic"),)
    atf3_queries = (("atf 3 generic", "atf 3-generic", "atf3 generic", "atf3-generic"),)
    atf4_queries = (("atf 4 generic", "atf 4-generic", "atf4 generic", "atf4-generic"),)
    atf5_queries = (("atf 5 generic", "atf 5-generic", "atf5 generic", "atf5-generic"),)
    atf6_queries = (("atf 6 generic", "atf 6-generic", "atf6 generic", "atf6-generic"),)

    # Slot 4: ATF I/O if present; otherwise ATF 2 moves up.
    add_first_available(io_queries + atf2_queries)

    # Continue remaining core ATF images in order. Used URLs are skipped automatically.
    add_first_available(atf2_queries)
    add_first_available(atf3_queries)
    add_first_available(atf4_queries)
    add_first_available(atf5_queries)

    # ATF 6 is always last among CVS ATF assets.
    add_first_available(atf6_queries)

    for img in imgs:
        add(img)

    return ordered[:max_slots]
def reorder_cvs_retailer_images_for_visual(images, max_slots=MAX_IMAGE_SLOTS_TO_COMPARE):
    urls = [str(u or "").strip() for u in (images or []) if str(u or "").strip()]
    if not urls:
        return []
    slotted = {}
    unslotted = []
    seen = set()
    for url in urls:
        base = url.split("?", 1)[0].strip()
        if not base or base in seen:
            continue
        seen.add(base)
        slot_num = infer_cvs_image_slot_from_url(base)
        if slot_num is not None and slot_num not in slotted:
            slotted[slot_num] = base
        else:
            unslotted.append(base)
    ordered = []
    used = set()
    def add_url(v):
        v = str(v or "").strip()
        if not v or v in used:
            return False
        ordered.append(v)
        used.add(v)
        return True
    has_any_explicit_top3 = any(slot in slotted for slot in (1,2,3))
    if has_any_explicit_top3:
        for slot_num in (1,2,3):
            ordered.append(slotted.get(slot_num, ""))
            if slotted.get(slot_num):
                used.add(slotted[slot_num])
    else:
        first_three = urls[:3]
        for i in range(3):
            value = first_three[i] if i < len(first_three) else ""
            ordered.append(value)
            if value:
                used.add(value)
        unslotted = [u for u in urls[3:] if u not in used]
    for slot_num in sorted(k for k in slotted.keys() if k > 3):
        add_url(slotted[slot_num])
    for url in unslotted:
        add_url(url)
    return ordered[:max_slots]

def apply_retailer_salsify_image_limits(retailer_name, images):
    retailer = str(retailer_name or "").strip().lower()
    limits = get_retailer_salsify_requirements(retailer_name)
    max_images = int(limits.get("max_images", MAX_IMAGE_SLOTS_TO_COMPARE) or MAX_IMAGE_SLOTS_TO_COMPARE)
    max_images = max(0, min(max_images, MAX_IMAGE_SLOTS_TO_COMPARE))
    if retailer == "cvs":
        return reorder_cvs_salsify_images_for_visual(images, max_slots=max_images)
    out = []
    seen_urls = set()
    for img in list(images or []):
        if not isinstance(img, dict):
            continue
        url = str(img.get("url", "") or "").strip()
        if not url or url in seen_urls:
            continue
        out.append(img)
        seen_urls.add(url)
        if len(out) >= max_images:
            break
    return out

def prepare_input_df(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    df.rename(
        columns={
            "salsify url": "salsify_url",
            "retail url": "retail_url",
            "sku id": "sku",
            "product sku": "sku",
            "retailer name": "retailer",
            "retailer_name": "retailer",
            "kroger url": "retail_url",
            "kroger_url": "retail_url",
            "kroger rpc": "kroger_rpc",
        },
        inplace=True,
    )

    rpc_candidates = []
    for rpc_col in ["retailer_rpc", "kroger_rpc", "cvs rpc", "walgreens rpc", "sams club rpc"]:
        if rpc_col in df.columns:
            rpc_candidates.append(
                df[rpc_col]
                .replace("#N/A", "")
                .fillna("")
                .astype(str)
                .str.replace(".0", "", regex=False)
                .str.strip()
            )
    if rpc_candidates:
        retailer_rpc = rpc_candidates[0].copy()
        for series in rpc_candidates[1:]:
            retailer_rpc = retailer_rpc.where(retailer_rpc != "", series)
        df["retailer_rpc"] = retailer_rpc
    else:
        df["retailer_rpc"] = ""

    for rpc_col in ["kroger_rpc", "cvs rpc", "walgreens rpc", "sams club rpc"]:
        if rpc_col in df.columns:
            df.drop(columns=[rpc_col], inplace=True)

    for col in ["sku", "salsify_url", "retail_url", "brand", "retailer_rpc", "rating", "review_count"]:
        if col not in df.columns:
            df[col] = ""

    for col in ["sku", "salsify_url", "retail_url", "brand", "retailer_rpc", "rating", "review_count"]:
        df[col] = df[col].replace("#N/A", "").fillna("").astype(str).str.strip()

    if "retailer" not in df.columns:
        df["retailer"] = df["retail_url"].apply(infer_retailer_name_from_url)
    else:
        df["retailer"] = df["retailer"].replace("#N/A", "").fillna("").astype(str).str.strip()
    df["retailer"] = df["retailer"].apply(normalize_retailer_name)

    required = ["sku", "salsify_url", "retail_url"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return df



def strict_filter_rows_for_selected_retailer(df, selected_retailer, dedupe_by_url=False):
    """
    Hard retailer isolation guard.

    This prevents any CVS, Walgreens, Kroger, or Sam's Club rows from leaking into a
    different retailer batch, even if the uploaded file contains multiple retailers.
    """
    selected_retailer_norm = normalize_retailer_name(selected_retailer)
    if df is None:
        return pd.DataFrame()

    out = df.copy()
    if "retailer" not in out.columns:
        out["retailer"] = "Retailer"

    out["retailer"] = out["retailer"].astype(str).apply(normalize_retailer_name)
    out = out[out["retailer"] == selected_retailer_norm].copy()

    if "retail_url" not in out.columns:
        out["retail_url"] = ""

    out["retail_url"] = out["retail_url"].fillna("").astype(str).str.strip()
    out = out[out["retail_url"] != ""].copy()

    if dedupe_by_url and not out.empty:
        out = out.drop_duplicates(subset=["retail_url"], keep="first").copy()

    return out
def clear_in_memory_caches():
    global html_cache, image_hash_cache, image_compare_cache, display_image_cache, walgreens_api_cache

    if "html_cache" not in globals() or not isinstance(globals().get("html_cache"), dict):
        html_cache = {}
    if "image_hash_cache" not in globals() or not isinstance(globals().get("image_hash_cache"), dict):
        image_hash_cache = {}
    if "image_compare_cache" not in globals() or not isinstance(globals().get("image_compare_cache"), dict):
        image_compare_cache = {}
    if "display_image_cache" not in globals() or not isinstance(globals().get("display_image_cache"), dict):
        display_image_cache = {}

    html_cache.clear()
    image_hash_cache.clear()
    image_compare_cache.clear()
    display_image_cache.clear()
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

    cached = html_cache.get(url)
    if cached:
        return cached

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


def parse_debug_headers_text(headers_text):
    """Accept JSON object text or Header: Value lines."""
    raw = str(headers_text or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    headers = {}
    for line in raw.splitlines():
        line = str(line or "").strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = str(key or "").strip()
        value = str(value or "").strip()
        if key:
            headers[key] = value
    return headers


def fetch_url_debug(
    url,
    retailer_name="",
    headers_text="",
    timeout_override=None,
    use_mobile=False,
    proxy_url="",
):
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
        "request_headers_used": {},
        "proxy_used": "",
        "elapsed_seconds": 0.0,
    }

    url = str(url or "").strip()
    retailer_name = str(retailer_name or "").strip().lower()
    proxy_url = str(proxy_url or "").strip()

    if not url:
        result["error"] = "No URL provided."
        return result

    if retailer_name == "kroger":
        try:
            url = normalize_kroger_url(url)
        except Exception:
            pass

    desktop_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    mobile_ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    )

    headers = dict(HEADERS)
    headers.update(parse_debug_headers_text(headers_text))
    headers["User-Agent"] = mobile_ua if use_mobile else desktop_ua
    headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8")
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    headers.setdefault("Cache-Control", "no-cache")
    headers.setdefault("Pragma", "no-cache")
    headers.setdefault("Upgrade-Insecure-Requests", "1")
    result["request_headers_used"] = headers
    result["proxy_used"] = proxy_url

    timeout_value = (6, 30)
    if retailer_name == "walgreens":
        timeout_value = (6, WALGREENS_DEBUG_TIMEOUT)
    elif retailer_name == "kroger":
        timeout_value = (8, 45)

    try:
        if timeout_override is not None and str(timeout_override).strip() != "":
            timeout_override = max(1, int(timeout_override))
            timeout_value = (min(timeout_override, 10), timeout_override)
    except Exception:
        pass

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    start = time.monotonic()
    try:
        session = get_session()
        r = session.get(
            url,
            headers=headers,
            timeout=timeout_value,
            proxies=proxies,
            allow_redirects=True,
        )
        result["elapsed_seconds"] = round(time.monotonic() - start, 3)
        result["final_url"] = str(r.url or "")
        result["status_code"] = int(r.status_code)
        result["reason"] = str(getattr(r, "reason", "") or "")
        result["content_type"] = str(r.headers.get("Content-Type", "") or "")
        result["content_length_header"] = str(r.headers.get("Content-Length", "") or "")
        result["history"] = [{"status_code": int(h.status_code), "url": str(h.url or "")} for h in r.history]
        interesting_headers = ["Content-Type", "Content-Length", "Server", "Cache-Control", "Set-Cookie", "Location", "X-Cache", "X-Served-By", "CF-Cache-Status", "CF-Ray"]
        result["response_headers"] = {k: v for k, v in r.headers.items() if k in interesting_headers}
        raw_html = r.text or ""
        result["raw_html"] = raw_html
        result["text_length"] = len(raw_html)
        result["dom_text"] = html_to_debug_textblob(raw_html)
        result["prettified_dom"] = html_to_prettified_dom(raw_html)
    except Exception as e:
        result["elapsed_seconds"] = round(time.monotonic() - start, 3)
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



def normalize_uploaded_capture_url(url):
    url = str(url or "").strip()
    if not url:
        return ""
    try:
        if "kroger.com" in url.lower():
            url = normalize_kroger_url(url)
    except Exception:
        pass
    url = html.unescape(url)
    url = url.split("#", 1)[0].strip()
    url = re.sub(r"[​-‍﻿]", "", url)
    return url


def uploaded_capture_url_candidates(url):
    normalized = normalize_uploaded_capture_url(url)
    if not normalized:
        return []
    candidates = []
    for candidate in [normalized]:
        if candidate not in candidates:
            candidates.append(candidate)
        no_query = candidate.split("?", 1)[0].strip()
        if no_query and no_query not in candidates:
            candidates.append(no_query)
        lowered = candidate.lower()
        if lowered not in candidates:
            candidates.append(lowered)
        lowered_no_query = no_query.lower() if no_query else ""
        if lowered_no_query and lowered_no_query not in candidates:
            candidates.append(lowered_no_query)
    return candidates


def parse_uploaded_raw_html_map(raw_text):
    raw_text = str(raw_text or "")
    if not raw_text.strip():
        return {}

    html_map = {}
    block_pattern = re.compile(
        r'(?is)Requested\s+URL\s*:\s*(https?://\S+).*?-----BEGIN HTML-----(.*?)-----END HTML-----'
    )

    for match in block_pattern.finditer(raw_text):
        requested_url = str(match.group(1) or "").strip()
        html_text = html.unescape(str(match.group(2) or "").strip())
        if not requested_url or len(html_text) < 30:
            continue
        key = normalize_uploaded_capture_url(requested_url)
        if key:
            html_map[key] = html_text
    return html_map

def lookup_uploaded_raw_html(uploaded_html_map, retail_url, target_rpc=""):
    uploaded_html_map = uploaded_html_map or {}
    retail_url = str(retail_url or "").strip()
    target_rpc = str(target_rpc or "").strip()

    if retail_url and "kroger.com" in retail_url.lower():
        key = normalize_uploaded_capture_url(retail_url)
        html_text = str(uploaded_html_map.get(key, "") or "")
        if html_text:
            return html_text
        matched_key = find_kroger_url_in_uploaded_map(uploaded_html_map, target_rpc=target_rpc)
        if matched_key:
            return str(uploaded_html_map.get(matched_key, "") or "")
        return ""

    if not retail_url and target_rpc:
        matched_key = find_kroger_url_in_uploaded_map(uploaded_html_map, target_rpc=target_rpc)
        if matched_key:
            return str(uploaded_html_map.get(matched_key, "") or "")

    for key in uploaded_capture_url_candidates(retail_url):
        html_text = uploaded_html_map.get(key, "")
        if html_text:
            return html_text
    return ""

def build_extension_batch_payload(retailer_df, retailer_name, current_batch_key, capture_mode, txt_ready=False):
    retailer_name_norm = normalize_retailer_name(retailer_name)
    retailer_df = strict_filter_rows_for_selected_retailer(
        retailer_df,
        retailer_name_norm,
        dedupe_by_url=True,
    )

    retail_urls = []
    row_payload = []
    if retailer_df is not None and not retailer_df.empty:
        retail_urls = [
            str(x).strip()
            for x in retailer_df["retail_url"].fillna("").astype(str).tolist()
            if str(x).strip()
        ]
        row_payload = [
            {
                "sku": str(row.get("sku", "") or "").strip(),
                "retail_url": str(row.get("retail_url", "") or "").strip(),
                "retailer_rpc": str(row.get("retailer_rpc", "") or "").strip(),
                "retailer": retailer_name_norm,
            }
            for _, row in retailer_df.iterrows()
            if str(row.get("retail_url", "") or "").strip()
        ]

    return {
        "ready": True,
        "retailer": retailer_name_norm,
        "retailerGuard": retailer_name_norm,
        "batchKey": str(current_batch_key or ""),
        "captureMode": str(capture_mode or ""),
        "txtReady": bool(txt_ready),
        "totalRows": int(len(retail_urls)),
        "uniqueRetailUrlCount": int(len(retail_urls)),
        "retailUrls": retail_urls,
        "rows": row_payload,
        "timestamp": int(time.time()),
    }


def render_extension_batch_bridge(payload):
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
    retailer_html = html.escape(str((payload or {}).get("retailer", "") or ""), quote=True)
    batch_key_html = html.escape(str((payload or {}).get("batchKey", "") or ""), quote=True)
    capture_mode_html = html.escape(str((payload or {}).get("captureMode", "") or ""), quote=True)
    payload_b64_html = html.escape(payload_b64, quote=True)

    st.markdown(
        f"""
        <div id="pdp-extension-batch-ready"
             data-pdp-extension-batch-ready="1"
             data-pdp-extension-batch-payload-b64="{payload_b64_html}"
             data-pdp-extension-retailer="{retailer_html}"
             data-pdp-extension-batch-key="{batch_key_html}"
             data-pdp-extension-capture-mode="{capture_mode_html}"
             style="display:none !important; visibility:hidden !important; width:0 !important; height:0 !important; max-height:0 !important; overflow:hidden !important; opacity:0 !important; pointer-events:none !important; position:absolute !important; left:-9999px !important; top:-9999px !important;"
        ></div>
        <script id="pdp-extension-batch-json" type="application/json">{payload_json}</script>
        """,
        unsafe_allow_html=True,
    )

    bridge_html = f"""
    <script>
    (function() {{
      const payload = {payload_json};
      const payloadB64 = '{payload_b64}';
      const TARGET_KEYS = [
        '__PDP_EXTENSION_BATCH__', '__RAW_HTML_EXTENSION_BATCH__', '__STREAMLIT_EXTENSION_BATCH__', '__EXTENSION_BATCH__',
        '__RAW_HTML_BATCH__', 'pdpExtensionBatch', 'rawHtmlExtensionBatch', 'streamlitExtensionBatch', 'extensionBatch'
      ];
      const STORAGE_KEYS = [
        '__PDP_EXTENSION_BATCH__', '__RAW_HTML_EXTENSION_BATCH__', '__STREAMLIT_EXTENSION_BATCH__', '__EXTENSION_BATCH__',
        'pdpExtensionBatch', 'rawHtmlExtensionBatch', 'streamlitExtensionBatch', 'extensionBatch'
      ];
      const EVENT_NAMES = [
        'pdp-extension-batch-ready', 'raw-html-extension-batch-ready', 'streamlit-extension-batch-ready',
        'extension-batch-ready', 'PDP_EXTENSION_BATCH_READY'
      ];

      function uniqueTargets() {{
        const out = [];
        for (const candidate of [window, window.parent, window.top]) {{
          try {{ if (candidate && !out.includes(candidate)) out.push(candidate); }} catch (e) {{}}
        }}
        return out;
      }}

      function ensureDomBeacon(targetWindow) {{
        try {{
          const doc = targetWindow.document;
          if (!doc) return;
          const root = doc.documentElement || doc.body;
          const body = doc.body || doc.documentElement;
          const payloadText = JSON.stringify(payload);
          if (root) {{
            root.setAttribute('data-pdp-extension-batch-ready', payload && payload.ready ? '1' : '0');
            root.setAttribute('data-pdp-extension-batch-payload-b64', payloadB64);
            root.setAttribute('data-pdp-extension-retailer', payload && payload.retailer ? String(payload.retailer) : '');
            root.setAttribute('data-pdp-extension-batch-key', payload && payload.batchKey ? String(payload.batchKey) : '');
            root.setAttribute('data-pdp-extension-capture-mode', payload && payload.captureMode ? String(payload.captureMode) : '');
          }}
          if (body) {{
            body.setAttribute('data-pdp-extension-batch-ready', payload && payload.ready ? '1' : '0');
            body.setAttribute('data-pdp-extension-batch-payload-b64', payloadB64);
          }}
          let beacon = doc.getElementById('pdp-extension-batch-ready');
          if (!beacon) {{
            beacon = doc.createElement('div');
            beacon.id = 'pdp-extension-batch-ready';
            beacon.style.display = 'none';
            beacon.style.visibility = 'hidden';
            beacon.style.width = '0';
            beacon.style.height = '0';
            beacon.style.maxHeight = '0';
            beacon.style.overflow = 'hidden';
            beacon.style.opacity = '0';
            beacon.style.pointerEvents = 'none';
            beacon.style.position = 'absolute';
            beacon.style.left = '-9999px';
            beacon.style.top = '-9999px';
            (doc.body || doc.documentElement).appendChild(beacon);
          }}
          beacon.setAttribute('data-pdp-extension-batch-ready', payload && payload.ready ? '1' : '0');
          beacon.setAttribute('data-pdp-extension-batch-payload-b64', payloadB64);
          beacon.setAttribute('data-pdp-extension-retailer', payload && payload.retailer ? String(payload.retailer) : '');
          beacon.setAttribute('data-pdp-extension-batch-key', payload && payload.batchKey ? String(payload.batchKey) : '');
          beacon.setAttribute('data-pdp-extension-capture-mode', payload && payload.captureMode ? String(payload.captureMode) : '');
          let scriptTag = doc.getElementById('pdp-extension-batch-json');
          if (!scriptTag) {{
            scriptTag = doc.createElement('script');
            scriptTag.id = 'pdp-extension-batch-json';
            scriptTag.type = 'application/json';
            (doc.body || doc.documentElement).appendChild(scriptTag);
          }}
          scriptTag.textContent = payloadText;
        }} catch (e) {{}}
      }}

      function publishToTarget(targetWindow) {{
        if (!targetWindow) return;
        try {{ for (const key of TARGET_KEYS) targetWindow[key] = payload; }} catch (e) {{}}
        try {{
          for (const key of STORAGE_KEYS) {{
            try {{ if (targetWindow.localStorage) targetWindow.localStorage.setItem(key, JSON.stringify(payload)); }} catch (e) {{}}
            try {{ if (targetWindow.sessionStorage) targetWindow.sessionStorage.setItem(key, JSON.stringify(payload)); }} catch (e) {{}}
          }}
        }} catch (e) {{}}
        ensureDomBeacon(targetWindow);
        try {{ for (const eventName of EVENT_NAMES) targetWindow.dispatchEvent(new CustomEvent(eventName, {{ detail: payload }})); }} catch (e) {{}}
        try {{
          targetWindow.postMessage({{ type: 'pdp-extension-batch-ready', payload }}, '*');
          targetWindow.postMessage({{ type: 'raw-html-extension-batch-ready', payload }}, '*');
          targetWindow.postMessage({{ type: 'streamlit-extension-batch-ready', payload }}, '*');
          targetWindow.postMessage({{ type: 'extension-batch-ready', payload }}, '*');
          targetWindow.postMessage({{ type: 'PDP_EXTENSION_BATCH_READY', payload }}, '*');
        }} catch (e) {{}}
      }}

      function publishAll() {{ for (const target of uniqueTargets()) publishToTarget(target); }}
      publishAll();
      let publishCount = 0;
      const timer = setInterval(() => {{ publishAll(); publishCount += 1; if (publishCount >= 40) clearInterval(timer); }}, 500);
      try {{
        window.addEventListener('message', function(event) {{
          const data = event && event.data ? event.data : {{}};
          const msgType = data && data.type ? String(data.type) : '';
          if (msgType === 'pdp-extension-batch-request' || msgType === 'raw-html-extension-batch-request' || msgType === 'streamlit-extension-batch-request' || msgType === 'extension-batch-request' || msgType === 'PDP_EXTENSION_BATCH_REQUEST') publishAll();
        }});
      }} catch (e) {{}}
    }})();
    </script>
    """
    components.html(bridge_html, height=0, width=0)

def normalize_kroger_url(url):
    url = str(url or "").strip()
    if not url:
        return ""
    url = html.unescape(url)
    url = url.split("#", 1)[0].strip()
    url = re.sub(r'([?&])msockid=[^&]+', r'\1', url, flags=re.IGNORECASE)
    url = re.sub(r'([?&])searchType=[^&]+', r'\1', url, flags=re.IGNORECASE)
    url = re.sub(r'([?&])fulfillment=[^&]+', r'\1', url, flags=re.IGNORECASE)
    url = re.sub(r'([?&])campaign=[^&]+', r'\1', url, flags=re.IGNORECASE)
    url = re.sub(r'([?&])adgroup=[^&]+', r'\1', url, flags=re.IGNORECASE)
    url = re.sub(r'([?&])pid=[^&]+', r'\1', url, flags=re.IGNORECASE)
    url = re.sub(r'\?&', '?', url)
    url = re.sub(r'[?&]+$', '', url)
    url = re.sub(r'\?{2,}', '?', url)
    return url.strip()

def clean_kroger_rpc(value):
    value = str(value or "").strip()
    value = value.replace(".0", "")
    value = re.sub(r"[^0-9A-Za-z]", "", value)
    return value


def kroger_rpc_candidates(value):
    rpc = clean_kroger_rpc(value)
    if not rpc:
        return []
    out = [rpc]
    if rpc.isdigit() and len(rpc) < 13:
        out.append(rpc.zfill(13))
    if rpc.isdigit() and len(rpc) < 12:
        out.append(rpc.zfill(12))
    deduped = []
    for item in out:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def find_kroger_url_in_uploaded_map(uploaded_html_map, target_rpc=""):
    uploaded_html_map = uploaded_html_map or {}
    rpc_values = kroger_rpc_candidates(target_rpc)
    if not rpc_values:
        return ""
    for key in uploaded_html_map.keys():
        key_str = normalize_uploaded_capture_url(key)
        for rpc in rpc_values:
            if rpc and rpc in key_str:
                return key_str
    return ""

def resolve_debug_views(
    debug_url,
    retailer_name="",
    use_manual_html_override=False,
    manual_html_text="",
    manual_html_file=None,
    headers_text="",
    timeout_override=None,
    use_mobile=False,
    proxy_url="",
):
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

    return fetch_url_debug(
        debug_url,
        retailer_name=retailer_name,
        headers_text=headers_text,
        timeout_override=timeout_override,
        use_mobile=use_mobile,
        proxy_url=proxy_url,
    )


def is_debug_view_robot_page(debug_views):
    raw_html = str(debug_views.get("raw_html", "") or "").lower()
    final_url = str(debug_views.get("final_url", "") or "").lower()
    requested_url = str(debug_views.get("requested_url", "") or "").lower()

    combined = " ".join([raw_html, final_url, requested_url])

    markers = [
        "let us know you're not a robot",
        "let us know you’re not a robot",
        "let us know you're human",
        "let us know you’re human",
        "no robots allowed",
        "captcha",
        "px-captcha",
        "/are-you-human",
        "challenge-platform",
    ]

    return any(marker in combined for marker in markers)
      
def render_debugger_panel(
    debug_views,
    sku="",
    marker_start="",
    marker_end="",
    marker_target="Raw HTML",
    use_manual_html_override=False,
):
    requested_url = str(debug_views.get("requested_url", "") or "")
    final_url = str(debug_views.get("final_url", "") or "")
    status_code = str(debug_views.get("status_code", "") or "")
    reason = str(debug_views.get("reason", "") or "")
    text_length = int(debug_views.get("text_length", 0) or 0)
    history = debug_views.get("history", []) or []
    raw_html = str(debug_views.get("raw_html", "") or "")
    dom_text = str(debug_views.get("dom_text", "") or "")
    prettified_dom = str(debug_views.get("prettified_dom", "") or "")
    error_text = str(debug_views.get("error", "") or "")
    response_headers = debug_views.get("response_headers", {}) or {}
    request_headers_used = debug_views.get("request_headers_used", {}) or {}
    elapsed_seconds = debug_views.get("elapsed_seconds", 0.0)
    proxy_used = str(debug_views.get("proxy_used", "") or "")

    if use_manual_html_override:
        st.success("Using manual HTML override for debugger.")
    elif error_text:
        st.error(f"Debugger fetch error: {error_text}")

    metric_cols = st.columns(6)
    with metric_cols[0]:
        st.caption("Status")
        st.markdown(f"### {status_code if status_code else 'N/A'}")
    with metric_cols[1]:
        st.caption("Reason")
        st.markdown(f"### {reason if reason else 'N/A'}")
    with metric_cols[2]:
        st.caption("Content Length")
        st.markdown(f"### {text_length}")
    with metric_cols[3]:
        st.caption("Redirects")
        st.markdown(f"### {len(history)}")
    with metric_cols[4]:
        st.caption("Elapsed Seconds")
        st.markdown(f"### {elapsed_seconds}")
    with metric_cols[5]:
        st.caption("Final URL Set")
        st.markdown(f"### {'Yes' if final_url else 'No'}")

    st.text_input("Requested URL", value=requested_url, key=f"debug_requested_url_{sku}")
    st.text_input("Final URL", value=final_url, key=f"debug_final_url_{sku}")
    if proxy_used:
        st.text_input("Proxy Used", value=proxy_used, key=f"debug_proxy_used_{sku}")
    if request_headers_used:
        with st.expander("Request headers used"):
            st.json(request_headers_used)
    if response_headers:
        with st.expander("Response headers"):
            st.json(response_headers)

    tab_raw, tab_dom, tab_pretty = st.tabs(["Raw HTML", "DOM Text", "Prettified DOM"])
    with tab_raw:
        st.download_button(
            "Download raw HTML",
            data=raw_html.encode("utf-8"),
            file_name=f"raw_html_{sku or 'debug'}.html",
            mime="text/html",
            key=f"download_raw_html_{sku}",
        )
        st.text_area(f"raw_html_{sku or 'debug'}", value=raw_html, height=1200, key=f"debug_raw_html_{sku}")
    with tab_dom:
        st.text_area(f"dom_text_{sku or 'debug'}", value=dom_text, height=1000, key=f"debug_dom_text_{sku}")
    with tab_pretty:
        st.text_area(f"prettified_dom_{sku or 'debug'}", value=prettified_dom, height=1000, key=f"debug_prettified_dom_{sku}")


# =========================================
# SALSIFY PARSERS
# =========================================


def _normalize_salsify_property_key(value):
    value = normalize_salsify_asset_name(value)
    value = value.replace(" ", "_")
    return value.strip("_")


def _prepend_salsify_property_value(prop_map, prop_name, prop_value):
    normalized_name = _normalize_salsify_property_key(prop_name)
    clean_value = normalize_space(prop_value)
    if not normalized_name or not clean_value:
        return
    values = prop_map.setdefault(normalized_name, [])
    if clean_value in values:
        values.remove(clean_value)
    values.insert(0, clean_value)


def extract_salsify_visible_property_map(html_text):
    result = {"properties": {}, "assets": {}}
    if not html_text:
        return result

    raw_html = html.unescape(str(html_text or ""))
    soup = BeautifulSoup(raw_html, "html.parser")

    def store_asset(label, url):
        normalized_label = normalize_salsify_asset_name(label)
        clean_url = html.unescape(str(url or "").strip())
        if not normalized_label or not clean_url:
            return
        result["assets"][normalized_label] = clean_url if is_video_like_url(clean_url) else clean_url.split("?", 1)[0]

    # Standard 2-column table rows: left cell = property label, right cell = actual value.
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = normalize_space(cells[0].get_text(" ", strip=True))
        if not label:
            continue
        value_cell = cells[1]
        value_text = normalize_space(value_cell.get_text(" ", strip=True))
        if value_text:
            _prepend_salsify_property_value(result["properties"], label, value_text)
        for a in value_cell.find_all("a", href=True):
            href = html.unescape(str(a.get("href", "") or "").strip())
            if href:
                store_asset(label, href)

    # Also support definition-list style blocks.
    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            label = normalize_space(dt.get_text(" ", strip=True))
            value_text = normalize_space(dd.get_text(" ", strip=True))
            if label and value_text:
                _prepend_salsify_property_value(result["properties"], label, value_text)
            for a in dd.find_all("a", href=True):
                href = html.unescape(str(a.get("href", "") or "").strip())
                if label and href:
                    store_asset(label, href)

    # Visible asset label -> href patterns.
    visible_asset_patterns = [
        r'>\s*(Main Variant Image-Club|Online Optimized Image-|Shipping-|ATF I/O-Sams Club|ATF I/O-Generic|ATF Video-Sams Club|ATF [0-9]+-Sams Club)\s*<.*?href="([^"]+)"',
        r'"property"\s*:\s*"(Main Variant Image-Club|Online Optimized Image-|Shipping-|ATF I/O-Sams Club|ATF I/O-Generic|ATF Video-Sams Club|ATF [0-9]+-Sams Club)"[^{}]{0,1200}?"value"\s*:\s*"([^"]+)"',
    ]
    for pattern in visible_asset_patterns:
        for matched_name, matched_url in re.findall(pattern, raw_html, flags=re.IGNORECASE | re.DOTALL):
            store_asset(matched_name, matched_url)

    return result

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
            "feature6": "",
            "feature7": "",
            "features": [],
            "retailer_overrides": {},
        },
        "images": [],
    }
    if not html_text:
        return empty

    soup = BeautifulSoup(html_text, "html.parser")
    visible_property_map = extract_salsify_visible_property_map(html_text)
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    data = {}
    if script:
        try:
            data = json.loads(script.string)
        except Exception:
            data = {}

    def iter_nodes(obj):
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from iter_nodes(value)
        elif isinstance(obj, list):
            for item in obj:
                yield from iter_nodes(item)

    def looks_like_image_url(value):
        value = str(value or "").strip().lower()
        return bool(
            value
            and (value.startswith("http://") or value.startswith("https://"))
            and any(value.endswith(ext) or f"{ext}?" in value for ext in [".jpg", ".jpeg", ".png", ".webp", ".avif"])
        )

    def extract_node_values(node):
        values_out = []
        seen_local = set()

        def add_value(value):
            if not isinstance(value, str):
                return
            clean_value = html.unescape(str(value or "")).strip()
            if not clean_value or looks_like_image_url(clean_value):
                return
            if clean_value not in seen_local:
                seen_local.add(clean_value)
                values_out.append(clean_value)

        def walk_value(value):
            if isinstance(value, str):
                add_value(value)
            elif isinstance(value, dict):
                # Only inspect value-like fields. Do not pull generic property labels
                # such as "Sams Club Product Title" from dict keys like name/title.
                for key in ["value", "label", "displayValue", "text"]:
                    if isinstance(value.get(key), str):
                        add_value(value.get(key))
                for nested_key, nested in value.items():
                    if nested_key in {"property", "name", "key", "title"}:
                        continue
                    if isinstance(nested, (dict, list)):
                        walk_value(nested)
            elif isinstance(value, list):
                for item in value:
                    walk_value(item)

        if not isinstance(node, dict):
            return []

        for key in ["value", "label", "displayValue", "text"]:
            if isinstance(node.get(key), str):
                add_value(node.get(key))

        walk_value(node.get("values", []))
        return values_out

    def normalize_prop_name(value):
        value = normalize_salsify_asset_name(value)
        value = value.replace(" ", "_")
        return value.strip("_")

    property_values = {}
    for node in iter_nodes(data):
        if not isinstance(node, dict):
            continue
        prop_name = node.get("property") or node.get("name") or node.get("key")
        if not isinstance(prop_name, str) or not prop_name.strip():
            continue
        normalized_name = normalize_prop_name(prop_name)
        raw_values = extract_node_values(node)
        if not raw_values:
            continue
        property_values.setdefault(normalized_name, [])
        for raw_value in raw_values:
            if raw_value not in property_values[normalized_name]:
                property_values[normalized_name].append(raw_value)

    def collect_property_values(*keys):
        out = []
        seen = set()
        for key in keys:
            normalized_key = normalize_prop_name(key)
            for value in property_values.get(normalized_key, []) or []:
                clean_value = normalize_space(value)
                if clean_value and clean_value not in seen:
                    seen.add(clean_value)
                    out.append(clean_value)
        return out

    def collect_property_values_loose(*keys):
        out = []
        seen = set()
        normalized_queries = [normalize_prop_name(key) for key in keys if normalize_prop_name(key)]
        for query in normalized_queries:
            for prop_key, values in property_values.items():
                if not prop_key:
                    continue
                if query == prop_key or query in prop_key or prop_key in query:
                    for value in values or []:
                        clean_value = normalize_space(value)
                        if clean_value and clean_value not in seen:
                            seen.add(clean_value)
                            out.append(clean_value)
        return out


    for prop_key, prop_values in (visible_property_map.get("properties", {}) or {}).items():
        if not prop_key or not prop_values:
            continue
        property_values.setdefault(prop_key, [])
        for prop_value in reversed(list(prop_values)):
            clean_value = normalize_space(prop_value)
            if not clean_value:
                continue
            if clean_value in property_values[prop_key]:
                property_values[prop_key].remove(clean_value)
            property_values[prop_key].insert(0, clean_value)

    def first_property(*keys):
        values = collect_property_values(*keys)
        if values:
            return values[0]
        values = collect_property_values_loose(*keys)
        return values[0] if values else ""

    title = first_property(
        "PRODUCT_TITLE",
        "TITLE",
        "PRODUCT NAME",
        "PRODUCT_NAME",
        "NAME",
        "Display Name",
    )
    description = first_property(
        "DESCRIPTION",
        "LONG_DESCRIPTION",
        "PRODUCT_DESCRIPTION",
        "MARKETING_DESCRIPTION",
        "ROMANCE_COPY",
    )

    feature_candidates = []
    for key, values in property_values.items():
        if key.startswith("feature_") or key.startswith("bullet_") or key.startswith("benefit_"):
            for value in values:
                clean_value = normalize_space(value)
                if clean_value:
                    feature_candidates.append(clean_value)

    if not feature_candidates:
        for fallback_key in [
            "FEATURES",
            "BULLETS",
            "BULLET_POINTS",
            "BENEFITS",
            "HIGHLIGHTS",
            "KEY_FEATURES",
        ]:
            values = property_values.get(normalize_prop_name(fallback_key), []) or []
            for value in values:
                if "|" in value:
                    parts = [normalize_space(x) for x in value.split("|")]
                    feature_candidates.extend([x for x in parts if x])
                else:
                    clean_value = normalize_space(value)
                    if clean_value:
                        feature_candidates.append(clean_value)

    feature_candidates = dedupe_preserve_order(feature_candidates)

    if not title:
        meta_title = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "twitter:title"})
        if meta_title and meta_title.get("content"):
            title = normalize_space(meta_title.get("content", ""))
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = normalize_space(h1.get_text(" ", strip=True))
    if not title and soup.title:
        title = normalize_space(soup.title.get_text(" ", strip=True))

    if not description:
        meta_desc = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = normalize_space(meta_desc.get("content", ""))

    # Retailer-specific Salsify fields should override generic deck placeholders when present.
    kroger_feature_values = []
    for i in range(1, 11):
        kroger_feature_values.extend(
            collect_property_values(
                f"Kroger Feature {i}",
                f"Kroger Feature{i}",
                f"Kroger Bullet {i}",
                f"Kroger Bullet{i}",
            )
        )
    kroger_feature_values = [
    (lambda v: re.sub(r'^(kroger\s*feature\s*\d+\s*[:\-]?\s*|feature\s*\d+\s*[:\-]?\s*|\d+\s*[\.\-\)]\s*)', '', normalize_space(v), flags=re.IGNORECASE).strip())(v)
    for v in dedupe_preserve_order(kroger_feature_values)
    if v
]

    sams_feature_values = []
    sams_feature_slots = {}
    for i in range(1, 11):
        exact_values = collect_property_values(
            f"Sam's Club Feature {i}",
            f"Sam's Club Feature{i}",
            f"Sams Club Feature {i}",
            f"Sams Club Feature{i}",
            f"Sam's Club Bullet {i}",
            f"Sam's Club Bullet{i}",
            f"Sams Club Bullet {i}",
            f"Sams Club Bullet{i}",
        )
        loose_values = collect_property_values_loose(
            f"Sam's Club Feature {i}",
            f"Sams Club Feature {i}",
            f"Sam's Club Bullet {i}",
            f"Sams Club Bullet {i}",
        )
        slot_values = dedupe_preserve_order((exact_values or []) + (loose_values or []))
        if slot_values:
            sams_feature_slots[i] = slot_values[0]
            sams_feature_values.extend(slot_values)
    sams_feature_values = dedupe_preserve_order(sams_feature_values)

    cvs_feature_values = []
    cvs_feature_slots = {}
    general_feature_values = []
    for i in range(1, 11):
        cvs_exact_values = collect_property_values(
            f"CVS Feature {i}",
            f"CVS Feature{i}",
            f"CVS Product Feature {i}",
            f"CVS Product Feature{i}",
            f"CVS Selling Point {i}",
            f"CVS Selling Point{i}",
            f"CVS Bullet {i}",
            f"CVS Bullet{i}",
            f"Retailer Feature {i} - CVS",
            f"Retailer Bullet {i} - CVS",
        )
        cvs_loose_values = collect_property_values_loose(
            f"CVS Feature {i}",
            f"CVS Product Feature {i}",
            f"CVS Selling Point {i}",
            f"CVS Bullet {i}",
            f"Retailer Feature {i} - CVS",
            f"Retailer Bullet {i} - CVS",
        )
        cvs_slot_values = dedupe_preserve_order((cvs_exact_values or []) + (cvs_loose_values or []))
        cvs_slot_values = [v for v in cvs_slot_values if v and not is_placeholder_salsify_copy_value(v)]
        if cvs_slot_values:
            cvs_feature_slots[i] = cvs_slot_values[0]
            cvs_feature_values.extend(cvs_slot_values)

        general_feature_values.extend(
            collect_property_values(
                f"General Feature {i}",
                f"General Feature{i}",
                f"General Bullet {i}",
                f"General Bullet{i}",
            )
        )

    if not cvs_feature_values:
        broad_cvs_feature_values = []
        for prop_key, values in property_values.items():
            pk = normalize_space(prop_key).lower().replace('_', ' ')
            if 'cvs' not in pk:
                continue
            if not any(token in pk for token in ['feature', 'bullet', 'selling point', 'benefit', 'highlight']):
                continue
            for value in values or []:
                clean_value = normalize_space(value)
                if clean_value and not is_placeholder_salsify_copy_value(clean_value):
                    broad_cvs_feature_values.append(clean_value)
        cvs_feature_values = dedupe_preserve_order(broad_cvs_feature_values)

    cvs_feature_values = normalize_salsify_feature_values(dedupe_preserve_order(cvs_feature_values), max_features=10)
    general_feature_values = normalize_salsify_feature_values(dedupe_preserve_order(general_feature_values), max_features=10)

    walgreens_feature_values = []
    walgreens_feature_slots = {}
    for i in range(1, 11):
        walgreens_exact_values = collect_property_values(
            f"Walgreens Feature {i}",
            f"Walgreens Feature{i}",
            f"Walgreens Product Feature {i}",
            f"Walgreens Product Feature{i}",
            f"Walgreens Selling Point {i}",
            f"Walgreens Selling Point{i}",
            f"Walgreens Bullet {i}",
            f"Walgreens Bullet{i}",
            f"Retailer Feature {i} - Walgreens",
            f"Retailer Bullet {i} - Walgreens",
        )
        walgreens_loose_values = collect_property_values_loose(
            f"Walgreens Feature {i}",
            f"Walgreens Product Feature {i}",
            f"Walgreens Selling Point {i}",
            f"Walgreens Bullet {i}",
            f"Retailer Feature {i} - Walgreens",
            f"Retailer Bullet {i} - Walgreens",
        )
        walgreens_slot_values = dedupe_preserve_order((walgreens_exact_values or []) + (walgreens_loose_values or []))
        walgreens_slot_values = [v for v in walgreens_slot_values if v and not is_placeholder_salsify_copy_value(v)]
        if walgreens_slot_values:
            walgreens_feature_slots[i] = walgreens_slot_values[0]
            walgreens_feature_values.extend(walgreens_slot_values)

    if not walgreens_feature_values:
        broad_walgreens_feature_values = []
        for prop_key, values in property_values.items():
            pk = normalize_space(prop_key).lower().replace('_', ' ')
            if 'walgreens' not in pk:
                continue
            if not any(token in pk for token in ['feature', 'bullet', 'selling point', 'benefit', 'highlight']):
                continue
            for value in values or []:
                clean_value = normalize_space(value)
                if clean_value and not is_placeholder_salsify_copy_value(clean_value):
                    broad_walgreens_feature_values.append(clean_value)
        walgreens_feature_values = dedupe_preserve_order(broad_walgreens_feature_values)

    walgreens_feature_values = normalize_salsify_feature_values(
        dedupe_preserve_order(walgreens_feature_values),
        max_features=10,
    )


    retailer_overrides = {
        "kroger": {
            "title": first_property("Kroger Product Title", "Kroger Title"),
            "description": first_property("Kroger Description", "Kroger Product Description"),
            "features": kroger_feature_values,
        },
        "sam's club": {
            "title": first_property(
                "Sam's Club Product Title",
                "Sams Club Product Title",
                "Sam's Club Title",
                "Sams Club Title",
            ),
            "description": first_property(
                "Sam's Club Description",
                "Sams Club Description",
                "Sam's Club Product Description",
                "Sams Club Product Description",
            ),
            "features": sams_feature_values,
            "feature_slots": sams_feature_slots,
        },
        "cvs": {
            "title": first_non_placeholder_copy_value(
                first_property("CVS Product Title", "CVS Title", "CVS Product Name"),
                first_property("General Product Title", "General Title", "Product Title"),
            ),
            "description": first_non_placeholder_copy_value(
                first_property("CVS Description", "CVS Product Description", "CVS Long Description"),
                first_property("General Description", "General Product Description", "Description"),
            ),
            "features": normalize_salsify_feature_values(
                cvs_feature_values or general_feature_values,
                max_features=10,
            ),
            "feature_slots": cvs_feature_slots,
        },
        "walgreens": {
            "title": first_non_placeholder_copy_value(
                first_property("Walgreens Product Title", "Walgreens Title", "Walgreens Product Name"),
                first_property("General Product Title", "General Title", "Product Title"),
            ),
            "description": first_non_placeholder_copy_value(
                first_property("Walgreens Description", "Walgreens Product Description", "Walgreens Long Description"),
                first_property("General Description", "General Product Description", "Description"),
            ),
            "features": normalize_salsify_feature_values(
                walgreens_feature_values or general_feature_values,
                max_features=10,
            ),
            "feature_slots": walgreens_feature_slots,
        },
    }

    text_bundle = {
        "title": title,
        "description": description,
        "feature1": feature_candidates[0] if len(feature_candidates) > 0 else "",
        "feature2": feature_candidates[1] if len(feature_candidates) > 1 else "",
        "feature3": feature_candidates[2] if len(feature_candidates) > 2 else "",
        "feature4": feature_candidates[3] if len(feature_candidates) > 3 else "",
        "feature5": feature_candidates[4] if len(feature_candidates) > 4 else "",
        "feature6": feature_candidates[5] if len(feature_candidates) > 5 else "",
        "feature7": feature_candidates[6] if len(feature_candidates) > 6 else "",
        "features": feature_candidates[:10],
        "retailer_overrides": retailer_overrides,
    }

    asset_lookup = {}
    try:
        properties = data["props"]["pageProps"]["product"]["digitalAssets"]["properties"]
        for prop in properties:
            raw_name = prop.get("property", "")
            normalized_name = normalize_salsify_asset_name(raw_name)
            values = prop.get("values", [])
            if not normalized_name or not values:
                continue
            val = ""
            first = values[0]
            if isinstance(first, dict):
                val = str(first.get("value", "") or "")
            elif isinstance(first, str):
                val = str(first or "")
            if not val:
                continue
            asset_lookup[normalized_name] = val.split("?")[0]
    except Exception:
        pass


    for asset_name, asset_url in (visible_property_map.get("assets", {}) or {}).items():
        clean_asset_name = normalize_salsify_asset_name(asset_name)
        clean_asset_url = html.unescape(str(asset_url or "").strip())
        if clean_asset_name and clean_asset_url and clean_asset_name not in asset_lookup:
            asset_lookup[clean_asset_name] = clean_asset_url if is_video_like_url(clean_asset_url) else clean_asset_url.split("?", 1)[0]

    try:
        raw_html_text = html.unescape(str(html_text or ""))
        fallback_asset_patterns = [
            r'>\s*(Main Variant Image-Club|Online Optimized Image-|Shipping-|ATF I/O-Sams Club|ATF I/O-Generic|ATF Video-Sams Club|ATF [0-9]+-Sams Club)\s*<.*?href="([^"]+)"',
            r'"property"\s*:\s*"(Main Variant Image-Club|Online Optimized Image-|Shipping-|ATF I/O-Sams Club|ATF I/O-Generic|ATF Video-Sams Club|ATF [0-9]+-Sams Club)"[^{}]{0,800}?"value"\s*:\s*"([^"]+)"',
        ]
        for pattern in fallback_asset_patterns:
            for matched_name, matched_url in re.findall(pattern, raw_html_text, flags=re.IGNORECASE | re.DOTALL):
                normalized_name = normalize_salsify_asset_name(matched_name)
                clean_url = html.unescape(str(matched_url or "").strip())
                if normalized_name and clean_url and normalized_name not in asset_lookup:
                    asset_lookup[normalized_name] = clean_url.split("?")[0] if not is_video_like_url(clean_url) else clean_url
    except Exception:
        pass

    def pick_kroger_priority_image(asset_lookup):
    priority_order = [
        "online optimized image kroger",
        "online optimized image grocery",
        "online optimized image",
    ]
    best=None
    best_idx=999
    for name,url in asset_lookup.items():
        n=normalize_salsify_asset_name(name)
        u=str(url or "").strip()
        for i,p in enumerate(priority_order):
            if p in n and u:
                if i<best_idx:
                    best_idx=i
                    best={"name":name,"url":u.split("?",1)[0]}
    return [best] if best else []

images = pick_kroger_priority_image(asset_lookup)
    seen_urls = set()
    for asset_name, asset_url in asset_lookup.items():
        clean_url = str(asset_url or "").strip()
        if not clean_url or clean_url in seen_urls:
            continue
        images.append({"name": asset_name, "url": clean_url})
        seen_urls.add(clean_url)

    return {
        "text": text_bundle,
        "images": images[:MAX_IMAGE_SLOTS_TO_COMPARE],
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
        candidate_match_score = int(candidate.get("match_score", 0) or 0)
        if target_rpc and STRICT_CVS_VARIANT_MATCH and candidate_match_score < CVS_VARIANT_MIN_MATCH_SCORE:
            continue

        candidate_debug = debug.copy()
        candidate_debug["variantWindowMatched"] = candidate_match_score > 0
        candidate_debug["variantMatchScore"] = candidate_match_score
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

    if target_rpc and STRICT_CVS_VARIANT_MATCH:
        strict_debug = debug.copy()
        strict_debug["variantWindowMatched"] = False
        strict_debug["variantMatchScore"] = 0
        strict_debug["variantMatchReason"] = "strict_variant_match_required_no_confident_variant"
        strict_debug["Source Used"] = f"{source_name} | strict_variant_match_required" if source_name else "strict_variant_match_required"
        return {"features": [], "description": "", "debug": strict_debug}

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
    matches = re.findall(r'/bizcontent/merchandising/productimages/high_res/[^\s\"]+\.jpg\?[^\"]*', html_text or "")

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

    ordered_urls = [best_images[name]["url"] for name in order]
    return reorder_cvs_retailer_images_for_visual(ordered_urls, max_slots=MAX_IMAGE_SLOTS_TO_COMPARE)


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
# KROGER PARSERS
# =========================================
def clean_kroger_text(text):
    if not text:
        return ""
    text = str(text)
    text = html.unescape(text)
    text = text.replace("\u003c", "<")
    text = text.replace("\u003e", ">")
    text = text.replace("\u0026", "&")
    text = text.replace("\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\"', '"')
    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_kroger_features(items, max_features=10):
    if not items:
        return []
    out = []
    for item in items:
        val = clean_kroger_text(item)
        if val:
            out.append(val)
    return dedupe_preserve_order(out[:max_features])


def extract_kroger_description_and_features_from_html(html_text):
    debug = {
        "description_marker_found": False,
        "description_end_marker_found": False,
        "feature_block_found": False,
        "feature_count": 0,
        "description_excerpt": "",
        "features_excerpt": "",
        "parser_path": "",
    }

    if not html_text:
        return "", [], debug

    working = html.unescape(str(html_text or ""))
    soup = BeautifulSoup(working, "html.parser")
    romance = soup.select_one('[data-testid="product-details-romance-description"]')

    if romance is not None:
        description = ""
        p_tag = romance.find('p')
        if p_tag is not None:
            description = clean_kroger_text(p_tag.get_text(' ', strip=True))
        if not description:
            text_parts = []
            for child in romance.find_all(recursive=False):
                if getattr(child, 'name', None) == 'ul':
                    continue
                child_text = clean_kroger_text(getattr(child, 'get_text', lambda *a, **k: '')(' ', strip=True))
                if child_text:
                    text_parts.append(child_text)
            description = normalize_space(' '.join(text_parts))

        ul_tag = romance.find('ul')
        features = []
        if ul_tag is not None:
            features = normalize_kroger_features([li.get_text(' ', strip=True) for li in ul_tag.find_all('li')], max_features=10)

        debug["description_marker_found"] = bool(description)
        debug["description_end_marker_found"] = bool(description)
        debug["feature_block_found"] = bool(features)
        debug["feature_count"] = len(features)
        debug["description_excerpt"] = description[:500]
        debug["features_excerpt"] = " | ".join(features[:5])[:1000]
        debug["parser_path"] = "kroger_data_testid_romance_div"
        if description or features:
            return description, features, debug

    for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
        raw = (script.string or script.get_text(' ', strip=True) or '').strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if isinstance(node, dict) and str(node.get('@type', '')).lower() == 'product':
                description = clean_kroger_text(node.get('description', ''))
                if description:
                    debug["description_marker_found"] = True
                    debug["description_end_marker_found"] = True
                    debug["description_excerpt"] = description[:500]
                    debug["parser_path"] = "kroger_jsonld_product_description"
                    return description, [], debug
    return "", [], debug

def extract_kroger_text_from_html(html_text, retail_url="", target_rpc=""):
    debug = {
        "Title Path": "",
        "Description Path": "",
        "Features Path": "",
        "Source Used": "uploaded_txt_html",
        "Retailer": "Kroger",
    }

    if not html_text:
        debug["Title Path"] = "kroger_txt_missing"
        debug["Description Path"] = "kroger_txt_missing"
        debug["Features Path"] = "kroger_txt_missing"
        return {"title": "", "description": "", "features": [], "rating": "", "review_count": "", "debug": debug}

    working = html.unescape(str(html_text or ""))
    soup = BeautifulSoup(working, "html.parser")

    title = ""
    h1 = soup.find('h1')
    if h1:
        title = normalize_space(h1.get_text(' ', strip=True))
        debug["Title Path"] = "h1"
    if not title:
        heading_match = re.search(r'(?m)^##\s+(.+?)\s*$', working)
        if heading_match:
            title = normalize_space(heading_match.group(1))
            debug["Title Path"] = "txt_markdown_h2"
    if not title:
        for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
            raw = (script.string or script.get_text(' ', strip=True) or '').strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            nodes = payload if isinstance(payload, list) else [payload]
            for node in nodes:
                if isinstance(node, dict) and str(node.get('@type', '')).lower() == 'product':
                    title = normalize_space(node.get('name', ''))
                    if title:
                        debug["Title Path"] = "kroger_jsonld_product_name"
                        break
            if title:
                break
    if not title and soup.title:
        title = normalize_space(soup.title.get_text(' ', strip=True))
        title = re.sub(r'\s*-\s*Kroger\s*$', '', title, flags=re.IGNORECASE)
        debug["Title Path"] = "html_title"
    if not title:
        debug["Title Path"] = "kroger_title_missing"

    description, features, kroger_debug = extract_kroger_description_and_features_from_html(working)
    debug["Description Path"] = kroger_debug.get("parser_path", "") if description else "kroger_description_missing"
    debug["Features Path"] = kroger_debug.get("parser_path", "") if features else "kroger_features_missing"
    debug["Kroger Parser Debug"] = kroger_debug
    debug["Retail URL Lookup"] = normalize_kroger_url(retail_url)
    if target_rpc:
        debug["Retailer RPC"] = str(target_rpc or "").strip()

    rating = ""
    review_count = ""
    for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
        raw = (script.string or script.get_text(' ', strip=True) or '').strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if isinstance(node, dict) and str(node.get('@type', '')).lower() == 'product':
                agg = node.get('aggregateRating', {})
                if isinstance(agg, dict):
                    rating = str(agg.get('ratingValue', '') or '').strip()
                    review_count = str(agg.get('reviewCount', '') or '').strip()
                if rating or review_count:
                    debug["Rating Path"] = "kroger_jsonld_aggregateRating"
                    break
        if rating or review_count:
            break

    if not rating and not review_count:
        rating_match = re.search(
            r'"aggregateRating"\s*:\s*\{.*?"ratingValue"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
            working,
            flags=re.IGNORECASE | re.DOTALL,
        )
        review_count_match = re.search(
            r'"aggregateRating"\s*:\s*\{.*?"reviewCount"\s*:\s*"?([0-9][0-9,]*)"?',
            working,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if rating_match:
            rating = str(rating_match.group(1) or '').strip()
        if review_count_match:
            review_count = str(review_count_match.group(1) or '').strip()
        if rating or review_count:
            debug["Rating Path"] = "kroger_raw_aggregateRating_regex"

    return {
        "title": title,
        "description": description,
        "features": (features or [])[:10],
        "rating": rating,
        "review_count": review_count,
        "debug": debug,
    }

def _absolutize_kroger_image_url(url):
    url = html.unescape(str(url or "").strip())
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://www.kroger.com" + url
    if not re.match(r'^https?://', url, flags=re.IGNORECASE):
        return ""
    lowered = url.lower()
    if "/product/images/" not in lowered:
        return ""
    url = url.split("?", 1)[0].strip()
    return url


def _extract_kroger_perspective_from_text(text):
    text = normalize_space(text).lower()
    if not text:
        return ""
    for key in ["front", "back", "left", "right", "top", "bottom"]:
        if re.search(rf'{re.escape(key)}', text, flags=re.IGNORECASE):
            return key
    return ""


def _extract_kroger_perspective_from_url(url):
    url = str(url or "")
    m = re.search(r'/product/images/(?:large|medium|small)/([^/]+)/', url, flags=re.IGNORECASE)
    if m:
        return str(m.group(1) or "").strip().lower()
    return ""


def extract_kroger_images_from_html(html_text):
    if not html_text:
        return []

    working = html.unescape(str(html_text or ""))
    soup = BeautifulSoup(working, "html.parser")

    perspective_rank = {
        "front": 0,
        "back": 1,
        "left": 2,
        "right": 3,
        "top": 4,
        "bottom": 5,
    }

    candidates = []
    seen = set()

    def add_candidate(url, slot_index=999, perspective_hint=""):
        clean_url = _absolutize_kroger_image_url(url)
        if not clean_url or clean_url in seen:
            return
        perspective = perspective_hint or _extract_kroger_perspective_from_url(clean_url)
        rank = perspective_rank.get(perspective, 999)
        candidates.append((slot_index, rank, clean_url))
        seen.add(clean_url)

    def choose_single_slide_image(slide):
        """
        Kroger slide 1 often contains both:
        - img.ProductImages-image (real visual slot image)
        - img.iiz__zoom-img (zoom duplicate of the same front image)

        We only want one retailer image per slide, so prefer ProductImages-image
        and skip zoom-image duplicates.
        """
        main_imgs = slide.select('img.ProductImages-image[src]')
        if main_imgs:
            return main_imgs[0]

        # Fallback only if the main class is missing in the captured HTML.
        for img in slide.select('img[src]'):
            class_tokens = [str(x or '').strip().lower() for x in (img.get('class') or [])]
            if any('zoom' in token for token in class_tokens):
                continue
            src = str(img.get('src', '') or '')
            if '/product/images/' not in src.lower():
                continue
            return img
        return None

    # Primary path: use one chosen image per visible slide, in site order.
    slide_nodes = soup.select('[data-testid="main-image-perspective"]')
    for idx, slide in enumerate(slide_nodes):
        aria_label = str(slide.get('aria-label', '') or '')
        perspective_hint = _extract_kroger_perspective_from_text(aria_label)
        chosen_img = choose_single_slide_image(slide)
        if chosen_img is None:
            continue
        src = chosen_img.get('src', '')
        alt = str(chosen_img.get('alt', '') or '')
        img_perspective = perspective_hint or _extract_kroger_perspective_from_text(alt)
        add_candidate(src, slot_index=idx, perspective_hint=img_perspective)

    # Secondary path: if the main slide nodes are missing, use the thumbnail carousel.
    if not candidates:
        thumb_imgs = soup.select('[data-testid="product-thumbnail-carousel"] img[src]')
        for idx, img in enumerate(thumb_imgs):
            src = img.get('src', '')
            alt = str(img.get('alt', '') or '')
            perspective_hint = _extract_kroger_perspective_from_text(alt)
            add_candidate(src, slot_index=idx, perspective_hint=perspective_hint)

    # Final fallback: raw URL regex.
    if not candidates:
        raw_urls = re.findall(
            r'https://www\.kroger\.com/product/images/(?:large|medium|small|thumbnail)/[^\s<>]+',
            working,
            flags=re.IGNORECASE,
        )
        for idx, raw_url in enumerate(raw_urls):
            add_candidate(raw_url, slot_index=idx)

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    ordered_urls = [url for _, _, url in candidates]
    return ordered_urls[:MAX_IMAGE_SLOTS_TO_COMPARE]

@st.cache_data(show_spinner=False)
def get_kroger_bundle(retail_url, target_rpc=""):
    return build_empty_retailer_bundle("Kroger", "kroger_txt_only_no_live_fetch")

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


def extract_walgreens_reviews_from_app_state_payload(payload):
    """
    Pull live Walgreens review fields from app-state payload:
    productData -> prodDetails -> section[] -> reviews
    """
    rating = ""
    review_count = ""

    if not isinstance(payload, dict):
        return rating, review_count

    root = payload
    if isinstance(payload.get("productData"), dict):
        root = payload.get("productData", {})

    prod_details = root.get("prodDetails", {}) if isinstance(root, dict) else {}
    section_list = prod_details.get("section", []) if isinstance(prod_details, dict) else []

    if isinstance(section_list, list):
        for section in section_list:
            if not isinstance(section, dict):
                continue
            reviews_obj = section.get("reviews", {})
            if isinstance(reviews_obj, dict):
                rating = str(reviews_obj.get("overallRating", "") or "").strip()
                review_count = str(reviews_obj.get("reviewCount", "") or "").strip()
                if rating or review_count:
                    return rating, review_count

    return rating, review_count


def extract_walgreens_reviews_from_html(html_text):
    """
    Parse live Walgreens review values from:
    window.__APP_INITIAL_STATE__ = {...};
    """
    if not html_text:
        return "", ""

    html_text = str(html_text or "")
    app_state_match = re.search(
        r'window\.__APP_INITIAL_STATE__\s*=\s*(\{.*?\})\s*;',
        html_text,
        flags=re.DOTALL,
    )
    if not app_state_match:
        return "", ""

    raw_json = app_state_match.group(1)

    try:
        payload = json.loads(raw_json)
    except Exception:
        return "", ""

    return extract_walgreens_reviews_from_app_state_payload(payload)


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

def _collect_walgreens_api_copy_candidates(payload):
    """
    Walk the full Walgreens API payload and collect any likely
    description / feature-bearing fields.
    """
    desc_candidates = []
    feature_candidates = []

    if not payload:
        return desc_candidates, feature_candidates

    likely_desc_keys = {
        "productDesc",
        "description",
        "longDescription",
        "marketingDescription",
        "productDescription",
        "copy",
        "details",
    }

    likely_feature_keys = {
        "features",
        "bullets",
        "bulletPoints",
        "highlights",
        "benefits",
        "featureBullets",
        "vendorDetailsBullets",
    }

    for node in walk_json(payload):
        if not isinstance(node, dict):
            continue

        for key, value in node.items():
            key_str = str(key or "").strip()

            # Description-like keys.
            if key_str in likely_desc_keys:
                if isinstance(value, str) and value.strip():
                    desc_candidates.append(value)
                elif isinstance(value, dict):
                    for sub_k, sub_v in value.items():
                        if isinstance(sub_v, str) and sub_v.strip():
                            desc_candidates.append(sub_v)

            # Feature-like keys.
            if key_str in likely_feature_keys:
                if isinstance(value, list):
                    feature_candidates.append(value)
                elif isinstance(value, str) and value.strip():
                    feature_candidates.append([value])
                elif isinstance(value, dict):
                    sub_items = []
                    for sub_k, sub_v in value.items():
                        if isinstance(sub_v, str) and sub_v.strip():
                            sub_items.append(sub_v)
                        elif isinstance(sub_v, list):
                            sub_items.extend(
                                [x for x in sub_v if isinstance(x, str) and x.strip()]
                            )
                    if sub_items:
                        feature_candidates.append(sub_items)

    return desc_candidates, feature_candidates

def _extract_best_walgreens_copy_from_api_payload(payload):
    """
    Flexible fallback extractor for Walgreens API payloads when
    prodDetails.section does not contain usable copy.
    """
    best_description = ""
    best_features = []

    desc_candidates, feature_candidates = _collect_walgreens_api_copy_candidates(payload)

    for candidate in desc_candidates:
        candidate_clean = clean_walgreens_text(candidate)
        candidate_clean = strip_walgreens_description_tail(candidate_clean)
        best_description = _walgreens_choose_richer_description(
            best_description,
            candidate_clean,
        )

    for candidate_list in feature_candidates:
        cleaned = normalize_walgreens_features_final(candidate_list, max_features=5)
        if _walgreens_feature_richness_tuple(cleaned) > _walgreens_feature_richness_tuple(best_features):
            best_features = cleaned

    return best_description, best_features
    
def build_walgreens_bundle_from_api_payload(payload):
    empty = {
        "text": {
            "title": "",
            "description": "",
            "features": [],
            "rating": "",
            "review_count": "",
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

    live_rating, live_review_count = extract_walgreens_reviews_from_app_state_payload(payload)

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

    # First try the original structured path.
    section_list = prod_details.get("section", []) if isinstance(prod_details, dict) else []
    description, features = extract_walgreens_copy_from_product_sections(section_list)

    desc_path = (
        "walgreens_api_prodDetails_section_description_productDesc_preUL"
        if description else
        "walgreens_api_description_missing"
    )
    feat_path = (
        "walgreens_api_prodDetails_section_description_productDesc_rawLI"
        if features else
        "walgreens_api_features_missing"
    )
    source_used = "walgreens_api"

    # New fallback: scan the full API payload if original path is empty/weak.
    if not description or not features:
        payload_description, payload_features = _extract_best_walgreens_copy_from_api_payload(root)

        if payload_description:
            description = _walgreens_choose_richer_description(description, payload_description)
            desc_path = "walgreens_api_payload_fallback"

        if _walgreens_feature_richness_tuple(payload_features) > _walgreens_feature_richness_tuple(features):
            features = payload_features
            feat_path = "walgreens_api_payload_fallback"

        if payload_description or payload_features:
            source_used = "walgreens_api | walgreens_api_payload_fallback"

    images = extract_walgreens_images_from_product_info(product_info)

    return {
        "text": {
            "title": final_title,
            "description": description,
            "features": features[:5],
            "rating": live_rating,
            "review_count": live_review_count,
            "debug": {
                "Title Path": (
                    "walgreens_api_productInfo_title_plus_sizeCount"
                    if final_title else
                    "walgreens_api_title_missing"
                ),
                "Description Path": desc_path,
                "Features Path": feat_path,
                "Source Used": source_used,
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
    Lightweight Walgreens copy endpoint. Often more reliable than the full PDP HTML for prod... items.
    """
    url = get_walgreens_prod_desc_url(product_id)
    return fetch_html_with_timeout(url, WALGREENS_REQUEST_TIMEOUT)
    
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
        "Source Used": "walgreens_live_html",
    }
    if not html_text:
        return {
            "title": "",
            "description": "",
            "features": [],
            "rating": "",
            "review_count": "",
            "debug": debug,
        }

    title, title_path = _extract_walgreens_title_from_source(html_text)
    description, features, copy_path = _extract_walgreens_description_and_features_from_product_desc(html_text)
    live_rating, live_review_count = extract_walgreens_reviews_from_html(html_text)

    chosen_features = normalize_walgreens_features_final(features, max_features=5)

    debug["Title Path"] = title_path
    debug["Description Path"] = copy_path if description else "walgreens_live_html_description_missing"
    debug["Features Path"] = copy_path if chosen_features else "walgreens_live_html_features_missing"

    return {
        "title": title,
        "description": description,
        "features": chosen_features[:5],
        "rating": live_rating,
        "review_count": live_review_count,
        "debug": debug,
    }

def extract_walgreens_images_from_html(html_text):
    """
    Preserve Walgreens image slot order from HTML.

    IMPORTANT:
    - Capture the unnumbered "largeImageUrl" hero image as slot 1.
    - Then capture numbered keys like largeImageUrl1, largeImageUrl2, etc.
    - If a hero exists, shift numbered slots by +1 so the remaining images
      do not move up and replace the main image.
    - Only keep 450 images.
    """
    if not html_text:
        return []

    slot_candidates = {}
    seen = set()

    def maybe_store(slot_num, url):
        url = _absolutize_walgreens_image_url(url)
        if not url or not _is_walgreens_450_image(url):
            return
        if slot_num not in slot_candidates:
            slot_candidates[slot_num] = url

    # 1. Capture the main unnumbered hero image first.
    hero_match = re.search(
        r'"largeImageUrl"\s*:\s*"((?:\\\\.|[^"\\\\])*)"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    has_main_hero = False
    if hero_match:
        hero_url = _decode_walgreens_json_string(hero_match.group(1))
        maybe_store(1, hero_url)
        has_main_hero = 1 in slot_candidates

    # 2. Capture numbered image keys.
    # If the hero exists, shift numbered slots by +1 so slot 1 stays the hero.
    for m in re.finditer(
        r'"largeImageUrl(\d+)"\s*:\s*"((?:\\\\.|[^"\\\\])*)"',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw_slot_num = int(m.group(1))
        slot_num = raw_slot_num + 1 if has_main_hero else raw_slot_num
        slot_url = _decode_walgreens_json_string(m.group(2))
        maybe_store(slot_num, slot_url)

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
            "rating": "",
            "review_count": "",
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

        if not merged["text"].get("rating") and bundle_text.get("rating"):
            merged["text"]["rating"] = str(bundle_text.get("rating", "") or "").strip()
        if not merged["text"].get("review_count") and bundle_text.get("review_count"):
            merged["text"]["review_count"] = str(bundle_text.get("review_count", "") or "").strip()

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
def get_walgreens_bundle(retail_url, target_rpc="", sku=""):
    """
    Prefer live Walgreens HTML first, but recover copy/images when the live page under-pulls.
    Header values should still come from the richest recovered bundle.
    """
    retail_url = str(retail_url or "").strip()
    retail_url_lc = retail_url.lower()
    product_id = get_walgreens_product_id_from_url(retail_url)

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

    html_bundle = build_html_bundle()

    if "/search/results.jsp" in retail_url_lc:
        if _walgreens_has_copy_or_images(html_bundle):
            return html_bundle
        return build_empty_retailer_bundle("Walgreens", "walgreens_search_results_url_not_pdp")

    fallback_candidates = [html_bundle]

    if _walgreens_bundle_is_rich_enough(html_bundle):
        return html_bundle

    if product_id:
        prod_desc_bundle = build_walgreens_bundle_from_prod_desc_fragment(
            product_id,
            retail_url=retail_url,
        )
        if _walgreens_has_copy_or_images(prod_desc_bundle):
            fallback_candidates.append(prod_desc_bundle)

        api_payload = get_walgreens_product_api_payload(product_id)
        api_bundle = build_walgreens_bundle_from_api_payload(api_payload)
        if _walgreens_has_copy_or_images(api_bundle):
            fallback_candidates.append(api_bundle)

    merged_bundle = merge_walgreens_bundles_prefer_richer_copy(*fallback_candidates)
    if _walgreens_has_copy_or_images(merged_bundle):
        return merged_bundle

    return build_empty_retailer_bundle("Walgreens", "walgreens_live_html_missing")

def is_sams_robot_page(html_text):
    """
    Detect Sam's Club anti-bot / challenge pages so we do not accidentally
    parse footer/help links as product copy.
    """
    if not html_text:
        return False

    text = str(html_text or "").lower()

    robot_markers = [
        "let us know you're not a robot",
        "let us know you’re not a robot",
        "let us know you’re human",
        "let us know you're human",
        "no robots allowed",
        "verify you are human",
        "verify you're human",
        "verify you’re human",
        "press and hold",
        "press & hold",
        "captcha",
        "px-captcha",
        "/akam/",
        "challenge-platform",
        "bot protection",
        "/are-you-human",
    ]

    return any(marker in text for marker in robot_markers)

# =========================================
# SAM'S CLUB PARSERS
# =========================================
def _decode_sams_json_string(raw_value):
    if not raw_value:
        return ""

    raw_value = str(raw_value)

    try:
        decoded = json.loads(f'"{raw_value}"')
    except Exception:
        decoded = raw_value

    decoded = decoded.replace("\\/", "/")
    decoded = html.unescape(decoded)
    return decoded.strip()


def clean_sams_text(text):
    if not text:
        return ""

    text = str(text)
    text = html.unescape(text)
    text = text.replace("\\u003c", "<")
    text = text.replace("\\u003e", ">")
    text = text.replace("\\u0026", "&")
    text = text.replace("\\u00a0", " ")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')

    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)

    return normalize_space(text)


def clean_sams_title(text):
    text = clean_sams_text(text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ", ", text)
    return normalize_space(text)


def normalize_sams_features_final(items, max_features=5):
    if not items:
        return []

    if isinstance(items, str):
        items = [items]

    cleaned = []
    for item in items:
        val = clean_sams_text(item)
        if not val:
            continue
        cleaned.append(val)

    return dedupe_preserve_order(cleaned)[:max_features]


def build_sams_title_from_url_slug(retail_url):
    retail_url = str(retail_url or "").strip()
    if not retail_url:
        return ""

    m = re.search(r"/ip/([^/]+)/", retail_url, flags=re.IGNORECASE)
    if not m:
        return ""

    slug = m.group(1)
    title = slug.replace("-", " ")
    title = html.unescape(title)
    return clean_sams_title(title)


def extract_sams_description_from_long_description_html(long_desc_html):
    if not long_desc_html:
        return ""

    working = str(long_desc_html)
    working = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        working,
        flags=re.IGNORECASE | re.DOTALL,
    )

    soup = BeautifulSoup(working, "html.parser")

    parts = []
    candidates = list(soup.body.children) if soup.body else list(soup.children)

    for node in candidates:
        node_name = getattr(node, "name", None)

        if node_name in {"ul", "ol", "script", "style"}:
            continue

        if hasattr(node, "get_text"):
            node_text = node.get_text(" ", strip=True)
        else:
            node_text = str(node).strip()

        node_text = clean_sams_text(node_text)

        if node_text:
            parts.append(node_text)

    return normalize_space(" ".join(parts))


def extract_sams_features_from_short_description_html(short_desc_html):
    if not short_desc_html:
        return []

    working = str(short_desc_html)
    working = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        working,
        flags=re.IGNORECASE | re.DOTALL,
    )

    soup = BeautifulSoup(working, "html.parser")

    items = []
    for li in soup.find_all("li"):
        li_text = clean_sams_text(li.get_text(" ", strip=True))
        if li_text:
            items.append(li_text)

    if items:
        return normalize_sams_features_final(items, max_features=5)

    fallback_text = clean_sams_text(working)
    if not fallback_text:
        return []

    if " | " in fallback_text:
        parts = [x.strip() for x in fallback_text.split(" | ")]
    elif "•" in fallback_text:
        parts = [x.strip() for x in fallback_text.split("•")]
    else:
        parts = [fallback_text]

    return normalize_sams_features_final(parts, max_features=5)


def _extract_visible_sams_title(source_text):
    if not source_text:
        return ""

    m = re.search(r"##\s+(.+?)\n", source_text)
    if m:
        return clean_sams_title(m.group(1))

    m = re.search(
        r"Hero image 0 of\s+(.+?),\s+0 of\s+\d+",
        source_text,
        flags=re.IGNORECASE,
    )
    if m:
        return clean_sams_title(m.group(1))

    return ""


def _extract_visible_sams_highlights(source_text):
    if not source_text:
        return []

    m = re.search(
        r"###\s+Highlights\s*(.+?)\s*Read more",
        source_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return []

    block = m.group(1)
    bullets = re.findall(r"-\s+(.+?)(?=\s*-\s+|$)", block, flags=re.DOTALL)

    cleaned = [clean_sams_text(x) for x in bullets if clean_sams_text(x)]
    return normalize_sams_features_final(cleaned, max_features=5)




def _extract_visible_sams_product_details(source_text):
    if not source_text:
        return ""

    source_text = str(source_text or "")
    start_match = re.search(r'####\s+Product details\s*\n', source_text, flags=re.IGNORECASE)
    if not start_match:
        return ""

    remainder = source_text[start_match.end():]
    stop_match = re.search(
        r'(?im)^###\s+(Specifications|Member ratings\s*&\s*reviews|Related pages)\b|^Directions:|^Ingredients:|^info:|^Shop more FSA',
        remainder,
    )
    block = remainder[:stop_match.start()] if stop_match else remainder

    lines = []
    for raw_line in block.splitlines():
        line = clean_sams_text(raw_line)
        if not line:
            continue
        if re.match(r'^###\s+', raw_line):
            break
        if line.lower() in {"product details", "about this item"}:
            continue
        if line.lower().startswith("view all reviews"):
            break
        lines.append(line)

    description = normalize_space(" ".join(lines))
    return clean_sams_text(description)


def _extract_visible_sams_rating_and_reviews(source_text):
    if not source_text:
        return "", ""

    source_text = str(source_text or "")
    rating = ""
    review_count = ""
    rating_count = ""

    rating_match = re.search(r'\b([0-9]+(?:\.[0-9]+)?)\s+out of 5\b', source_text, flags=re.IGNORECASE)
    if not rating_match:
        rating_match = re.search(r'\(([0-9]+(?:\.[0-9]+)?)\)\s*\|\s*\[[0-9,]+\s+ratings?\]', source_text, flags=re.IGNORECASE)
    if rating_match:
        rating = str(rating_match.group(1) or "").strip()

    review_matchers = [
        r'View all reviews\s*\(([0-9,]+)\)',
        r'Showing\s+\d+\s*-\s*\d+\s+of\s+([0-9,]+)\s+reviews',
        r'\|\s*\[([0-9,]+)\s+reviews\]',
    ]
    for pattern in review_matchers:
        m_review = re.search(pattern, source_text, flags=re.IGNORECASE)
        if m_review:
            review_count = str(m_review.group(1) or "").replace(",", "").strip()
            break

    rating_count_match = re.search(r'\b([0-9,]+)\s+ratings?\b', source_text, flags=re.IGNORECASE)
    if rating_count_match:
        rating_count = str(rating_count_match.group(1) or "").replace(",", "").strip()

    if not review_count and rating_count:
        review_count = rating_count

    return rating, review_count


def extract_sams_copy_from_source(source_text, retail_url=""):
    """
    Sam's Club copy extraction priority:

    1. JSON-like title:
       "name":"...","personalizable"

    2. JSON-like description:
       "longDescription":"..."

    3. JSON-like features:
       "shortDescription":"..." with:
       - strict quoted pattern first
       - relaxed pattern ending at \\u003c/ul(?:\\u003e)? second

    4. Visible page title fallback:
       ## Product Title
       or Hero image alt

    5. Visible Highlights fallback:
       ### Highlights
       - bullet

    6. Visible Product details fallback:
       #### Product details
       paragraph copy between Product details and Specifications / Reviews.
    """
    debug = {
        "Title Path": "",
        "Description Path": "",
        "Features Path": "",
        "Rating Path": "",
        "Source Used": "sams_raw_source",
    }

    if not source_text:
        return {
            "title": "",
            "description": "",
            "features": [],
            "rating": "",
            "review_count": "",
            "debug": debug,
        }

    source_text = str(source_text)

    title = ""
    name_match = re.search(
        r'"name"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"personalizable"',
        source_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if name_match:
        title = _decode_sams_json_string(name_match.group(1))
        title = clean_sams_title(title)
        debug["Title Path"] = "sams_name_personalizable"

    if not title:
        title = _extract_visible_sams_title(source_text)
        if title:
            debug["Title Path"] = "sams_visible_title_fallback"

    if not title and retail_url:
        title = build_sams_title_from_url_slug(retail_url)
        if title:
            debug["Title Path"] = "retail_url_slug_fallback"

    description = ""
    long_match = re.search(
        r'"longDescription"\s*:\s*"((?:\\.|[^"\\])*)"',
        source_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if long_match:
        long_html = _decode_sams_json_string(long_match.group(1))
        description = extract_sams_description_from_long_description_html(long_html)
        description = clean_sams_text(description)
        if description:
            debug["Description Path"] = "sams_longDescription_html"

    if not description:
        description = _extract_visible_sams_product_details(source_text)
        if description:
            debug["Description Path"] = "sams_visible_product_details_fallback"
        else:
            debug["Description Path"] = "sams_description_missing"

    features = []
    short_match = re.search(
        r'"shortDescription"\s*:\s*"((?:\\.|[^"\\])*)"',
        source_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if short_match:
        short_html = _decode_sams_json_string(short_match.group(1))
        features = extract_sams_features_from_short_description_html(short_html)
        features = normalize_sams_features_final(features, max_features=5)
        if features:
            debug["Features Path"] = "sams_shortDescription_html"

    if not features:
        short_match_relaxed = re.search(
            r'"shortDescription"\s*:\s*"(.*?\\u003c/ul(?:\\u003e)?)',
            source_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if short_match_relaxed:
            short_html = _decode_sams_json_string(short_match_relaxed.group(1))
            features = extract_sams_features_from_short_description_html(short_html)
            features = normalize_sams_features_final(features, max_features=5)
            if features:
                debug["Features Path"] = "sams_shortDescription_relaxed_ul"

    if not features:
        features = _extract_visible_sams_highlights(source_text)
        if features:
            debug["Features Path"] = "sams_visible_highlights_fallback"
        else:
            debug["Features Path"] = "sams_features_missing"

    rating, review_count = _extract_visible_sams_rating_and_reviews(source_text)
    if rating or review_count:
        debug["Rating Path"] = "sams_visible_member_ratings_reviews"
    else:
        debug["Rating Path"] = "sams_rating_reviews_missing"

    return {
        "title": title,
        "description": description,
        "features": features[:5],
        "rating": rating,
        "review_count": review_count,
        "debug": debug,
    }


def _normalize_sams_medium_image_url(url):
    """
    Normalize Sam's image URLs to a clean medium-sized 450 x 450 asset.
    """
    if not url:
        return ""

    url = html.unescape(str(url).strip())

    m = re.search(
        r"(https://i5\.samsclubimages\.com/asr/[^?\"'<>\\s]+\\.jpe?g)",
        url,
        flags=re.IGNORECASE,
    )
    if not m:
        return ""

    base = m.group(1)
    return f"{base}?odnHeight=450&odnWidth=450&odnBg=FFFFFF"




def extract_sams_images_from_html(html_text):
    """
    Pull Sam's Club PDP Images & Videos rail in true onsite slot order.
    Keep image 1, video 2, the other front image 3, then the rest.
    Prefer the actual mp4 for video slots when present in raw HTML.
    """
    if not html_text:
        return []
    working = str(html_text or "")
    for _ in range(3):
        unescaped = html.unescape(working)
        if unescaped == working:
            break
        working = unescaped

    def _base_asr_url(url):
        url = html.unescape(str(url or "").strip())
        m = re.search(r'(https://i5\.samsclubimages\.com/asr/[^?\s"<>]+\.jpe?g)', url, flags=re.IGNORECASE)
        return m.group(1) if m else ""

    def _normalized_medium(url):
        url = html.unescape(str(url or "").strip())
        if not url:
            return ""
        if is_video_like_url(url):
            return url
        base = _base_asr_url(url)
        if base:
            return f"{base}?odnHeight=450&odnWidth=450&odnBg=FFFFFF"
        if re.match(r'^https?://', url, flags=re.IGNORECASE) and re.search(r'\.(?:jpg|jpeg|png|webp|avif)(?:\?|$)', url, flags=re.IGNORECASE):
            return url
        return ""

    def _is_unwanted_alt(alt_text):
        alt_text = normalize_space(alt_text).lower()
        return any(token in alt_text for token in ['customer photos', 'member photos', 'review image', 'related product', 'sponsored'])

    def _extract_slot_num_from_alt(alt_text):
        alt_text = normalize_space(alt_text)
        m = re.search(r'thumbnail\s+image\s+(\d+)\s+of', alt_text, flags=re.IGNORECASE)
        if m:
            return int(m.group(1)), 'image'
        m = re.search(r'thumbnail\s+video\s+image\s+(\d+)\s+of', alt_text, flags=re.IGNORECASE)
        if m:
            return int(m.group(1)), 'video'
        m = re.search(r'thumbnail\s+video\s+(\d+)\s+of', alt_text, flags=re.IGNORECASE)
        if m:
            return int(m.group(1)), 'video'
        return None, ''

    def _first_url_from_srcset(srcset_value):
        srcset_value = str(srcset_value or '').strip()
        if not srcset_value:
            return ''
        first = srcset_value.split(',')[0].strip()
        if not first:
            return ''
        return first.split()[0].strip()

    def _video_urls_from_text(text):
        found = re.findall(r'https?://[^\s"<>]+(?:\.mp4|\.m3u8)(?:\?[^\s"<>]*)?', str(text or ''), flags=re.IGNORECASE)
        out, seen = [], set()
        for url in found:
            clean = html.unescape(str(url or '').strip())
            if clean and clean not in seen:
                seen.add(clean)
                out.append(clean)
        return out

    slot_candidates = {}
    global_video_urls = _video_urls_from_text(working)
    img_tag_pattern = re.compile(r'<img\b[^>]*>', flags=re.IGNORECASE | re.DOTALL)
    for tag_match in img_tag_pattern.finditer(working):
        tag = tag_match.group(0)
        alt_match = re.search(r'alt="([^"]+)"', tag, flags=re.IGNORECASE | re.DOTALL)
        if not alt_match:
            continue
        alt_text = html.unescape(alt_match.group(1) or '')
        if _is_unwanted_alt(alt_text):
            continue
        slot_num, slot_kind = _extract_slot_num_from_alt(alt_text)
        if slot_num is None:
            continue
        src_match = re.search(r'src="([^"]+)"', tag, flags=re.IGNORECASE | re.DOTALL)
        chosen_url = src_match.group(1) if src_match else ''
        if not chosen_url:
            srcset_match = re.search(r'srcset="([^"]+)"', tag, flags=re.IGNORECASE | re.DOTALL)
            if srcset_match:
                chosen_url = _first_url_from_srcset(srcset_match.group(1))
        if not chosen_url:
            data_src_match = re.search(r'data-src="([^"]+)"', tag, flags=re.IGNORECASE | re.DOTALL)
            if data_src_match:
                chosen_url = data_src_match.group(1)
        normalized = _normalized_medium(chosen_url)
        if slot_kind == 'video':
            local_window = working[max(0, tag_match.start() - 1500): min(len(working), tag_match.end() + 5000)]
            local_videos = _video_urls_from_text(local_window)
            if local_videos:
                normalized = local_videos[0]
            elif global_video_urls:
                normalized = global_video_urls[0]
        if normalized and slot_num not in slot_candidates:
            slot_candidates[slot_num] = normalized

    ordered_urls = [slot_candidates[k] for k in sorted(slot_candidates.keys()) if slot_candidates.get(k)]
    if not ordered_urls:
        hero_patterns = [
            r'<img\b[^>]*data-testid="hero-image"[^>]*src="([^"]+)"',
            r'<img\b[^>]*data-seo-id="hero-image"[^>]*src="([^"]+)"',
            r'<img\b[^>]*alt="[^"]*Hero image 0 of[^"]*"[^>]*src="([^"]+)"',
        ]
        for pattern in hero_patterns:
            hero_match = re.search(pattern, working, flags=re.IGNORECASE | re.DOTALL)
            if hero_match:
                normalized = _normalized_medium(hero_match.group(1))
                if normalized:
                    ordered_urls.append(normalized)
                    break
    if not ordered_urls:
        raw_urls = re.findall(r'https://i5\.samsclubimages\.com/asr/[^\s"<>]+', working, flags=re.IGNORECASE)
        for raw in raw_urls:
            normalized = _normalized_medium(raw)
            if normalized:
                ordered_urls.append(normalized)

    out, seen = [], set()
    for url in ordered_urls:
        if is_video_like_url(url):
            key = html.unescape(str(url or '').strip())
            final_url = key
        else:
            key = _base_asr_url(url) or str(url or '').split('?', 1)[0].strip()
            final_url = f"{_base_asr_url(url)}?odnHeight=450&odnWidth=450&odnBg=FFFFFF" if _base_asr_url(url) else str(url or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(final_url)

    if out:
        first_key = (_base_asr_url(out[0]) or str(out[0]).split('?', 1)[0].strip())
        deduped_out, first_seen = [], False
        for url in out:
            current_key = (_base_asr_url(url) or str(url).split('?', 1)[0].strip())
            if current_key == first_key:
                if first_seen:
                    continue
                first_seen = True
            deduped_out.append(url)
        out = deduped_out
    return out[:MAX_IMAGE_SLOTS_TO_COMPARE]

def extract_sams_text_from_html(html_text, retail_url="", target_rpc=""):
    debug = {
        "Title Path": "",
        "Description Path": "",
        "Features Path": "",
        "Rating Path": "",
        "Source Used": "sams_html",
    }

    if not html_text:
        return {
            "title": "",
            "description": "",
            "features": [],
            "rating": "",
            "review_count": "",
            "debug": debug,
        }

    result = extract_sams_copy_from_source(html_text, retail_url=retail_url)
    result_debug = result.get("debug", {}) or {}

    has_meaningful_content = bool(
        clean_sams_title(result.get("title", "")) or
        clean_sams_text(result.get("description", "")) or
        normalize_sams_features_final(result.get("features", []), max_features=5) or
        str(result.get("rating", "") or "").strip() or
        str(result.get("review_count", "") or "").strip()
    )

    if is_sams_robot_page(html_text) and not has_meaningful_content:
        fallback_title = build_sams_title_from_url_slug(retail_url)
        return {
            "title": fallback_title,
            "description": "",
            "features": [],
            "rating": "",
            "review_count": "",
            "debug": {
                "Title Path": "retail_url_slug_fallback" if fallback_title else "sams_robot_page_blocked",
                "Description Path": "sams_robot_page_blocked",
                "Features Path": "sams_robot_page_blocked",
                "Rating Path": "sams_robot_page_blocked",
                "Source Used": "sams_robot_page_blocked",
            },
        }

    result_debug["Source Used"] = "sams_html"
    result["debug"] = result_debug
    return result


@st.cache_data(show_spinner=False)
def get_sams_bundle(retail_url, target_rpc="", sku=""):
    html_text = get_html(retail_url)

    return {
        "text": extract_sams_text_from_html(
            html_text,
            retail_url=retail_url,
            target_rpc=target_rpc,
        ),
        "images": [] if is_sams_robot_page(html_text) else extract_sams_images_from_html(html_text),
    }
    
def get_retailer_bundle(retailer_name, retail_url, target_rpc="", sku="", row_source_code=""):
    retailer = normalize_retailer_name(retailer_name).strip().lower()
    uploaded_html = str(row_source_code or "")

    if retailer == "kroger":
        if uploaded_html.strip():
            bundle = {
                "text": extract_kroger_text_from_html(uploaded_html, retail_url=retail_url, target_rpc=target_rpc),
                "images": extract_kroger_images_from_html(uploaded_html),
            }
            bundle.setdefault("text", {}).setdefault("debug", {})["Source Used"] = "uploaded_txt_html"
            bundle.setdefault("text", {}).setdefault("debug", {})["Image Path"] = "kroger_main_image_perspective"
            return bundle
        return build_empty_retailer_bundle("Kroger", "kroger_txt_required_missing_or_rpc_not_matched")

    if uploaded_html.strip():
        if retailer == "cvs":
            bundle = {"text": _extract_cvs_text_from_html(uploaded_html, retail_url=retail_url, target_rpc=target_rpc), "images": extract_cvs_images_from_html(uploaded_html)}
            bundle.setdefault("text", {}).setdefault("debug", {})["Source Used"] = "uploaded_txt_html"
            return bundle
        if retailer == "walgreens":
            bundle = {"text": extract_walgreens_text_from_html(uploaded_html, retail_url=retail_url, target_rpc=target_rpc), "images": extract_walgreens_images_from_html(uploaded_html)}
            bundle.setdefault("text", {}).setdefault("debug", {})["Source Used"] = "uploaded_txt_html"
            return bundle
        if retailer == "sam's club":
            bundle = {"text": extract_sams_text_from_html(uploaded_html, retail_url=retail_url, target_rpc=target_rpc), "images": extract_sams_images_from_html(uploaded_html)}
            bundle.setdefault("text", {}).setdefault("debug", {})["Source Used"] = "uploaded_txt_html"
            return bundle

    retailer_fetchers = {
        "cvs": lambda: get_cvs_bundle(retail_url, target_rpc),
        "walgreens": lambda: get_walgreens_bundle(retail_url, target_rpc, sku=sku),
        "sam's club": lambda: get_sams_bundle(retail_url, target_rpc, sku=sku),
        "kroger": lambda: get_kroger_bundle(retail_url, target_rpc),
    }
    fetcher = retailer_fetchers.get(retailer)
    if fetcher is None:
        return build_empty_retailer_bundle(retailer_name or "Retailer", "retailer_not_supported_no_default_cvs_fallback")
    return fetcher()

def strip_walgreens_description_tail(text):
    """
    Keep live Walgreens marketing description exactly as shown on site,
    but still remove true legal / utility footer sections.
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

def strip_walgreens_utility_tail(text):
    """
    Removes non-marketing utility/footer copy that should not live in the final description/features.
    IMPORTANT:
    Do NOT remove 'Also check out our ...' cross-sell copy anymore.
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
        "do not flush",
        "to dispose",
        "to use",
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
            r"\s*[:\-])"
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
    text = text.replace("\\u003c", "<")
    text = text.replace("\\u003e", ">")
    text = text.replace("\\u0026", "&")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')

    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)

    text = normalize_space(text)
    text = strip_walgreens_description_tail(text)

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


def _looks_like_walgreens_feature_fragment(text):
    """
    Detect short/incomplete feature fragments that should be merged
    with the next line/bullet.
    """
    text = normalize_space(text)
    if not text:
        return False

    lower = text.lower()

    if lower.endswith((" of", " for", " with", " to", " your", " our", " an")):
        return True

    if text.endswith(":"):
        return True

    if re.match(
        r"^[A-Z0-9&/\-\s\(\)'\"\.]+:\s*\d+\s+count\s+of\s*$",
        text
    ):
        return True

    if len(text) <= 42 and re.match(r"^[A-Z0-9&/\-\s\(\)'\":,.]+$", text):
        return True

    if len(text) <= 60 and text.count(" ") <= 8:
        if any(lower.endswith(x) for x in [" of", " for", " with", " to"]):
            return True

    return False


def _looks_like_walgreens_feature_continuation(text):
    """
    Detect feature lines that are really the continuation of the previous fragment.
    """
    text = normalize_space(text)
    if not text:
        return False

    brand_starts = (
        "Depend",
        "Goodnites",
        "Huggies",
        "Poise",
        "Kotex",
        "Pull-Ups",
        "Pull Ups",
        "Scott",
        "Kleenex",
        "Cottonelle",
        "U by Kotex",
        "U By Kotex",
        "Thinx",
        "Viva",
    )

    if text.startswith(brand_starts):
        return True

    if text[:1].islower():
        return True

    if text.startswith("(") or text.startswith("*"):
        return True

    if re.match(r"^[A-Z][a-z]", text):
        return True

    return False


def merge_walgreens_feature_fragments(items, max_features=5):
    """
    Merge adjacent Walgreens feature fragments like:

    'DEPEND FRESH PROTECTION: 15 count of'
    'Depend Fresh Protection Incontinence Underwear for Men, size extra-large (44-54" waist)'

    into one clean bullet.
    """
    cleaned_items = [
        clean_walgreens_text(x)
        for x in (items or [])
        if clean_walgreens_text(x)
    ]

    merged = []
    i = 0

    while i < len(cleaned_items):
        current = normalize_space(cleaned_items[i])

        if not current or is_walgreens_utility_feature(current):
            i += 1
            continue

        if i + 1 < len(cleaned_items):
            nxt = normalize_space(cleaned_items[i + 1])

            if nxt and not is_walgreens_utility_feature(nxt):
                if _looks_like_walgreens_feature_fragment(current) and _looks_like_walgreens_feature_continuation(nxt):
                    current = normalize_space(f"{current} {nxt}")
                    i += 1

        merged.append(current)
        i += 1

    return dedupe_preserve_order(merged[:max_features])


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

    expanded = dedupe_preserve_order(
        [normalize_space(x) for x in expanded if normalize_space(x)]
    )

    merged = merge_walgreens_feature_fragments(expanded, max_features=max_features)

    out = []
    for item in merged:
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

    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ", ", text)
    text = normalize_space(text)

    return text

def split_walgreens_description_into_features(description_text, existing_features=None, max_features=5):
    """
    If Walgreens description contains feature-style headings like:
    DEPEND FRESH PROTECTION:
    OUTSTANDING ABSORBENCY:
    ODOR CONTROL:
    DRYNESS:
    ACTIVE FIT:
    split those out into feature bullets when the dedicated feature list is empty,
    and keep only the intro paragraph as description.
    """
    description_text = clean_walgreens_text(description_text)
    existing_features = normalize_walgreens_features_final(
        existing_features or [],
        max_features=max_features,
    )

    # If real features already exist, keep them.
    if existing_features:
        return description_text, existing_features

    if not description_text:
        return "", []

    # Strong heading list used to identify feature-style sections inside description text.
    heading_pattern = re.compile(
        r"(?=(?:"
        r"WHAT'S INCLUDED|ALL DAY PROTECTION|UNDERWEAR-LIKE COMFORT|UP TO ZERO ODOR|UNCOMPROMISED COMFORT|"
        r"UNBEATABLE PROTECTION|ODOR CONTROL|DRYNESS|ACTIVE FIT|INSTANT ABSORPTION|FRESHSENSE|GUSHPROTECT ZONE|"
        r"GRAVITY CORE|NIGHTDEFENSE|LEAKSHIELD|DESIGNED FOR MEN|SECURE FIT|FOR LARGE BLADDER LEAKS|"
        r"DEPEND SHIELDS|DEPEND FRESH PROTECTION|OUTSTANDING ABSORBENCY|FRONT AND BACK BLOWOUT BLOCKER|"
        r"UP TO 100% LEAKPROOF|LUXURY SOFTNESS|FASTABSORB SYSTEM|99% WATER|DERMATOLOGIST TESTED|"
        r"NATIONAL ECZEMA ASSOCIATION SEAL OF ACCEPTANCE|THICK AND ABSORBENT|COMPACT COMFORT, POWERFUL PROTECTION|"
        r"#1 COMPACT TAMPON BRAND|GYNECOLOGIST-TESTED|THIN AND SOFT|ALL-NIGHT DRYNESS|NEW! 60% WIDER BACK|"
        r"CLEAN SHIELD|DOUBLE GRIP STRIPS|GENTLEABSORB|QUICKSORB PROTECTION|HYPOALLERGENIC"
        r")\s*[:\-])",
        flags=re.IGNORECASE,
    )

    matches = list(heading_pattern.finditer(description_text))

    # No feature-style headings found.
    if not matches:
        return description_text, []

    # Description = everything before the first heading.
    first_start = matches[0].start()
    trimmed_description = normalize_space(description_text[:first_start])

    # Features = each heading section up to the next heading.
    raw_features = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(description_text)
        chunk = normalize_space(description_text[start:end])
        chunk = strip_walgreens_utility_tail(chunk)

        if not chunk:
            continue
        if is_walgreens_utility_feature(chunk):
            continue

        raw_features.append(chunk)

    cleaned_features = normalize_walgreens_features_final(
        raw_features,
        max_features=max_features,
    )

    return trimmed_description, cleaned_features[:max_features]
    



def is_placeholder_salsify_copy_value(value):
    normalized = normalize_salsify_asset_name(value or "")
    if not normalized:
        return True
    exact_placeholders = {
        "general product title",
        "general title",
        "product title",
        "cvs product title",
        "cvs title",
        "general description",
        "product description",
        "description",
        "cvs description",
        "cvs product description",
        "feature",
        "bullet",
    }
    if normalized in exact_placeholders:
        return True
    if re.fullmatch(r"general feature ?\d+", normalized):
        return True
    if re.fullmatch(r"cvs feature ?\d+", normalized):
        return True
    if re.fullmatch(r"feature ?\d+", normalized):
        return True
    if re.fullmatch(r"general bullet ?\d+", normalized):
        return True
    if re.fullmatch(r"cvs bullet ?\d+", normalized):
        return True
    if re.fullmatch(r"bullet ?\d+", normalized):
        return True
    return False


def first_non_placeholder_copy_value(*values):
    for value in values:
        clean_value = normalize_space(value)
        if clean_value and not is_placeholder_salsify_copy_value(clean_value):
            return clean_value
    for value in values:
        clean_value = normalize_space(value)
        if clean_value:
            return clean_value
    return ""


def normalize_salsify_feature_values(values, max_features=10):
    out = []
    seen = set()
    for value in values or []:
        clean_value = normalize_space(value)
        if not clean_value or is_placeholder_salsify_copy_value(clean_value):
            continue
        if clean_value not in seen:
            seen.add(clean_value)
            out.append(clean_value)
        if len(out) >= max_features:
            break
    return out

def finalize_salsify_copy_for_retailer(retailer_name, s_text):
    """
    Normalize Salsify copy for retailer-specific comparison only.
    Kroger should prefer Kroger Product Title / Kroger Description / Kroger Feature N.
    Sam's Club should prefer Sam's Club Product Title / Description / Feature N.
    CVS should prefer CVS-specific Salsify fields first, then General fields only as fallback.
    """
    retailer = str(retailer_name or "").strip().lower()
    out = dict(s_text or {})

    def generic_feature_list():
        candidates = []
        for i in range(1, 11):
            value = normalize_space(out.get(f"feature{i}", ""))
            if value:
                candidates.append(value)
        for value in out.get("features", []) or []:
            value = normalize_space(value)
            if value:
                candidates.append(value)
        return dedupe_preserve_order(candidates)

    retailer_overrides = out.get("retailer_overrides", {}) or {}

    if retailer == "kroger":
        kroger_override = retailer_overrides.get("kroger", {}) or {}
        selected_title = first_non_placeholder_copy_value(kroger_override.get("title", ""), out.get("title", ""))
        selected_description = clean_kroger_text(first_non_placeholder_copy_value(kroger_override.get("description", ""), out.get("description", "")))
        selected_features = normalize_kroger_features(
            normalize_salsify_feature_values(kroger_override.get("features", []) or generic_feature_list(), max_features=10),
            max_features=10,
        )
        out["title"] = selected_title
        out["description"] = selected_description
        out["features"] = selected_features
        for i in range(1, 8):
            out[f"feature{i}"] = selected_features[i - 1] if i - 1 < len(selected_features) else ""
        return out

    if retailer in {"sam's club", "sams club", "samsclub"}:
        sams_override = retailer_overrides.get("sam's club", {}) or {}
        selected_title = clean_sams_title(first_non_placeholder_copy_value(sams_override.get("title", ""), out.get("title", "")))
        selected_description = clean_sams_text(first_non_placeholder_copy_value(sams_override.get("description", ""), out.get("description", "")))
        override_features = sams_override.get("features", []) or []
        override_feature_slots = sams_override.get("feature_slots", {}) or {}
        generic_features = generic_feature_list()
        selected_features = []
        for i in range(1, 11):
            slot_value = first_non_placeholder_copy_value(override_feature_slots.get(i, ""))
            if slot_value:
                selected_features.append(slot_value)
        if not selected_features:
            selected_features = normalize_sams_features_final(normalize_salsify_feature_values(override_features or generic_features, max_features=10), max_features=10)
        else:
            tail_features = normalize_sams_features_final(normalize_salsify_feature_values(override_features, max_features=10), max_features=10)
            selected_features = dedupe_preserve_order(selected_features + tail_features)[:10]
        out["title"] = selected_title
        out["description"] = selected_description
        out["features"] = selected_features
        for i in range(1, 8):
            out[f"feature{i}"] = selected_features[i - 1] if i - 1 < len(selected_features) else ""
        return out

    if retailer == "cvs":
        cvs_override = retailer_overrides.get("cvs", {}) or {}
        selected_title = first_non_placeholder_copy_value(cvs_override.get("title", ""), out.get("title", ""))
        selected_description = first_non_placeholder_copy_value(cvs_override.get("description", ""), out.get("description", ""))
        override_features = cvs_override.get("features", []) or []
        override_feature_slots = cvs_override.get("feature_slots", {}) or {}
        generic_features = generic_feature_list()
        selected_features = []
        for i in range(1, 11):
            slot_value = first_non_placeholder_copy_value(override_feature_slots.get(i, ""))
            if slot_value:
                selected_features.append(slot_value)
        if not selected_features:
            selected_features = normalize_salsify_feature_values(override_features, max_features=10)
        else:
            tail_features = normalize_salsify_feature_values(override_features, max_features=10)
            selected_features = dedupe_preserve_order(selected_features + tail_features)[:10]
        if not selected_features:
            selected_features = normalize_salsify_feature_values(generic_features, max_features=10)
        out["title"] = selected_title
        out["description"] = selected_description
        out["features"] = selected_features
        for i in range(1, 8):
            out[f"feature{i}"] = selected_features[i - 1] if i - 1 < len(selected_features) else ""
        return out

    if retailer == "walgreens":
        walgreens_override = retailer_overrides.get("walgreens", {}) or {}
        exclusive_mode = retailer in EXCLUSIVE_SALSIFY_COPY_RETAILERS

        if exclusive_mode:
            selected_title = clean_walgreens_title(
                first_non_placeholder_copy_value(walgreens_override.get("title", ""))
            )
            selected_description = strip_walgreens_description_tail(
                first_non_placeholder_copy_value(walgreens_override.get("description", ""))
            )
        else:
            selected_title = clean_walgreens_title(
                first_non_placeholder_copy_value(walgreens_override.get("title", ""), out.get("title", ""))
            )
            selected_description = strip_walgreens_description_tail(
                first_non_placeholder_copy_value(walgreens_override.get("description", ""), out.get("description", ""))
            )

        override_features = walgreens_override.get("features", []) or []
        override_feature_slots = walgreens_override.get("feature_slots", {}) or {}
        generic_features = generic_feature_list()
        selected_features = []
        for i in range(1, 11):
            slot_value = first_non_placeholder_copy_value(override_feature_slots.get(i, ""))
            if slot_value:
                selected_features.append(slot_value)
        if not selected_features:
            source_features = override_features if exclusive_mode else (override_features or generic_features)
            selected_features = normalize_walgreens_features_final(
                normalize_salsify_feature_values(source_features, max_features=10),
                max_features=10,
            )
        else:
            tail_features = normalize_walgreens_features_final(
                normalize_salsify_feature_values(override_features, max_features=10),
                max_features=10,
            )
            selected_features = dedupe_preserve_order(selected_features + tail_features)[:10]
        out["title"] = selected_title
        out["description"] = selected_description
        out["features"] = selected_features
        for i in range(1, 8):
            out[f"feature{i}"] = selected_features[i - 1] if i - 1 < len(selected_features) else ""
        return out

    out["title"] = first_non_placeholder_copy_value(out.get("title", ""))
    out["description"] = first_non_placeholder_copy_value(out.get("description", ""))
    out["features"] = normalize_salsify_feature_values(out.get("features", []) or generic_feature_list(), max_features=10)
    return out


_original_finalize_salsify_copy_for_retailer = finalize_salsify_copy_for_retailer

def finalize_salsify_copy_for_retailer(retailer_name, s_text):
    out = _original_finalize_salsify_copy_for_retailer(retailer_name, s_text)
    return apply_retailer_salsify_copy_limits(retailer_name, out)

def finalize_retailer_copy(retailer_name, r_text):
    retailer = str(retailer_name or "").strip().lower()
    out = dict(r_text or {})

    if retailer == "kroger":
        out["title"] = normalize_space(out.get("title", ""))
        out["description"] = clean_kroger_text(out.get("description", ""))
        out["features"] = normalize_kroger_features(out.get("features", []), max_features=10)
        return out

    if retailer == "walgreens":
        out["title"] = clean_walgreens_title(out.get("title", ""))
        cleaned_description = strip_walgreens_description_tail(out.get("description", ""))
        cleaned_features = normalize_walgreens_features_final(
            out.get("features", []),
            max_features=5,
        )

        cleaned_description, cleaned_features = split_walgreens_description_into_features(
            cleaned_description,
            cleaned_features,
            max_features=5,
        )

        out["description"] = cleaned_description
        out["features"] = cleaned_features
        return out

    if retailer in ["sam's club", "sams club", "samsclub"]:
        out["title"] = clean_sams_title(out.get("title", ""))
        out["description"] = clean_sams_text(out.get("description", ""))
        out["features"] = normalize_sams_features_final(
            out.get("features", []),
            max_features=5,
        )
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
def _compute_dhash_from_pil_image(img, crop_ratio=0.0):
    if img is None:
        return None

    working = img.copy().convert("L")
    width, height = working.size

    if crop_ratio > 0 and width > 20 and height > 20:
        dx = int(width * crop_ratio)
        dy = int(height * crop_ratio)
        if dx * 2 < width and dy * 2 < height:
            working = working.crop((dx, dy, width - dx, height - dy))

    working.thumbnail((256, 256))
    working = working.resize((IMAGE_HASH_WIDTH, IMAGE_HASH_HEIGHT))

    bits = []
    for y in range(IMAGE_HASH_HEIGHT):
        for x in range(IMAGE_HASH_WIDTH - 1):
            left_pixel = working.getpixel((x, y))
            right_pixel = working.getpixel((x + 1, y))
            bits.append(1 if left_pixel > right_pixel else 0)

    h = 0
    for bit in bits:
        h = (h << 1) | bit
    return h


def _cache_image_hash_result(url, value):
    global image_hash_cache
    if "image_hash_cache" not in globals() or not isinstance(globals().get("image_hash_cache"), dict):
        image_hash_cache = {}
    image_hash_cache[url] = value
    while len(image_hash_cache) > IMAGE_HASH_CACHE_MAX:
        image_hash_cache.pop(next(iter(image_hash_cache)))


def _download_image_bytes_once(url, timeout_seconds):
    session = get_session()
    with image_fetch_semaphore:
        r = session.get(url, timeout=timeout_seconds, stream=True)
        if r.status_code != 200:
            return None

        content_type = str(r.headers.get("Content-Type", "") or "")
        if "image" not in content_type.lower():
            return None

        content_length = str(r.headers.get("Content-Length", "") or "").strip()
        if content_length.isdigit() and int(content_length) > MAX_IMAGE_BYTES:
            return None

        image_bytes = bytearray()
        for chunk in r.iter_content(chunk_size=IMAGE_FETCH_CHUNK_SIZE):
            if not chunk:
                continue
            image_bytes.extend(chunk)
            if len(image_bytes) > MAX_IMAGE_BYTES:
                return None

    return bytes(image_bytes) if image_bytes else None


def _download_image_bytes_with_retry(url):
    timeout_plan = [IMAGE_TIMEOUT]
    for _ in range(max(0, int(IMAGE_RETRY_COUNT or 0))):
        timeout_plan.append(IMAGE_RETRY_TIMEOUT)

    for timeout_seconds in timeout_plan:
        try:
            image_bytes = _download_image_bytes_once(url, timeout_seconds)
            if image_bytes:
                return image_bytes
        except Exception:
            continue
    return None


def get_image_dhash(url):
    global image_hash_cache

    if "image_hash_cache" not in globals() or not isinstance(globals().get("image_hash_cache"), dict):
        image_hash_cache = {}

    url = str(url or "").strip()
    if not url:
        return None

    # Cache both successes and failures. A failed image should not be retried dozens
    # of times in the same batch; use Clear caches to force a fresh retry.
    if url in image_hash_cache:
        return image_hash_cache[url]

    try:
        image_bytes = _download_image_bytes_with_retry(url)
        if not image_bytes:
            _cache_image_hash_result(url, None)
            return None

        bio = BytesIO(image_bytes)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(bio)
            width, height = img.size
            if width * height > MAX_SAFE_IMAGE_PIXELS:
                _cache_image_hash_result(url, None)
                return None
            img.load()

        variants = {
            "full": _compute_dhash_from_pil_image(img, crop_ratio=0.0),
            "center_6": _compute_dhash_from_pil_image(img, crop_ratio=0.06),
            "center_12": _compute_dhash_from_pil_image(img, crop_ratio=0.12),
        }

        _cache_image_hash_result(url, variants)
        return variants

    except (Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError):
        _cache_image_hash_result(url, None)
        return None
    except Exception:
        _cache_image_hash_result(url, None)
        return None


def hamming_distance(a, b):
    return bin(a ^ b).count("1")



def compare_images_visually(s_url, r_url):
    global image_compare_cache
    if "image_compare_cache" not in globals() or not isinstance(globals().get("image_compare_cache"), dict):
        image_compare_cache = {}
    if not s_url or not r_url:
        return 0
    s_url_clean = str(s_url or "").split("?", 1)[0].strip()
    r_url_clean = str(r_url or "").split("?", 1)[0].strip()
    cache_key = (str(s_url), str(r_url))
    if cache_key in image_compare_cache:
        return image_compare_cache[cache_key]
    if s_url_clean and r_url_clean and s_url_clean == r_url_clean:
        image_compare_cache[cache_key] = 100
        return 100
    s_is_video = is_video_like_url(s_url)
    r_is_video = is_video_like_url(r_url)
    if s_is_video or r_is_video:
        score = 100 if (s_is_video and r_is_video) else 0
        image_compare_cache[cache_key] = score
        while len(image_compare_cache) > IMAGE_COMPARE_CACHE_MAX:
            image_compare_cache.pop(next(iter(image_compare_cache)))
        return score
    s_hashes = get_image_dhash(s_url)
    r_hashes = get_image_dhash(r_url)
    if not s_hashes or not r_hashes:
        score = 0
    else:
        distances = []
        for key in ["full", "center_6", "center_12"]:
            s_hash = s_hashes.get(key)
            r_hash = r_hashes.get(key)
            if s_hash is None or r_hash is None:
                continue
            distances.append(hamming_distance(s_hash, r_hash))
        if not distances:
            score = 0
        else:
            dist = min(distances)
            if dist <= 2:
                score = 100
            elif dist <= 5:
                score = 95
            elif dist <= 8:
                score = 90
            elif dist <= 12:
                score = 80
            elif dist <= 16:
                score = 70
            elif dist <= 22:
                score = 55
            else:
                score = 30
    image_compare_cache[cache_key] = score
    while len(image_compare_cache) > IMAGE_COMPARE_CACHE_MAX:
        image_compare_cache.pop(next(iter(image_compare_cache)))
    return score



def align_salsify_images_for_retailer(retailer_name, s_images, max_slots=MAX_IMAGE_SLOTS_TO_COMPARE, brand=""):
    """
    Build the retailer-specific Salsify comparison image list.

    Sam's Club rules for Depend, Kotex, U by Kotex, Poise, and Thinx/Thix:
    - Keep ATF Video-Sams Club in spot 2 whenever that asset exists.
    - If Main Variant Image-Club exists, use it in spot 1.
    - If Main Variant Image-Club does not exist, shift up and use Online Optimized Image- in spot 1.
    - Only keep Online Optimized Image- in spot 3 when it is a distinct asset not already used in spot 1.
    - After those slots, continue with Shipping-, ATF I/O, ATF 2-10, then remaining assets.

    CVS rules:
    - Lock only the top 3 Salsify slots.
    - If one of the top 3 is missing, keep the slot blank and do not shift later images up.
    - After slot 3, continue with the remaining Salsify images in original order.
    """
    retailer = str(retailer_name or "").strip().lower()
    brand_norm = normalize_salsify_asset_name(brand or "")
    source_images = list(s_images or [])

    def dedupe_images_preserve_order(images):
        out = []
        seen = set()
        for img in images:
            if not isinstance(img, dict):
                continue
            url = str(img.get("url", "") or "").strip()
            if not url or url in seen:
                continue
            out.append(img)
            seen.add(url)
        return out

    def find_first_image(images, *queries):
        query_tokens = [normalize_salsify_asset_name(q) for q in queries if normalize_salsify_asset_name(q)]
        for query in query_tokens:
            for img in images:
                if not isinstance(img, dict):
                    continue
                name = normalize_salsify_asset_name(img.get("name", ""))
                if name and (query == name or query in name):
                    return img
        return None

    def find_first_retailer_preferred_image(images, retailer_token, *queries, strict=False, excluded_retailer_tokens=None, used_urls=None):
        retailer_token = normalize_salsify_asset_name(retailer_token or "")
        excluded_retailer_tokens = {
            normalize_salsify_asset_name(x)
            for x in (excluded_retailer_tokens or [])
            if normalize_salsify_asset_name(x)
        }
        used_urls = {str(x or "").strip() for x in (used_urls or set()) if str(x or "").strip()}

        preferred = []
        generic_only = []

        for img in images or []:
            if not isinstance(img, dict):
                continue
            url = str(img.get("url", "") or "").strip()
            if used_urls and url in used_urls:
                continue
            name = normalize_salsify_asset_name(img.get("name", ""))
            if retailer_token and retailer_token in name:
                preferred.append(img)
                continue
            if any(token and token in name for token in excluded_retailer_tokens):
                continue
            generic_only.append(img)

        if strict:
            return find_first_image(preferred, *queries)
        return find_first_image(preferred, *queries) or find_first_image(generic_only, *queries)

    if retailer in {"sam's club", "sams club", "samsclub"}:
        sams_brands = {"depend", "kotex", "u by kotex", "poise", "thinx", "thix"}
        if brand_norm in sams_brands:
            aligned = []
            used_urls = set()

            def append_unique(img):
                if not isinstance(img, dict):
                    return False
                url = str(img.get("url", "") or "").strip()
                if not url or url in used_urls:
                    return False
                aligned.append(img)
                used_urls.add(url)
                return True

            def image_name(img):
                return normalize_salsify_asset_name((img or {}).get("name", "")) if isinstance(img, dict) else ""

            mvi_img = find_first_image(source_images, "main variant image club", "main variant image-club")
            video_img = find_first_image(source_images, "atf video sams club", "atf video sam's club", "video sams club")
            ooi_img = find_first_image(source_images, "online optimized image", "online optimized image-", "online image", "online", "front")

            used_ooi_in_slot1 = False
            if not append_unique(mvi_img):
                used_ooi_in_slot1 = append_unique(ooi_img)
                if not used_ooi_in_slot1:
                    aligned.append(make_blank_salsify_image_slot("main variant image club"))

            if video_img:
                append_unique(video_img)

            if ooi_img and not used_ooi_in_slot1:
                append_unique(ooi_img)

            append_unique(find_first_image(source_images, "shipping", "shipping-"))
            append_unique(find_first_image(
                source_images,
                "atf i/o generic", "atf i o generic", "atf io generic", "atf i/o-generic",
                "atf io sams club", "atf i/o sams club", "atf i o sams club", "atf i/o-sams club", "atf io-sams club", "atf io",
            ))
            for slot_num in range(2, 11):
                append_unique(find_first_image(source_images, f"atf {slot_num} sam's club", f"atf {slot_num} sams club"))

            reserved_tokens = [
                "main variant image club", "main variant image-club",
                "atf video sams club", "atf video sam's club", "video sams club",
                "online optimized image", "online optimized image-", "online image",
                "shipping", "shipping-",
                "atf i/o generic", "atf i o generic", "atf io generic", "atf i/o-generic",
                "atf io sams club", "atf i/o sams club", "atf i o sams club", "atf i/o-sams club", "atf io-sams club", "atf io",
            ] + [f"atf {i} sam's club" for i in range(2, 11)] + [f"atf {i} sams club" for i in range(2, 11)]

            for img in source_images:
                if not isinstance(img, dict):
                    continue
                name = image_name(img)
                if any(token in name for token in reserved_tokens):
                    continue
                append_unique(img)

            return aligned[:max_slots]

        return dedupe_images_preserve_order(source_images)[:max_slots]

    if retailer == "cvs":
        return reorder_cvs_salsify_images_for_visual(source_images, max_slots=max_slots)

    if retailer == "walgreens":
        strict_image_mode = retailer in EXCLUSIVE_SALSIFY_IMAGE_RETAILERS
        used_urls = set()
        aligned = []
        excluded_retailer_tokens = {
            "cvs",
            "kroger",
            "sam's club",
            "sams club",
            "samsclub",
            "walmart",
            "target",
            "amazon",
        }

        # Walgreens Salsify rules:
        # 1. Slot 1 must be Online Optimized Image.
        # 2. Slot 2 must be Ingredient Label Image.
        # 3. If either slot is missing, keep that slot blank.
        # 4. ATF images must start only after slots 1 and 2 and never move up.
        slot_plan = [
            (("online optimized image-", "online optimized image", "online image", "online", "front"), "online optimized image", True),
            (("ingredient label image", "ingredients label image", "ingredient label", "ingredients label"), "ingredient label image", True),
            (("atf io", "atf i/o generic", "atf i o generic", "atf io generic"), "atf io", False),
            (("atf 2",), "atf 2", False),
            (("atf 3",), "atf 3", False),
            (("atf 4",), "atf 4", False),
            (("atf 5",), "atf 5", False),
            (("atf 6",), "atf 6", False),
        ]

        for query_group, blank_name, keep_blank in slot_plan:
            img = find_first_retailer_preferred_image(
                source_images,
                "walgreens",
                *query_group,
                strict=strict_image_mode,
                excluded_retailer_tokens=excluded_retailer_tokens,
                used_urls=used_urls,
            )
            if isinstance(img, dict) and str(img.get("url", "") or "").strip():
                aligned.append(img)
                used_urls.add(str(img.get("url", "") or "").strip())
            elif keep_blank:
                aligned.append(make_blank_salsify_image_slot(blank_name))

        return aligned[:min(max_slots, 6)]

    return dedupe_images_preserve_order(source_images)[:max_slots]


_original_align_salsify_images_for_retailer = align_salsify_images_for_retailer

def align_salsify_images_for_retailer(retailer_name, s_images, max_slots=MAX_IMAGE_SLOTS_TO_COMPARE, brand=""):
    aligned = _original_align_salsify_images_for_retailer(retailer_name, s_images, max_slots=max_slots, brand=brand)
    return apply_retailer_salsify_image_limits(retailer_name, aligned)

def align_image_slots_for_comparison(s_images, r_images, max_slots=MAX_IMAGE_SLOTS_TO_COMPARE, strong_threshold=80):
    """
    Preserve the original slot order exactly as captured.

    No image reordering, no sequence alignment, and no slot-shift correction.
    If one side is missing an image in a given position, that slot should stay mismatched
    and score 0% (Poor) instead of being auto-aligned to a later slot.
    """
    s_seq = list(s_images or [])[:max_slots]
    r_seq = list(r_images or [])[:max_slots]
    return trim_trailing_empty_image_slots(s_seq, r_seq)


def get_image_slot_url(slot_value):
    if isinstance(slot_value, dict):
        return str(slot_value.get("url", "") or "").strip()
    return str(slot_value or "").strip()


def trim_trailing_empty_image_slots(s_images, r_images):
    s_images = list(s_images or [])
    r_images = list(r_images or [])

    keep_len = max(len(s_images), len(r_images))
    while keep_len > 0:
        s_url = get_image_slot_url(s_images[keep_len - 1]) if keep_len - 1 < len(s_images) else ""
        r_url = get_image_slot_url(r_images[keep_len - 1]) if keep_len - 1 < len(r_images) else ""

        if s_url or r_url:
            break

        keep_len -= 1

    return s_images[:keep_len], r_images[:keep_len]


def build_image_score_fields(s_images, r_images, max_slots=MAX_IMAGE_SLOTS_TO_SCORE):
    """
    Build per-slot image scores and an average image score.

    Rules:
    - Compare image slots in order.
    - Score = 0 if one side is missing.
    - Only score up to max_slots.
    """
    s_images = s_images or []
    r_images = r_images or []

    slots_to_score = min(max(len(s_images), len(r_images)), max_slots)

    if slots_to_score <= 0:
        return 0, {}

    img_scores = []
    image_position_scores = {}

    for i in range(slots_to_score):
        s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else ""
        r_url = r_images[i] if i < len(r_images) and isinstance(r_images[i], str) else ""

        score = compare_images_visually(s_url, r_url) if (s_url and r_url) else 0

        img_scores.append(score)
        image_position_scores[f"Image {i + 1} %"] = score

    avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0
    return avg_img_score, image_position_scores
    
@st.cache_data(show_spinner=False)
def get_visual_row_payload(
    salsify_url,
    retailer_name,
    retail_url,
    current_target_sku="",
    sku="",
    row_source_code="",
):
    s_bundle = get_salsify_bundle(salsify_url)

    retailer_norm = normalize_retailer_name(retailer_name).strip().lower()
    retail_url = str(retail_url or "").strip()
    row_source_code = str(row_source_code or "")
    uploaded_html_map = st.session_state.uploaded_raw_html_map or {}

    # Visual QA must reuse the same Kroger TXT-matched HTML used in batch processing.
    if retailer_norm == "kroger":
        if not retail_url and current_target_sku:
            retail_url = find_kroger_url_in_uploaded_map(uploaded_html_map, target_rpc=current_target_sku)
        if not row_source_code:
            row_source_code = lookup_uploaded_raw_html(
                uploaded_html_map,
                retail_url,
                target_rpc=current_target_sku,
            )

    r_bundle = get_retailer_bundle(
        retailer_name,
        retail_url,
        current_target_sku,
        sku=sku,
        row_source_code=row_source_code,
    )

    visual_max_slots = MAX_IMAGE_SLOTS_TO_COMPARE
    if str(retailer_name or "").strip().lower() == "walgreens":
        visual_max_slots = 6

    s_text = finalize_salsify_copy_for_retailer(retailer_name, s_bundle["text"] or {})
    if str(retailer_name or "").strip().lower() == "cvs":
        raw_text = dict(s_bundle.get("text", {}) or {})
        raw_override = (raw_text.get("retailer_overrides", {}) or {}).get("cvs", {}) or {}
        rescue_features = []
        rescue_features.extend(raw_override.get("features", []) or [])
        for i in range(1, 11):
            rescue_features.append(raw_text.get(f"feature{i}", ""))
        rescue_features.extend(raw_text.get("features", []) or [])
        rescue_features = normalize_salsify_feature_values(rescue_features, max_features=10)
        if rescue_features:
            s_text["features"] = rescue_features
            for i in range(1, 8):
                s_text[f"feature{i}"] = rescue_features[i - 1] if i - 1 < len(rescue_features) else ""
    s_images = align_salsify_images_for_retailer(
        retailer_name,
        s_bundle["images"],
        max_slots=visual_max_slots,
        brand="",
    )

    r_text = finalize_retailer_copy(retailer_name, r_bundle["text"] or {})
    r_images = r_bundle["images"] or []

    if str(retailer_name or "").strip().lower() == "cvs":
        cvs_max_slots = int(get_retailer_salsify_requirements(retailer_name).get("max_images", MAX_IMAGE_SLOTS_TO_COMPARE) or MAX_IMAGE_SLOTS_TO_COMPARE)
        r_images = reorder_cvs_retailer_images_for_visual(r_images, max_slots=cvs_max_slots)
    elif str(retailer_name or "").strip().lower() == "walgreens":
        r_images = r_images[:6]

    s_images, r_images = align_image_slots_for_comparison(
        s_images,
        r_images,
        max_slots=visual_max_slots,
    )

    return {
        "s_text": s_text,
        "s_images": s_images,
        "r_text": r_text,
        "r_images": r_images,
    }

def process_row(row):
    try:
        retail_url = row.get("retail_url", "")
        salsify_url = row.get("salsify_url", "")
        cvs_rpc = row.get("retailer_rpc", "")
        row_source_code = row.get("copy_source_code", "")
        retailer_name = row.get("retailer", "") or infer_retailer_name_from_url(retail_url)
        rating_value = row.get("rating", "")
        review_count_value = row.get("review_count", "")

        salsify_url = str(salsify_url or "").strip()
        retail_url = str(retail_url or "").strip()
        cvs_rpc = str(cvs_rpc or "").strip()

        if str(retailer_name).strip().lower() == "kroger" and not retail_url and cvs_rpc:
            retail_url = find_kroger_url_in_uploaded_map(st.session_state.uploaded_raw_html_map or {}, target_rpc=cvs_rpc)

        title_score = 0
        desc_score = 0
        avg_feature_score = 0
        avg_img_score = 0
        overall = 0
        feature_score_fields = {}
        image_position_scores = {}

        status_notes = []

        if not salsify_url:
            status_notes.append("Missing Salsify URL")
        if not retail_url:
            status_notes.append("Missing Retail URL")
        if status_notes:
            return {
                "summary": {
                    "SKU": row.get("sku", ""),
                    "Retailer": retailer_name,
                    "Retailer RPC": cvs_rpc,
                    "Brand": row.get("brand", ""),
                    "Salsify URL": salsify_url,
                    "Retail URL": retail_url,
                    "Rating": rating_value,
                    "Review Count": review_count_value,
                    "Title %": title_score,
                    "Description %": desc_score,
                    "Feature %": avg_feature_score,
                    "Image Match %": avg_img_score,
                    "Overall %": overall,
                    "Status": ", ".join(status_notes),
                    **feature_score_fields,
                    **image_position_scores,
                },
                "detail": {
                    "SKU": row.get("sku", ""),
                    "Retailer": retailer_name,
                    "Retailer RPC": cvs_rpc,
                    "Brand": row.get("brand", ""),
                    "Salsify URL": salsify_url,
                    "Retail URL": retail_url,
                    "Rating": rating_value,
                    "Review Count": review_count_value,
                    "Title %": title_score,
                    "Description %": desc_score,
                    "Feature %": avg_feature_score,
                    "Image Match %": avg_img_score,
                    "Overall %": overall,
                    "Status": ", ".join(status_notes),
                    **feature_score_fields,
                    **image_position_scores,
                },
                "debug": {
                    "SKU": row.get("sku", ""),
                    "Retailer": retailer_name,
                    "Retailer RPC": cvs_rpc,
                    "Brand": row.get("brand", ""),
                    "Retail URL": retail_url,
                    "Rating": rating_value,
                    "Review Count": review_count_value,
                    "Salsify URL": salsify_url,
                    "Status": ", ".join(status_notes),
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
            row_source_code=row_source_code,
        )
        if str(retailer_name).strip().lower() == "walgreens":
            print("WAGS SOURCE:", (r_bundle.get("text", {}) or {}).get("debug", {}).get("Source Used", ""))

        s_text = finalize_salsify_copy_for_retailer(
            retailer_name,
            s_bundle["text"] or {},
        )
        s_images = align_salsify_images_for_retailer(
            retailer_name,
            s_bundle["images"],
            max_slots=MAX_IMAGE_SLOTS_TO_SCORE,
            brand=row.get("brand", ""),
        )

        r_text = finalize_retailer_copy(
            retailer_name,
            r_bundle["text"] or {},
        )
        r_images = (r_bundle["images"] or [])[:6] if str(retailer_name or "").strip().lower() == "walgreens" else (r_bundle["images"] or [])
        s_images, r_images = align_image_slots_for_comparison(
            s_images,
            r_images,
            max_slots=MAX_IMAGE_SLOTS_TO_SCORE,
        )

        debug_data = r_text.get("debug", {})

        output_rating_value = (r_text.get("rating", "") if isinstance(r_text, dict) else "") or rating_value
        output_review_count_value = (r_text.get("review_count", "") if isinstance(r_text, dict) else "") or review_count_value

        title_score = keyword_score(s_text.get("title", ""), r_text.get("title", ""))

        s_desc_debug = debug_description(s_text.get("description", ""))
        r_desc_debug = debug_description(r_text.get("description", ""))

        desc_score = description_similarity_score(
            s_text.get("description", ""),
            r_text.get("description", ""),
        )

        retailer_features = r_text.get("features", []) if isinstance(r_text, dict) else []
        retailer_norm = str(retailer_name or "").strip().lower()
        feature_fields = ["feature1", "feature2", "feature3", "feature4", "feature5", "feature6", "feature7"] if retailer_norm == "kroger" else ["feature1", "feature2", "feature3", "feature4", "feature5"]

        feature_scores = []
        feature_score_fields = {}

        for i, f_key in enumerate(feature_fields, start=1):
            s_val = s_text.get(f_key, "")
            r_val = retailer_features[i - 1] if i - 1 < len(retailer_features) else ""

            score = keyword_score(s_val, r_val) if r_val else 0
            feature_scores.append(score)
            feature_score_fields[f"Feature {i} %"] = score

        avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0

        if max(len(s_images), len(r_images)) > 0:
            avg_img_score, image_position_scores = build_image_score_fields(
                s_images,
                r_images,
                max_slots=MAX_IMAGE_SLOTS_TO_SCORE,
            )
        else:
            avg_img_score, image_position_scores = 0, {}
        
        overall = int((title_score + desc_score + avg_feature_score + avg_img_score) / 4)

        return {
            "summary": {
                "SKU": row.get("sku", ""),
                "Retailer": retailer_name,
                "CVS RPC": cvs_rpc,
                "Brand": row.get("brand", ""),
                "Salsify URL": salsify_url,
                "Retail URL": retail_url,
                "Rating": output_rating_value,
                "Review Count": output_review_count_value,
                "Title %": title_score,
                "Description %": desc_score,
                "Feature %": avg_feature_score,
                "Image Match %": avg_img_score,
                "Overall %": overall,
                "Status": ", ".join(status_notes),
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
                "Rating": output_rating_value,
                "Review Count": output_review_count_value,
                "Title %": title_score,
                "Description %": desc_score,
                "Feature %": avg_feature_score,
                "Image Match %": avg_img_score,
                "Overall %": overall,
                "Status": "",
                "Salsify Title": s_text.get("title", ""),
                "Retailer Title": r_text.get("title", ""),
                    "CVS Title": r_text.get("title", ""),
                "Salsify Description": s_text.get("description", ""),
                "Retailer Description": r_text.get("description", ""),
                    "CVS Description": r_text.get("description", ""),
                "Salsify Feature 1": s_text.get("feature1", ""),
                "Salsify Feature 2": s_text.get("feature2", ""),
                "Salsify Feature 3": s_text.get("feature3", ""),
                "Salsify Feature 4": s_text.get("feature4", ""),
                "Salsify Feature 5": s_text.get("feature5", ""),
                "Salsify Feature 6": s_text.get("feature6", ""),
                "Salsify Feature 7": s_text.get("feature7", ""),
                "Retailer Features": " | ".join(r_text.get("features", [])),
                    "CVS Features": " | ".join(r_text.get("features", [])),
                "Salsify Images": " | ".join([img.get("url", "") for img in s_images if isinstance(img, dict)]),
                "Retailer Images": " | ".join(r_images),
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
                **image_position_scores,
            },
            "debug": {
                "SKU": row.get("sku", ""),
                "Retailer": retailer_name,
                "CVS RPC": cvs_rpc,
                "Brand": row.get("brand", ""),
                "Retail URL": retail_url,
                "Rating": output_rating_value,
                "Review Count": output_review_count_value,
                "Salsify URL": salsify_url,
                "Retailer Title": r_text.get("title", ""),
                    "Retailer Description": r_text.get("description", ""),
                    "Retailer Features": " | ".join(r_text.get("features", [])),
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
if "batch_run_requested" not in st.session_state:
    st.session_state.batch_run_requested = False
if "batch_started_key" not in st.session_state:
    st.session_state.batch_started_key = ""
if "batch_status_message" not in st.session_state:
    st.session_state.batch_status_message = ""
if "batch_error_text" not in st.session_state:
    st.session_state.batch_error_text = ""
if "capture_mode" not in st.session_state:
    st.session_state.capture_mode = CAPTURE_MODE_USE_EXTENSION
if "uploaded_raw_html_map" not in st.session_state:
    st.session_state.uploaded_raw_html_map = {}
if "uploaded_raw_html_filename" not in st.session_state:
    st.session_state.uploaded_raw_html_filename = ""
if "raw_html_upload_hash" not in st.session_state:
    st.session_state.raw_html_upload_hash = ""
if "auto_batch_upload_key" not in st.session_state:
    st.session_state.auto_batch_upload_key = ""

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
            label="⬇ Download Excel Report",
            data=st.session_state.report_bytes,
            file_name=st.session_state.report_filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_excel_report_top_single",
        )

master_df = None
retailer_df = None
all_retailers = []
multi_retailer = False
selected_retailer = ""
current_batch_key = ""
file_hash = ""
file_ready_for_batch = False
selected_capture_mode = st.session_state.capture_mode
uploaded_raw_html_file = None
uploaded_raw_html_map = st.session_state.uploaded_raw_html_map or {}
matched_uploaded_html_count = 0
missing_uploaded_html_count = 0
capture_batch_key_part = "no_txt"

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
            st.session_state.batch_run_requested = False
            st.session_state.batch_started_key = ""
            st.session_state.batch_status_message = ""
            st.session_state.batch_error_text = ""
            st.session_state.capture_mode = CAPTURE_MODE_USE_EXTENSION
            st.session_state.uploaded_raw_html_map = {}
            st.session_state.uploaded_raw_html_filename = ""
            st.session_state.raw_html_upload_hash = ""
            st.session_state.auto_batch_upload_key = ""
            clear_in_memory_caches()
            st.cache_data.clear()

        master_df = read_uploaded_file_from_bytes(file_bytes, uploaded_file.name)
        master_df = prepare_input_df(master_df)
        all_retailers = sorted(master_df["retailer"].dropna().astype(str).unique().tolist()) if "retailer" in master_df.columns else ["CVS"]
        if not all_retailers:
            all_retailers = ["CVS"]
        multi_retailer = len(all_retailers) > 1

        with top_upload_col:
            st.caption("Detected retailers in upload: " + ", ".join(all_retailers))

        with top_upload_col:
            st.radio(
                "⚙️ Capture Mode",
                [CAPTURE_MODE_USE_EXTENSION, CAPTURE_MODE_SKIP_EXTENSION],
                key="capture_mode",
                horizontal=True,
                help="Use the browser extension + TXT upload, or skip extension and run directly for supported retailers.",
            )
            selected_capture_mode = st.session_state.capture_mode

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
                    key="selected_retailer",
                    disabled=True,
                )
                file_ready_for_batch = True

            uploaded_raw_html_file = st.file_uploader(
                "Upload Captured Retailer HTML TXT",
                type=["txt", "html"],
                key="uploaded_raw_html_txt_top",
                help="If you ran the extension and downloaded the TXT, upload it here so batch can run from the captured retailer HTML.",
            )
            if uploaded_raw_html_file is not None:
                raw_html_bytes = uploaded_raw_html_file.getvalue()
                raw_html_hash = hashlib.md5(raw_html_bytes or b"").hexdigest()
                if st.session_state.raw_html_upload_hash != raw_html_hash:
                    raw_html_text = get_uploaded_text_file_bytes(uploaded_raw_html_file)
                    st.session_state.uploaded_raw_html_map = parse_uploaded_raw_html_map(raw_html_text)
                    st.session_state.uploaded_raw_html_filename = uploaded_raw_html_file.name
                    st.session_state.raw_html_upload_hash = raw_html_hash
                    st.session_state.auto_batch_upload_key = ""
                    st.session_state.batch_error_text = ""
                uploaded_raw_html_map = st.session_state.uploaded_raw_html_map or {}
                if uploaded_raw_html_map:
                    st.success(f"Loaded TXT capture map from {st.session_state.uploaded_raw_html_filename} with {len(uploaded_raw_html_map)} URL keys.")
                else:
                    st.warning("TXT uploaded, but no labeled URL + HTML blocks were found yet. If needed, keep using the extension and re-download the TXT.")
            else:
                uploaded_raw_html_map = st.session_state.uploaded_raw_html_map or {}

        if file_ready_for_batch:
            capture_batch_key_part = "use_ext" if selected_capture_mode == CAPTURE_MODE_USE_EXTENSION else "skip_ext"
            if st.session_state.raw_html_upload_hash:
                capture_batch_key_part += f"::{st.session_state.raw_html_upload_hash}"
            retailer_df = strict_filter_rows_for_selected_retailer(
                master_df,
                selected_retailer,
                dedupe_by_url=(selected_capture_mode == CAPTURE_MODE_USE_EXTENSION),
            )

            if selected_retailer == "Kroger":
                retailer_df = retailer_df.copy()
                retailer_df["retail_url"] = retailer_df["retail_url"].fillna("").astype(str).str.strip()
                if uploaded_raw_html_map and "retailer_rpc" in retailer_df.columns:
                    retailer_df["retail_url"] = retailer_df.apply(
                        lambda row: row["retail_url"] if str(row.get("retail_url", "")).strip() else find_kroger_url_in_uploaded_map(uploaded_raw_html_map, target_rpc=row.get("retailer_rpc", "")),
                        axis=1,
                    )
                retailer_df = strict_filter_rows_for_selected_retailer(
                    retailer_df,
                    selected_retailer,
                    dedupe_by_url=(selected_capture_mode == CAPTURE_MODE_USE_EXTENSION),
                )

            if "copy_source_code" not in retailer_df.columns:
                retailer_df["copy_source_code"] = ""
            if uploaded_raw_html_map:
                retailer_df["copy_source_code"] = retailer_df.apply(lambda row: lookup_uploaded_raw_html(uploaded_raw_html_map, row.get("retail_url", ""), target_rpc=row.get("retailer_rpc", "")), axis=1)
                matched_uploaded_html_count = int((retailer_df["copy_source_code"].astype(str).str.len() > 0).sum())
                missing_uploaded_html_count = max(len(retailer_df) - matched_uploaded_html_count, 0)
            current_batch_key = f"{file_hash}::{selected_retailer}::{capture_batch_key_part}"

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
                st.session_state.batch_run_requested = False
                st.session_state.batch_started_key = ""
                st.session_state.batch_status_message = ""
                st.session_state.batch_error_text = ""
                st.session_state.auto_batch_upload_key = ""

            txt_ready_for_batch = bool(matched_uploaded_html_count > 0)
            isolated_unique_url_count = int(retailer_df["retail_url"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()) if retailer_df is not None and not retailer_df.empty and "retail_url" in retailer_df.columns else 0
            st.caption(f"Strict retailer isolation active: {selected_retailer} only. Rows queued: {len(retailer_df)}. Unique retailer URLs queued: {isolated_unique_url_count}.")
            if selected_capture_mode == CAPTURE_MODE_USE_EXTENSION:
                extension_payload = build_extension_batch_payload(
                    retailer_df=retailer_df,
                    retailer_name=selected_retailer,
                    current_batch_key=current_batch_key,
                    capture_mode=selected_capture_mode,
                    txt_ready=txt_ready_for_batch,
                )
                render_extension_batch_bridge(extension_payload)
                st.caption(f"Extension bridge ready for {selected_retailer}. For Kroger, the app can now connect Kroger RPC to the matching Requested URL in the TXT file, fill retail_url from that match, and use that matched retail_url for lookup and display.")
            elif selected_retailer in AUTO_SKIP_EXTENSION_RETAILERS:
                st.caption(f"{selected_retailer} is in skip-extension mode, so the app can auto-run straight to batch with live retailer fetches.")

            if uploaded_raw_html_map:
                st.caption(f"TXT match status for {selected_retailer}: {matched_uploaded_html_count} matched rows, {missing_uploaded_html_count} unmatched rows.")

            should_auto_run = False
            auto_run_reason = ""
            if selected_capture_mode == CAPTURE_MODE_USE_EXTENSION and txt_ready_for_batch:
                should_auto_run = True
                auto_run_reason = "uploaded TXT capture"
            elif selected_capture_mode == CAPTURE_MODE_SKIP_EXTENSION and selected_retailer in AUTO_SKIP_EXTENSION_RETAILERS:
                should_auto_run = True
                auto_run_reason = "skip-extension direct batch"

            if (
                should_auto_run
                and st.session_state.auto_batch_upload_key != current_batch_key
                and st.session_state.batch_started_key != current_batch_key
                and not st.session_state.processing_done
            ):
                st.session_state.batch_run_requested = True
                st.session_state.batch_started_key = current_batch_key
                st.session_state.batch_status_message = f"Auto-starting batch for {selected_retailer} using {auto_run_reason}."
                st.session_state.batch_error_text = ""
                st.session_state.completed_batch_key = ""
                st.session_state.start_idx = 0
                st.session_state.summary_rows = []
                st.session_state.export_rows = []
                st.session_state.debug_rows = []
                st.session_state.summary_skus = set()
                st.session_state.detail_skus = set()
                st.session_state.debug_skus = set()
                st.session_state.progress_bar = None
                st.session_state.report_bytes = None
                st.session_state.report_filename = None
                st.session_state.report_batch_key = ""
                st.session_state.auto_download_done = False
                st.session_state.auto_batch_upload_key = current_batch_key
                clear_in_memory_caches()
                st.cache_data.clear()
                st.rerun()


            run_button_col, run_msg_col = st.columns([1.2, 3.8], gap="small")
            with run_button_col:
                if st.button(
                    "Run Batch",
                    key=f"run_batch_btn::{current_batch_key}",
                    use_container_width=True,
                ):
                    st.session_state.batch_run_requested = True
                    st.session_state.batch_started_key = current_batch_key
                    st.session_state.batch_status_message = ""
                    st.session_state.batch_error_text = ""
                    st.session_state.processing_done = False
                    st.session_state.completed_batch_key = ""
                    st.session_state.start_idx = 0
                    st.session_state.summary_rows = []
                    st.session_state.export_rows = []
                    st.session_state.debug_rows = []
                    st.session_state.summary_skus = set()
                    st.session_state.detail_skus = set()
                    st.session_state.debug_skus = set()
                    st.session_state.progress_bar = None
                    st.session_state.report_bytes = None
                    st.session_state.report_filename = None
                    st.session_state.report_batch_key = ""
                    st.session_state.auto_download_done = False
                    clear_in_memory_caches()
                    st.cache_data.clear()
                    st.rerun()

            with run_msg_col:
                if st.session_state.batch_error_text:
                    st.error(st.session_state.batch_error_text)
                elif st.session_state.processing_done and st.session_state.completed_batch_key == current_batch_key:
                    st.success(st.session_state.batch_status_message or f"Batch finished for {selected_retailer}. Visual QA review is ready below.")
                elif selected_capture_mode == CAPTURE_MODE_USE_EXTENSION and not matched_uploaded_html_count:
                    st.info(f"Load the extension, run the retailer batch, then upload the TXT capture for {selected_retailer}. As soon as the TXT has matches, batch will run from that captured HTML.")
                elif selected_capture_mode == CAPTURE_MODE_SKIP_EXTENSION and selected_retailer in AUTO_SKIP_EXTENSION_RETAILERS:
                    st.info(f"Skip-extension mode is enabled for {selected_retailer}. The app will go straight into batch automatically.")
                else:
                    st.info(f"Use the top selections, then click Run Batch for {selected_retailer}. The visual QA review will appear below after extract/report generation finishes.")

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
hide_good = st.checkbox("🎉 Hide Strong Matches (80%+)", key="hide_good")
show_below_90_only = st.checkbox("🔎 Show Only Scores Below 90%", key="show_below_90_only")


st.markdown("### 🧪 Debug Controls")

show_html_debugger = st.checkbox(
    "Debug HTML",
    key="show_html_debugger",
)

# Keep downstream variables defined so the rest of the app keeps working unchanged.
debugger_source = "Retailer page"
debug_only_sku = ""
use_manual_html_override = False
manual_html_file = None
manual_html_text = ""
debug_marker_start = ""
debug_marker_end = ""
debug_marker_target = "Raw HTML"

standalone_debug_url = st.text_input(
    "URL to pull raw HTML",
    key="standalone_debug_url",
).strip()

debug_timeout_override = st.number_input(
    "Timeout (s)",
    min_value=1,
    max_value=120,
    value=30,
    step=1,
    key="debug_timeout_override",
)

debug_headers_text = st.text_area(
    "Custom headers (optional)",
    placeholder="Either JSON, e.g. {'Accept-Language': 'en-US'} or one per line: Key: Value",
    height=100,
    key="debug_headers_text",
)

col_debug_1, col_debug_2 = st.columns(2)
with col_debug_1:
    debug_use_mobile = st.checkbox("Use mobile User-Agent", value=False, key="debug_use_mobile")
with col_debug_2:
    debug_proxy_url = st.text_input(
        "Proxy URL (optional)",
        key="debug_proxy_url",
        placeholder="http://user:pass@host:port",
    ).strip()

st.caption(
    "Paste a URL and the debugger will fetch the raw HTML response so you can inspect the exact source before we build a retailer-specific parser."
)

if show_html_debugger and standalone_debug_url:
    standalone_retailer_name = infer_retailer_name_from_url(standalone_debug_url)
    standalone_debug_views = resolve_debug_views(
        standalone_debug_url,
        retailer_name=standalone_retailer_name,
        use_manual_html_override=False,
        manual_html_text="",
        manual_html_file=None,
        headers_text=debug_headers_text,
        timeout_override=int(debug_timeout_override),
        use_mobile=debug_use_mobile,
        proxy_url=debug_proxy_url,
    )

    with st.expander("🔎 Debug HTML", expanded=True):
        render_debugger_panel(
            standalone_debug_views,
            sku="top_debugger",
            marker_start="",
            marker_end="",
            marker_target="Raw HTML",
            use_manual_html_override=False,
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
if retailer_df is not None and file_ready_for_batch and st.session_state.batch_started_key == current_batch_key:
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
                st.session_state.batch_status_message = f"Batch finished for {selected_retailer}. Extract/report generated successfully."
                st.session_state.batch_run_requested = False
                st.session_state.auto_batch_upload_key = current_batch_key
                st.rerun()
    except Exception as e:
        st.session_state.batch_error_text = str(e)
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())

# =========================================
# TOP EXPORT SECTION
# =========================================
if (
    st.session_state.processing_done
    and st.session_state.completed_batch_key
    and st.session_state.report_batch_key != st.session_state.completed_batch_key
):
    summary_df = pd.DataFrame(st.session_state.summary_rows)
    detail_df = pd.DataFrame(st.session_state.export_rows)
    debug_df = pd.DataFrame(st.session_state.debug_rows)

    selected_retailer_rpc_header = f"{str(selected_retailer or '').strip() or 'Retailer'} RPC"
    for _df in [summary_df, detail_df, debug_df]:
        _df.rename(
            columns={
                "CVS RPC": selected_retailer_rpc_header,
                "Retailer RPC": selected_retailer_rpc_header,
            },
            inplace=True,
        )

    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        detail_df.to_excel(writer, sheet_name="Details", index=False)
        debug_df.to_excel(writer, sheet_name="Debug", index=False)

        wb = writer.book

        green_fill = PatternFill(fill_type="solid", fgColor="C6EFCE")
        yellow_fill = PatternFill(fill_type="solid", fgColor="FFEB9C")
        red_fill = PatternFill(fill_type="solid", fgColor="FFC7CE")

        for sheet_name in ["Summary", "Details"]:
            ws = wb[sheet_name]

            header_map = {}
            for cell in ws[1]:
                header_map[str(cell.value).strip()] = cell.column

            for col_name, col_idx in header_map.items():
                if "%" in col_name:
                    for row_idx in range(2, ws.max_row + 1):
                        cell = ws.cell(row=row_idx, column=col_idx)
                        value = cell.value

                        if value is None or value == "":
                            continue

                        try:
                            score_val = float(value)
                        except Exception:
                            continue

                        if score_val >= 80:
                            cell.fill = green_fill
                        elif score_val >= 50:
                            cell.fill = yellow_fill
                        else:
                            cell.fill = red_fill

            for col_cells in ws.columns:
                max_length = 0
                col_letter = col_cells[0].column_letter

                for cell in col_cells:
                    try:
                        cell_len = len(str(cell.value or ""))
                        if cell_len > max_length:
                            max_length = cell_len
                    except Exception:
                        pass

                adjusted_width = min(max(max_length + 2, 12), 60)
                ws.column_dimensions[col_letter].width = adjusted_width

    safe_retailer = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(selected_retailer or "retailer").lower().strip(),
    ).strip("_") or "retailer"

    st.session_state.report_bytes = output.getvalue()
    st.session_state.report_filename = f"pdp_qa_results_{safe_retailer}_all_brands.xlsx"
    st.session_state.report_batch_key = st.session_state.completed_batch_key
    st.session_state.auto_download_done = False
    if not st.session_state.batch_status_message:
        st.session_state.batch_status_message = f"Batch finished for {selected_retailer}. Extract/report generated successfully."
    st.rerun()
        
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
        st.caption("This full visual UI appears only after the top batch run finishes and the extract/report rows are ready.")
        st.caption("This full visual UI appears only after the new top-section batch run finishes and the extract/report rows are ready. Kroger visual rows reuse the uploaded TXT-matched HTML instead of live fetch.")

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
                row_source_code=row.get("copy_source_code", ""),
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
            retailer_norm = str(retailer_name or "").strip().lower()
            salsify_requirements = get_retailer_salsify_requirements(retailer_name)
            feature_fields = get_retailer_salsify_feature_fields(retailer_name)

            title_score = keyword_score(s_title, r_title)
            desc_score = description_similarity_score(s_desc, r_desc)

            max_features = min(
                max(len(feature_fields), len(retailer_features)),
                int(salsify_requirements.get("max_features", len(feature_fields)) or len(feature_fields)),
            )
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

            s_images, r_images = trim_trailing_empty_image_slots(s_images, r_images)
            retailer_image_limit = int(salsify_requirements.get("max_images", MAX_IMAGE_SLOTS_TO_COMPARE) or MAX_IMAGE_SLOTS_TO_COMPARE)
            max_images = min(max(len(s_images), len(r_images)), MAX_IMAGE_SLOTS_TO_COMPARE, retailer_image_limit)
            
            # Compute image score before applying row filters.
            avg_img_score, _image_position_scores = build_image_score_fields(
                s_images,
                r_images,
                max_slots=min(MAX_IMAGE_SLOTS_TO_SCORE, retailer_image_limit),
            )
            
            overall_score = int((title_score + desc_score + avg_feature_score + avg_img_score) / 4)
            
            if show_only_issues and overall_score >= 80:
                continue
            if hide_good and overall_score >= 80:
                continue
            if show_below_90_only and overall_score >= 90:
                continue

            left, right = st.columns([2.72, 0.95], gap="small")
        
            with left:
                raw_rpc = current_target_sku or current_rpc
                clean_rpc = clean_item_number(raw_rpc)

                salsify_header_html = column_header_link_html("Salsify", sku, salsify_url)
                retailer_header_html = column_header_link_html(
                    retailer_name,
                    clean_rpc,
                    retail_url,
                )

                rating_html = ""
                if str(retailer_name or "").strip().lower() in {"walgreens", "kroger"}:
                    retailer_name_norm = str(retailer_name or "").strip().lower()
                    rating_value = (r_text.get("rating", "") if isinstance(r_text, dict) else "") or row.get("rating", "") or ""
                    review_count_value = (r_text.get("review_count", "") if isinstance(r_text, dict) else "") or row.get("review_count", "") or ""
                    if rating_value or review_count_value:
                        kroger_star_size = 28 if retailer_name_norm == "kroger" else 18
                        rating_html = rating_stars_html(rating_value, review_count_value, font_size_px=kroger_star_size)

                st.markdown(
                    locked_visual_header_row_html(
                        salsify_header_html,
                        retailer_header_html,
                        rating_html=rating_html,
                        retailer_name=retailer_name,
                    ),
                    unsafe_allow_html=True,
                )

                st.markdown(
                    avg_score_bar_html("Copy — Avg", copy_avg_score),
                    unsafe_allow_html=True,
                )

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
                head_i2.markdown(
                    f"<div style='margin-left:-42px;'>" + image_header_html(retailer_name) + "</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    avg_score_bar_html("Images — Avg", avg_img_score),
                    unsafe_allow_html=True,
                )

                img_scores = []
                max_images_to_score = min(max_images, MAX_IMAGE_SLOTS_TO_SCORE)
                
                for i in range(max_images):
                    s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else ""
                    r_url = r_images[i] if i < len(r_images) and isinstance(r_images[i], str) else ""
                
                    slot_score = compare_images_visually(s_url, r_url) if (s_url and r_url) else 0
                
                    if i < max_images_to_score:
                        img_scores.append(slot_score)
                
                    st.markdown(
                        image_compare_row_html(s_url, r_url, slot_score),
                        unsafe_allow_html=True,
                    )
                
                avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0
                overall_score = int((title_score + desc_score + avg_feature_score + avg_img_score) / 4)

            if show_html_debugger:
                should_render_debugger = (not debug_only_sku) or (
                    str(sku).strip() == str(debug_only_sku).strip()
                )
            
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
            
                    with st.expander(f"🔎 HTML / DOM Debugger — {sku}", expanded=True):
                        render_debugger_panel(
                            debug_views,
                            sku=sku,
                            marker_start=debug_marker_start,
                            marker_end=debug_marker_end,
                            marker_target=debug_marker_target,
                            use_manual_html_override=use_manual_html_override,
                        )
            st.divider()
    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())

                      
