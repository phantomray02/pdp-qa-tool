
def get_cvs_images(url):
    try:
        soup = get_soup(url)

        thumbnails = []

        for img in soup.find_all("img"):

            src = img.get("src") or ""
            width = img.get("width")

            # ✅ EXACT thumbnail rule
            if width == "80" and "high_res" in src:

                # ✅ fix relative URL
                if src.startswith("/"):
                    src = "https://www.cvs.com" + src

                thumbnails.append(src)

        # ✅ remove duplicates
        return list(dict.fromkeys(thumbnails))

    except:
        return []
