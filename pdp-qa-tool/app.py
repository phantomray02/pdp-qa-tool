# =========================================
# ✅ IMPORTS (TOP OF FILE)
# =========================================
import re
import html
from bs4 import BeautifulSoup
import streamlit as st
import pandas as pd
import requests
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO
import traceback
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(layout="wide")

st.title("PDP QA Tool ✅")

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

# =========================================
# ✅ CACHE HTML
# =========================================
html_cache = {}
MAX_CACHE = 100

def get_html(url):
    if url in html_cache:
        html_cache[url] = html_cache.pop(url)  # refresh order
        return html_cache[url]

    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            html_cache[url] = r.text

            # ✅ enforce cache limit AFTER insert
            while len(html_cache) > MAX_CACHE:
                html_cache.pop(next(iter(html_cache)))

            return r.text
    except:
        pass

    return ""

# =========================================
# ✅ LOAD IMAGE
# =========================================

def load_image(url):
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except Exception as e:
        return None
    return None

# =========================================
# ✅ NORMALIZE FILE NAME (DEDUP CORE)
# =========================================
def normalize_filename(fname):
    fname = fname.lower()
    fname = re.sub(r'(_|-)?\d+x\d+', '', fname)
    fname = re.sub(r'(_|-)?\d+', '', fname)
    return fname

# =========================================
# ✅ ✅ SALSIFY (FINAL CORRECT ENGINE)
# =========================================
import json

def get_salsify_images(url):

    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return []

    data = json.loads(script.string)

    try:
        properties = data["props"]["pageProps"]["product"]["digitalAssets"]["properties"]
    except:
        return []

    asset_map = {}

    # ✅ BUILD PROPERTY MAP
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

    # ✅ BASE IMAGES (1–3 ALWAYS)
    ordered = [
        find("online"),    # 1
        find("back"),      # 2
        find("left"),      # 3
    ]

    # ✅ CHECK FOR ATF I/O
    atf_io = find("atf io")

    if atf_io:
        # ✅ USE ATF I/O IN SLOT 4
        ordered.append(atf_io)

        # ✅ THEN ADD ALL ATFs
        for k in ["atf 2", "atf 3", "atf 4", "atf 5", "atf 6"]:
            ordered.append(find(k))

    else:
        # ✅ SHIFT UP — KEEP ALL ATFs INCLUDING 6 ✅
        for k in ["atf 2", "atf 3", "atf 4", "atf 5", "atf 6"]:
            ordered.append(find(k))

    # ✅ KEEP STRUCTURE — DO NOT REMOVE NONE
    ordered = ordered[:8]

    # ✅ LIMIT TO 8 SLOTS
    ordered = ordered[:8]

    return [{"url": img} for img in ordered]

# =========================================
# ✅ CVS IMAGES (UNLIMITED + BEST RES)
# =========================================
def get_cvs_images(url):

    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^"]*',
        html
    )

    best_images = {}
    order = []

    for m in matches:
        full = "https://www.cvs.com" + m
        base = full.split("?")[0]
        name = base.split("/")[-1]

        # ✅ extract resolution
        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        # ✅ first time seen → preserve order
        if name not in best_images:
            order.append(name)
            best_images[name] = {
                "url": base,
                "size": size
            }
        else:
            # ✅ keep highest resolution
            if size > best_images[name]["size"]:
                best_images[name] = {
                    "url": base,
                    "size": size
                }

    # ✅ return in original PDP order
    return [best_images[name]["url"] for name in order]
    
# =========================================
# ✅ TEXT EXTRACTION
# =========================================
def get_salsify_text(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return {}

    data = json.loads(script.string)

    try:
        props = data["props"]["pageProps"]["product"]["propertySets"][0]["properties"]
    except:
        return {}

    text_map = {}

    for p in props:
        key = p.get("property")
        values = p.get("values", [])

        if values:
            text_map[key] = values[0]

    # ✅ RETURN EXACT FIELDS YOU WANT
    return {
        "title": text_map.get("PRODUCT_TITLE", ""),
        "description": text_map.get("DESCRIPTION", ""),
        "feature1": text_map.get("FEATURE_1", ""),
        "feature2": text_map.get("FEATURE_2", ""),
        "feature3": text_map.get("FEATURE_3", ""),
        "feature4": text_map.get("FEATURE_4", ""),
        "feature5": text_map.get("FEATURE_5", "")

    }
# =========================================
# ✅ CVS COPY EXTRACTION (FINAL WITH TITLE)
# =========================================
def get_cvs_text(html_text):

    import re
    import html
    from bs4 import BeautifulSoup

    if not html_text:
        return {"title": "", "description": "", "features": []}

    soup = BeautifulSoup(html_text, "html.parser")

    # ✅ REBUILD FULL STREAMED DATA (CRITICAL FIX)
    chunks = re.findall(
        r'self\.__next_f\.push\(\[1,"(.*?)"\]\)',
        html_text,
        re.DOTALL
    )
    
    combined = "".join(chunks)
    
    # ✅ CLEAN ESCAPED JSON
    combined = combined.replace('\\"', '"')
    combined = combined.replace('\\u0026', '&')
    combined = combined.replace('\\n', ' ')
    
    desc = ""
    features = []
    title = ""

    # =====================================
    # ✅ DIRECT FIELD EXTRACTION (FINAL FIX ✅)
    # =====================================
    try:
    
        # ✅ DESCRIPTION
        desc_match = re.search(
            r'vendorDetailsParagraph":"(.*?)"',
            combined
        )
        desc = html.unescape(desc_match.group(1)) if desc_match else ""
    
        # ✅ FEATURES
        bullet_match = re.search(
            r'vendorDetailsBullets":\[(.*?)\]',
            combined,
            re.DOTALL
        )
    
        features = []
        if bullet_match:
            parts = bullet_match.group(1).split('","')
            features = [html.unescape(p.strip(' "')) for p in parts if p.strip()]
    
        # ✅ TITLE (already working, keep it)
        title_match = re.search(r'"title":"(.*?)"', combined)
        title = title_match.group(1) if title_match else ""
    
        # ✅ ONLY RETURN IF DATA FOUND
        if desc or features:
            return {
                "title": title.strip(),
                "description": desc.strip(),
                "features": features
            }
    
    except Exception:
        pass

    # =====================================
    # ✅ DESCRIPTION
    # =====================================

    desc_match = re.search(
        r'vendorDetailsParagraph\\":\\"(.*?)\\"',
        combined
    )

    if desc_match:

        raw_desc = desc_match.group(1)

        # =====================================
        # ✅ HANDLE NESTED POINTERS ($32 → $34)
        # =====================================
        if raw_desc.startswith("$"):

            pointer = raw_desc.replace("$", "")

            nested_match = re.search(
                rf'{pointer}:\{{.*?"vendorDetailsParagraph":"\$(\d+)".*?\}}',
                combined,
                re.DOTALL
            )

            if nested_match:
                pointer = nested_match.group(1)
                raw_desc = f"${pointer}"

        # =====================================
        # ✅ POINTER CASE
        # =====================================
        if raw_desc.startswith("$"):

            pointer = raw_desc.replace("$", "")

            pointer_match = re.search(
                rf'{pointer}:(T\d+,.+)',
                combined,
                re.DOTALL
            )

            if pointer_match and pointer_match.lastindex:

                raw_text = pointer_match.group(1)

                # ✅ rebuild streaming chunks
                chunks = re.findall(
                    r'self\.__next_f\.push\(\[1,"(.*?)"\]\)',
                    combined,
                    re.DOTALL
                )

                for chunk in chunks:
                    raw_text += chunk

                # ✅ remove prefix
                raw_text = re.sub(r'^T\d+,', '', raw_text)

                # ✅ decode characters
                raw_text = raw_text.replace('\\u0026', '&')
                raw_text = raw_text.replace('\\"', '"')

                # ✅ remove stream artifacts
                raw_text = raw_text.replace('"])', '')
                raw_text = raw_text.replace('self.__next_f.push([1,"', '')
                raw_text = re.sub(r'</?script>', '', raw_text)

                raw_text = raw_text.replace('\n', ' ')

                # ✅ stop before JSON blocks
                raw_text = re.split(
                    rf'(?:\d+:{{|\d+:\[)',
                    raw_text
                )[0]

                # ✅ FIX ONLY TRUE BROKEN WORD SPLITS (SAFE)
                raw_text = re.sub(r'\b([A-Za-z])\s([a-z]{2,})\b', r'\1\2', raw_text)

                # ✅ remove trailing backslashes
                raw_text = re.sub(r'\\+$', '', raw_text)
                
                # ✅ normalize spacing
                raw_text = re.sub(r'\s+', ' ', raw_text).strip()

                desc = html.unescape(raw_text)

        # =====================================
        # ✅ NON-POINTER CASE
        # =====================================
        else:
            desc = html.unescape(raw_desc)

    # =====================================
    # ✅ FALLBACK SCAN (CRITICAL EDGE CASE FIX)
    # =====================================

    if not desc or len(desc) < 100:

        fallback_candidates = []
    
        for i in range(20, 41):
    
            m = re.search(
                rf'{i}:(T\d+,.+)',
                combined,
                re.DOTALL
            )
    
            if not m:
                continue
    
            raw_text = m.group(1)
    
            # ✅ rebuild chunks
            chunks = re.findall(
                r'self\.__next_f\.push\(\[1,"(.*?)"\]\)',
                combined,
                re.DOTALL
            )
    
            for chunk in chunks:
                raw_text += chunk
    
            # ✅ clean
            raw_text = re.sub(r'^T\d+,', '', raw_text)
            raw_text = raw_text.replace('\\u0026', '&')
            raw_text = raw_text.replace('\\"', '"')
    
            raw_text = raw_text.replace('"])', '')
            raw_text = raw_text.replace('self.__next_f.push([1,"', '')
            raw_text = re.sub(r'</?script>', '', raw_text)
    
            raw_text = raw_text.replace('\n', ' ')
    
            raw_text = re.split(
                rf'(?:\d+:{{|\d+:\[)',
                raw_text
            )[0]
    
            raw_text = re.sub(r'\s+', ' ', raw_text).strip()
    
            # ✅ 🚨 FILTER BAD BLOCKS
            
            if (
                "<div" in raw_text or
                "class=" in raw_text or
                "icon." in raw_text or
                "jojyo" in raw_text or
                "react" in raw_text.lower()
            ):
                continue

    
            # ✅ ✅ ACCEPT ONLY REAL PDP TEXT
            if (
                len(raw_text) > 200 and
                any(k in raw_text.lower() for k in [
                    "pad", "pads", "incontinence", "absorb", "protection", "leak"
                ])
            ):
                fallback_candidates.append(raw_text)
    
        if fallback_candidates:
            desc = html.unescape(max(fallback_candidates, key=len))

    # =====================================
    # ✅ FEATURES
    # =====================================

    bullet_match = re.search(
        r'vendorDetailsBullets\\":\[(.*?)\]',
        combined,
        re.DOTALL
    )
    
    if bullet_match:
        raw_block = bullet_match.group(1)
    
        # ✅ decode first
        raw_block = raw_block.encode().decode("unicode_escape")
    
        # ✅ split safely on ","
        parts = raw_block.split('","')
    
        for p in parts:
            clean = p.strip(' "')
            clean = clean.replace("\\", "")
            clean = html.unescape(clean)
    
            if len(clean) > 20:
                features.append(clean)

    # =====================================
    # ✅ TITLE (FIXED)
    # =====================================
    
    title_match = re.search(r'"title":"(.*?)"', combined)
    
    if not title_match:
        title_match = re.search(r'"displayName":"(.*?)"', combined)
    
    if not title_match:
        title_match = re.search(r'"productName":"(.*?)"', combined)
    
    if title_match:
        title = title_match.group(1).strip()
    else:
        title = ""

    # =====================================
    # ✅ FINAL SAFE RETURN
    # =====================================

    return {
        "title": title if isinstance(title, str) else "",
        "description": desc if isinstance(desc, str) else "",
        "features": features if isinstance(features, list) else []
    }

# =====================================
# ✅ CVS TEXT CLEANER (FINAL)
# =====================================
def clean_cvs_text(text):

    if not text:
        return ""

    # ✅ fix encoding once
    try:
        text = text.encode('latin1', errors='ignore').decode('utf-8', errors='ignore')
    except:
        pass

    # ✅ decode escape sequences once
    try:
        text = bytes(text, "utf-8").decode("unicode_escape")
    except:
        pass

    # ✅ remove slashes
    text = text.replace("\\", "")

    # ✅ HTML decode
    text = html.unescape(text)

    # ✅ remove junk
    text = re.sub(r'\$?\d+:\{.*?\}', '', text)
    text = re.sub(r'\$?\d+:\[.*?\]', '', text)
    text = re.sub(r'self\.__next_f\.push\(.*?\)', '', text)

    # ✅ normalize
    text = re.sub(r'\s+', ' ', text).strip()

    return text
    
# =========================================
# ✅ SCORE
# =========================================

def normalize_text(t):
    if not isinstance(t, str):
        return ""
    return re.sub(r'[^a-z0-9\s]', '', t.lower())

def keyword_score(a, b):
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)

