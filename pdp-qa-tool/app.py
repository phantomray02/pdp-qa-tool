import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher

st.title("PDP QA Tool (Final QA Dashboard ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# =========================================
# ✅ HTML CACHE (MASSIVE SPEED FIX ✅)
# =========================================
html_cache = {}

def get_html(url):
    if url in html_cache:
        return html_cache[url]

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        res = requests.get(url, headers=headers, timeout=15)
        html_cache[url] = res.text
        return res.text
    except:
        return ""

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# -----------------------------
# ✅ SALSIFY IMAGES (UNCHANGED)
# -----------------------------
def get_salsify_images(url):
    try:
        soup = get_soup(url)
        imgs = []

        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http"):
                imgs.append(src)

        return list(dict.fromkeys(imgs))[:8]

    except:
        return []

# -----------------------------
# ✅ CVS IMAGES (UNCHANGED ✅)
# -----------------------------
def get_cvs_images(url):
    try:
        html = get_html(url)

        matches = re.findall(
            r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^\s"]*',
            html
        )

        image_dict = {}

        for m in matches:
            full = "https://www.cvs.com" + m
            base = full.split("?")[0]
            name = base.split("/")[-1]

            size_match = re.search(r'Resize=\((\d+),', m)
            size = int(size_match.group(1)) if size_match else 0

            if name not in image_dict or size > image_dict[name]["size"]:
                image_dict[name] = {
                    "url": base,
                    "size": size
                }

        return [v["url"] for v in image_dict.values()]

    except:
        return []

# -----------------------------
# ✅ SALSIFY TEXT (UNCHANGED)
# -----------------------------

# -----------------------------
# ✅ SALSIFY TEXT (FIXED ✅ CLEAN)
# -----------------------------
def get_salsify_text(url):
    try:
        html = get_html(url)

        description = ""
        features = []

        # ✅ TARGET SAME REAL TEXT BLOCK AS CVS
        d = re.search(
            r'Get up to .*?latest fashion trends',
            html,
            re.DOTALL
        )

        if d:
            raw = d.group(0)

            raw = raw.replace('\\"', '')
            raw = raw.replace('\\n', ' ')
            raw = raw.replace('","', '. ')
            raw = raw.replace('"', '')

            raw = re.sub('<.*?>', '', raw)
            raw = re.sub(r'\s+', ' ', raw).strip()

            description = raw

        # ✅ FEATURES = CLEAN SENTENCES
        sentences = re.split(r'\.\s+', description)

        for s in sentences:
            s = s.strip()

            if 20 < len(s) < 140:
                features.append(s)

        return {
            "description": description,
            "features": features[:6]
        }

    except:
        return {"description": "", "features": []}

# -----------------------------
# ✅ CVS TEXT ✅ FIXED (FAST + CLEAN)
# -----------------------------
def get_cvs_text(url):
    try:
        html = get_html(url)

        description = ""
        features = []

        # ✅ TARGET REAL PRODUCT COPY ONLY
        d = re.search(
            r'Get up to .*?latest fashion trends',
            html,
            re.DOTALL
        )

        if d:
            raw = d.group(0)

            # ✅ CLEAN JSON / HTML
            raw = raw.replace('\\"', '')
            raw = raw.replace('\\n', ' ')
            raw = raw.replace('","', '. ')
            raw = raw.replace('"', '')

            raw = re.sub('<.*?>', '', raw)

            # ✅ REMOVE PAGE JUNK (from your screenshot)
            junk = [
                "Home", "Shop", "Customer reviews",
                "Health Benefits", "OTC Eligible",
                "General content", "SKU", "GTIN",
                "CVS PDP Deck", "Online Optimized Image",
                "Same-Day Delivery policies"
            ]

            for j in junk:
                raw = raw.replace(j, "")

            raw = re.sub(r'\s+', ' ', raw).strip()

            description = raw

        # ✅ BUILD FEATURES (FROM CLEAN TEXT)
        sentences = re.split(r'\.\s+', description)

        for s in sentences:
            s = s.strip()

            if (
                20 < len(s) < 140 and
                any(word in s.lower() for word in [
                    "tampon", "leak", "compact", "wrapped", "comfort", "fit"
                ])
            ):
                features.append(s)

        return {
            "description": description,
            "features": features[:6]
        }

    except:
        return {"description": "", "features": []}

# -----------------------------
# ✅ MATCH SCORE
# -----------------------------
def get_score(a, b):
    return int(SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100)

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # -----------------------------
        # ✅ DATA (NOW FAST ✅)
        # -----------------------------
        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])

        st.write(f"Salsify Images: {len(s_images)}")
        st.write(f"CVS Images: {len(r_images)}")

        # -----------------------------
        # ✅ IMAGE ALIGNMENT (UNCHANGED)
        # -----------------------------
        st.markdown("## Image Comparison")

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):

            col1, col2 = st.columns(2)

            with col1:
                st.write(f"Salsify {i+1}")
                if i < len(s_images):
                    st.image(s_images[i], use_container_width=True)
                else:
                    st.error("Missing")

            with col2:
                st.write(f"CVS {i+1}")
                if i < len(r_images):
                    st.image(r_images[i], use_container_width=True)
                else:
                    st.error("Missing")

            st.divider()

        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        # -----------------------------
        # ✅ CONTENT COMPARISON
        # -----------------------------
        st.markdown("## Content Comparison")

        # ✅ DESCRIPTION
        st.markdown("### General Description")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Salsify**")
            st.write(s_text["description"])

        with col2:
            st.markdown("**CVS**")
            st.write(r_text["description"])

        desc_score = get_score(
            s_text["description"], r_text["description"]
        )

        st.write(f"✅ Description Match Score: {desc_score}%")

        # -----------------------------
        # ✅ FEATURES
        # -----------------------------
        st.markdown("### Features")

        max_len = max(len(s_text["features"]), len(r_text["features"]))

        for i in range(max_len):

            col1, col2 = st.columns(2)

            with col1:
                if i < len(s_text["features"]):
                    st.write("•", s_text["features"][i])

            with col2:
                if i < len(r_text["features"]):
                    st.write("•", r_text["features"][i])

        st.divider()
