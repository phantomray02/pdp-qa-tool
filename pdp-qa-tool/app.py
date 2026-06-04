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
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
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
        "description": desc.strip() if isinstance(desc, str) else "",
        "features": features if isinstance(features, list) else []
    }
# =========================================
# ✅ SCORE
# =========================================
def normalize_text(t):
    return re.sub(r'[^a-z0-9\s]', '', str(t).lower())

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
    img = Image.open(BytesIO(img_data)).convert("RGBA")

    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))

    if img.mode == "RGBA":
        white_bg.paste(img, mask=img.split()[3])
    else:
        white_bg.paste(img)

    return white_bg.convert("L")


def compare_images_visually(s_url, r_url):
    try:
        # ✅ CACHE DOWNLOAD
        if s_url in image_cache:
            s_img_data = image_cache[s_url]
        else:
            s_img_data = requests.get(s_url, timeout=5).content
            image_cache[s_url] = s_img_data

        if r_url in image_cache:
            r_img_data = image_cache[r_url]
        else:
            r_img_data = requests.get(r_url, timeout=5).content
            image_cache[r_url] = r_img_data

        from PIL import ImageFilter

        # ✅ normalize + blur
        s_img = load_image_with_white_bg(s_img_data).resize((64, 64)).filter(ImageFilter.GaussianBlur(2))
        r_img = load_image_with_white_bg(r_img_data).resize((64, 64)).filter(ImageFilter.GaussianBlur(2))

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

summary_rows = []
export_rows = []