# =========================================
# ✅ HELPERS
# =========================================
def equal_height_block(text):
    return f"""
    <div style="
        min-height: 180px;
        display: flex;
        align-items: flex-start;
    ">
        {text}
    </div>
    """

def equal_feature_block(text):
    return f"""
    <div style="
        min-height: 70px;
        display: flex;
        align-items: flex-start;
    ">
        {text}
    </div>
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
        padding:6px 10px;   /* ✅ smaller */
        border-radius:6px;
        color:white;
        font-weight:600;
        margin-top:6px;
        margin-bottom:6px;  /* ✅ reduce from 12px */
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
# ✅ TRUE IMAGE VISUAL COMPARISON
# =========================================
def load_image_with_white_bg(img_data):
    
    try:
        img = Image.open(BytesIO(img_data))

        # ✅ shrink BEFORE heavy processing
        img.thumbnail((256, 256))

        img = img.convert("RGBA")
    except:
        return None

    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))

    if img.mode == "RGBA":
        white_bg.paste(img, mask=img.split()[3])
    else:
        white_bg.paste(img)

    return white_bg.convert("L")
# =====================================
# ✅ STREAMLIT IMAGE CACHE (BIG SPEED BOOST)
# =====================================
@st.cache_data(show_spinner=False)
def process_row_cached(row_dict):
    return process_row(row_dict)
    
def fetch_image_cached(url):
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", ""):
            return resp.content
    except:
        return None
    return None
# =====================================
# ✅ IMAGE PREFETCH
# =====================================
def prefetch_images(urls):
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(fetch_image_cached, urls))
    
def compare_images_visually(s_url, r_url):
    try:
        
        def fetch_and_cache(url):
            return fetch_image_cached(url)

        # ✅ FETCH BOTH
        s_img_data = fetch_and_cache(s_url)
        r_img_data = fetch_and_cache(r_url)

        if not s_img_data or not r_img_data:
            return 0

        from PIL import ImageFilter

        # ✅ normalize + blur
        try:
            s_img = load_image_with_white_bg(s_img_data)
            r_img = load_image_with_white_bg(r_img_data)

            if s_img is None or r_img is None:
                return 0

            s_img = s_img.resize((64, 64)).filter(ImageFilter.GaussianBlur(2))
            r_img = r_img.resize((64, 64)).filter(ImageFilter.GaussianBlur(2))

        except:
            return 0

        import numpy as np

        s_arr = np.array(s_img)
        r_arr = np.array(r_img)

        if s_arr.shape != r_arr.shape:
            return 0

        diff = float(np.mean(np.abs(s_arr.astype("float32") - r_arr.astype("float32"))))

        # ✅ scoring buckets
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

    except:
        return 0
# =====================================
# ✅ IMAGE MATCHING
# =====================================
def match_images_visual(s_images, r_images):

    results = []

    max_len = max(len(s_images), len(r_images))

    for i in range(max_len):

        s_url = (
            s_images[i].get("url")
            if i < len(s_images) and isinstance(s_images[i], dict)
            else None
        )
        
        r_url = (
            r_images[i]
            if i < len(r_images) and isinstance(r_images[i], str)
            else None
        )

        # ✅ ✅ USE VISUAL COMPARISON (NOT STRING MATCH)
        if s_url and r_url:
            score = compare_images_visually(s_url, r_url)
        else:
            score = 0

        results.append((s_url, r_url, score))

    return results
    
def process_row(row):

    try:
        retail_html = get_html(row.get("retail_url", ""))
        s_text = get_salsify_text(row.get("salsify_url", ""))
        
        r_text = get_cvs_text(retail_html) or {}
        
        desc_raw = r_text.get("description", "")
        
        if any(x in desc_raw for x in ["\\", "self.__next_f", "\\u0026", "\\n"]):
            r_text["description"] = clean_cvs_text(desc_raw)
        else:
            r_text["description"] = desc_raw
            
        cleaned_features = []
        
        for f in r_text.get("features", []):
            if any(x in f for x in ["\\", "self.__next_f", "\\u0026", "\\n"]):
                cleaned_features.append(clean_cvs_text(f))
            else:
                cleaned_features.append(f)
        
        r_text["features"] = cleaned_features

        s_images = get_salsify_images(row.get("salsify_url", ""))
        r_images = get_cvs_images(row.get("retail_url", ""))
    
        # ✅ PREFETCH IMAGES
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

        # ✅ SCORES
        title_score = keyword_score(s_text.get("title", ""), r_text.get("title", ""))
        desc_score = keyword_score(s_text.get("description", ""), r_text.get("description", ""))

        cvs_features = r_text.get("features") if isinstance(r_text, dict) else []
        if not isinstance(cvs_features, list):
            cvs_features = []

        feature_scores = []

        for f_key in ["feature1","feature2","feature3","feature4","feature5"]:
            s_val = s_text.get(f_key, "")

            scores = [keyword_score(s_val, f) for f in cvs_features if isinstance(f, str)]
            feature_scores.append(max(scores) if scores else 0)

        avg_feature_score = int(sum(feature_scores)/len(feature_scores)) if feature_scores else 0

        # ✅ IMAGE SCORE
        img_scores = []

        for i in range(max(len(s_images), len(r_images))):
            s_url = s_images[i].get("url") if i < len(s_images) and isinstance(s_images[i], dict) else None
            r_url = r_images[i] if i < len(r_images) else None

            if s_url and r_url:
                sc = compare_images_visually(s_url, r_url)
                if sc > 0:
                    img_scores.append(sc)

        avg_img_score = int(sum(img_scores)/len(img_scores)) if img_scores else 0

        overall = int((title_score + desc_score + avg_feature_score + avg_img_score)/4)

        return {
            "SKU": row.get("sku", ""),
            "CVS RPC": row.get("cvs_rpc") or row.get("CVS RPC") or "",
            "Salsify URL": row.get("salsify_url", ""),
            "Retail URL": row.get("retail_url", ""),
            "Title %": title_score,
            "Description %": desc_score,
            "Feature %": avg_feature_score,
            "Image Match %": avg_img_score,
            "Overall %": overall
        }
        
    except:
        return None
        
# =========================================
# ✅ MAIN APP
# =========================================

# =====================================
# ✅ VIEW + FILTER CONTROLS (SAFE ✅)
# =====================================
st.markdown("## 🔎 QA Viewer Controls")

view_mode = st.checkbox(
    "👁️ View Full QA",
    key="view_mode",
    disabled=not st.session_state.processing_done
)

if st.session_state.get("processing_done", False) and not view_mode:
    st.success("✅ Processing complete")

show_only_issues = st.checkbox(
    "❌ Show ONLY Issues",
    key="show_issues"
)

hide_good = st.checkbox(
    "✅ Hide Strong Matches (80%+)",
    key="hide_good"
)


# =====================================
# ✅ FILE + PROCESSING
# =====================================
if uploaded_file:
    try:

        # ✅ RESET STATE ON NEW FILE
        if (
            "last_file" not in st.session_state or
            st.session_state.last_file != uploaded_file.name
        ):
            st.session_state.summary_rows = []
            st.session_state.export_rows = []
            st.session_state.start_idx = 0
            st.session_state.processing_done = False
            st.session_state.last_file = uploaded_file.name
    
        df = pd.read_csv(uploaded_file)
        
        # ✅ normalize columns
        df.columns = [c.strip().lower() for c in df.columns]
        
        column_map = {
            "salsify url": "salsify_url",
            "retail url": "retail_url",
            "sku id": "sku",
            "product sku": "sku",
            "cvs rpc": "cvs_rpc"
        }
        
        df.rename(columns=column_map, inplace=True)

        # ✅ ensure brand column exists (column E fallback)
        if "brand" not in df.columns:
            if len(df.columns) >= 5:
                df.rename(columns={df.columns[4]: "brand"}, inplace=True)

        required_cols = ["sku", "salsify_url", "retail_url"]

        missing = [c for c in required_cols if c not in df.columns]
        
        if missing:
            st.error(f"❌ Missing required columns: {missing}")
            st.write("Detected columns:", list(df.columns))
            st.stop()

        # ✅ BRAND FILTER (NEW)
        brands = sorted(df["brand"].dropna().unique()) if "brand" in df.columns else []
        selected_brand = st.selectbox("🏷️ Select Brand", ["All"] + brands)

        if selected_brand != "All":
            df = df[df["brand"] == selected_brand]

        BATCH_SIZE = 20
    
        start = st.session_state.start_idx
        end = start + BATCH_SIZE

        if start >= len(df):
            st.session_state.processing_done = True

        batch_df = df.iloc[start:end]
    
        # =====================================
        # ✅ OPTIONAL SAFETY (VIEW MODE RESET ✅)
        # =====================================
        if view_mode:
            st.session_state.start_idx = 0
    
        # =====================================
        # ✅ STATUS + PROGRESS
        # =====================================
        st.write(f"Processing SKUs {start+1} to {min(end, len(df))} of {len(df)}")
    
        progress_bar = st.progress(0)
        status_text = st.empty()
        total = len(batch_df)
        st.write("### Overall Progress")
        overall_progress_bar = st.progress(0)
    
        # =====================================
        # ✅ PROCESSING LOOP (FAST MODE)
        # =====================================
        if not st.session_state.processing_done:    
            results = []
    
                with ThreadPoolExecutor(max_workers=3) as executor:
    
                    futures = [
                        executor.submit(process_row_cached, row.to_dict())
                        for _, row in batch_df.iterrows()
                    ]
                
                    for i, future in enumerate(as_completed(futures)):
                        result = future.result()
                
                        if result:
                            results.append(result)
                
                            # ✅ summary
                            existing = {r["SKU"] for r in st.session_state.summary_rows}
                            if result["SKU"] not in existing:
                                st.session_state.summary_rows.append(result)
                
                            # ✅ export
                            existing_export = {r["SKU"] for r in st.session_state.export_rows}
                            if result["SKU"] not in existing_export:
                                st.session_state.export_rows.append({
                                    "SKU": result["SKU"],
                                    "CVS RPC": result["CVS RPC"],
                                    "Salsify URL": result["Salsify URL"],
                                    "Retail URL": result["Retail URL"]
                                })
                
                        progress_bar.progress((i + 1) / total)
                        status_text.markdown(f"Processed {i+1}/{total}")
                        
                        overall_progress = (start + i + 1) / len(df)
                        overall_progress_bar.progress(overall_progress)
    
                st.write(f"✅ Rows processed so far: {len(st.session_state.summary_rows)}")
                
                # ✅ AUTO-BATCH
                if st.session_state.start_idx + BATCH_SIZE < len(df):
                    st.session_state.start_idx += BATCH_SIZE
                    time.sleep(0.3)
                    st.rerun()
                else:
                    st.session_state.processing_done = True

        # =====================================
        # ✅ FULL VISUAL MODE (COMPLETE PDP QA ✅)
        # =====================================
        elif view_mode:
            if st.session_state.download_clicked:
                st.session_state.download_clicked = False
                st.stop()  # 🚀 prevents rerender
            
            for idx, (_, row) in enumerate(df.iterrows()):
        
                sku = row.get("sku", "Missing SKU")
        
                retail_html = get_html(row.get("retail_url", ""))
                s_text = get_salsify_text(row.get("salsify_url", ""))
                
                r_text = get_cvs_text(retail_html) or {}

                # ✅ DESCRIPTION (SAFE CLEAN)
                desc_raw = r_text.get("description", "")
                
                if any(x in desc_raw for x in ["\\", "self.__next_f", "\\u0026", "\\n"]):
                    r_text["description"] = clean_cvs_text(desc_raw)
                else:
                    r_text["description"] = desc_raw
                
                # ✅ FEATURES (THIS IS THE PART YOU MISSED)
                cleaned_features = []
                
                for f in r_text.get("features", []):
                    if any(x in f for x in ["\\", "self.__next_f", "\\u0026", "\\n"]):
                        cleaned_features.append(clean_cvs_text(f))
                    else:
                        cleaned_features.append(f)
                
                r_text["features"] = cleaned_features
                
                # ✅ THEN CONTINUE
                s_images = get_salsify_images(row.get("salsify_url", ""))
                r_images = get_cvs_images(row.get("retail_url", ""))

                # ✅ IMAGE COUNT CHECK (UI SIDE ✅)
                image_flags = []
                
                if len(r_images) < len(s_images):
                    image_flags.append(f"Missing {len(s_images) - len(r_images)} images")
                
                elif len(r_images) > len(s_images):
                    image_flags.append(f"{len(r_images) - len(s_images)} extra images")

                if not isinstance(s_images, list):
                    s_images = []
                        
                if not isinstance(r_images, list):
                    r_images = []
        
                # ✅ SAFE TEXT
                s_title = s_text.get("title") if isinstance(s_text, dict) else ""
                r_title = r_text.get("title") if isinstance(r_text, dict) else ""
                
                s_desc = s_text.get("description") if isinstance(s_text, dict) else ""
                r_desc = r_text.get("description") if isinstance(r_text, dict) else ""

                cvs_features = r_text.get("features") or []
                # ✅ MISSING CONTENT FLAGS (ADD HERE ✅)
                missing_flags = []
                
                if not s_title or not r_title:
                    missing_flags.append("Title")
                
                if not s_desc.strip() or not r_desc.strip():
                    missing_flags.append("Description")
                
                if not cvs_features:
                    missing_flags.append("Features")
                
                if not s_images or not r_images:
                    missing_flags.append("Images")
                    
                feature_fields = ["feature1","feature2","feature3","feature4","feature5"]
        
                # ✅ TITLE SCORE
                title_score = keyword_score(s_title, r_title)
        
                # ✅ DESCRIPTION SCORE
                desc_score = keyword_score(s_desc, r_desc)
        
                # ✅ FEATURES (POSITIONAL)
                feature_scores = []
                max_features = max(len(feature_fields), len(cvs_features))
        
                # ✅ IMAGE CALC
                img_scores = []
                max_images = max(len(s_images), len(r_images))
        
                for i in range(max_images):
                    
                    s_url = (
                        s_images[i].get("url")
                        if i < len(s_images) and isinstance(s_images[i], dict)
                        else None
                    )
                    
                    r_url = (
                        r_images[i]
                        if i < len(r_images) and isinstance(r_images[i], str)
                        else None
                    )

                    if s_url and r_url:
                        sc = compare_images_visually(s_url, r_url)
                        img_scores.append(sc)
        
                avg_img_score = int(sum(img_scores)/len(img_scores)) if img_scores else 0
        
                # ✅ FEATURE SCORE
                for i in range(max_features):
                    s_val = s_text.get(feature_fields[i], "") if i < len(feature_fields) else ""
                    r_val = cvs_features[i] if i < len(cvs_features) else ""
        
                    feature_scores.append(keyword_score(s_val, r_val))
        
                avg_feature_score = int(sum(feature_scores)/len(feature_scores)) if feature_scores else 0
        
                # ✅ OVERALL
                overall_score = int((title_score + desc_score + avg_feature_score + avg_img_score)/4)
                
                # ✅ HARD FAIL DETECTION (ADD HERE ✅)
                hard_fail = title_score < 40 or desc_score < 40
        
                # ✅ FILTERS
                is_issue = overall_score < 80
                if show_only_issues and not is_issue:
                    continue
                if hide_good and overall_score >= 80:
                    continue

        
                # =====================================
                # ✅ RENDER UI
                # =====================================
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

                # --------------------
                # ✅ TITLE
                # --------------------
                st.markdown("""
                <div style='display:flex; flex-direction:column; height:100%;'>
                """, unsafe_allow_html=True)

                with left:
                    
                    st.markdown(f"### 🏷️ Title {score_badge(title_score)}", unsafe_allow_html=True)
                    c1, c2 = st.columns(2)
                    c1.markdown(
                        f"<div style='font-size:26px; line-height:1.5'>{s_title or '❌ Missing'}</div>",
                        unsafe_allow_html=True
                    )
                    
                    c2.markdown(
                        f"<div style='font-size:26px; line-height:1.5'>{r_title or '❌ Missing'}</div>",
                        unsafe_allow_html=True
                    )
                    # --------------------
                    # ✅ DESCRIPTION
                    # --------------------
                    st.markdown(f"### 📄 Description {score_badge(desc_score)}", unsafe_allow_html=True)
                    c1, c2 = st.columns(2)
                    c1.markdown(
                        f"<div style='font-size:25px; line-height:1.5'>{s_desc or '❌ Missing'}</div>",
                        unsafe_allow_html=True
                    )
                    
                    c2.markdown(
                        f"<div style='font-size:25px; line-height:1.5'>{r_desc or '❌ Missing'}</div>",
                        unsafe_allow_html=True
                    )

            
                    # --------------------
                    # ✅ FEATURES (SIDE-BY-SIDE ✅)
                    # --------------------
                    st.markdown(f"### 📌 Features {score_badge(avg_feature_score)}", unsafe_allow_html=True)
            
                    for i in range(max_features):
            
                        s_val = s_text.get(feature_fields[i], "") if i < len(feature_fields) else ""
                        r_val = cvs_features[i] if i < len(cvs_features) else ""
            
                        score = keyword_score(s_val, r_val)
            
                        c1, c2 = st.columns(2)
                        c1.markdown(
                            f"<div style='font-size:25px; line-height:1.5'>{s_val or '❌ Missing'}</div>",
                            unsafe_allow_html=True
                        )
                        
                        c2.markdown(
                            f"<div style='font-size:25px; line-height:1.5'>{r_val or '❌ Missing'}</div>",
                            unsafe_allow_html=True
                        )
            
                        st.markdown(score_badge(score), unsafe_allow_html=True)
                        st.divider()
            
                    st.markdown(score_bar(avg_feature_score), unsafe_allow_html=True)
                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
                    st.markdown("<div style='flex-grow:1'></div>", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)
                    
                    if len(r_images) > len(s_images):
                        st.markdown(
                            f"<div style='min-height:{len(r_images)*120}px'></div>",
                            unsafe_allow_html=True
                        )

        
                # --------------------
                # ✅ IMAGES (ALL + SCORES ✅)
                # --------------------
                with right:
                
                    st.markdown(f"### 🖼️ Images — Avg {score_badge(avg_img_score)}", unsafe_allow_html=True)
                    
                    st.markdown(score_bar(avg_img_score), unsafe_allow_html=True)
                
                    # ✅ controlled spacing
                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
                
                    max_images = max(len(s_images), len(r_images))
                
                    for i in range(max_images):
                
                        col1, col2, col3 = st.columns([3, 3, 1])
                
                        # ✅ get urls safely
                        s_url = (
                            s_images[i].get("url")
                            if i < len(s_images) and isinstance(s_images[i], dict)
                            else None
                        )
                        
                        r_url = (
                            r_images[i]
                            if i < len(r_images) and isinstance(r_images[i], str)
                            else None
                        )
                
                        # ✅ SALSIFY IMAGE
                        if s_url:
                            col1.markdown(
                                f"<img src='{s_url}' style='width:100%; max-width:200px; border-radius:6px;'>",
                                unsafe_allow_html=True
                            )
                        else:
                            col1.write("")
                
                        # ✅ CVS IMAGE
                        if r_url:
                            col2.markdown(
                                f"<img src='{r_url}' style='width:100%; max-width:200px; border-radius:6px;'>",
                                unsafe_allow_html=True
                            )
                        else:
                            col2.write("")
                
                        # ✅ SCORE
                        sc = compare_images_visually(s_url, r_url) if (s_url and r_url) else 0
                
                        col3.markdown(score_badge(sc), unsafe_allow_html=True)


                # --------------------
                # ✅ FINAL SCORE
                # --------------------
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
                    f"Title: {title_score}% | "
                    f"Desc: {desc_score}% | "
                    f"Feat: {avg_feature_score}% | "
                    f"Img: {avg_img_score}%"
                )
                
    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))
        st.text(traceback.format_exc())

# =====================================
# ✅ EXPORT FILE
# =====================================
if st.session_state.summary_rows:

    summary_df = pd.DataFrame(st.session_state.summary_rows)
    detail_df = pd.DataFrame(st.session_state.export_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        detail_df.to_excel(writer, index=False, sheet_name="Details")

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
        if download_placeholder.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ):
            st.session_state.download_clicked = True



