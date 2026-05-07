import pdfplumber
import requests
import re
import json
import os
import tempfile
from io import BytesIO
from doe_RegionData_fetcher import get_all_latest_reports
#from Manager_DB import save_fuel_data, save_adjustment_data

# 1. Added missing brands found in the PDFs
BRANDS = ["PETRON", "SHELL", "CALTEX", "PHOENIX", "UNIOIL", "SEAOIL", "TOTAL", "PTT", "FLYING V", "INDEPENDENT", "OVERALL RANGE"]
FUELS = ["RON 100", "RON 97", "RON 95", "RON 91", "DIESEL", "DIESEL PLUS", "KEROSENE"]
ADJUSTMENT_PRODUCTS = ["GASOLINE", "DIESEL", "KEROSENE"]
_PADDLE_OCR = None

VISAYAS_CITY_PROVINCE_GROUPS = {
    "Negros Occidental": [
        "Bacolod City",
        "San Carlos City",
        "Kabankalan City",
        "Binalbagan",
        "Enrique B. Magalona",
        "Escalante City",
        "Manapla",
        "Sipalay City",
        "Toboso",
        "Cadiz City",
    ],
    "Guimaras": ["Jordan", "Nueva Valencia"],
    "Iloilo": [
        "Alimodian",
        "Lambunao",
        "Leganes",
        "Leon",
        "San Enrique",
        "Tigbauan",
        "Tubungan",
        "Zarraga",
        "Passi City",
    ],
    "Capiz": ["Cuartero", "Ivisan", "Maayon", "Pilar", "Sapian", "Sigma", "Roxas City"],
    "Antique": ["Tibiao", "San Jose"],
    "Aklan": ["Banga", "Malay", "Numancia", "New Washington", "Tangalan", "Kalibo", "Caticlan", "Boracay"],
    "Cebu": [
        "Cebu City",
        "Mandaue City",
        "Carcar City",
        "Naga City",
        "Talisay City",
        "Danao City",
        "Bogo City",
        "San Remigio",
        "Daan Bantayan",
        "Madridejos",
        "Catmon",
        "Pinamungajan",
        "Sibonga",
        "Bantayan",
    ],
    "Negros Oriental": [
        "Dumaguete City",
        "La Libertad",
        "Bayawan City",
        "Canlaon City",
        "Bais City",
        "Tanjay City",
        "Guihulngan City",
    ],
    "Bohol": [
        "Tagbilaran City",
        "Batuan",
        "Bien Unido",
        "Candijay",
        "Catigbian",
        "Corella",
        "Dagohoy",
        "Dauis",
        "Dimiao",
        "Guindulman",
        "Loon",
        "Maribojoc",
        "Sevilla",
        "Sikatuna",
        "Jagna",
        "Tubigon",
        "Ubay",
        "Anda",
    ],
    "Siquijor": ["San Juan", "Siquijor"],
    "Leyte": ["Tacloban City", "Ormoc City", "Carigara", "Albuera", "Bato", "Inopacan", "Leyte", "Merida", "Baybay City"],
    "Southern Leyte": ["Bontoc", "Hinundayan", "Libagon", "Maasin City"],
    "Biliran": ["Biliran"],
    "Samar": ["Catbalogan City", "Calbayog City"],
    "Eastern Samar": ["Borongan City", "Guiuan", "Quinapondan"],
    "Northern Samar": ["Rosario", "Catarman"],
}

def clean_price(price_str):
    """Extracts min/max prices. Allows 0.00 as a minimum if a valid max exists."""
    if not price_str:
        return None, None
        
    raw_str = str(price_str).upper().replace(',', '')
    
    # 1. Only exclude known 'junk' strings. 
    # Removed "0.00" and "0.0" from this list so they don't trigger an early exit.
    if any(x in raw_str for x in ["LFRO", "#N/A", "NONE", "EMPTY"]):
        return None, None
    
    # 2. Extract all numbers with decimals
    prices = re.findall(r'\d+\.\d+', raw_str)
    
    # 3. Convert to floats. We allow 0.0 here (>= 0).
    floats = [float(p) for p in prices]
    
    if not floats:
        return None, None
        
    # 4. Logic check: If all prices found are 0.0, it's effectively empty data.
    if max(floats) == 0:
        return None, None

    return min(floats), max(floats)

def get_column_mapping(table_rows):
    """Scans the top rows to map Brand columns. Ignores Location/Product."""
    mapping = {}
    for row in table_rows[:6]: # Increased buffer to 6 rows to be safe
        clean_row = [str(c).upper().replace('\n', ' ') if c else "" for c in row]

        for idx, cell in enumerate(clean_row):
            if "INDEPENDENT" in cell and "OVERALL RANGE" in cell:
                mapping.setdefault("INDEPENDENT", idx)
                if idx + 1 < len(clean_row):
                    mapping.setdefault("OVERALL RANGE", idx + 1)
        
        for brand in BRANDS:
            if brand not in mapping:
                for idx, cell in enumerate(clean_row):
                    if brand in cell:
                        mapping[brand] = idx
                        
    return mapping


def _blank_brand_fields():
    fields = {}
    for brand in BRANDS:
        key_base = brand.lower().replace(" ", "_")
        fields[f"{key_base}_min"] = None
        fields[f"{key_base}_max"] = None
    return fields


def _build_base_entry(category_name, province, city, product):
    entry = {
        "category": category_name,
        "province": province,
        "city": city,
        "product": product,
    }
    entry.update(_blank_brand_fields())
    return entry


def _set_brand_prices(entry, brand, min_price, max_price):
    key_base = brand.lower().replace(" ", "_")
    entry[f"{key_base}_min"] = min_price
    entry[f"{key_base}_max"] = max_price


def _ncr_province_or_default(province):
    if province is None:
        return "Metro Manila"

    province_text = str(province).strip()
    if not province_text or province_text.upper() in {"UNKNOWN", "NULL", "NONE", "N/A", "#N/A"}:
        return "Metro Manila"

    return province_text


def _extract_float_prices(value):
    if not value:
        return []
    raw = str(value).upper().replace(",", "")
    if any(x in raw for x in ["LFRO", "#N/A", "NONE", "EMPTY"]):
        return []
    return [float(p) for p in re.findall(r"\d+\.\d+", raw)]


def _clean_cell_text(value):
    return str(value).strip().replace("\n", " ") if value else ""


def _compact_header_text(value):
    return re.sub(r"[^A-Z0-9]+", "", str(value).upper())


def _city_key(value):
    return re.sub(r"[^A-Z0-9]+", "", str(value).upper())


VISAYAS_CITY_PROVINCE_OVERRIDES = {
    _city_key(city): province
    for province, cities in VISAYAS_CITY_PROVINCE_GROUPS.items()
    for city in cities
}


def _format_city_spacing(value):
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"(?i)\bC\s*I\s*T\s*Y$", "City", text)
    text = re.sub(r"(?i)\bC\s*T\s*Y$", "Cty", text)
    text = re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z]$)", "", text)
    text = re.sub(r"(?i)(?<!\s)(CITY|CTY)$", r" \1", text)
    return text


def _format_location_spacing(value):
    return re.sub(r"\s+", " ", str(value)).strip()


def _keyed_char_positions(value):
    keyed_chars = []
    keyed_index = 0
    for char in str(value):
        char_key = _city_key(char)
        if char_key:
            keyed_chars.append((char, char_key, keyed_index))
            keyed_index += len(char_key)
        else:
            keyed_chars.append((char, "", None))
    return keyed_chars


def _repair_location_from_key_indexes(location_text, remove_key_indexes, formatter):
    repaired_chars = [
        char
        for char, _, key_idx in _keyed_char_positions(location_text)
        if key_idx not in remove_key_indexes
    ]
    return formatter("".join(repaired_chars))


