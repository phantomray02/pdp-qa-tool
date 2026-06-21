# How To Wire This Into Your Existing Streamlit App

This pack removes the `localhost:8765` dependency and uses a **page ↔ extension bridge** instead.

## What changes in the app

### 1. Add the bridge helper import near the top of your app

```python
from streamlit_extension_bridge import extension_bridge
import uuid
```

### 2. Add a session cache helper near your existing HTML cache helpers

```python
def get_session_bridged_html_map():
    if "bridged_html_by_url" not in st.session_state or not isinstance(st.session_state.get("bridged_html_by_url"), dict):
        st.session_state["bridged_html_by_url"] = {}
    return st.session_state["bridged_html_by_url"]
```

### 3. Patch `get_html(url)` so it checks Streamlit session results first

Put this at the top of your existing `get_html(url)` function body, before the old localhost fetch logic:

```python
bridged_map = get_session_bridged_html_map()
url = str(url or "").strip()
if not url:
    return ""
if url in bridged_map and bridged_map[url]:
    return bridged_map[url]
```

### 4. Create the bridge batch after the user uploads the master file and chooses a retailer

Once you already have a filtered DataFrame for the chosen retailer, build the URL list:

```python
retailer_rows = []
for _, row in retailer_df.iterrows():
    retail_url = str(row.get("retail_url", "") or "").strip()
    if not retail_url:
        continue
    retailer_rows.append({
        "url": retail_url,
        "label": str(row.get("sku", "") or "").strip(),
    })

batch_id = st.session_state.get("bridge_batch_id") or str(uuid.uuid4())
st.session_state["bridge_batch_id"] = batch_id
bridge_payload = extension_bridge(
    rows=retailer_rows,
    retailer=selected_retailer,
    batch_id=batch_id,
    auto_show_status=True,
    key=f"bridge_{selected_retailer}_{batch_id}",
)
```

### 5. Save returned HTML into session state when the extension finishes

Place this right after the `extension_bridge(...)` call:

```python
if bridge_payload and isinstance(bridge_payload, dict):
    results = bridge_payload.get("results", []) or []
    bridged_map = get_session_bridged_html_map()
    for item in results:
        url = str(item.get("url", "") or "").strip()
        html = str(item.get("html", "") or "")
        if url and html:
            bridged_map[url] = html
    st.session_state["bridged_html_by_url"] = bridged_map
```

### 6. Optional: add a button to clear the extension-fed cache

```python
if st.button("Clear bridged HTML cache"):
    st.session_state["bridged_html_by_url"] = {}
```

---

## What changes in the workflow

1. User uploads the normal master file in Streamlit Cloud.
2. User picks a retailer.
3. The hidden bridge component exposes those URLs inside the page.
4. User clicks the Edge extension once.
5. The extension reads the URL batch from the page, opens/fetches each page in real Edge, captures raw HTML, and sends the finished results back into the same Streamlit tab.
6. Your existing parser keeps using `get_html(url)` — but now the HTML comes from session state instead of localhost.

---

## Important note

This pack is a **full architecture starter**, but it is still a starter pack because I could not directly patch your exact existing `manifest.json`, `background.js`, `popup.js`, and `popup.html` from the uploaded files in this turn. Use these files as the new baseline for the bridge version.
