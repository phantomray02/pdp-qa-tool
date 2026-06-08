import re
import html
import json
import time
import traceback
from io import BytesIO
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter

# =========================================
# GLOBALS
# =========================================
requests.adapters.DEFAULT_RETRIES = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 12
MAX_CACHE = 100
html_cache = {}

st.set_page_config(layout="wide")
st.title("PDP QA Tool ✅")


# =========================================
# GENERIC HELPERS
# =========================================
def normalize_space(text):
    text = str(text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def dedupe_preserve_order(items):
    output = []
    seen = set()
    for item in items:
        item = normalize_space(item)
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def normalize_text(t):
    if not isinstance(t, str):
        return ""
    return re.sub(r'[^a-z0-9\s]', '', t.lower())


def keyword_score(a, b):
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)


def equal_height_block(text):
    return f"""
    <div style="min-height: 180px; display: flex; align-items: flex-start;">{text}</div>
    """


def equal_feature_block(text):
    return f"""
    <div style="min-height: 70px; display: flex; align-items: flex-start;">{text}</div>
    """


def score_bar(score):
    if score >= 80:
        color = "#2E7D32"
    elif score >= 50:
        color = "#F9A825"
    else:
        color = "#C62828"

    return f"""
    <div style="
        background-color:{color};
        padding:6px 10px;
        border-radius:6px;
        color:white;
        font-weight:600;
        margin-top:6px;
        margin-bottom:6px;
    ">
        Score: {score}%
    </div>
    """


def score_badge(score):
    if score >= 80:
        return f"✅ <span style='color:#4CAF50; font-weight:700'>{score}% (Strong)</span>"
    elif score >= 50:
        return f"🟡 <span style='color:#FFC107; font-weight:700'>{score}% (Review)</span>"
    else:
        return f"🔴 <span style='color:#F44336; font-weight:700'>{score}% (Poor)</span>"


# =========================================
# HTML CACHE
# =========================================
def get_html(url):
    if url in html_cache:
        html_cache[url] = html_cache.pop(url)
        return html_cache[url]

    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            html_cache[url] = r.text
            while len(html_cache) > MAX_CACHE:
                html_cache.pop(next(iter(html_cache)))
            return r.text
    except Exception:
        pass

    return ""


# =========================================
# SALSIFY IMAGES
# =========================================
def get_salsify_images(url):
    html_text = get_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return []

    data = json.loads(script.string)

    try:
        properties = data["props"]["pageProps"]["product"]["digitalAssets"]["properties"]
    except Exception:
        return []

    asset_map = {}
    for prop in properties:
        name = prop.get("property", "").lower()
        values = prop.get("values", [])
        if values:
            val = values[0].get("value", "")
            if val:
                asset_map[name] = val.split("?")[0]

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

    ordered = [img for img in ordered[:8] if img]
    return [{"url": img} for img in ordered]


# =========================================
# SALSIFY TEXT
# =========================================
def get_salsify_text(url):
    html_text = get_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return {}

    data = json.loads(script.string)

    try:
        props = data["props"]["pageProps"]["product"]["propertySets"][0]["properties"]
    except Exception:
        return {}

    text_map = {}
    for p in props:
        key = p.get("property")
        values = p.get("values", [])
        if values:
            text_map[key] = values[0]

    return {
        "title": text_map.get("PRODUCT_TITLE", ""),
        "description": text_map.get("DESCRIPTION", ""),
        "feature1": text_map.get("FEATURE_1", ""),
        "feature2": text_map.get("FEATURE_2", ""),
        "feature3": text_map.get("FEATURE_3", ""),
        "feature4": text_map.get("FEATURE_4", ""),
        "feature5": text_map.get("FEATURE_5", ""),
    }