def _location_repair_score(repaired_text):
    text = str(repaired_text).strip()
    if not text:
        return -1000

    score = 0
    if text[:1].isupper():
        score += 20
    if re.search(r"\b[A-Za-z]\b", text):
        score -= 10
    if re.search(r"[a-z][A-Z]", text):
        score -= 5
    score -= len(re.findall(r"\s{2,}", text)) * 5
    return score


def _best_interleaved_contaminant_removal(location_text, contaminant_key, formatter):
    keyed_positions = [
        (char_key, key_idx)
        for _, char_key, key_idx in _keyed_char_positions(location_text)
        if key_idx is not None
    ]
    candidates = []

    def walk(position_idx, contaminant_idx, picked):
        if contaminant_idx == len(contaminant_key):
            remove_indexes = set(picked)
            repaired = _repair_location_from_key_indexes(location_text, remove_indexes, formatter)
            candidates.append((repaired, remove_indexes))
            return
        if position_idx >= len(keyed_positions):
            return

        remaining_positions = len(keyed_positions) - position_idx
        remaining_contaminant = len(contaminant_key) - contaminant_idx
        if remaining_positions < remaining_contaminant:
            return

        char_key, key_idx = keyed_positions[position_idx]
        if char_key == contaminant_key[contaminant_idx]:
            walk(position_idx + 1, contaminant_idx + 1, picked + [key_idx])
        walk(position_idx + 1, contaminant_idx, picked)

    walk(0, 0, [])
    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: (
            _location_repair_score(item[0]),
            -sum(
                1
                for left, right in zip(sorted(item[1]), sorted(item[1])[1:])
                if right == left + 1
            ),
        ),
    )[0]


def _remove_location_contaminant(location_text, contaminant_text, formatter=_format_location_spacing):
    contaminant_key = _city_key(contaminant_text)
    location_key = _city_key(location_text)
    if not location_text or not contaminant_key or len(location_key) <= len(contaminant_key):
        return location_text

    remove_key_indexes = set()
    if location_key.startswith(contaminant_key):
        remove_key_indexes.update(range(len(contaminant_key)))
    elif location_key.endswith(contaminant_key):
        start_idx = len(location_key) - len(contaminant_key)
        remove_key_indexes.update(range(start_idx, len(location_key)))
    else:
        repaired = _best_interleaved_contaminant_removal(location_text, contaminant_key, formatter)
        if not repaired:
            return location_text
        return repaired if len(_city_key(repaired)) >= 4 else location_text

    repaired = _repair_location_from_key_indexes(location_text, remove_key_indexes, formatter)
    return repaired if len(_city_key(repaired)) >= 4 else location_text


def _remove_interleaved_city_contaminant(city_text, contaminant_text):
    """
    Some NCR PDFs leak the first city name from the previous page into the
    next page's first city cell, interleaving both names character-by-character.
    Remove the known contaminant only when it is fully present as a subsequence.
    """
    return _remove_location_contaminant(city_text, contaminant_text, _format_city_spacing)


def _is_adjustment_header_row(row):
    clean_cells = [_clean_cell_text(cell) for cell in row]
    compact_cells = [_compact_header_text(cell) for cell in clean_cells]
    row_compact = "".join(compact_cells)

    if "OILCOMPANY" in row_compact:
        return True

    exact_header_cells = {
        "COMPANY",
        "DATE",
        "TIME",
        "DATETIME",
        "EFFECTIVITY",
        "GASOLINE",
        "DIESEL",
        "KEROSENE",
        "PRODUCT",
        "PRODUCTS",
    }
    if any(cell in exact_header_cells for cell in compact_cells):
        return True

    return any("EFFECTIVITY" in cell for cell in compact_cells)


def _parse_adjustment_amount(value):
    """
    Parse fuel price adjustment cells from DOE prior notice tables.
    Keeps explicit rollbacks/decreases as negative values and blanks/no movement as None.
    """
    raw = _clean_cell_text(value)
    if not raw:
        return None

    normalized = raw.upper()
    if any(token in normalized for token in ["NO ADJUSTMENT", "NO MOVEMENT", "N/A", "#N/A", "NONE"]):
        return None
    if re.fullmatch(r"[-–—]+", normalized):
        return None

    match = re.search(r"[-+]?\(?\d+(?:,\d{3})*(?:\.\d+)?\)?", raw)
    if not match:
        return None

    token = match.group(0).replace(",", "")
    is_parenthesized = token.startswith("(") and token.endswith(")")
    token = token.strip("()")
    try:
        amount = float(token)
    except ValueError:
        return None

    decrease_words = ["DECREASE", "ROLLBACK", "ROLLED BACK", "LESS", "REDUCE"]
    if is_parenthesized or any(word in normalized for word in decrease_words):
        amount = -abs(amount)
    return amount


def _find_adjustment_columns(table):
    """
    Locate standard DOE adjustment columns:
    Oil Company, Date & Time of Effectivity, Gasoline, Diesel, and Kerosene.
    The notices sometimes use multi-row headers, so this combines the top rows per column.
    """
    if not table:
        return None, None

    non_empty_rows = [row for row in table if row]
    if not non_empty_rows:
        return None, None

    max_cols = max(len(row) for row in non_empty_rows)
    header_scan_limit = min(len(table), 8)
    columns_text = []
    for col_idx in range(max_cols):
        parts = []
        for row in table[:header_scan_limit]:
            if col_idx < len(row):
                text = _clean_cell_text(row[col_idx])
                if text:
                    parts.append(text)
        columns_text.append(" ".join(parts))

    columns = {"products": {}}
    for idx, text in enumerate(columns_text):
        compact = _compact_header_text(text)
        if "OILCOMPANY" in compact or compact == "COMPANY" or compact.endswith("COMPANY"):
            columns["company"] = idx
        if (
            "EFFECTIVITY" in compact
            or ("DATE" in compact and "TIME" in compact)
            or compact in {"DATE", "TIME", "DATETIME"}
        ):
            columns.setdefault("effectivity", []).append(idx)
        for product in ADJUSTMENT_PRODUCTS:
            if product in compact:
                columns["products"][product.lower()] = idx

    if "company" not in columns:
        columns["company"] = 0
    if "effectivity" not in columns:
        columns["effectivity"] = [1] if max_cols > 1 else []

    required_products = {"gasoline", "diesel", "kerosene"}
    if not required_products.intersection(columns["products"]):
        return None, None

    header_end_idx = -1
    for row_idx, row in enumerate(table[:header_scan_limit]):
        if _is_adjustment_header_row(row):
            header_end_idx = row_idx

    return columns, header_end_idx + 1


def _extract_adjustment_from_table(table):
    columns, data_start_idx = _find_adjustment_columns(table)
    if not columns:
        return []

    rows = []
    current_company = ""
    current_effectivity = ""

    for row in table[data_start_idx:]:
        clean_row = [_clean_cell_text(cell) for cell in row]
        row_text = " ".join(clean_row).strip()
        if not row_text:
            continue
        row_text_upper = row_text.upper()
        if "SOURCE" in row_text_upper or "NOTE" in row_text_upper:
            continue

        company_idx = columns["company"]
        company = clean_row[company_idx] if company_idx < len(clean_row) else ""
        if company:
            current_company = company

        effectivity_parts = []
        for idx in columns.get("effectivity", []):
            if idx < len(clean_row) and clean_row[idx]:
                effectivity_parts.append(clean_row[idx])
        if effectivity_parts:
            current_effectivity = " ".join(effectivity_parts)

        product_values = {}
        has_product_value = False
        for product, idx in columns["products"].items():
            raw_value = clean_row[idx] if idx < len(clean_row) else ""
            amount = _parse_adjustment_amount(raw_value)
            product_values[product] = amount
            if amount is not None:
                has_product_value = True

        if not current_company or not has_product_value:
            continue

        rows.append({
            "category": "Adjustment",
            "oil_company": current_company,
            "date_time_of_effectivity": current_effectivity or None,
            "gasoline": product_values.get("gasoline"),
            "diesel": product_values.get("diesel"),
            "kerosene": product_values.get("kerosene"),
        })

    return rows


