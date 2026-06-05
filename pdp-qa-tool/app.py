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
    
if st.session_state.processing_done and not st.session_state.get("view_mode", False):
    st.success("✅ Processing complete")

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

# =====================================
# ✅ VIEW + FILTER CONTROLS (SAFE ✅)
# =====================================
st.markdown("## 🔎 QA Viewer Controls")

view_mode = st.checkbox(
    "👁️ View Full QA (after processing)",
    key="view_mode"
)

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
    
        BATCH_SIZE = 20
    
        start = st.session_state.start_idx
        end = start + BATCH_SIZE
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
        if not view_mode and not st.session_state.processing_done:
        
            for i, (_, row) in enumerate(batch_df.iterrows()):
                try:
                    
                    status_text.markdown(
                        f"**Processing SKU:** {row.get('sku','')}  \n"
                        f"**Batch Progress:** {i+1}/{total}  \n"
                        f"**Overall Progress:** {start + i + 1}/{len(df)}"
                    )
        
                    # ✅ LOAD DATA
                    retail_html = get_html(row.get("retail_url", ""))
                    s_text = get_salsify_text(row.get("salsify_url", ""))
                    r_text = get_cvs_text(retail_html) or {}
        
                    s_images = get_salsify_images(row.get("salsify_url", ""))
                    r_images = get_cvs_images(row.get("retail_url", ""))
        
                    # ✅ SCORES
                    title_score = keyword_score(s_text.get("title", ""), r_text.get("title", ""))
                    desc_score = keyword_score(s_text.get("description", ""), r_text.get("description", ""))
        
                    cvs_features = r_text.get("features") or []
                    feature_scores = []
        
                    for f_key in ["feature1","feature2","feature3","feature4","feature5"]:
                        s_val = s_text.get(f_key, "")
                        best = max([keyword_score(s_val, f) for f in cvs_features], default=0)
                        feature_scores.append(best)
        
                    avg_feature_score = int(sum(feature_scores) / len(feature_scores)) if feature_scores else 0
        
                    # ✅ IMAGE SCORE
                    img_scores = []
                    image_row_scores = []
        
                    max_len = max(len(s_images), len(r_images))
        
                    for idx in range(max_len):
        
                        s_url = s_images[idx]["url"] if idx < len(s_images) and s_images[idx] else None
                        r_url = r_images[idx] if idx < len(r_images) else None
        
                        if s_url and r_url:
                            sc = compare_images_visually(s_url, r_url)
                        else:
                            sc = 0
        
                        if sc > 0:
                            img_scores.append(sc)
        
                        image_row_scores.append(sc)
        
                    avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0
        
                    overall_score = int(
                        (title_score + desc_score + avg_feature_score + avg_img_score) / 4
                    )
        
                    # ✅ RESULT ROWS
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
        
                    export_row = {
                        "SKU": row.get("sku", ""),
                        "CVS RPC": row.get("cvs_rpc") or row.get("CVS RPC") or "",
                        "Salsify URL": row.get("salsify_url", ""),
                        "Retail URL": row.get("retail_url", "")
                    }
        
                    # ✅ DEDUPE
                    existing_skus = {r["SKU"] for r in st.session_state.summary_rows}
                    if summary_row["SKU"] not in existing_skus:
                        st.session_state.summary_rows.append(summary_row)
        
                    existing_export = {r["SKU"] for r in st.session_state.export_rows}
                    if export_row["SKU"] not in existing_export:
                        st.session_state.export_rows.append(export_row)
        
                    # ✅ PROGRESS
                    progress_bar.progress((i + 1) / total)
        
                    overall_progress = (start + i + 1) / len(df)
                    overall_progress_bar.progress(overall_progress)
        
                except Exception as e:
                    st.error(f"❌ Error processing SKU: {row.get('sku','')}")
                    continue
        
            # ✅ AUTO-BATCH (CORRECT ✅)
            if st.session_state.start_idx + BATCH_SIZE < len(df):
                st.session_state.start_idx += BATCH_SIZE
                import time
                time.sleep(0.3)
                st.rerun()
            else:
                st.session_state.processing_done = True
        
            # ✅ DEBUG
            skus = [r["SKU"] for r in st.session_state.summary_rows]
            st.write("✅ Unique SKUs:", len(set(skus)))


        # =====================================
        # ✅ FULL VISUAL MODE (COMPLETE PDP QA ✅)
        # =====================================
        else:
            st.markdown("## 👁️ Full Visual QA Review")
        
            for _, row in df.iterrows():
        
                sku = row.get("sku", "Missing SKU")
        
                retail_html = get_html(row.get("retail_url", ""))
                s_text = get_salsify_text(row.get("salsify_url", ""))
                r_text = get_cvs_text(retail_html) or {}
        
                s_images = get_salsify_images(row.get("salsify_url", ""))
                r_images = get_cvs_images(row.get("retail_url", ""))
        
                # ✅ SAFE TEXT
                s_title = s_text.get("title") or ""
                r_title = r_text.get("title") or ""
        
                s_desc = s_text.get("description") or ""
                r_desc = r_text.get("description") or ""
        
                cvs_features = r_text.get("features") or []
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
                    s_url = s_images[i]["url"] if i < len(s_images) else None
                    r_url = r_images[i] if i < len(r_images) else None
        
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
        
                # ✅ FILTERS
                is_issue = overall_score < 80
                if show_only_issues and not is_issue:
                    continue
                if hide_good and overall_score >= 80:
                    continue
        
                # =====================================
                # ✅ RENDER UI
                # =====================================
                st.subheader(f"SKU: {sku}")
        
                # --------------------
                # ✅ TITLE
                # --------------------
                st.markdown("### 🏷️ Title")
                c1, c2 = st.columns(2)
                c1.write(s_title or "❌ Missing")
                c2.write(r_title or "❌ Missing")
                st.write(f"Score: {title_score}%")
        
                # --------------------
                # ✅ DESCRIPTION
                # --------------------
                st.markdown("### 📄 Description")
                c1, c2 = st.columns(2)
                c1.write(s_desc or "❌ Missing")
                c2.write(r_desc or "❌ Missing")
                st.write(f"Score: {desc_score}%")
        
                # --------------------
                # ✅ FEATURES (SIDE-BY-SIDE ✅)
                # --------------------
                st.markdown("### 📌 Features")
        
                for i in range(max_features):
        
                    s_val = s_text.get(feature_fields[i], "") if i < len(feature_fields) else ""
                    r_val = cvs_features[i] if i < len(cvs_features) else ""
        
                    score = keyword_score(s_val, r_val)
        
                    c1, c2 = st.columns(2)
                    c1.write(s_val or "❌ Missing")
                    c2.write(r_val or "❌ Missing")
        
                    st.write(f"Score: {score}%")
                    st.divider()
        
                st.write(f"✅ Feature Avg: {avg_feature_score}%")
        
                # --------------------
                # ✅ IMAGES (ALL + SCORES ✅)
                # --------------------
                st.markdown("### 🖼️ Images")
                
                max_images = max(len(s_images), len(r_images))
                
                for i in range(max_images):
                
                    col1, col2, col3 = st.columns([4,4,1])
                
                    # ✅ HANDLE NONE CORRECTLY
                    s_url = s_images[i]["url"] if i < len(s_images) and s_images[i] else None
                    r_url = r_images[i] if i < len(r_images) else None
                
                    # ✅ SALSIFY DISPLAY (KEEP THIS FLAG ✅)
                    if s_url:
                        col1.image(s_url)
                    else:
                        col1.write("❌ Missing")
                        col1.markdown("🚨 Missing Salsify Asset")
                
                    # ✅ CVS DISPLAY (CLEAN — NO FLAGS ✅)
                    if r_url:
                        col2.image(r_url)
                    else:
                        col2.write("❌ Missing")
                
                    # ✅ SCORE
                    if s_url and r_url:
                        sc = compare_images_visually(s_url, r_url)
                    else:
                        sc = 0
                
                    # ✅ COLOR ONLY (NO FLAGS)
                    if sc >= 80:
                        col3.markdown(f"✅ {sc}%")
                    elif sc >= 50:
                        col3.markdown(f"🟡 {sc}%")
                    else:
                        col3.markdown(f"🔴 {sc}%")
                        
                
                # ✅ IMAGE AVG
                valid_img_scores = []
                
                for i in range(max_images):
                    s_url = s_images[i]["url"] if i < len(s_images) and s_images[i] else None
                    r_url = r_images[i] if i < len(r_images) else None
                
                    if s_url and r_url:
                        valid_img_scores.append(compare_images_visually(s_url, r_url))
                
                avg_img_score = int(sum(valid_img_scores)/len(valid_img_scores)) if valid_img_scores else 0
                
                st.write(f"✅ Image Avg: {avg_img_score}%")
                
                
                # --------------------
                # ✅ FINAL SCORE
                # --------------------
                st.success(f"✅ Overall Score: {overall_score}%")
                st.divider()

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
        download_placeholder.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )



