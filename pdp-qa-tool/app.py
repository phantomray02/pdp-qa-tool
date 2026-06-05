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
    
if st.session_state.processing_done:
    st.success("✅ All SKUs processed")

# =========================================
# ✅ CACHE HTML
# =========================================
html_cache = {}

def get_html(url):
    if url in html_cache:
        return html_cache[url]

    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            html_cache[url] = r.text
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

    images = []

    for prop in properties:

        values = prop.get("values", [])
        if not values:
            continue

        first = values[0]
        url = first.get("value", "")

        if not url:
            continue

        clean = url.split("?")[0]

        images.append({
            "url": clean,
            "type": prop.get("property", "")
        })

    return images[:8]

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

    for m in matches:
        full = "https://www.cvs.com" + m
        base = full.split("?")[0]
        name = base.split("/")[-1]

        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in best_images or size > best_images[name]["size"]:
            best_images[name] = {
                "url": base,
                "size": size
            }

    return [v["url"] for v in best_images.values()]
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

    combined = ""
    for s in soup.find_all("script"):
        if s.string:
            combined += s.string

    desc = ""
    features = []
    title = ""

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

        for x in re.findall(r'"(.*?)"', raw_block):
            clean = html.unescape(x).strip()
            if len(clean) > 20:
                features.append(clean)

    # =====================================
    # ✅ TITLE
    # =====================================

    title_match = re.search(
        r'"productName":"(.*?)"',
        combined
    )

    if not title_match:
        title_match = re.search(
            r'"name":"(.*?)"',
            combined
        )

    if title_match:
        title = title_match.group(1).strip()

    # =====================================
    # ✅ FINAL SAFE RETURN
    # =====================================

    return {
        "title": title if isinstance(title, str) else "",
        "description": desc if isinstance(desc, str) else "",
        "features": features if isinstance(features, list) else []
    }

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
# =========================================
# ✅ TRUE IMAGE VISUAL COMPARISON
# =========================================
image_cache = {}

def load_image_with_white_bg(img_data):
    
    try:
        img = Image.open(BytesIO(img_data)).convert("RGBA")
    except:
        return None

    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))

    if img.mode == "RGBA":
        white_bg.paste(img, mask=img.split()[3])
    else:
        white_bg.paste(img)

    return white_bg.convert("L")


def compare_images_visually(s_url, r_url):
    try:
        # ✅ SAFE CACHE DOWNLOAD — SALSIFY
        if s_url in image_cache:
            s_img_data = image_cache[s_url]
        else:
            try:
                resp = requests.get(s_url, timeout=5)
                if resp.status_code != 200 or "image" not in resp.headers.get("Content-Type", ""):
                    return 0
                s_img_data = resp.content
                image_cache[s_url] = s_img_data
            except:
                return 0
        
        # ✅ SAFE CACHE DOWNLOAD — CVS
        if r_url in image_cache:
            r_img_data = image_cache[r_url]
        else:
            try:
                resp = requests.get(r_url, timeout=5)
                if resp.status_code != 200 or "image" not in resp.headers.get("Content-Type", ""):
                    return 0
                r_img_data = resp.content
                image_cache[r_url] = r_img_data
            except:
                return 0
                
        from PIL import ImageFilter

        # ✅ normalize + blur (SAFE)
        try:
            s_img = load_image_with_white_bg(s_img_data)
            r_img = load_image_with_white_bg(r_img_data)
        
            if s_img is None or r_img is None:
                return 0
        
            s_img = s_img.resize((64, 64)).filter(ImageFilter.GaussianBlur(2))
            r_img = r_img.resize((64, 64)).filter(ImageFilter.GaussianBlur(2))
        
        except:
            return 0


        diff = sum(
            abs(a - b)
            for a, b in zip(s_img.getdata(), r_img.getdata())
        ) / (64 * 64)

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

        s_url = s_images[i]["url"] if i < len(s_images) else None
        r_url = r_images[i] if i < len(r_images) else None

        # ✅ ✅ USE VISUAL COMPARISON (NOT STRING MATCH)
        if s_url and r_url:
            score = compare_images_visually(s_url, r_url)
        else:
            score = 0

        results.append((s_url, r_url, score))

    return results
# =========================================
# ✅ MAIN APP
# =========================================