def parse_price_adjustment_pdf(pdf_url):
    """
    Scrape DOE prior notice / price adjustment PDFs using pdfplumber only.

    Returns one record per oil company with actual per-liter changes for
    gasoline, diesel, and kerosene when present in the extracted PDF table.
    """
    print(f"Opening price adjustment PDF: {pdf_url}")
    try:
        if os.path.exists(pdf_url):
            pdf_open_target = pdf_url
        else:
            response = requests.get(pdf_url, timeout=10)
            response.raise_for_status()
            pdf_open_target = BytesIO(response.content)

        data_rows = []
        with pdfplumber.open(pdf_open_target) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                if not tables:
                    table_obj = page.find_table()
                    if table_obj:
                        tables = [table_obj.extract()]

                for table in tables:
                    data_rows.extend(_extract_adjustment_from_table(table))

        return data_rows
    except Exception as e:
        print(f"Error scraping price adjustment PDF {pdf_url}: {e}")
        return []

def _row_has_any_brand_prices(clean_row, col_map, exclude_brand=None):
    """True if any OTHER brand cell on this row has numeric prices."""
    for brand, idx in col_map.items():
        # OVERALL RANGE often has values even when brand prices are merged elsewhere.
        # It should not block South Luzon carry-over consumption.
        if brand == "OVERALL RANGE":
            continue
        if exclude_brand and brand == exclude_brand:
            continue
        if idx >= len(clean_row):
            continue
        if _extract_float_prices(clean_row[idx]):
            return True
    return False


def _price_pairs_from_floats(price_floats):
    return [
        (price_floats[i], price_floats[i + 1])
        for i in range(0, len(price_floats) - 1, 2)
    ]


def _center_y(box):
    return (box[1] + box[3]) / 2.0


def _column_bbox(table_obj, col_idx):
    for row in table_obj.rows:
        if col_idx < len(row.cells) and row.cells[col_idx]:
            return row.cells[col_idx]
    return None


def _row_idx_for_word(table_obj, word, candidate_rows):
    word_y = (word["top"] + word["bottom"]) / 2.0
    containing_rows = []
    best_idx = None
    best_dist = None
    for row_idx in candidate_rows:
        bbox = table_obj.rows[row_idx].bbox
        y0, y1 = bbox[1], bbox[3]
        if y0 - 2 <= word_y <= y1 + 2:
            containing_rows.append((y1 - y0, row_idx))
            continue
        dist = abs(word_y - _center_y(bbox))
        if best_dist is None or dist < best_dist:
            best_idx = row_idx
            best_dist = dist
    if containing_rows:
        return min(containing_rows)[1]
    return best_idx if best_dist is not None and best_dist <= 12 else None


def _build_geometric_price_map(table_obj, table_rows, col_map, sorted_fuels):
    """
    Build {(row_idx, brand): (min, max)} using PDF word positions.
    This keeps merged visual cells from shifting values into blank fuel rows.
    """
    if not table_obj:
        return {}

    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY", "CITY/AREA"]
    product_rows = []
    for row_idx, row in enumerate(table_rows):
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, _ = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is not None and found_fuel:
            product_rows.append(row_idx)
    if not product_rows:
        return {}

    words = table_obj.page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
    price_map = {}

    for brand, col_idx in col_map.items():
        bbox = _column_bbox(table_obj, col_idx)
        if not bbox:
            continue

        x0, _, x1, _ = bbox
        line_numbers = {}
        for word in words:
            text = str(word.get("text", ""))
            if not re.fullmatch(r"\d+\.\d+", text):
                continue
            word_x = (word["x0"] + word["x1"]) / 2.0
            if not (x0 - 2 <= word_x <= x1 + 2):
                continue

            row_idx = _row_idx_for_word(table_obj, word, product_rows)
            if row_idx is None:
                continue
            line_key = (row_idx, round((word["top"] + word["bottom"]) / 2.0, 1))
            line_numbers.setdefault(line_key, []).append((word["x0"], float(text)))

        for (row_idx, _), values in line_numbers.items():
            nums = [num for _, num in sorted(values)]
            if len(nums) >= 2 and max(nums[:2]) > 0:
                price_map[(row_idx, brand)] = (nums[0], nums[1])

    return price_map


def _configure_ocr_cache():
    cache_root = os.path.join(tempfile.gettempdir(), ".paddlex")
    os.environ["HOME"] = tempfile.gettempdir()
    os.environ["XDG_CACHE_HOME"] = os.path.join(tempfile.gettempdir(), ".cache")
    os.environ["PADDLE_PDX_CACHE_HOME"] = cache_root
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.makedirs(cache_root, exist_ok=True)


def _get_paddle_ocr():
    global _PADDLE_OCR
    if _PADDLE_OCR is None:
        _configure_ocr_cache()
        from paddleocr import PaddleOCR

        _PADDLE_OCR = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _PADDLE_OCR


def _ocr_pdf_document(pdf_open_target):
    import pypdfium2 as pdfium

    if isinstance(pdf_open_target, BytesIO):
        return pdfium.PdfDocument(pdf_open_target.getvalue())
    return pdfium.PdfDocument(pdf_open_target)


def _ocr_result_to_words(result):
    words = []
    data = dict(result)
    texts = data.get("rec_texts") or []
    scores = data.get("rec_scores") or []
    boxes = data.get("rec_boxes")
    polys = data.get("rec_polys") or data.get("dt_polys") or []

    for idx, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            continue

        score = scores[idx] if idx < len(scores) else 1.0
        if score is not None and score < 0.45:
            continue

        if boxes is not None and idx < len(boxes):
            x0, y0, x1, y1 = [float(v) for v in boxes[idx]]
        elif idx < len(polys):
            points = polys[idx]
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        else:
            continue

        words.append({
            "text": text,
            "x0": x0,
            "x1": x1,
            "top": y0,
            "bottom": y1,
            "cx": (x0 + x1) / 2.0,
            "cy": (y0 + y1) / 2.0,
            "score": score,
        })
    return words


def _normalize_ocr_text(text):
    return re.sub(r"[^A-Z0-9#./& -]", "", str(text).upper()).strip()


def _looks_like_price_token(text):
    normalized = _normalize_ocr_text(text)
    return bool(
        re.fullmatch(r"\d+\.\d+", normalized)
        or normalized in {"#N/A", "N/A", "NA", "-", "0.00"}
    )


def _ocr_line_groups(words, y_tolerance=10):
    lines = []
    for word in sorted(words, key=lambda w: (w["cy"], w["cx"])):
        matched = None
        for line in lines:
            if abs(line["cy"] - word["cy"]) <= y_tolerance:
                matched = line
                break
        if matched is None:
            matched = {"cy": word["cy"], "words": []}
            lines.append(matched)
        matched["words"].append(word)
        matched["cy"] = sum(w["cy"] for w in matched["words"]) / len(matched["words"])

    for line in lines:
        line["words"].sort(key=lambda w: w["cx"])
    return sorted(lines, key=lambda line: line["cy"])


def _find_fuel_in_text(text):
    normalized = _normalize_ocr_text(text)
    for fuel in sorted(FUELS, key=len, reverse=True):
        if fuel in normalized:
            return fuel
    return None


