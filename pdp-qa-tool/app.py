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