# =========================================
# CVS COPY EXTRACTION - NEW RULES
# =========================================
def clean_cvs_text(text):
    if not text:
        return ""

    text = text.replace("\\u0026", "&")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')
    text = html.unescape(text)

    text = re.sub(r'\]\).*?self\.__next_f\.push\(\[1,"', ' ', text, flags=re.DOTALL)
    text = re.sub(r'<\/script><script>self\.__next_f\.push\(\[1,"', ' ', text, flags=re.DOTALL)
    text = re.sub(r'</script><script>self\.__next_f\.push\(\[1,"', ' ', text, flags=re.DOTALL)
    text = re.sub(r'^T[0-9a-zA-Z]+,', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' \t\r\n"')


def get_nextjs_chunks(html_text):
    pattern = r'self\.__next_f\.push\(\[1,(.*?)\]\)</script>'
    matches = re.findall(pattern, html_text or "", re.DOTALL)

    chunks = []
    for m in matches:
        text = m.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        chunks.append(text)

    return "\n".join(chunks)


def extract_balanced_bracket_block(source, start_index):
    if start_index < 0 or start_index >= len(source) or source[start_index] != '[':
        return ""

    depth = 0
    in_str = False
    escape = False

    for i in range(start_index, len(source)):
        ch = source[i]

        if in_str:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return source[start_index:i + 1]

    return ""


def extract_top_level_value_block(source, key):
    pattern = rf'(?m)(?:^|\n){re.escape(str(key))}:'
    m = re.search(pattern, source)
    if not m:
        return ""

    start = m.end()
    i = start
    depth = 0
    in_str = False
    escape = False

    while i < len(source):
        ch = source[i]

        if in_str:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in '[{(':
                depth += 1
            elif ch in ']})':
                depth = max(0, depth - 1)
            elif depth == 0:
                next_key = re.match(r'\n[0-9a-zA-Z]{1,3}:(?=[\[{T"])', source[i:])
                if next_key:
                    break

        i += 1

    return source[start:i].strip()


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
                return [clean_cvs_text(v) for v in value if isinstance(v, str)]
        except Exception:
            pass

    inner = array_text[1:-1] if array_text.startswith('[') and array_text.endswith(']') else array_text
    parts = re.split(r'"\s*,\s*"', inner)

    cleaned = []
    for part in parts:
        part = part.strip().strip('"')
        part = clean_cvs_text(part)
        if part:
            cleaned.append(part)

    return cleaned


def extract_vendor_copy_from_nextjs(html_text):
    raw_text = get_nextjs_chunks(html_text)

    if not raw_text:
        return {
            "features": [],
            "description": "",
            "debug": {
                "vendorDetailsBulletsRef": "",
                "vendorDetailsParagraphRef": "",
                "featuresKey": "",
                "descriptionKey": "",
                "rawTextLength": 0,
            },
        }

    vendor_match = re.search(
        r'\{"vendorDetailsBullets":"\$([0-9a-zA-Z]{1,3})","vendorDetailsParagraph":"\$([0-9a-zA-Z]{1,3})"\}',
        raw_text,
    )

    if not vendor_match:
        vendor_match = re.search(
            r'vendorDetailsBullets"\s*:\s*"\$([0-9a-zA-Z]{1,3})"\s*,\s*"vendorDetailsParagraph"\s*:\s*"\$([0-9a-zA-Z]{1,3})"',
            raw_text,
        )

    if not vendor_match:
        return {
            "features": [],
            "description": "",
            "debug": {
                "vendorDetailsBulletsRef": "",
                "vendorDetailsParagraphRef": "",
                "featuresKey": "",
                "descriptionKey": "",
                "rawTextLength": len(raw_text),
            },
        }

    features_key = vendor_match.group(1)
    description_key = vendor_match.group(2)

    features = []
    features_marker = re.search(rf'(?m)(?:^|\n){re.escape(features_key)}:\[', raw_text)
    if features_marker:
        array_start = features_marker.end() - 1
        array_text = extract_balanced_bracket_block(raw_text, array_start)
        features = parse_jsonish_array_text(array_text)

    desc_block = extract_top_level_value_block(raw_text, description_key)
    description = clean_cvs_text(desc_block)

    return {
        "features": dedupe_preserve_order(features),
        "description": description,
        "debug": {
            "vendorDetailsBulletsRef": f'${features_key}',
            "vendorDetailsParagraphRef": f'${description_key}',
            "featuresKey": features_key,
            "descriptionKey": description_key,
            "rawTextLength": len(raw_text),
        },
    }