def _detect_ocr_columns(words):
    aliases = {
        "province": ["PROVINCE"],
        "city": ["MUNICIPALITY", "CITYT", "CITY/"],
        "product": ["PRODUCT"],
        "PETRON": ["PETRON"],
        "SHELL": ["SHELL"],
        "CALTEX": ["CALTEX"],
        "PHOENIX": ["PHOENIX"],
        "UNIOIL": ["UNIOIL"],
        "SEAOIL": ["SEAOIL"],
        "TOTAL": ["TOTAL"],
        "PTT": ["PTT"],
        "FLYING V": ["FLYING V", "FLYING"],
        "INDEPENDENT": ["INDEPENDENT"],
        "OVERALL RANGE": ["OVERALL"],
    }
    columns = {}
    for key, names in aliases.items():
        candidates = []
        for word in words:
            text = _normalize_ocr_text(word["text"])
            if any(name in text for name in names):
                candidates.append(word)
        if candidates:
            columns[key] = min(candidates, key=lambda w: w["cy"])["cx"]

    # North Luzon uses TOTAL/CLEAN FUEL columns, but the current schema does not
    # expose those brands. Keep only columns that map to existing output fields.
    return columns


def _column_for_x(x, columns):
    usable = {key: val for key, val in columns.items() if key in BRANDS}
    if not usable:
        return None

    ordered = sorted(usable.items(), key=lambda item: item[1])
    best_key = None
    best_dist = None
    for idx, (key, center) in enumerate(ordered):
        left = (ordered[idx - 1][1] + center) / 2.0 if idx > 0 else -float("inf")
        right = (center + ordered[idx + 1][1]) / 2.0 if idx + 1 < len(ordered) else float("inf")
        if left <= x < right:
            return key
        dist = abs(x - center)
        if best_dist is None or dist < best_dist:
            best_key = key
            best_dist = dist
    return best_key if best_dist is not None and best_dist <= 90 else None


def _text_in_column(line, columns, column_name):
    if column_name not in columns:
        return ""
    center = columns[column_name]
    other_centers = sorted(set(columns.values()))
    pos = other_centers.index(center)
    left = (other_centers[pos - 1] + center) / 2.0 if pos > 0 else -float("inf")
    right = (center + other_centers[pos + 1]) / 2.0 if pos + 1 < len(other_centers) else float("inf")
    return " ".join(
        word["text"]
        for word in line["words"]
        if left <= word["cx"] < right
    ).strip()


NORTH_LUZON_PROVINCES = [
    "ABRA",
    "APAYAO",
    "IFUGAO",
    "BENGUET",
    "ILOCOS NORTE",
    "ILOCOS SUR",
    "LA UNION",
    "PANGASINAN",
    "CAGAYAN",
    "ISABELA",
    "NUEVA VIZCAYA",
    "QUIRINO",
    "BATAAN",
    "BULACAN",
    "NUEVA ECIJA",
    "PAMPANGA",
    "TARLAC",
    "ZAMBALES",
    "AURORA",
]

NORTH_LUZON_CITY_PROVINCE_OVERRIDES = {
    "BAGUIO CITY": "BENGUET",
    "LA TRIDINDAD": "BENGUET",
    "BATAC CITY": "ILOCOS NORTE",
    "LAOAG CITY": "ILOCOS NORTE",
    "CANDON CITY": "ILOCOS SUR",
    "VIGAN CITY": "ILOCOS SUR",
    "SAN FERNANDO CITY": "LA UNION",
    "DAGUPAN CITY": "PANGASINAN",
    "URDANETA CITY": "PANGASINAN",
    "SAN CARLOS CITY": "PANGASINAN",
    "ALAMINOS CITY": "PANGASINAN",
    "CLAVERIA": "CAGAYAN",
    "IGUIG": "CAGAYAN",
    "PENABLANCA": "CAGAYAN",
    "SOLANA": "CAGAYAN",
    "TUGUEGARAO CITY": "CAGAYAN",
    "CAUAYAN CITY": "ISABELA",
    "ILAGAN CITY": "ISABELA",
    "CABAGAN": "ISABELA",
    "TUMAUINI": "ISABELA",
    "SANTIAGO CITY": "ISABELA",
    "BAYOMBONG": "NUEVA VIZCAYA",
    "BAGABAG": "NUEVA VIZCAYA",
    "BAMBANG": "NUEVA VIZCAYA",
    "SOLANO": "NUEVA VIZCAYA",
    "DIFFUN": "QUIRINO",
    "MADDELA": "QUIRINO",
    "BALANGA CITY": "BATAAN",
    "MARIVELES": "BATAAN",
    "MALOLOS CITY": "BULACAN",
    "MEYCAUAYAN CITY": "BULACAN",
    "SAN JOSE DEL MONTE CITY": "BULACAN",
    "CABANATUAN CITY": "NUEVA ECIJA",
    "STA ROSA": "NUEVA ECIJA",
    "GAPAN CITY": "NUEVA ECIJA",
    "SAN JOSE CITY": "NUEVA ECIJA",
    "SCIENCE CITY OF MUNOZ": "NUEVA ECIJA",
    "ANGELES CITY": "PAMPANGA",
    "TARLAC CITY": "TARLAC",
    "OLONGAPO CITY": "ZAMBALES",
    "SUBIC": "ZAMBALES",
    "BALER": "AURORA",
}


def _ocr_location_bounds(columns, column_name):
    product_center = columns.get("product")
    if product_center is None:
        return None

    province_center = columns.get("province")
    city_center = columns.get("city")

    if province_center is None and city_center is None:
        province_center = product_center * 0.28
        city_center = product_center * 0.64
    elif province_center is None:
        province_center = city_center * 0.45
    elif city_center is None or abs(city_center - province_center) < 35:
        city_center = product_center * 0.62

    split = (province_center + city_center) / 2.0
    city_right = (city_center + product_center) / 2.0

    if column_name == "province":
        return -float("inf"), split
    if column_name == "city":
        return split, city_right
    return None


def _is_location_noise(text):
    normalized = _normalize_ocr_text(text)
    if not normalized:
        return True
    if _find_fuel_in_text(normalized) or _looks_like_price_token(normalized):
        return True
    return normalized in {
        "PROVINCE",
        "CITYT",
        "CITY/",
        "MUNICIPALITY",
        "PRODUCT",
        "FUEL",
        "RANGE",
        "PRICE",
        "COMMON",
        "OVERALL",
    }


def _text_in_ocr_band(words, columns, column_name, top, bottom):
    bounds = _ocr_location_bounds(columns, column_name)
    if not bounds:
        return ""

    left, right = bounds
    candidates = [
        word
        for word in words
        if top <= word["cy"] <= bottom
        and left <= word["cx"] < right
        and not _is_location_noise(word["text"])
    ]
    candidates.sort(key=lambda word: (round(word["cy"] / 8.0), word["cx"]))
    return _format_location_spacing(" ".join(word["text"] for word in candidates))


def _canonical_north_luzon_province(text):
    normalized = _format_location_spacing(_normalize_ocr_text(text))
    if not normalized:
        return None

    for province in sorted(NORTH_LUZON_PROVINCES, key=len, reverse=True):
        if normalized == province:
            return province
        if normalized.startswith(f"{province} ") or normalized.endswith(f" {province}"):
            return province
    return None


def _clean_north_luzon_city(text):
    cleaned = _format_city_spacing(_normalize_ocr_text(text))
    cleaned = re.sub(r"(?i)\bCITY\s*/?\s*MUNICIPALITY\b", "", cleaned).strip()
    for province in sorted(NORTH_LUZON_PROVINCES, key=len, reverse=True):
        cleaned = _remove_location_contaminant(cleaned, province, _format_city_spacing)
    return cleaned or "Unknown"


