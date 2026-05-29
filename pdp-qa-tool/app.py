
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

st.title("PDP QA Tool")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

def get_text(url):
    try:
        res = requests.get(url)
        soup = BeautifulSoup(res.text, "html.parser")
        title = soup.find("h1")
        return title.get_text(strip=True) if title else ""
    except:
        return ""

if uploaded_file:
    df = pd.read_csv(uploaded_file)

    results = []

    for _, row in df.iterrows():
        s_text = get_text(row["salsify_url"])
        r_text = get_text(row["retail_url"])

        score = fuzz.ratio(s_text, r_text)

        results.append({
            "SKU": row["sku"],
            "Title Match Score": score
        })

    result_df = pd.DataFrame(results)

    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode('utf-8')
    st.download_button("Download Results", csv, "results.csv")
``