def get_cvs_images(url):
    html_text = get_html(url)
    matches = re.findall(r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^\"]*', html_text)

    best_images = {}
    order = []
    for m in matches:
        full = "https://www.cvs.com" + m
        base = full.split("?")[0]
        name = base.split("/")[-1]
        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in best_images:
            order.append(name)
            best_images[name] = {"url": base, "size": size}
        else:
            if size > best_images[name]["size"]:
                best_images[name] = {"url": base, "size": size}

    return [best_images[name]["url"] for name in order]


def get_cvs_text(html_text, retail_url=""):
    debug = {
        "Title Path": "",
        "Description Path": "",
        "Features Path": "",
        "vendorDetailsBulletsRef": "",
        "vendorDetailsParagraphRef": "",
        "featuresKey": "",
        "descriptionKey": "",
        "rawTextLength": 0,
    }

    if not html_text:
        return {"title": "", "description": "", "features": [], "debug": debug}

    title = ""
    soup = BeautifulSoup(html_text, "html.parser")
    h1 = soup.find("h1")
    if h1:
        title = normalize_space(h1.get_text(" ", strip=True))
        debug["Title Path"] = "h1"
    elif soup.title:
        title = normalize_space(soup.title.get_text(" ", strip=True))
        debug["Title Path"] = "html_title"

    vendor_copy = extract_vendor_copy_from_nextjs(html_text)
    description = vendor_copy.get("description", "")
    features = vendor_copy.get("features", [])

    debug.update(vendor_copy.get("debug", {}))
    debug["Description Path"] = "vendorDetailsParagraph" if description else "description_empty"
    debug["Features Path"] = "vendorDetailsBullets" if features else "features_empty"

    return {
        "title": title,
        "description": description,
        "features": features[:5],
        "debug": debug,
    }


# =========================================
# DESCRIPTION DEBUGGER
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
    is_truncated = (not desc.strip().endswith((".", "!", "?")) or length < 80)

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
    return {"length": length, "quality_score": quality_score, "issues": issues}


def suggest_description_fix(debug):
    suggestions = []
    if "Missing absorbency info" in debug["issues"]:
        suggestions.append("Add absorbency or protection level")
    if "Missing size/count" in debug["issues"]:
        suggestions.append("Include pack size and quantity")
    if "Missing benefits" in debug["issues"]:
        suggestions.append("Add comfort, odor or dryness benefits")
    if "Too short" in debug["issues"]:
        suggestions.append("Expand description with more detail")
    if "Possible truncation" in debug["issues"]:
        suggestions.append("Fix incomplete or cut-off sentence")
    if "Repetitive content" in debug["issues"]:
        suggestions.append("Reduce repetition and diversify wording")
    return suggestions


# =========================================
# IMAGE COMPARISON
# =========================================
def fetch_image_cached(url):
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", ""):
            return resp.content
    except Exception:
        return None
    return None


def load_image_with_white_bg(img_data):
    try:
        img = Image.open(BytesIO(img_data))
        img.thumbnail((128, 128))
        img = img.convert("RGBA")
    except Exception:
        return None

    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    if img.mode == "RGBA":
        white_bg.paste(img, mask=img.split()[3])
    else:
        white_bg.paste(img)

    return white_bg.convert("L")


def prefetch_images(urls):
    urls = [u for u in urls if u]
    if not urls:
        return
    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(fetch_image_cached, urls))