def _repair_north_luzon_province_fragments(blocks):
    pair_map = {
        ("ILOCOS", "NORTE"): "ILOCOS NORTE",
        ("ILOCOS", "SUR"): "ILOCOS SUR",
        ("NUEVA", "VIZCAYA"): "NUEVA VIZCAYA",
        ("NUEVA", "ECIJA"): "NUEVA ECIJA",
    }

    for idx, block in enumerate(blocks):
        province = block["province"]
        next_province = blocks[idx + 1]["province"] if idx + 1 < len(blocks) else ""
        combined = None

        if next_province.startswith(f"{province} ") and province in {"ILOCOS", "NUEVA"}:
            combined = next_province
        elif idx > 0:
            prev_province = blocks[idx - 1]["province"]
            combined = pair_map.get((prev_province, province))
            if combined:
                blocks[idx - 1]["province"] = combined

        if combined:
            block["province"] = combined

    for idx in range(len(blocks) - 1):
        block = blocks[idx]
        next_block = blocks[idx + 1]
        if (
            not block.get("raw_province_text")
            and next_block.get("raw_province_text")
            and next_block["province"] != block["province"]
        ):
            block["province"] = next_block["province"]

    for block in blocks:
        if _normalize_ocr_text(block["city"]) == "CITY" and block["province"] != "Unknown":
            block["city"] = _format_city_spacing(f"{block['province']} CITY")
        city_key = _normalize_ocr_text(block["city"])
        if city_key in NORTH_LUZON_CITY_PROVINCE_OVERRIDES:
            block["province"] = NORTH_LUZON_CITY_PROVINCE_OVERRIDES[city_key]


def _scrape_north_luzon_ocr(pdf_open_target, category_name):
    try:
        ocr = _get_paddle_ocr()
    except Exception as exc:
        print(f"North Luzon OCR unavailable: {exc}")
        return []

    pdf_doc = _ocr_pdf_document(pdf_open_target)
    blocks = []
    current_province = "Unknown"
    current_city = "Unknown"

    for page_index in range(len(pdf_doc)):
        page = pdf_doc[page_index]
        image = page.render(scale=2).to_pil()
        import numpy as np
        ocr_result = ocr.predict(np.array(image))
        words = []
        for result in ocr_result:
            words.extend(_ocr_result_to_words(result))
        if not words:
            continue

        columns = _detect_ocr_columns(words)
        if "product" not in columns:
            continue

        product_lines = []
        for line in _ocr_line_groups(words):
            product_text = _text_in_column(line, columns, "product")
            found_fuel = _find_fuel_in_text(product_text)
            if not found_fuel:
                continue

            product_lines.append({
                "line": line,
                "fuel": found_fuel,
                "cy": line["cy"],
            })

        block_ranges = []
        block_start = None
        for idx, product_line in enumerate(product_lines):
            if product_line["fuel"] == "RON 100" or block_start is None:
                if block_start is not None:
                    block_ranges.append((block_start, idx))
                block_start = idx
        if block_start is not None:
            block_ranges.append((block_start, len(product_lines)))

        for start_idx, end_idx in block_ranges:
            block_product_lines = product_lines[start_idx:end_idx]
            previous_line = product_lines[start_idx - 1] if start_idx > 0 else None
            band_top = (
                previous_line["cy"] - 3
                if previous_line
                else block_product_lines[0]["cy"] - 30
            )
            band_bottom = block_product_lines[-1]["cy"] + 3

            province_text = _text_in_ocr_band(words, columns, "province", band_top, band_bottom)
            city_text = _text_in_ocr_band(words, columns, "city", band_top, band_bottom)

            province_candidate = _canonical_north_luzon_province(province_text)
            if province_candidate:
                current_province = province_candidate
            elif province_text:
                normalized_province = _format_location_spacing(_normalize_ocr_text(province_text))
                if normalized_province in {"ILOCOS", "NORTE", "SUR", "NUEVA", "VIZCAYA", "ECIJA"}:
                    current_province = normalized_province
                elif not city_text:
                    city_text = province_text

            if city_text:
                current_city = _clean_north_luzon_city(city_text)
            elif province_text and not province_candidate:
                current_city = _clean_north_luzon_city(province_text)

            entries = []
            for product_line in block_product_lines:
                entry = _build_base_entry(category_name, current_province, current_city, product_line["fuel"])
                price_tokens_by_brand = {}
                for word in product_line["line"]["words"]:
                    if not _looks_like_price_token(word["text"]):
                        continue
                    brand = _column_for_x(word["cx"], columns)
                    if not brand:
                        continue
                    price_tokens_by_brand.setdefault(brand, []).append(word)

                for brand, tokens in price_tokens_by_brand.items():
                    tokens = sorted(tokens, key=lambda w: w["cx"])
                    raw_price = " ".join(token["text"] for token in tokens)
                    b_min, b_max = clean_price(raw_price)
                    _set_brand_prices(entry, brand, b_min, b_max)

                entries.append(entry)

            blocks.append({
                "province": current_province,
                "city": current_city,
                "raw_province_text": province_text,
                "entries": entries,
            })

    _repair_north_luzon_province_fragments(blocks)

    data_rows = []
    for block in blocks:
        for entry in block["entries"]:
            entry["province"] = block["province"]
            entry["city"] = block["city"]
            data_rows.append(entry)

    return data_rows

def _avg_pair(pair):
    return (pair[0] + pair[1]) / 2.0

def _fuel_match_score(fuel, pair):
    """
    Higher score means the price pair is more plausible for that fuel type.
    """
    v = _avg_pair(pair)
    ranges = {
        "RON 100": (100.0, 130.0),
        "RON 97": (98.0, 120.0),
        "RON 95": (85.0, 108.0),
        "RON 91": (80.0, 102.0),
        "DIESEL": (110.0, 155.0),
        "DIESEL PLUS": (115.0, 165.0),
        "KEROSENE": (90.0, 145.0),
    }
    low, high = ranges.get(fuel, (0.0, 999.0))
    if low <= v <= high:
        return 100.0
    # Soft penalty outside range to still allow best-effort match.
    if v < low:
        return max(0.0, 100.0 - (low - v))
    return max(0.0, 100.0 - (v - high))

def _align_pairs_to_fuels_with_gaps(grouped_pairs, fuel_sequence):
    """
    Align pairs to a fuel sequence while allowing missing fuels (gaps).
    This avoids shifted assignments when a brand has empty fuel rows.
    """
    if not grouped_pairs or not fuel_sequence:
        return []

    n = len(grouped_pairs)
    m = len(fuel_sequence)
    neg_inf = -10**9
    dp = [[neg_inf] * (m + 1) for _ in range(n + 1)]
    choice = [[None] * (m + 1) for _ in range(n + 1)]

    for j in range(m + 1):
        dp[n][j] = 0
    for i in range(n - 1, -1, -1):
        dp[i][m] = neg_inf

    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            skip_score = dp[i][j + 1]
            match_score = _fuel_match_score(fuel_sequence[j], grouped_pairs[i]) + dp[i + 1][j + 1]
            if match_score >= skip_score:
                dp[i][j] = match_score
                choice[i][j] = "match"
            else:
                dp[i][j] = skip_score
                choice[i][j] = "skip"

    tagged = []
    i = 0
    j = 0
    while i < n and j < m:
        if choice[i][j] == "match":
            tagged.append({"fuel": fuel_sequence[j], "pair": grouped_pairs[i]})
            i += 1
            j += 1
        else:
            j += 1
    return tagged