if uploaded_file:

    df = pd.read_csv(uploaded_file)

    BATCH_SIZE = 20

    start = st.session_state.start_idx
    end = start + BATCH_SIZE

    batch_df = df.iloc[start:end]

    st.write(f"Processing SKUs {start+1} to {min(end, len(df))} of {len(df)}")

    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(batch_df)

    for i, (_, row) in enumerate(batch_df.iterrows()):
        try:

            status_text.write(f"Processing SKU {row.get('sku','')} ({i+1}/{total})")
            with st.expander(f"SKU: {row['sku']}", expanded=False):

            # ✅ LOAD DATA
            retail_html = get_html(row.get("retail_url", ""))
            s_text = get_salsify_text(row.get("salsify_url", ""))
            r_text = get_cvs_text(retail_html) or {
                "title": "",
                "description": "",
                "features": []
            }

            s_images = get_salsify_images(row.get("salsify_url", ""))
            r_images = get_cvs_images(row.get("retail_url", ""))

            if not isinstance(s_images, list):
                s_images = []
            if not isinstance(r_images, list):
                r_images = []

            # =====================================
            # ✅ IMAGE CLEANUP (LOCK FIRST 3)
            # =====================================
            def is_ooi(img):
                if not img:
                    return False
                t = img.get("type", "").lower().replace(" ", "")
                return "onlineoptimized" in t

            adjusted = []
            remaining = s_images.copy()

            for slot in range(3):
                if len(remaining) > 0 and is_ooi(remaining[0]):
                    adjusted.append(remaining.pop(0))
                else:
                    adjusted.append(None)

            adjusted.extend(remaining)

            seen_urls = set()
            final_images = []

            for img in adjusted:
                if img is None:
                    final_images.append(None)
                    continue

                url = img.get("url")
                if url and url not in seen_urls:
                    final_images.append(img)
                    seen_urls.add(url)

            s_images = final_images[:8]

            # =====================================
            # ✅ COPY SCORES
            # =====================================
            title_score = keyword_score(s_text.get("title", ""), r_text.get("title", ""))
            desc_score = keyword_score(s_text.get("description", ""), r_text.get("description", ""))

            cvs_features = r_text.get("features") or []
            feature_scores = []

            for f_key in ["feature1","feature2","feature3","feature4","feature5"]:
                s_val = s_text.get(f_key, "")
                best = max([keyword_score(s_val, f) for f in cvs_features], default=0)
                feature_scores.append(best)

            avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0

            # =====================================
            # ✅ IMAGE COMPARISON (LOCK SALSIFY, FREE CVS)
            # =====================================
            from itertools import zip_longest

            image_row_scores = []
            img_scores = []

            max_len = max(len(s_images), len(r_images))

            for idx in range(max_len):

                if idx < len(s_images) and s_images[idx]:
                    s_url = s_images[idx]["url"]
                else:
                    s_url = None

                if idx < len(r_images):
                    r_url = r_images[idx]
                else:
                    r_url = None

                if s_url and r_url:
                    sc = compare_images_visually(s_url, r_url)
                else:
                    sc = 0

                sc = min(100, sc)

                if sc > 0:
                    img_scores.append(sc)

                image_row_scores.append(sc)

            avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0

            # =====================================
            # ✅ OVERALL SCORE
            # =====================================
            overall_score = int(
                (title_score + desc_score + avg_feature_score + avg_img_score) / 4
            )

            # =====================================
            # ✅ SAVE RESULTS (CORRECT PLACE ✅)
            # =====================================
            summary_row = {
                "SKU": row.get("sku", ""),
                "CVS RPC": row.get("cvs_rpc") or row.get("CVS RPC") or "",
                "Title %": title_score,
                "Description %": desc_score,
                "Feature %": avg_feature_score,
                "Image Match %": avg_img_score,
                "Overall %": overall_score
            }

            for idx in range(8):
                summary_row[f"Image {idx+1} %"] = (
                    image_row_scores[idx] if idx < len(image_row_scores) else ""
                )

            st.session_state.summary_rows.append(summary_row)

            export_row = {
                "SKU": row.get("sku", ""),
                "CVS RPC": row.get("cvs_rpc") or row.get("CVS RPC") or "",
                "Salsify URL": row.get("salsify_url", ""),
                "Retail URL": row.get("retail_url", ""),
                "Salsify Title": s_text.get("title", ""),
                "CVS Title": r_text.get("title", ""),
                "Salsify Description": s_text.get("description", ""),
                "CVS Description": r_text.get("description", "")
            }

            st.session_state.export_rows.append(export_row)

            # ✅ PROGRESS
            progress_bar.progress((i + 1) / total)

        except Exception as e:
            st.error(f"❌ Error processing SKU: {row.get('sku','')}")
            continue

    # =====================================
    # ✅ AUTO-BATCH NEXT (OUTSIDE LOOP ✅)
    # =====================================
    if st.session_state.start_idx + BATCH_SIZE < len(df):
        st.session_state.start_idx += BATCH_SIZE
        st.rerun()
    else:
        st.session_state.processing_done = True
        
from openpyxl import load_workbook
from openpyxl.styles import PatternFill

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

    # ✅ APPLY COLOR FORMATTING
    wb = load_workbook(file_name)
    ws = wb["Summary"]
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    yellow = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

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
        download_placeholder.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