def compare_images_visually(s_url, r_url):
    try:
        s_img_data = fetch_image_cached(s_url)
        r_img_data = fetch_image_cached(r_url)
        if not s_img_data or not r_img_data:
            return 0

        s_img = load_image_with_white_bg(s_img_data)
        r_img = load_image_with_white_bg(r_img_data)
        if s_img is None or r_img is None:
            return 0

        s_img = s_img.resize((64, 64)).filter(ImageFilter.GaussianBlur(1))
        r_img = r_img.resize((64, 64)).filter(ImageFilter.GaussianBlur(1))

        s_arr = np.array(s_img)
        r_arr = np.array(r_img)
        if s_arr.shape != r_arr.shape:
            return 0

        diff = float(np.mean(np.abs(s_arr.astype("float32") - r_arr.astype("float32"))))
        if diff < 5:
            return 100
        elif diff < 15:
            return 90
        elif diff < 30:
            return 75
        elif diff < 45:
            return 60
        elif diff < 60:
            return 45
        elif diff < 80:
            return 30
        else:
            return 15
    except Exception:
        return 0


def match_images_visual(s_images, r_images):
    results = []
    max_len = max(len(s_images), len(r_images))

    for i in range(max_len):
        s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else None
        r_url = r_images[i] if i < len(r_images) and isinstance(r_images[i], str) else None

        if s_url and r_url:
            score = compare_images_visually(s_url, r_url)
        else:
            score = 0

        results.append((s_url, r_url, score))

    return results


# =========================================
# PROCESS ROW
# =========================================
@st.cache_data(show_spinner=False)
def process_row_cached(row_dict):
    return process_row(row_dict)


def process_row(row):
    try:
        retail_url = row.get("retail_url", "")
        retail_html = get_html(retail_url)
        s_text = get_salsify_text(row.get("salsify_url", ""))

        r_text = get_cvs_text(retail_html, retail_url=retail_url) or {}
        debug_data = r_text.get("debug", {})

        desc_raw = r_text.get("description", "")
        r_text["description"] = clean_cvs_text(desc_raw)

        cleaned_features = []
        for f in r_text.get("features", []):
            if any(x in f for x in ["\\", "self.__next_f", "\\u0026", "\\n"]):
                cleaned_features.append(clean_cvs_text(f))
            else:
                cleaned_features.append(f)
        r_text["features"] = cleaned_features

        s_images = get_salsify_images(row.get("salsify_url", ""))
        r_images = get_cvs_images(retail_url)

        all_urls = []
        for img in s_images:
            if isinstance(img, dict) and img.get("url"):
                all_urls.append(img["url"])
        for img in r_images:
            if isinstance(img, str):
                all_urls.append(img)
        prefetch_images(all_urls)

        if not isinstance(s_images, list):
            s_images = []
        if not isinstance(r_images, list):
            r_images = []

        title_score = keyword_score(s_text.get("title", ""), r_text.get("title", ""))

        s_desc_debug = debug_description(s_text.get("description", ""))
        r_desc_debug = debug_description(r_text.get("description", ""))

        text_similarity = keyword_score(
            s_text.get("description", ""),
            r_text.get("description", "")
        )

        quality_penalty = int((100 - r_desc_debug["quality_score"]) * 0.5)
        desc_score = max(0, text_similarity - quality_penalty)

        cvs_features = r_text.get("features") if isinstance(r_text, dict) else []
        if not isinstance(cvs_features, list):
            cvs_features = []

        feature_fields = ["feature1", "feature2", "feature3", "feature4", "feature5"]
        feature_scores = []

        for f_key in feature_fields:
            s_val = s_text.get(f_key, "")
            scores = [keyword_score(s_val, f) for f in cvs_features if isinstance(f, str)]
            best = max(scores) if scores else 0
            feature_scores.append(best)

        avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0

        img_scores = []
        for i in range(max(len(s_images), len(r_images))):
            s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else None
            r_url = r_images[i] if i < len(r_images) else None

            if s_url and r_url:
                sc = compare_images_visually(s_url, r_url)
                if sc > 0:
                    img_scores.append(sc)

        avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0
        overall = int((title_score + desc_score + avg_feature_score + avg_img_score) / 4)

        return {
            "summary": {
                "SKU": row.get("sku", ""),
                "CVS RPC": row.get("cvs_rpc") or row.get("CVS RPC") or "",
                "Brand": row.get("brand", ""),
                "Salsify URL": row.get("salsify_url", ""),
                "Retail URL": retail_url,
                "Title %": title_score,
                "Description %": desc_score,
                "Feature %": avg_feature_score,
                "Image Match %": avg_img_score,
                "Overall %": overall,
            },
            "detail": {
                "SKU": row.get("sku", ""),
                "CVS RPC": row.get("cvs_rpc") or row.get("CVS RPC") or "",
                "Brand": row.get("brand", ""),
                "Salsify URL": row.get("salsify_url", ""),
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
                "Title Path": debug_data.get("Title Path", ""),
                "Description Path": debug_data.get("Description Path", ""),
                "Features Path": debug_data.get("Features Path", ""),
                "vendorDetailsBulletsRef": debug_data.get("vendorDetailsBulletsRef", ""),
                "vendorDetailsParagraphRef": debug_data.get("vendorDetailsParagraphRef", ""),
                "featuresKey": debug_data.get("featuresKey", ""),
                "descriptionKey": debug_data.get("descriptionKey", ""),
            },
            "debug": {
                "SKU": row.get("sku", ""),
                "Desc Final": r_text.get("description", ""),
                "Desc Quality Score": r_desc_debug["quality_score"],
                "Desc Length": r_desc_debug["length"],
                "Desc Issues": ", ".join(r_desc_debug["issues"]),
                "Salsify Desc Quality Score": s_desc_debug["quality_score"],
                "Final Features": " | ".join(r_text.get("features", [])),
                "Title Path": debug_data.get("Title Path", ""),
                "Description Path": debug_data.get("Description Path", ""),
                "Features Path": debug_data.get("Features Path", ""),
                "vendorDetailsBulletsRef": debug_data.get("vendorDetailsBulletsRef", ""),
                "vendorDetailsParagraphRef": debug_data.get("vendorDetailsParagraphRef", ""),
                "featuresKey": debug_data.get("featuresKey", ""),
                "descriptionKey": debug_data.get("descriptionKey", ""),
                "rawTextLength": debug_data.get("rawTextLength", 0),
            }
        }

    except Exception:
        return None