def _pair_within_range(pair, range_pair, tolerance=0.25):
    if not pair or not range_pair:
        return False
    return (
        range_pair[0] - tolerance <= pair[0] <= range_pair[1] + tolerance
        and range_pair[0] - tolerance <= pair[1] <= range_pair[1] + tolerance
    )


def _align_pairs_to_fuels_by_overall_range(grouped_pairs, fuel_sequence, overall_ranges):
    """
    Align merged brand prices using the PDF's own per-fuel overall ranges.
    This is safer than a plain ladder when a blank fuel row is omitted.
    """
    if not grouped_pairs or not fuel_sequence:
        return []

    n = len(grouped_pairs)
    m = len(fuel_sequence)
    neg_inf = -10**9
    dp = [[neg_inf] * (m + 1) for _ in range(n + 1)]
    choice = [[None] * (m + 1) for _ in range(n + 1)]

    for j in range(m + 1):
        dp[n][j] = 0

    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            skip_score = dp[i][j + 1]
            range_pair = overall_ranges.get(fuel_sequence[j])
            if _pair_within_range(grouped_pairs[i], range_pair):
                match_score = 1000 + _fuel_match_score(fuel_sequence[j], grouped_pairs[i]) + dp[i + 1][j + 1]
            else:
                match_score = neg_inf

            if match_score >= skip_score:
                dp[i][j] = match_score
                choice[i][j] = "match"
            else:
                dp[i][j] = skip_score
                choice[i][j] = "skip"

    if dp[0][0] <= neg_inf // 2:
        return _align_pairs_to_fuels_with_gaps(grouped_pairs, fuel_sequence)

    tagged = []
    i = 0
    j = 0
    while i < n and j < m:
        if choice[i][j] == "match":
            tagged.append({"fuel": fuel_sequence[j], "pair": grouped_pairs[i]})
            i += 1
            j += 1
        else:
            j += 1

    return tagged if len(tagged) == n else _align_pairs_to_fuels_with_gaps(grouped_pairs, fuel_sequence)


def _collect_following_overall_ranges(table, start_row_idx, col_map, sorted_fuels):
    overall_idx = col_map.get("OVERALL RANGE")
    if overall_idx is None:
        return {}

    ranges = {}
    for row in table[start_row_idx + 1:]:
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, _ = _extract_row_context(
            clean_row,
            sorted_fuels,
            ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY", "CITY/AREA"],
        )
        if p_idx is None:
            continue
        if clean_row[0]:
            break

        raw_range = clean_row[overall_idx] if overall_idx < len(clean_row) else ""
        range_min, range_max = clean_price(raw_range)
        if range_min is not None and range_max is not None:
            ranges[found_fuel] = (range_min, range_max)

    return ranges


def _extract_row_context(clean_row, sorted_fuels, headers_to_ignore):
    p_idx = -1
    found_fuel = ""
    for idx, cell in enumerate(clean_row):
        cell_upper = cell.upper()
        for fuel in sorted_fuels:
            if fuel in cell_upper:
                p_idx = idx
                found_fuel = fuel
                break
        if p_idx != -1:
            break

    if p_idx == -1:
        return None, None, None

    current_province = None
    current_city = None
    if p_idx == 1:
        val = clean_row[0]
        if val and not any(h == val.upper() for h in headers_to_ignore):
            current_city = val
    elif p_idx >= 2:
        prov_val = clean_row[0]
        city_val = clean_row[1]
        if prov_val and not any(h == prov_val.upper() for h in headers_to_ignore):
            current_province = prov_val
        if city_val and not any(h == city_val.upper() for h in headers_to_ignore):
            current_city = city_val

    return p_idx, found_fuel, (current_province, current_city)


def _first_city_in_table(table, sorted_fuels, headers_to_ignore):
    for row in table:
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, _, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        _, city_from_row = location_ctx
        if city_from_row:
            return city_from_row
    return None


def _first_province_in_table(table, sorted_fuels, headers_to_ignore):
    for row in table:
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, _, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        province_from_row, _ = location_ctx
        if province_from_row:
            return province_from_row
    return None


def _repair_ncr_page_boundary_city(table, previous_page_first_city):
    if not table or not previous_page_first_city:
        return table

    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY", "CITY/AREA"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)

    for row_idx, row in enumerate(table):
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, _, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        _, city_from_row = location_ctx
        if not city_from_row:
            return table

        repaired_city = _remove_interleaved_city_contaminant(city_from_row, previous_page_first_city)
        if repaired_city == city_from_row:
            return table

        repaired_table = [list(table_row) for table_row in table]
        city_col_idx = 0 if p_idx == 1 else 1
        repaired_table[row_idx][city_col_idx] = repaired_city
        return repaired_table

    return table


def _repair_south_luzon_location_contaminants(table, first_province, first_city):
    if not table or (not first_province and not first_city):
        return table

    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)
    repaired_table = None

    for row_idx, row in enumerate(table):
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, _, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        province_from_row, city_from_row = location_ctx
        if province_from_row and first_province:
            repaired_province = _remove_location_contaminant(province_from_row, first_province)
            if repaired_province != province_from_row:
                if repaired_table is None:
                    repaired_table = [list(table_row) for table_row in table]
                repaired_table[row_idx][0] = repaired_province

        if city_from_row and first_city:
            repaired_city = _remove_location_contaminant(city_from_row, first_city, _format_city_spacing)
            if repaired_city != city_from_row:
                if repaired_table is None:
                    repaired_table = [list(table_row) for table_row in table]
                city_col_idx = 0 if p_idx == 1 else 1
                repaired_table[row_idx][city_col_idx] = repaired_city

    return repaired_table if repaired_table is not None else table


def _scrape_default_table(table, category_name, current_province, current_city, global_col_map):
    data_rows = []
    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)

    col_map = get_column_mapping(table)
    if col_map:
        global_col_map = col_map
    else:
        col_map = global_col_map
    if not col_map:
        return data_rows, current_province, current_city, global_col_map

    for row in table:
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        province_from_row, city_from_row = location_ctx
        if province_from_row:
            current_province = province_from_row
        if city_from_row:
            current_city = city_from_row

        entry = _build_base_entry(category_name, current_province, current_city, found_fuel)
        for brand in BRANDS:
            if brand in col_map:
                b_idx = col_map[brand]
                raw_price = clean_row[b_idx] if b_idx < len(clean_row) else ""
                b_min, b_max = clean_price(raw_price)
                _set_brand_prices(entry, brand, b_min, b_max)

        data_rows.append(entry)

    return data_rows, current_province, current_city, global_col_map


def _get_visayas_location_columns(table):
    """Find Visayas location columns from headers because pages can gain a blank leading column."""
    for row in table[:8]:
        clean_row = [_clean_cell_text(cell) for cell in row]
        compact_row = [_compact_header_text(cell) for cell in clean_row]
        province_idx = None
        city_idx = None
        product_idx = None

        for idx, cell in enumerate(compact_row):
            if cell == "PROVINCE":
                province_idx = idx
            elif "CITY" in cell and "MUNICIPALITY" in cell:
                city_idx = idx
            elif cell == "PRODUCT":
                product_idx = idx

        if product_idx is not None and (province_idx is not None or city_idx is not None):
            return {
                "province": province_idx,
                "city": city_idx,
                "product": product_idx,
            }

    return {}


def _infer_visayas_location_columns(product_idx):
    if product_idx is None:
        return {}
    if product_idx >= 2:
        return {
            "province": product_idx - 2,
            "city": product_idx - 1,
            "product": product_idx,
        }
    if product_idx == 1:
        return {
            "province": None,
            "city": 0,
            "product": product_idx,
        }
    return {}


