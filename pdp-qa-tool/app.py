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
# ✅ CVS COPY EXTRACTION (FINAL WITH TITLE)
# =========================================
def get_cvs_text(html_text):

    from bs4 import BeautifulSoup
    import re
    import html

    soup = BeautifulSoup(html_text, "html.parser")

    combined = ""

    # ✅ collect script content
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
        desc = html.unescape(desc_match.group(1))

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
            clean = clean.rstrip("\\").strip()
            clean = clean.rstrip('"').strip()

            if len(clean) > 20:
                features.append(clean)

    # =====================================
    # ✅ TITLE
    # =====================================
    title = ""
    
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
    # ✅ RETURN (MUST BE INSIDE FUNCTION)
    # =====================================

    return {
        "title": title,
        "description": desc.strip(),
        "features": features,
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
# ✅ MAIN APP
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        retail_html = get_html(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(retail_html)

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
        c1.markdown(equal_height_block(s_text.get("title", "")), unsafe_allow_html=True)

        c2.markdown("**CVS**")
        cvs_title = r_text.get("title", "")
        c2.markdown(equal_height_block(cvs_title), unsafe_allow_html=True)

        title_score = min(100, keyword_score(
            s_text.get("title", ""),
            cvs_title
        ))

        # ✅ SCORE BAR BELOW (NEW)
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
        c1.markdown(equal_height_block(s_text.get("description", "")), unsafe_allow_html=True)

        c2.markdown("**CVS**")
        cvs_desc = r_text.get("description", "")
        c2.markdown(equal_height_block(cvs_desc), unsafe_allow_html=True)

        desc_score = min(100, keyword_score(
            s_text.get("description", ""),
            cvs_desc
        ))

        # ✅ SCORE BAR BELOW (NEW)
        if desc_score >= 80:
            st.success(f"✅ Strong match: {desc_score}%")
        elif desc_score >= 50:
            st.warning(f"⚠️ Moderate match: {desc_score}%")
        else:
            st.error(f"❌ Weak match: {desc_score}%")

        # =====================================
        # ✅ FEATURE COMPARISON (UNCHANGED STYLE)
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

            col1, col2 = st.columns(2)

            s_val = s_text.get(key, "")

            col1.markdown("**Salsify**")
            col1.markdown(equal_feature_block(s_val), unsafe_allow_html=True)

            best_score = 0
            best_match = ""

            for f in cvs_features:

                score = keyword_score(s_val, f)

                if any(word in f.lower() for word in s_val.lower().split()[:3]):
                    score += 5

                if score > best_score:
                    best_score = score
                    best_match = f

            if not best_match and cvs_features:
                best_match = cvs_features[0]

            best_score = min(100, best_score)

            col2.markdown("**CVS**")
            col2.markdown(equal_feature_block(best_match), unsafe_allow_html=True)

            # ✅ KEEP YOUR EXISTING GOOD BAR
            if best_score >= 80:
                st.success(f"✅ Strong match: {best_score}%")
            elif best_score >= 50:
                st.warning(f"⚠️ Moderate match: {best_score}%")
            else:
                st.error(f"❌ Weak match: {best_score}%")

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
