
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher

st.title("PDP QA Tool (FULL QA FINAL ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# HTML HELPERS
# -----------------------------
def get_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).text

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# -----------------------------
# IMAGE FUNCTIONS
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
                image_dict[name] = {"url": base, "size": size}

        return [v["url"] for v in image_dict.values()]

    except:
        return []

# -----------------------------
# TEXT FUNCTIONS
# -----------------------------

# ✅ SALSIFY
def get_salsify_text(url):
    try:
        soup = get_soup(url)

        title = soup.title.text.strip() if soup.title else ""

        description = ""
        features = []

        # description
        for p in soup.find_all("p"):
            text = p.get_text().strip()
            if len(text) > 120 and "general feature" not in text.lower():
                description = text
                break

        # structured features
        raw = soup.get_text(" ")

        matches = re.findall(
            r'General Feature \d+(.*?)(?=General Feature \d+|$)',
            raw,
            re.IGNORECASE
        )

        for m in matches:
            clean = m.strip()
            if len(clean) > 10:
                features.append(clean)

        return {
            "title": title,
            "description": description,
            "features": features
        }

    except:
        return {"title": "", "description": "", "features": []}


# ✅ CVS

def get_cvs_text(url):
    try:
        soup = get_soup(url)

        title = ""
        description = ""
        features = []

        # -----------------------------
        # ✅ TITLE
        # -----------------------------
        title_tag = soup.find("p", class_=re.compile("text-lg"))

        if title_tag:
            title = title_tag.get_text(strip=True)

        # -----------------------------
        # ✅ DESCRIPTION
        # -----------------------------
        desc_tag = soup.find("span", class_=re.compile("text-base"))

        if desc_tag:
            description = desc_tag.get_text(strip=True)

        # -----------------------------
        # ✅ FEATURES (VERY IMPORTANT FIX)
        # -----------------------------
        for li in soup.find_all("li"):

            class_list = " ".join(li.get("class", []))

            # ✅ ONLY product bullet list (ignore nav links)
            if "vendorDetailsBullet" in class_list:

                txt = li.get_text(strip=True)

                if txt and len(txt) > 5:
                    features.append(txt)

        return {
            "title": title,
            "description": description,
            "features": features
        }

    except:
        return {
            "title": "",
            "description": "",
            "features": []
        }
# -----------------------------
# MATCH SCORE
# -----------------------------
def score(a, b):
    return int(SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100)

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])

        # -----------------------------
        # IMAGE ALIGNMENT
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

        # -----------------------------
        # TITLE
        # -----------------------------
        st.markdown("## Title Comparison")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Salsify Title**")
            st.write(s_text["title"])

        with col2:
            st.markdown("**CVS Title**")
            st.write(r_text["title"])

        st.write(f"✅ Title Match: {score(s_text['title'], r_text['title'])}%")

        # -----------------------------
        # DESCRIPTION
        # -----------------------------
        st.markdown("## General Description")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Salsify**")
            st.write(s_text["description"])

        with col2:
            st.markdown("**CVS**")
            st.write(r_text["description"])

        desc_score = score(s_text["description"], r_text["description"])

        st.write(f"✅ Description Match: {desc_score}%")

        # -----------------------------
        # FEATURES ALIGN + SCORE ✅ FIXED
        # -----------------------------
        st.markdown("## Features")

        max_len = max(len(s_text["features"]), len(r_text["features"]))

        for i in range(max_len):

            col1, col2, col3 = st.columns([3, 3, 1])

            f1 = s_text["features"][i] if i < len(s_text["features"]) else ""
            f2 = r_text["features"][i] if i < len(r_text["features"]) else ""

            with col1:
                if f1:
                    st.write("•", f1)

            with col2:
                if f2:
                    st.write("•", f2)
                else:
                    st.write("❌ Missing")

            with col3:
                if f1 and f2:
                    st.write(f"{score(f1, f2)}%")
                else:
                    st.write("—")

        st.divider()