def _extract_visayas_row_context(clean_row, sorted_fuels, headers_to_ignore, location_columns):
    p_idx = -1
    found_fuel = ""
    for idx, cell in enumerate(clean_row):
        cell_upper = cell.upper()
        for fuel in sorted_fuels:
            if fuel in cell_upper:
                p_idx = idx
                found_fuel = fuel
                break
        if p_idx != -1:
            break

    if p_idx == -1:
        return None, None, None

    columns = location_columns or _infer_visayas_location_columns(p_idx)
    province_idx = columns.get("province")
    city_idx = columns.get("city")

    current_province = None
    current_city = None
    if province_idx is not None and province_idx < len(clean_row):
        prov_val = clean_row[province_idx]
        if prov_val and not any(h == prov_val.upper() for h in headers_to_ignore):
            current_province = prov_val
    if city_idx is not None and city_idx < len(clean_row):
        city_val = clean_row[city_idx]
        if city_val and not any(h == city_val.upper() for h in headers_to_ignore):
            current_city = city_val

    return p_idx, found_fuel, (current_province, current_city)


def _scrape_visayas_table(table, category_name, current_province, current_city, global_col_map, global_location_columns):
    data_rows = []
    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)

    col_map = get_column_mapping(table)
    if col_map:
        global_col_map = col_map
    else:
        col_map = global_col_map
    if not col_map:
        return data_rows, current_province, current_city, global_col_map, global_location_columns

    location_columns = _get_visayas_location_columns(table)
    if location_columns:
        global_location_columns = location_columns
    else:
        location_columns = global_location_columns

    for row in table:
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, location_ctx = _extract_visayas_row_context(
            clean_row,
            sorted_fuels,
            headers_to_ignore,
            location_columns,
        )
        if p_idx is None:
            continue

        province_from_row, city_from_row = location_ctx
        city_province = VISAYAS_CITY_PROVINCE_OVERRIDES.get(_city_key(city_from_row)) if city_from_row else None
        if province_from_row:
            current_province = province_from_row
        if city_province:
            current_province = city_province
        if city_from_row:
            current_city = city_from_row

        entry = _build_base_entry(category_name, current_province, current_city, found_fuel)
        for brand in BRANDS:
            if brand in col_map:
                b_idx = col_map[brand]
                raw_price = clean_row[b_idx] if b_idx < len(clean_row) else ""
                b_min, b_max = clean_price(raw_price)
                _set_brand_prices(entry, brand, b_min, b_max)

        data_rows.append(entry)

    return data_rows, current_province, current_city, global_col_map, global_location_columns


def _repair_visayas_unknown_provinces(data_rows):
    first_known_province = next(
        (
            row["province"]
            for row in data_rows
            if row.get("province") and row.get("province") != "Unknown"
        ),
        None,
    )
    if not first_known_province:
        return data_rows

    current_province = first_known_province
    for row in data_rows:
        if row.get("province") and row["province"] != "Unknown":
            current_province = row["province"]
        elif row.get("province") == "Unknown":
            row["province"] = current_province
    return data_rows


def _scrape_south_luzon_table(table, category_name, current_province, current_city, global_col_map, pending_brand_pairs, table_obj=None):
    data_rows = []
    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)

    col_map = get_column_mapping(table)
    if col_map:
        global_col_map = col_map
    else:
        col_map = global_col_map
    if not col_map:
        return data_rows, current_province, current_city, global_col_map, pending_brand_pairs

    geometric_price_map = _build_geometric_price_map(table_obj, table, col_map, sorted_fuels)

    for row_idx, row in enumerate(table):
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        province_from_row, city_from_row = location_ctx
        location_changed = False
        if province_from_row:
            if province_from_row != current_province:
                current_province = province_from_row
                location_changed = True
            if not city_from_row and current_city != "Unknown":
                current_city = "Unknown"
                location_changed = True
        if city_from_row and city_from_row != current_city:
            current_city = city_from_row
            location_changed = True
        if location_changed:
            pending_brand_pairs = {}

        entry = _build_base_entry(category_name, current_province, current_city, found_fuel)

        for brand in BRANDS:
            if brand not in col_map:
                continue

            geometric_pair = geometric_price_map.get((row_idx, brand))
            if geometric_pair:
                _set_brand_prices(entry, brand, geometric_pair[0], geometric_pair[1])
                continue

            if geometric_price_map:
                continue

            b_idx = col_map[brand]
            raw_price = clean_row[b_idx] if b_idx < len(clean_row) else ""
            price_floats = _extract_float_prices(raw_price)

            if len(price_floats) >= 2:
                current_fuel_index = FUELS.index(found_fuel) if found_fuel in FUELS else -1
                # South Luzon PDFs sometimes place a multi-fuel merged value block on a blank RON 100 row.
                # In that case, keep RON 100 as null and defer all extracted pairs to lower fuel rows.
                shifted_from_blank_top_row = found_fuel == "RON 100" and len(price_floats) >= 4

                if shifted_from_blank_top_row:
                    grouped = _price_pairs_from_floats(price_floats)
                    expected_fuels = FUELS[current_fuel_index + 1:] if current_fuel_index >= 0 else []
                else:
                    current_pair = (price_floats[0], price_floats[1])
                    _set_brand_prices(entry, brand, current_pair[0], current_pair[1])
                    leftovers = price_floats[2:]
                    grouped = _price_pairs_from_floats(leftovers) if leftovers else []
                    expected_fuels = FUELS[current_fuel_index + 1:] if current_fuel_index >= 0 else []

                if grouped:
                    fuel_tagged_pairs = _align_pairs_to_fuels_with_gaps(grouped, expected_fuels)
                    if fuel_tagged_pairs:
                        pending_brand_pairs[brand] = {
                            "strong": len(grouped) >= 2,
                            "queue": fuel_tagged_pairs,
                        }
                    elif brand in pending_brand_pairs:
                        # If we can't map exactly, do NOT carry-over (prevents misaligned steals).
                        del pending_brand_pairs[brand]
                elif brand in pending_brand_pairs:
                    del pending_brand_pairs[brand]
            elif brand in pending_brand_pairs and pending_brand_pairs[brand].get("queue"):
                queue = pending_brand_pairs[brand]["queue"]
                next_expected = queue[0]
                # Only consume carry-over when:
                # - it matches the fuel row
                # - the row doesn't already look like it contains real per-row prices
                # - and either multiple brands are pending OR this brand was detected as a strong merged-block
                if next_expected["fuel"] == found_fuel:
                    row_has_other_prices = _row_has_any_brand_prices(clean_row, col_map, exclude_brand=brand)
                    pending_brands_count = sum(
                        1
                        for v in pending_brand_pairs.values()
                        if isinstance(v, dict) and v.get("queue")
                    )
                    allow_single_brand_carry = bool(pending_brand_pairs[brand].get("strong"))
                    if not row_has_other_prices and (pending_brands_count >= 2 or allow_single_brand_carry):
                        next_pair = queue.pop(0)["pair"]
                        _set_brand_prices(entry, brand, next_pair[0], next_pair[1])
                        if not queue:
                            del pending_brand_pairs[brand]
            else:
                b_min, b_max = clean_price(raw_price)
                _set_brand_prices(entry, brand, b_min, b_max)

        data_rows.append(entry)

    return data_rows, current_province, current_city, global_col_map, pending_brand_pairs


