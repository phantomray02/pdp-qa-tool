# =========================================
# ✅ IMPORTS (TOP OF FILE)
# =========================================
import re
import html
from bs4 import BeautifulSoup
import streamlit as st
import pandas as pd
import requests
import re
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
from bs4 import BeautifulSoup

def get_salsify_images(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    # ✅ grab Next.js data
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

        prop_name = prop.get("property", "").strip()

        values = prop.get("values", [])

        if not values:
            continue

        # ✅ CRITICAL FIX: ONLY FIRST IMAGE
        first = values[0]

        url = first.get("value", "")
        clean = url.split("?")[0]

        images.append({
            "type": prop_name,
            "url": clean
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
# ✅ CVS COPY EXTRACTION (SAFE FINAL)
# =========================================
def get_cvs_text(html_text):

    from bs4 import BeautifulSoup
    import re
    import html

    soup = BeautifulSoup(html_text, "html.parser")

    combined = ""

    # ✅ keep this (already working)
    for s in soup.find_all("script"):
        if s.string:
            combined += s.string

    desc = ""
    features = []

    # =====================================
    # ✅ DESCRIPTION (DO NOT TOUCH)
    # =====================================
    desc_match = re.search(
        r'vendorDetailsParagraph\\":\\"(.*?)\\"',
        combined
    )

    if desc_match:
        desc = html.unescape(desc_match.group(1))

    # =====================================
    # ✅ FEATURES (NEW — TARGET SAME STRUCTURE)
    # =====================================

    bullet_match = re.search(
        r'vendorDetailsBullets\\":\[(.*?)\]',
        combined,
        re.DOTALL
    )


    if bullet_match:

        raw_block = bullet_match.group(1)

        features = []
        
        for x in re.findall(r'"(.*?)"', raw_block):
        
            clean = html.unescape(x).strip()
        
            # ✅ remove trailing junk
            clean = clean.rstrip("\\").strip()
            clean = clean.rstrip('"').strip()
        
            if len(clean) > 20:
                features.append(clean)

    return {
        "description": desc.strip(),
        "features": features
    }
# =========================================
# ✅ SCORE
# =========================================
def normalize_text(t):
    return re.sub(r'[^a-z0-9\s]', '', str(t).lower())

def keyword_score(a, b):
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)

# =========================================
# ✅ MAIN APP
# =========================================

if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # =====================================
        # ✅ FETCH HTML
        # =====================================
        retail_html = get_html(row["retail_url"])

        # =====================================
        # ✅ GET DATA
        # =====================================
        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(retail_html)

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        # =====================================
        # ✅ CVS DEBUGGER
        # =====================================
        st.markdown("## 🧪 CVS DEBUG")

        st.write("CVS DATA:", r_text)
        st.write("HTML length:", len(retail_html))

        if "vendorDetailsParagraph" in retail_html:
            st.success("✅ FOUND vendorDetailsParagraph")

        if "vendorDetailsBullets" in retail_html:
            st.success("✅ FOUND vendorDetailsBullets")

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
        c1.write(s_text.get("title", ""))

        c2.markdown("**CVS**")
        c2.write("")

        # -------------------------------------
        # ✅ DESCRIPTION
        # -------------------------------------
        st.markdown("### Description")

        c1, c2 = st.columns(2)

        c1.markdown("**Salsify**")
        c1.write(s_text.get("description", ""))

        c2.markdown("**CVS**")
        c2.write(r_text.get("description", ""))

        desc_score = keyword_score(
            s_text.get("description", ""),
            r_text.get("description", "")
        )

        st.write(f"✅ Match: {desc_score}%")

        # =====================================
        # ✅ FEATURE COMPARISON (UPDATED)
        # =====================================
        st.markdown("## Feature Comparison")

        feature_fields = [
            ("Feature 1", "feature1"),
            ("Feature 3", "feature3"),
            ("Feature 4", "feature4"),
            ("Feature 5", "feature5"),
        ]

        cvs_features = r_text.get("features", [])

        for label, key in feature_fields:

            st.markdown(f"### {label}")

            c1, c2 = st.columns(2)

            s_val = s_text.get(key, "")

            # ✅ LEFT: SALSIFY
            c1.markdown("**Salsify**")
            c1.write(s_val if s_val else "—")

            # =====================================
            # ✅ FIND BEST MATCH
            # =====================================
            best_score = 0
            best_match = ""

            for f in cvs_features:
                score = keyword_score(s_val, f)

                if score > best_score:
                    best_score = score
                    best_match = f

            # ✅ RIGHT: CVS
            c2.markdown("**CVS**")

            if best_match:
                c2.write(best_match)
            else:
                c2.write("❌ Missing")

            # ✅ SCORE DISPLAY
            if best_match:
                if best_score >= 80:
                    st.success(f"✅ Strong match: {best_score}%")
                elif best_score >= 50:
                    st.warning(f"⚠️ Medium match: {best_score}%")
                else:
                    st.error(f"❌ Weak match: {best_score}%")
            else:
                st.error("❌ No matching feature found")

        # =====================================
        # ✅ IMAGE COMPARISON
        # =====================================
        st.markdown("## Image Comparison")

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):

            c1, c2 = st.columns(2)

            if i < len(s_images):
                c1.markdown(f"**{s_images[i]['type']}**")
                img = load_image(s_images[i]["url"])
                if img:
                    c1.image(img, use_container_width=True)

            if i < len(r_images):
                c2.markdown(f"**CVS {i+1}**")
                img = load_image(r_images[i])
                if img:
                    c2.image(img, use_container_width=True)

        # =====================================
        # ✅ SCORE SUMMARY
        # =====================================
        img_score = int(
            (min(len(s_images), len(r_images)) /
             max(len(s_images), len(r_images), 1)) * 100
        )

        overall = int((img_score + desc_score) / 2)

        summary_rows.append({
            "SKU": row["sku"],
            "Image %": img_score,
            "Description %": desc_score,
            "Overall %": overall
        })

# =========================================
# ✅ EXPORT
# =========================================
if 'summary_rows' in locals() and summary_rows:

    df = pd.DataFrame(summary_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            "📥 Download Excel",
            f,
            file_name
        )