if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # ✅ Load data
        retail_html = get_html(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(retail_html) or {
            "title": "",
            "description": "",
            "features": []
        }

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        # =====================================
        # ✅ IMAGE LOGIC (FINAL FIX — NO SHIFT BUG)
        # =====================================

        def is_ooi(img):
            if not img:
                return False
            t = img.get("type", "").lower().replace(" ", "")
            return "onlineoptimized" in t

        adjusted = []
        remaining = s_images.copy()

        # ✅ SLOT 1 ONLY — enforce OOI
        if remaining and is_ooi(remaining[0]):
            adjusted.append(remaining.pop(0))
        else:
            adjusted.append(None)

        # ✅ KEEP NATURAL ORDER (DO NOT FORCE SLOT 2/3)
        # This prevents your "2nd image shifting incorrectly" bug
        adjusted.extend(remaining)

        # ✅ REMOVE DUPLICATES
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

        # ✅ CAP LIST
        MAX_IMAGES = 8
        s_images = final_images[:MAX_IMAGES]

        # =====================================
        # ✅ COPY COMPARISON
        # =====================================
        st.markdown("## Copy Comparison")

        # -------------------------------------
        # ✅ TITLE
        # -------------------------------------
        st.markdown("### Title")

        c1, c2 = st.columns(2)

        c1.markdown("**Salsify**")
        c1.markdown(
            equal_height_block(s_text.get("title", "")),
            unsafe_allow_html=True
        )

        c2.markdown("**CVS**")
        cvs_title = r_text.get("title", "")
        c2.markdown(
            equal_height_block(cvs_title),
            unsafe_allow_html=True
        )

        title_score = min(100, keyword_score(
            s_text.get("title", ""),
            cvs_title
        ))

        # ✅ SCORE BAR BELOW
        if title_score >= 80:
            st.success(f"✅ Strong match: {title_score}%")
        elif title_score >= 50:
            st.warning(f"⚠️ Moderate match: {title_score}%")
        else:
            st.error(f"❌ Weak match: {title_score}%")

        # -------------------------------------
        # ✅ DESCRIPTION
        # -------------------------------------
        st.markdown("### Description")

        c1, c2 = st.columns(2)

        c1.markdown("**Salsify**")
        c1.markdown(
            equal_height_block(s_text.get("description", "")),
            unsafe_allow_html=True
        )

        c2.markdown("**CVS**")
        cvs_desc = r_text.get("description", "")
        c2.markdown(
            equal_height_block(cvs_desc),
            unsafe_allow_html=True
        )

        desc_score = min(100, keyword_score(
            s_text.get("description", ""),
            cvs_desc
        ))

        # ✅ SCORE BAR BELOW
        if desc_score >= 80:
            st.success(f"✅ Strong match: {desc_score}%")
        elif desc_score >= 50:
            st.warning(f"⚠️ Moderate match: {desc_score}%")
        else:
            st.error(f"❌ Weak match: {desc_score}%")

        # =====================================
        # ✅ FEATURE COMPARISON
        # =====================================
        st.markdown("## Feature Comparison")
        
        feature_fields = [
            ("Feature 1", "feature1"),
            ("Feature 3", "feature3"),
            ("Feature 4", "feature4"),
            ("Feature 5", "feature5"),
        ]
        
        cvs_features = r_text.get("features", [])
        
        # ✅ INIT FEATURE SCORES
        feature_scores = []
        
        for label, key in feature_fields:
        
            st.markdown(f"### {label}")
        
            col1, col2 = st.columns(2)
        
            s_val = s_text.get(key, "")
        
            # ✅ LEFT (Salsify)
            col1.markdown("**Salsify**")
            col1.markdown(
                equal_feature_block(s_val),
                unsafe_allow_html=True
            )
        
            # ✅ MATCHING
            best_score = 0
            best_match = ""
        
            for f in cvs_features:
                score = keyword_score(s_val, f)
        
                if any(word in f.lower() for word in s_val.lower().split()[:3]):
                    score += 5
        
                if score > best_score:
                    best_score = score
                    best_match = f
        
            # ✅ FALLBACK
            if not best_match and cvs_features:
                best_match = cvs_features[0]
        
            best_score = min(100, best_score)
        
            # ✅ STORE SCORE
            feature_scores.append(best_score)
        
            # ✅ RIGHT (CVS)
            col2.markdown("**CVS**")
            col2.markdown(
                equal_feature_block(best_match),
                unsafe_allow_html=True
            )
        
            # ✅ SCORE BAR
            if best_score >= 80:
                st.success(f"✅ Strong match: {best_score}%")
            elif best_score >= 50:
                st.warning(f"⚠️ Moderate match: {best_score}%")
            else:
                st.error(f"❌ Weak match: {best_score}%")
        
        # =====================================
        # ✅ FEATURE SCORE (AVG)
        # =====================================
        if feature_scores:
            avg_feature_score = int(sum(feature_scores) / len(feature_scores))
        else:
            avg_feature_score = 0
        
        avg_feature_score = min(100, avg_feature_score)
        
        # ✅ DISPLAY FEATURE SCORE
        st.markdown("### ✅ Feature Score Summary")
        
        if avg_feature_score >= 80:
            st.success(f"✅ Feature Match: {avg_feature_score}%")
        elif avg_feature_score >= 50:
            st.warning(f"⚠️ Feature Match: {avg_feature_score}%")
        else:
            st.error(f"❌ Feature Match: {avg_feature_score}%")
        
        # =====================================
        # ✅ IMAGE COMPARISON
        # =====================================
        img_scores = []
        image_row_scores = []   # ✅ REQUIRED
        
        st.markdown("## Image Comparison ✅")
        
        st.write(f"Salsify Images: {len(s_images)} | CVS Images: {len(r_images)}")
        
        from itertools import zip_longest

        # ✅ shift-based pairing (no index dependency)
        image_pairs = list(zip_longest(s_images, r_images, fillvalue=None))
        
        if not image_pairs:
            st.warning("No images found to compare.")
        else:
            for s, r in image_pairs:
        
                c1, c2, c3 = st.columns([4, 4, 1])
        
                # ✅ Salsify (already cleaned → no gaps)
                
                if s:
                    s_url = s["url"]
                    c1.image(s_url, use_container_width=True)
                else:
                    c1.markdown("**Missing**")

        
                # ✅ CVS
                if r:
                    c2.image(r, use_container_width=True)
                else:
                    c2.write("")
        
                # ✅ score (optional, keeps your logic intact)
                if s and r:
                    s_url = s.get("url") if isinstance(s, dict) else s
                    sc = compare_images_visually(s_url, r)
                else:
                    sc = 0
                
                # ✅ store score for averaging later
                
                sc = min(100, sc)
                
                # ✅ store for avg
                if sc > 0:
                    img_scores.append(sc)
                
                # ✅ store for export (row-level)
                image_row_scores.append(sc)


        
                c3.write(f"{sc}%")
        
        # =====================================
        # ✅ IMAGE SCORE
        # =====================================
        avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0
        
        if avg_img_score >= 80:
            st.success(f"✅ Image Match: {avg_img_score}%")
        elif avg_img_score >= 50:
            st.warning(f"⚠️ Image Match: {avg_img_score}%")
        else:
            st.error(f"❌ Image Match: {avg_img_score}%")
        
        # =====================================
        # ✅ OVERALL SCORE
        # =====================================
        overall_score = int(
            (title_score + desc_score + avg_feature_score + avg_img_score) / 4
        )
        
        st.markdown("## ✅ Overall Score")
        
        if overall_score >= 80:
            st.success(f"✅ Overall QA Score: {overall_score}%")
        elif overall_score >= 50:
            st.warning(f"⚠️ Overall QA Score: {overall_score}%")
        else:
            st.error(f"❌ Overall QA Score: {overall_score}%")
        
        
        # =====================================
        # ✅ SUMMARY SHEET
        # =====================================
        summary_row = {
            "SKU": row["sku"],
            "CVS RPC": row.get("cvs_rpc", ""),   # ✅ add this
        
            "Title %": title_score,
            "Description %": desc_score,
            "Feature %": avg_feature_score,
        }
        
        # ✅ IMAGE COLUMNS (1–8)
        for i in range(8):
            if i < len(image_row_scores):
                summary_row[f"Image {i+1} %"] = image_row_scores[i]
            else:
                summary_row[f"Image {i+1} %"] = ""
        
        summary_row["Image Match %"] = avg_img_score
        summary_row["Overall %"] = overall_score
        
        summary_rows.append(summary_row)
        
        # =====================================
        # ✅ DETAIL SHEET (WITH URLS + RPC)
        # =====================================
        
        def safe(val):
            return val if val else "❌ Missing"
        
        export_row = {
            "SKU": row["sku"],
            "CVS RPC": row.get("cvs_rpc", ""),              # ✅ NEW
            "Salsify URL": row.get("salsify_url", ""),      # ✅ NEW
            "Retail URL": row.get("retail_url", ""),        # ✅ NEW
        
            "Salsify Title": safe(s_text.get("title", "")),
            "CVS Title": safe(r_text.get("title", "")),
        
            "Salsify Description": safe(s_text.get("description", "")),
            "CVS Description": safe(r_text.get("description", "")),
        }
        
        # ✅ FEATURES (fixed 5 columns)
        for i in range(5):
        
            c_val = cvs_features[i] if i < len(cvs_features) else ""
        
            if not c_val:
                export_row[f"Feature {i+1}"] = "❌ Missing"
            else:
                export_row[f"Feature {i+1}"] = c_val
        
        export_rows.append(export_row)

# =====================================
# ✅ EXPORT FILE
# =====================================
if summary_rows:

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.DataFrame(export_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        detail_df.to_excel(writer, index=False, sheet_name="Details")

    with open(file_name, "rb") as f:
        st.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