def _scrape_ncr_table(table, category_name, current_province, current_city, global_col_map, pending_brand_pairs, table_obj=None):
    data_rows = []
    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY", "CITY/AREA"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)
    current_province = _ncr_province_or_default(current_province)

    col_map = get_column_mapping(table)
    if col_map:
        global_col_map = col_map
    else:
        col_map = global_col_map
    if not col_map:
        return data_rows, current_province, current_city, global_col_map, pending_brand_pairs

    geometric_price_map = _build_geometric_price_map(table_obj, table, col_map, sorted_fuels)

    for row_idx, row in enumerate(table):
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        province_from_row, city_from_row = location_ctx
        if province_from_row:
            current_province = _ncr_province_or_default(province_from_row)
        else:
            current_province = _ncr_province_or_default(current_province)

        if city_from_row and city_from_row != current_city:
            current_city = city_from_row
            pending_brand_pairs = {}

        entry = _build_base_entry(category_name, current_province, current_city, found_fuel)

        for brand in BRANDS:
            if brand not in col_map:
                continue

            geometric_pair = geometric_price_map.get((row_idx, brand))
            if geometric_pair:
                _set_brand_prices(entry, brand, geometric_pair[0], geometric_pair[1])
                continue

            if geometric_price_map:
                continue

            b_idx = col_map[brand]
            raw_price = clean_row[b_idx] if b_idx < len(clean_row) else ""
            floats = _extract_float_prices(raw_price)

            # NCR special: merged row at RON 100; align the extracted prices
            # with allowed gaps so blank fuel rows don't steal prices from lower fuels.
            if found_fuel == "RON 100" and len(floats) >= 4:
                pairs = _price_pairs_from_floats(floats)
                overall_idx = col_map.get("OVERALL RANGE")
                current_overall = None
                if overall_idx is not None and overall_idx < len(clean_row):
                    current_min, current_max = clean_price(clean_row[overall_idx])
                    if current_min is not None and current_max is not None:
                        current_overall = (current_min, current_max)
                if pairs and _pair_within_range(pairs[0], current_overall):
                    current_pair = pairs.pop(0)
                    _set_brand_prices(entry, brand, current_pair[0], current_pair[1])

                fuel_ladder = ["RON 97", "RON 95", "RON 91", "DIESEL", "DIESEL PLUS", "KEROSENE"]
                overall_ranges = _collect_following_overall_ranges(table, row_idx, col_map, sorted_fuels)
                if pairs:
                    pending_brand_pairs[brand] = _align_pairs_to_fuels_by_overall_range(
                        pairs,
                        fuel_ladder,
                        overall_ranges,
                    )
                continue

            if len(floats) >= 2:
                cur_min, cur_max = clean_price(raw_price)
                _set_brand_prices(entry, brand, cur_min, cur_max)
                continue

            if brand in pending_brand_pairs and pending_brand_pairs[brand]:
                next_expected = pending_brand_pairs[brand][0]
                if next_expected["fuel"] == found_fuel:
                    next_pair = pending_brand_pairs[brand].pop(0)["pair"]
                    _set_brand_prices(entry, brand, next_pair[0], next_pair[1])
                    if not pending_brand_pairs[brand]:
                        del pending_brand_pairs[brand]

        data_rows.append(entry)

    return data_rows, current_province, current_city, global_col_map, pending_brand_pairs


def _get_strategy(category_name):
    if str(category_name).upper().startswith("REGION IV"):
        return "south_luzon"
    if str(category_name).strip().upper() == "SOUTH LUZON":
        return "south_luzon"
    if str(category_name).strip().upper() == "NORTH LUZON":
        return "north_luzon"
    if str(category_name).strip().upper() == "NCR":
        return "ncr"
    if str(category_name).strip().upper() == "VISAYAS":
        return "visayas"
    return "default"

def scrape_pdf_content(pdf_url, category_name):
    print(f"Opening PDF: {pdf_url}")
    try:
        if str(category_name).strip().upper() == "ADJUSTMENT":
            return parse_price_adjustment_pdf(pdf_url)

        if os.path.exists(pdf_url):
            pdf_open_target = pdf_url
        else:
            response = requests.get(pdf_url, timeout=10)
            response.raise_for_status()
            pdf_open_target = BytesIO(response.content)

        with pdfplumber.open(pdf_open_target) as pdf:
            data_rows = []
            image_only_pages = 0
            
            # Initialize state
            current_province = "Unknown"
            current_city = "Unknown"
            global_col_map = {}
            global_location_columns = {}
            pending_brand_pairs = {}
            strategy = _get_strategy(category_name)
            previous_ncr_page_first_city = None
            south_luzon_first_province = None
            south_luzon_first_city = None

            if strategy == "north_luzon":
                return _scrape_north_luzon_ocr(pdf_open_target, category_name)

            for page in pdf.pages:
                table_obj = page.find_table()
                table = table_obj.extract() if table_obj else page.extract_table()
                if not table:
                    if page.images and not page.chars:
                        image_only_pages += 1
                    continue

                if strategy == "south_luzon":
                    sorted_fuels = sorted(FUELS, key=len, reverse=True)
                    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY"]
                    if south_luzon_first_province is None:
                        south_luzon_first_province = _first_province_in_table(
                            table,
                            sorted_fuels,
                            headers_to_ignore,
                        )
                    if south_luzon_first_city is None:
                        south_luzon_first_city = _first_city_in_table(
                            table,
                            sorted_fuels,
                            headers_to_ignore,
                        )
                    table = _repair_south_luzon_location_contaminants(
                        table,
                        south_luzon_first_province,
                        south_luzon_first_city,
                    )
                    page_rows, current_province, current_city, global_col_map, pending_brand_pairs = _scrape_south_luzon_table(
                        table,
                        category_name,
                        current_province,
                        current_city,
                        global_col_map,
                        pending_brand_pairs,
                        table_obj,
                    )
                elif strategy == "ncr":
                    table = _repair_ncr_page_boundary_city(table, previous_ncr_page_first_city)
                    previous_ncr_page_first_city = _first_city_in_table(
                        table,
                        sorted(FUELS, key=len, reverse=True),
                        ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY", "CITY/AREA"],
                    )
                    page_rows, current_province, current_city, global_col_map, pending_brand_pairs = _scrape_ncr_table(
                        table,
                        category_name,
                        current_province,
                        current_city,
                        global_col_map,
                        pending_brand_pairs,
                        table_obj,
                    )
                elif strategy == "visayas":
                    page_rows, current_province, current_city, global_col_map, global_location_columns = _scrape_visayas_table(
                        table,
                        category_name,
                        current_province,
                        current_city,
                        global_col_map,
                        global_location_columns,
                    )
                else:
                    page_rows, current_province, current_city, global_col_map = _scrape_default_table(
                        table,
                        category_name,
                        current_province,
                        current_city,
                        global_col_map,
                    )
                data_rows.extend(page_rows)
            if not data_rows and image_only_pages:
                print(
                    "No rows scraped because this PDF has no extractable text layer for pdfplumber. "
                    f"{image_only_pages} page(s) had page images but no PDF text objects. "
                    "Some PDF viewers can still select text using their own OCR; this scraper needs "
                    "an OCR step or an OCR fallback for this report."
                )
            if strategy == "visayas":
                data_rows = _repair_visayas_unknown_provinces(data_rows)
            return data_rows
    except Exception as e:
        print(f"Error scraping {pdf_url}: {e}")
        return []

if __name__ == "__main__":
    # --- TEST EXECUTION ---
    # Mocking the reports for the sake of standard testing
    mock_reports = [
{"url": "https://prod-cms.doe.gov.ph/documents/d/guest/ncr-price-monitoring-04282026-pdf", "category": "NCR"}
    ]

    final_data = []
    for report in mock_reports:
        if report["url"]:
            pdf_results = scrape_pdf_content(report["url"], report["category"])
            final_data.extend(pdf_results)

    print(f"\nScraped {len(final_data)} total fuel price records.")
    if final_data:
        #save_fuel_data(final_data)
        #save_adjustment_data(final_data)
        print(json.dumps(final_data[:3], indent=4))