# =========================================
# MAIN APP
# =========================================
uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
download_placeholder = st.empty()

if "start_idx" not in st.session_state:
    st.session_state.start_idx = 0
if "summary_rows" not in st.session_state:
    st.session_state.summary_rows = []
if "export_rows" not in st.session_state:
    st.session_state.export_rows = []
if "processing_done" not in st.session_state:
    st.session_state.processing_done = False
if "download_clicked" not in st.session_state:
    st.session_state.download_clicked = False
if "debug_rows" not in st.session_state:
    st.session_state.debug_rows = []
if "progress_bar" not in st.session_state:
    st.session_state.progress_bar = None

st.markdown("## 🔎 QA Viewer Controls")

view_mode = st.checkbox(
    "👁️ View Full QA",
    key="view_mode",
    disabled=not st.session_state.processing_done
)

if st.session_state.get("processing_done", False) and not view_mode:
    st.success("✅ Processing complete")

show_only_issues = st.checkbox("❌ Show ONLY Issues", key="show_issues")
hide_good = st.checkbox("✅ Hide Strong Matches (80%+)", key="hide_good")

if uploaded_file:
    try:
        file_bytes = uploaded_file.getvalue()
        file_id = hash(file_bytes)

        if (
            "last_file" not in st.session_state
            or st.session_state.last_file != file_id
        ):
            st.session_state.summary_rows = []
            st.session_state.export_rows = []
            st.session_state.start_idx = 0
            st.session_state.processing_done = False
            st.session_state.last_file = file_id
            st.session_state.debug_rows = []
            st.session_state.progress_bar = None

        df = pd.read_csv(uploaded_file)
        df.columns = [c.strip().lower() for c in df.columns]

        column_map = {
            "salsify url": "salsify_url",
            "retail url": "retail_url",
            "sku id": "sku",
            "product sku": "sku",
            "cvs rpc": "cvs_rpc"
        }
        df.rename(columns=column_map, inplace=True)

        if "brand" not in df.columns:
            if len(df.columns) >= 5:
                df.rename(columns={df.columns[4]: "brand"}, inplace=True)

        required_cols = ["sku", "salsify_url", "retail_url"]
        missing = [c for c in required_cols if c not in df.columns]

        if missing:
            st.error(f"❌ Missing required columns: {missing}")
            st.write("Detected columns:", list(df.columns))
            st.stop()

        brands = sorted(df["brand"].dropna().unique()) if "brand" in df.columns else []
        selected_brand = st.selectbox("🏷️ Select Brand", ["All"] + brands)

        if selected_brand != "All":
            df = df[df["brand"] == selected_brand]

        BATCH_SIZE = 40
        start = st.session_state.start_idx
        end = start + BATCH_SIZE

        if start >= len(df):
            st.session_state.processing_done = True

        batch_df = df.iloc[start:end]

        if not st.session_state.processing_done:
            st.write(f"Processing SKUs {start+1} to {min(end, len(df))} of {len(df)}")

            if st.session_state.progress_bar is None:
                st.session_state.progress_bar = st.progress(0)

            progress_bar = st.session_state.progress_bar
            status_text = st.empty()
            total = len(batch_df)
            st.write("### Overall Progress")
            overall_progress_bar = st.progress(0)

        st.info("⚙️ Processing batch...")

        if st.session_state.processing_done:
            st.success("✅ Processing complete")

        if not st.session_state.processing_done and not view_mode:
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [
                    executor.submit(process_row_cached, row.to_dict())
                    for _, row in batch_df.iterrows()
                ]

                for i, future in enumerate(as_completed(futures)):
                    result = future.result()

                    if result:
                        summary = result.get("summary")
                        detail = result.get("detail")
                        debug = result.get("debug")

                        if summary and summary["SKU"] not in {r["SKU"] for r in st.session_state.summary_rows}:
                            st.session_state.summary_rows.append(summary)

                        if detail and detail["SKU"] not in {r["SKU"] for r in st.session_state.export_rows}:
                            st.session_state.export_rows.append(detail)

                        if debug:
                            st.session_state.debug_rows.append(debug)

                    progress_bar.progress((i + 1) / max(total, 1))
                    status_text.markdown(f"Processed {i+1}/{total}")
                    overall_progress = (start + i + 1) / max(len(df), 1)
                    overall_progress_bar.progress(overall_progress)

            st.write(f"✅ Rows processed so far: {len(st.session_state.summary_rows)}")

            if st.session_state.start_idx + BATCH_SIZE < len(df):
                status_text.markdown("Loading next batch...")
                st.session_state.start_idx += BATCH_SIZE
                time.sleep(0.05)
                st.rerun()
            else:
                st.session_state.processing_done = True
                st.rerun()

        elif view_mode:
            if st.session_state.download_clicked:
                st.session_state.download_clicked = False
                st.stop()

            for _, row in df.iterrows():
                sku = row.get("sku", "Missing SKU")
                retail_url = row.get("retail_url", "")

                retail_html = get_html(retail_url)
                s_text = get_salsify_text(row.get("salsify_url", ""))
                r_text = get_cvs_text(retail_html, retail_url=retail_url) or {}
                debug_data = r_text.get("debug", {})

                desc_raw = r_text.get("description", "")
                r_text["description"] = clean_cvs_text(desc_raw)

                cleaned_features = []
                for f in r_text.get("features", []):
                    if any(x in f for x in ["\\", "self.__next_f", "\\u0026", "\\n"]):
                        cleaned_features.append(clean_cvs_text(f))
                    else:
                        cleaned_features.append(f)
                r_text["features"] = cleaned_features

                s_images = get_salsify_images(row.get("salsify_url", ""))
                r_images = get_cvs_images(retail_url)

                image_flags = []
                if len(r_images) < len(s_images):
                    image_flags.append(f"Missing {len(s_images) - len(r_images)} images")
                elif len(r_images) > len(s_images):
                    image_flags.append(f"{len(r_images) - len(s_images)} extra images")

                if not isinstance(s_images, list):
                    s_images = []
                if not isinstance(r_images, list):
                    r_images = []

                s_title = s_text.get("title") if isinstance(s_text, dict) else ""
                r_title = r_text.get("title") if isinstance(r_text, dict) else ""
                s_desc = s_text.get("description") if isinstance(s_text, dict) else ""
                r_desc = r_text.get("description") if isinstance(r_text, dict) else ""
                cvs_features = r_text.get("features") or []

                missing_flags = []
                if not s_title or not r_title:
                    missing_flags.append("Title")
                if not s_desc.strip() or not r_desc.strip():
                    missing_flags.append("Description")
                if not cvs_features:
                    missing_flags.append("Features")
                if not s_images or not r_images:
                    missing_flags.append("Images")

                feature_fields = ["feature1", "feature2", "feature3", "feature4", "feature5"]
                title_score = keyword_score(s_title, r_title)

                s_desc_debug = debug_description(s_desc)
                r_desc_debug = debug_description(r_desc)
                desc_text_similarity = keyword_score(s_desc, r_desc)
                desc_quality_penalty = int((100 - r_desc_debug["quality_score"]) * 0.5)
                desc_score = max(0, desc_text_similarity - desc_quality_penalty)

                feature_scores = []
                max_features = max(len(feature_fields), len(cvs_features))
                img_scores = []
                max_images = max(len(s_images), len(r_images))

                for i in range(max_images):
                    s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else None
                    r_url = r_images[i] if i < len(r_images) and isinstance(r_images[i], str) else None
                    if s_url and r_url:
                        sc = compare_images_visually(s_url, r_url)
                        img_scores.append(sc)

                avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0

                for i in range(max_features):
                    s_val = s_text.get(feature_fields[i], "") if i < len(feature_fields) else ""
                    r_val = cvs_features[i] if i < len(cvs_features) else ""
                    feature_scores.append(keyword_score(s_val, r_val))

                avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0
                overall_score = int((title_score + desc_score + avg_feature_score + avg_img_score) / 4)
                hard_fail = title_score < 40 or desc_score < 40
                is_issue = overall_score < 80

                if show_only_issues and not is_issue:
                    continue
                if hide_good and overall_score >= 80:
                    continue

                cvs_rpc = row.get("cvs_rpc") or row.get("CVS RPC") or "N/A"
                st.subheader(f"SKU: {sku} | CVS RPC: {cvs_rpc}")
                left, right = st.columns([2, 1])

                if missing_flags:
                    st.warning(f"⚠️ Missing: {', '.join(missing_flags)}")
                if image_flags:
                    st.warning(f"🖼️ Image Issue: {', '.join(image_flags)}")
                if hard_fail:
                    st.error("🚨 Critical content issue (possible wrong or broken PDP)")
                elif overall_score < 50:
                    st.warning("⚠️ Major quality issue")

                with left:
                    st.markdown(f"### 🏷️ Title {score_badge(title_score)}", unsafe_allow_html=True)
                    c1, c2 = st.columns(2)
                    c1.markdown(equal_height_block(s_title or '❌ Missing'), unsafe_allow_html=True)
                    c2.markdown(equal_height_block(r_title or '❌ Missing'), unsafe_allow_html=True)

                    st.markdown(f"### 📄 Description {score_badge(desc_score)}", unsafe_allow_html=True)
                    c1, c2 = st.columns(2)
                    c1.markdown(equal_height_block(s_desc or '❌ Missing'), unsafe_allow_html=True)
                    c2.markdown(equal_height_block(r_desc or '❌ Missing'), unsafe_allow_html=True)

                    if r_desc_debug["quality_score"] < 50:
                        st.error("🚨 Poor description quality")
                    elif r_desc_debug["quality_score"] < 75:
                        st.warning("⚠️ Description needs improvement")

                    if r_desc_debug["issues"]:
                        st.info(f"🛠 Issues: {', '.join(r_desc_debug['issues'])}")

                    fixes = suggest_description_fix(r_desc_debug)
                    if fixes:
                        st.markdown("### 💡 Suggested Fixes")
                        for f in fixes:
                            st.write(f"- {f}")

                    st.caption(
                        f"CVS extraction paths → Title: {debug_data.get('Title Path', '')} | "
                        f"Description: {debug_data.get('Description Path', '')} | "
                        f"Features: {debug_data.get('Features Path', '')}"
                    )

                    st.markdown(f"### 📌 Features {score_badge(avg_feature_score)}", unsafe_allow_html=True)
                    for i in range(max_features):
                        s_val = s_text.get(feature_fields[i], "") if i < len(feature_fields) else ""
                        r_val = cvs_features[i] if i < len(cvs_features) else ""
                        score = keyword_score(s_val, r_val)
                        c1, c2 = st.columns(2)
                        c1.markdown(equal_feature_block(s_val or '❌ Missing'), unsafe_allow_html=True)
                        c2.markdown(equal_feature_block(r_val or '❌ Missing'), unsafe_allow_html=True)
                        st.markdown(score_badge(score), unsafe_allow_html=True)
                        st.divider()

                    st.markdown(score_bar(avg_feature_score), unsafe_allow_html=True)

                with right:
                    st.markdown(f"### 🖼️ Images — Avg {score_badge(avg_img_score)}", unsafe_allow_html=True)
                    st.markdown(score_bar(avg_img_score), unsafe_allow_html=True)

                    max_images = max(len(s_images), len(r_images))
                    for i in range(max_images):
                        col1, col2, col3 = st.columns([3, 3, 1])
                        s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else None
                        r_url = r_images[i] if i < len(r_images) and isinstance(r_images[i], str) else None

                        if s_url:
                            col1.markdown(
                                f"<img src='{s_url}' style='width:100%; max-width:200px; border-radius:6px;'>",
                                unsafe_allow_html=True
                            )
                        else:
                            col1.write("")

                        if r_url:
                            col2.markdown(
                                f"<img src='{r_url}' style='width:100%; max-width:200px; border-radius:6px;'>",
                                unsafe_allow_html=True
                            )
                        else:
                            col2.write("")

                        sc = compare_images_visually(s_url, r_url) if (s_url and r_url) else 0
                        col3.markdown(score_badge(sc), unsafe_allow_html=True)

                if overall_score >= 80:
                    st.markdown(score_bar(overall_score), unsafe_allow_html=True)
                    st.success(f"✅ Strong Match: {overall_score}%")
                elif overall_score >= 50:
                    st.markdown(score_bar(overall_score), unsafe_allow_html=True)
                    st.warning(f"🟡 Needs Review: {overall_score}%")
                else:
                    st.markdown(score_bar(overall_score), unsafe_allow_html=True)
                    st.error(f"🔴 Critical Issue: {overall_score}%")

                st.caption(
                    f"Title: {title_score}% | Desc: {desc_score}% | Feat: {avg_feature_score}% | Img: {avg_img_score}%"
                )
                st.divider()

    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())


# =====================================
# EXPORT FILE
# =====================================
if st.session_state.processing_done and st.session_state.summary_rows:
    summary_df = pd.DataFrame(st.session_state.summary_rows)
    detail_df = pd.DataFrame(st.session_state.export_rows)
    debug_df = pd.DataFrame(st.session_state.get("debug_rows", []))

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        detail_df.to_excel(writer, index=False, sheet_name="Details")
        debug_df.to_excel(writer, index=False, sheet_name="Debug")

    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill

    wb = load_workbook(file_name)
    ws = wb["Summary"]

    green = PatternFill(start_color="C6EFCE", fill_type="solid")
    yellow = PatternFill(start_color="FFEB9C", fill_type="solid")
    red = PatternFill(start_color="FFC7CE", fill_type="solid")

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            val = cell.value
            if isinstance(val, (int, float)):
                if val >= 80:
                    cell.fill = green
                elif val >= 50:
                    cell.fill = yellow
                else:
                    cell.fill = red

    wb.save(file_name)

    with open(file_name, "rb") as f:
        st.markdown("## 📊 Export Results")
        if download_placeholder.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ):
            st.session_state.download_clicked = True
