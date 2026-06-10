from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageOps
import matplotlib.pyplot as plt

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

# ============================================================
# APP DIDACTICA PARA CORRECCION DE CURSORES EN CARDIOGRAFIA
# DE IMPEDANCIA
# ============================================================

APP_TITLE = "App didáctica para corrección de cursores en Cardiografía de Impedancia"
APP_SUBTITLE = "Corrección visual de B, C, X e Y integrando ECG, dZ/dt y fonocardiograma"
APP_DEVELOPER = "Desarrollador: Dr. Ricardo Daniel Olano — Cardiólogo Hipertensólogo"
DB_PATH = Path("cgi_cursores_didactica.sqlite3")
CURSORS = ["B", "C", "X", "Y"]

st.set_page_config(page_title="CGI cursores didáctica", page_icon="🫀", layout="wide")

# ------------------------------------------------------------
# ESTILO
# ------------------------------------------------------------

def css() -> None:
    st.markdown(
        """
        <style>
        .stApp{background:linear-gradient(180deg,#F4F8FB,#FFFFFF)!important;}
        .block-container{max-width:1440px;padding-top:1.0rem;padding-bottom:2.0rem;}
        .hero{background:linear-gradient(90deg,#082F49,#075985);border-radius:18px;padding:20px 24px;margin-bottom:16px;color:white;box-shadow:0 12px 28px rgba(8,47,73,.18)}
        .hero h1{margin:0;color:white!important;font-size:1.55rem}.hero p{margin:.25rem 0;color:#E0F2FE}.hero .dev{font-weight:800;color:#BAE6FD;margin-top:.45rem}
        .card{background:white;border:1px solid #D7E3EE;border-radius:16px;padding:14px 16px;margin-bottom:12px;box-shadow:0 4px 14px rgba(15,23,42,.06)}
        .guide{background:#EAF6FF;border:1px solid #BAE6FD;border-radius:14px;padding:12px;color:#075985;margin-bottom:10px;}
        .warn{background:#FFF7ED;border:1px solid #FED7AA;border-radius:14px;padding:12px;color:#7C2D12;margin-bottom:10px;}
        .ok{background:#ECFDF5;border:1px solid #99F6E4;border-radius:14px;padding:12px;color:#064E3B;margin-bottom:10px;}
        .small{font-size:.90rem;color:#5B6B7D;}
        .stButton>button,.stDownloadButton>button{background:#075985!important;color:white!important;border-radius:10px!important;border:1px solid #082F49!important;font-weight:800!important;}
        </style>
        """,
        unsafe_allow_html=True,
    )

css()
st.markdown(f"<div class='hero'><h1>{APP_TITLE}</h1><p>{APP_SUBTITLE}</p><div class='dev'>{APP_DEVELOPER}</div></div>", unsafe_allow_html=True)

# ------------------------------------------------------------
# BASE DE DATOS SIMPLE
# ------------------------------------------------------------

def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    c = conn()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS cursor_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            patient_code TEXT,
            study_date TEXT,
            condition_label TEXT,
            source_name TEXT,
            page_number INTEGER,
            rois_json TEXT NOT NULL,
            auto_json TEXT NOT NULL,
            manual_json TEXT NOT NULL,
            guide_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            conclusion TEXT NOT NULL
        )
        """
    )
    c.commit()
    c.close()


def save_session(patient_code: str, study_date: str, condition_label: str, source_name: str, page_number: int,
                 rois: Dict[str, Dict[str, int]], auto: Dict[str, Dict[str, float]], manual: Dict[str, Dict[str, float]],
                 guide: Dict[str, float], metrics: Dict[str, float], conclusion: str) -> int:
    c = conn()
    cur = c.execute(
        """
        INSERT INTO cursor_sessions
        (created_at, patient_code, study_date, condition_label, source_name, page_number, rois_json, auto_json, manual_json, guide_json, metrics_json, conclusion)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            datetime.now().isoformat(timespec="seconds"), patient_code, study_date, condition_label, source_name, page_number,
            json.dumps(rois, ensure_ascii=False), json.dumps(auto, ensure_ascii=False), json.dumps(manual, ensure_ascii=False),
            json.dumps(guide, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False), conclusion,
        ),
    )
    c.commit()
    sid = int(cur.lastrowid)
    c.close()
    return sid


def sessions_df() -> pd.DataFrame:
    c = conn()
    df = pd.read_sql_query("SELECT * FROM cursor_sessions ORDER BY created_at DESC", c)
    c.close()
    return df

init_db()

# ------------------------------------------------------------
# UTILIDADES PDF / IMAGEN
# ------------------------------------------------------------

def count_pdf_pages(pdf_bytes: bytes) -> int:
    if fitz is None:
        raise RuntimeError("Falta PyMuPDF. Agregue PyMuPDF al requirements.txt")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def render_pdf(pdf_bytes: bytes, page_index: int, zoom: float) -> Image.Image:
    if fitz is None:
        raise RuntimeError("Falta PyMuPDF. Agregue PyMuPDF al requirements.txt")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_index = max(0, min(page_index, doc.page_count - 1))
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(float(zoom), float(zoom)), alpha=False)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    finally:
        doc.close()


def open_uploaded_image(uploaded) -> Tuple[Image.Image, int, str]:
    data = uploaded.getvalue()
    ext = Path(uploaded.name).suffix.lower()
    if ext == ".pdf":
        n = count_pdf_pages(data)
        c1, c2 = st.columns([1, 1])
        with c1:
            page = st.number_input("Página del PDF con ECG + dZ/dt + fonocardiograma", 1, max(1, n), min(2, max(1, n)), 1)
        with c2:
            zoom = st.slider("Resolución del PDF", 1.5, 4.0, 2.5, 0.25)
        return render_pdf(data, int(page) - 1, float(zoom)), int(page), uploaded.name
    img = Image.open(io.BytesIO(data)).convert("RGB")
    return img, 1, uploaded.name

# ------------------------------------------------------------
# DIGITALIZACION DE TRES SEÑALES
# ------------------------------------------------------------

def clamp_roi(roi: Dict[str, int], w: int, h: int) -> Dict[str, int]:
    x0 = max(0, min(int(roi["x_min"]), w - 2))
    x1 = max(x0 + 2, min(int(roi["x_max"]), w))
    y0 = max(0, min(int(roi["y_min"]), h - 2))
    y1 = max(y0 + 2, min(int(roi["y_max"]), h))
    return {"x_min": x0, "x_max": x1, "y_min": y0, "y_max": y1}


def default_rois(w: int, h: int) -> Dict[str, Dict[str, int]]:
    """Propuesta inicial para una página con tres bandas horizontales.
    El usuario puede ajustar los recortes en la interfaz.
    """
    x0, x1 = int(w * 0.05), int(w * 0.94)
    return {
        "ecg":  {"x_min": x0, "x_max": x1, "y_min": int(h * 0.53), "y_max": int(h * 0.62)},
        "dzdt": {"x_min": x0, "x_max": x1, "y_min": int(h * 0.63), "y_max": int(h * 0.77)},
        "fono": {"x_min": x0, "x_max": x1, "y_min": int(h * 0.78), "y_max": int(h * 0.88)},
    }


def draw_rois(img: Image.Image, rois: Dict[str, Dict[str, int]]) -> Image.Image:
    out = img.copy().convert("RGB")
    d = ImageDraw.Draw(out)
    labels = {"ecg": "ECG", "dzdt": "dZ/dt", "fono": "Fono"}
    colors = {"ecg": "green", "dzdt": "blue", "fono": "orange"}
    for key, roi in rois.items():
        r = clamp_roi(roi, *out.size)
        d.rectangle([r["x_min"], r["y_min"], r["x_max"], r["y_max"]], outline=colors[key], width=max(3, out.size[0] // 350))
        d.text((r["x_min"] + 6, r["y_min"] + 6), labels[key], fill=colors[key])
    return out


def smooth(y: np.ndarray, window: int = 9) -> np.ndarray:
    if len(y) < 5:
        return y.astype(float)
    window = int(max(3, window))
    if window % 2 == 0:
        window += 1
    window = min(window, len(y) if len(y) % 2 == 1 else len(y) - 1)
    if window < 3:
        return y.astype(float)
    pad = window // 2
    yy = np.pad(y.astype(float), (pad, pad), mode="edge")
    return np.convolve(yy, np.ones(window) / window, mode="valid")


def color_mask(crop_rgb: np.ndarray, mode: str) -> np.ndarray:
    r = crop_rgb[:, :, 0].astype(int)
    g = crop_rgb[:, :, 1].astype(int)
    b = crop_rgb[:, :, 2].astype(int)
    if mode == "ecg_verde":
        mask = (g > r + 10) & (g > b + 4) & (g < 235) & (r < 210) & (b < 210)
    elif mode == "dzdt_azul":
        mask = (b > r + 5) & (b > g + 2) & (b < 245) & (r < 220) & (g < 220)
    elif mode == "fono_naranja":
        mask = (r > g + 5) & (g > b + 5) & (r > 120) & (b < 190)
    else:
        gray = np.array(ImageOps.grayscale(Image.fromarray(crop_rgb.astype("uint8"))))
        thr = np.percentile(gray, 35)
        mask = gray <= thr
    return mask


def digitize_signal(img: Image.Image, roi: Dict[str, int], mode: str, min_points: int = 10) -> pd.DataFrame:
    rgb = np.asarray(img.convert("RGB"))
    w, h = img.size
    r = clamp_roi(roi, w, h)
    crop_rgb = rgb[r["y_min"]:r["y_max"], r["x_min"]:r["x_max"], :]
    crop_h, crop_w = crop_rgb.shape[:2]
    mask = color_mask(crop_rgb, mode)

    # Respaldo por oscuridad si el color no aparece bien en el PDF.
    if int(mask.sum()) < 80:
        gray = np.array(ImageOps.grayscale(Image.fromarray(crop_rgb.astype("uint8"))))
        thr = np.percentile(gray, 35)
        mask = gray <= thr

    xs: List[float] = []
    ys: List[float] = []
    max_dense = max(5, int(crop_h * 0.45))
    for cx in range(crop_w):
        rows = np.where(mask[:, cx])[0]
        if len(rows) == 0 or len(rows) > max_dense:
            continue
        xs.append(float(r["x_min"] + cx))
        ys.append(float(r["y_min"] + np.median(rows)))

    if len(xs) < min_points:
        return pd.DataFrame(columns=["x", "y_pixel", "y_norm"])

    df = pd.DataFrame({"x": xs, "y_pixel": ys}).groupby("x", as_index=False)["y_pixel"].median()
    y_s = smooth(df["y_pixel"].to_numpy(float), max(5, int(len(df) * 0.015)))
    amp = r["y_max"] - y_s
    amp = amp - np.nanmin(amp)
    if np.nanmax(amp) > 0:
        amp = amp / np.nanmax(amp)
    df["y_smooth"] = y_s
    df["y_norm"] = amp
    return df


def resample_to_common_x(df: pd.DataFrame, x_common: np.ndarray) -> np.ndarray:
    if df.empty or len(df) < 2:
        return np.full_like(x_common, np.nan, dtype=float)
    x = df["x"].to_numpy(float)
    y = df["y_norm"].to_numpy(float)
    order = np.argsort(x)
    x, y = x[order], y[order]
    return np.interp(x_common, x, y, left=np.nan, right=np.nan)


def nearest_y(df: pd.DataFrame, x_value: float) -> float:
    if df.empty:
        return 0.0
    idx = int(np.nanargmin(np.abs(df["x"].to_numpy(float) - float(x_value))))
    return float(df.iloc[idx]["y_norm"])


def detect_guides(ecg: pd.DataFrame, dzdt: pd.DataFrame, fono: pd.DataFrame, x_min: float, x_max: float,
                  fono_line: float) -> Tuple[Dict[str, Dict[str, float]], Dict[str, float]]:
    """Detección inicial didáctica. No reemplaza la corrección médica.
    - QRS: pico ECG más alto.
    - C: pico dZ/dt.
    - B: inicio de ascenso antes de C.
    - X: nadir posterior a C.
    - S1/S2: dos primeras zonas del fonocardiograma que superan la línea horizontal.
    """
    auto = {c: {"x": float((x_min + x_max) / 2), "y": 0.5} for c in CURSORS}
    guide = {"qrs": np.nan, "s1": np.nan, "s2": np.nan, "fono_line": float(fono_line)}

    if not ecg.empty:
        e = ecg.dropna().copy()
        guide["qrs"] = float(e.iloc[int(np.argmax(e["y_norm"].to_numpy(float)))]["x"])

    if not fono.empty:
        f = fono.copy()
        y = smooth(f["y_norm"].to_numpy(float), max(5, int(len(f) * 0.025)))
        above = y >= float(fono_line)
        groups = []
        start = None
        for i, val in enumerate(above):
            if val and start is None:
                start = i
            if start is not None and ((not val) or i == len(above) - 1):
                end = i if not val else i + 1
                if end - start >= 3:
                    groups.append((start, end))
                start = None
        # filtra grupos muy cercanos y toma centro de los dos más importantes por energía
        scored = []
        for a, b in groups:
            energy = float(np.nanmean(y[a:b]))
            center = float(np.nanmean(f["x"].to_numpy(float)[a:b]))
            scored.append((center, energy))
        scored = sorted(scored, key=lambda t: t[0])
        if len(scored) >= 1:
            guide["s1"] = scored[0][0]
        if len(scored) >= 2:
            guide["s2"] = scored[1][0]

    if not dzdt.empty:
        d = dzdt.copy()
        x = d["x"].to_numpy(float)
        y = smooth(d["y_norm"].to_numpy(float), max(5, int(len(d) * 0.025)))
        c_idx = int(np.argmax(y))
        c_x = float(x[c_idx])
        # B: pie de ascenso, preferentemente después del QRS y antes de C.
        left_start = 0
        if np.isfinite(guide["qrs"]):
            left_start = int(np.searchsorted(x, guide["qrs"]))
            left_start = min(max(0, left_start), max(0, c_idx - 3))
        segment = y[left_start:max(c_idx, left_start + 2)]
        if len(segment) >= 4:
            grad = np.gradient(segment)
            b_idx = left_start + int(np.argmax(grad))
        else:
            b_idx = max(0, int(c_idx * 0.55))
        # X: mínimo posterior a C, preferentemente cerca o antes del S2 si está detectado.
        right_end = len(y)
        if np.isfinite(guide["s2"]):
            right_end = max(c_idx + 5, min(len(y), int(np.searchsorted(x, guide["s2"]) + len(y) * 0.06)))
        post = y[c_idx + 1:right_end]
        if len(post) >= 4:
            x_idx = c_idx + 1 + int(np.argmin(post))
        else:
            x_idx = min(len(y) - 1, c_idx + max(3, len(y) // 5))
        # Y: rebote posterior a X.
        post_x = y[x_idx + 1:]
        if len(post_x) >= 4:
            y_idx = x_idx + 1 + int(np.argmax(post_x))
        else:
            y_idx = min(len(y) - 1, x_idx + max(3, len(y) // 8))
        raw = {"B": b_idx, "C": c_idx, "X": x_idx, "Y": y_idx}
        for name, idx in raw.items():
            xx = float(x[idx])
            auto[name] = {"x": xx, "y": nearest_y(dzdt, xx)}
    return auto, guide

# ------------------------------------------------------------
# GRAFICOS
# ------------------------------------------------------------

def plot_three_signals(ecg: pd.DataFrame, dzdt: pd.DataFrame, fono: pd.DataFrame,
                       auto: Dict[str, Dict[str, float]], manual: Dict[str, Dict[str, float]],
                       guide: Dict[str, float], x_min: float, x_max: float, title: str) -> io.BytesIO:
    x_common = np.linspace(x_min, x_max, 800)
    ecg_y = resample_to_common_x(ecg, x_common)
    dz_y = resample_to_common_x(dzdt, x_common)
    fo_y = resample_to_common_x(fono, x_common)

    fig, ax = plt.subplots(figsize=(14, 6.2))
    ax.plot(x_common, ecg_y + 2.4, linewidth=1.7, label="ECG")
    ax.plot(x_common, dz_y + 1.2, linewidth=2.3, label="Curva dZ/dt")
    ax.plot(x_common, fo_y + 0.0, linewidth=1.8, label="Fonocardiograma")

    # Línea horizontal simple para fono: referencia visual de S1/S2.
    fono_level = float(guide.get("fono_line", 0.55))
    ax.hlines(fono_level, x_min, x_max, linestyles="--", linewidth=1.5, label="Línea horizontal fono S1/S2")

    for key, label in [("qrs", "QRS"), ("s1", "S1"), ("s2", "S2")]:
        val = guide.get(key, np.nan)
        if np.isfinite(val):
            ax.axvline(float(val), linestyle="-.", linewidth=1.2)
            ax.text(float(val), 3.53 if key == "qrs" else 0.92, label, rotation=90, va="bottom", ha="center", fontsize=9)

    for c in CURSORS:
        ax.axvline(auto[c]["x"], linestyle=":", linewidth=1.4)
        ax.text(auto[c]["x"], 2.18, f"{c} auto", rotation=90, ha="center", va="bottom", fontsize=8)
        ax.axvline(manual[c]["x"], linestyle="--", linewidth=2.0)
        ax.text(manual[c]["x"], 1.05, f"{c}", rotation=90, ha="center", va="bottom", fontsize=10, fontweight="bold")

    ax.set_title(title)
    ax.set_xlabel("Tiempo relativo / eje X del estudio")
    ax.set_yticks([0.5, 1.7, 2.9])
    ax.set_yticklabels(["Fonocardiograma", "dZ/dt", "ECG"])
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.15, 3.75)
    ax.grid(True, alpha=0.22)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    bio = io.BytesIO()
    fig.savefig(bio, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    bio.seek(0)
    return bio


def build_cursor_table(auto: Dict[str, Dict[str, float]], manual: Dict[str, Dict[str, float]], guide: Dict[str, float]) -> pd.DataFrame:
    rows = []
    qrs, s1, s2 = guide.get("qrs", np.nan), guide.get("s1", np.nan), guide.get("s2", np.nan)
    for c in CURSORS:
        mx = manual[c]["x"]
        ref = ""
        if c == "B":
            ref = "después de QRS y al inicio del ascenso de dZ/dt"
        elif c == "C":
            ref = "pico sistólico principal de dZ/dt"
        elif c == "X":
            ref = "nadir sistólico; debe contrastarse con S2/fonocardiograma"
        else:
            ref = "rebote diastólico posterior, si es visible"
        rows.append({
            "Cursor": c,
            "Auto_x": round(auto[c]["x"], 1),
            "Manual_x": round(mx, 1),
            "Delta_x": round(mx - auto[c]["x"], 1),
            "Distancia_a_QRS": round(mx - qrs, 1) if np.isfinite(qrs) else "",
            "Distancia_a_S1": round(mx - s1, 1) if np.isfinite(s1) else "",
            "Distancia_a_S2": round(mx - s2, 1) if np.isfinite(s2) else "",
            "Criterio didáctico": ref,
        })
    return pd.DataFrame(rows)


def make_conclusion(df: pd.DataFrame, guide: Dict[str, float]) -> Tuple[Dict[str, float], str]:
    deltas = pd.to_numeric(df["Delta_x"], errors="coerce").abs()
    mae = float(deltas.mean()) if len(deltas) else np.nan
    has_qrs = np.isfinite(guide.get("qrs", np.nan))
    has_s1 = np.isfinite(guide.get("s1", np.nan))
    has_s2 = np.isfinite(guide.get("s2", np.nan))
    metrics = {
        "error_medio_px": mae,
        "qrs_detectado": float(has_qrs),
        "s1_detectado": float(has_s1),
        "s2_detectado": float(has_s2),
    }
    conclusion = (
        "Corrección didáctica realizada sobre una vista sincronizada de ECG, curva dZ/dt y fonocardiograma. "
        "El cursor B debe validarse respecto del QRS y del inicio del ascenso de dZ/dt; C sobre el pico sistólico principal; "
        "X respecto del nadir sistólico y su relación temporal con el segundo ruido; Y como rebote diastólico posterior si la morfología lo permite. "
        f"El error medio entre propuesta automática y corrección manual fue {mae:.1f} píxeles del eje temporal. "
        "La línea horizontal del fonocardiograma se usa como referencia visual simple para identificar S1 y S2."
    )
    return metrics, conclusion


def excel_export() -> bytes:
    df = sessions_df()
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="sesiones")
        rows = []
        for _, r in df.iterrows():
            manual = json.loads(r["manual_json"])
            auto = json.loads(r["auto_json"])
            guide = json.loads(r["guide_json"])
            tab = build_cursor_table(auto, manual, guide)
            tab.insert(0, "session_id", r["id"])
            tab.insert(1, "created_at", r["created_at"])
            tab.insert(2, "patient_code", r["patient_code"])
            rows.append(tab)
        if rows:
            pd.concat(rows, ignore_index=True).to_excel(writer, index=False, sheet_name="cursores")
        else:
            pd.DataFrame().to_excel(writer, index=False, sheet_name="cursores")
    bio.seek(0)
    return bio.getvalue()

# ------------------------------------------------------------
# INTERFAZ PRINCIPAL
# ------------------------------------------------------------

st.markdown(
    """
    <div class='guide'>
    <b>Objetivo de esta versión:</b> simplificar la app para enseñar y corregir cursores. La digitalización ya no toma solo dZ/dt: trabaja con tres bandas sincronizadas —ECG, dZ/dt y fonocardiograma— para que la corrección manual tenga contexto fisiológico.
    </div>
    """,
    unsafe_allow_html=True,
)

tab1, tab2 = st.tabs(["Corrección didáctica de cursores", "Histórico y exportación"])

with tab1:
    col_a, col_b, col_c = st.columns([1.2, 1, 1])
    with col_a:
        patient_code = st.text_input("Paciente / código del estudio", value="CASO-001")
    with col_b:
        study_date = st.date_input("Fecha del estudio", value=date.today())
    with col_c:
        condition_label = st.selectbox("Condición", ["Basal / acostado / cinta", "Parado", "Spot", "Entrenamiento"])

    uploaded = st.file_uploader("Cargar PDF original o imagen donde se vean ECG, dZ/dt y fonocardiograma", type=["pdf", "png", "jpg", "jpeg"])
    if uploaded is None:
        st.info("Cargue el PDF/imagen. Luego ajuste tres recortes: ECG, dZ/dt y fonocardiograma.")
        st.stop()

    try:
        img, page_number, source_name = open_uploaded_image(uploaded)
    except Exception as exc:
        st.error(f"No se pudo abrir el archivo: {exc}")
        st.stop()

    w, h = img.size
    d_rois = default_rois(w, h)

    with st.expander("1. Ajustar recortes de ECG, dZ/dt y fonocardiograma", expanded=True):
        st.markdown("<div class='small'>Ajuste cada rectángulo hasta que contenga solo su señal. El eje X debe cubrir el mismo ciclo cardíaco en las tres bandas.</div>", unsafe_allow_html=True)
        rois: Dict[str, Dict[str, int]] = {}
        labels = {"ecg": "ECG", "dzdt": "Curva dZ/dt", "fono": "Fonocardiograma"}
        for key in ["ecg", "dzdt", "fono"]:
            st.markdown(f"**{labels[key]}**")
            c1, c2, c3, c4 = st.columns(4)
            base = d_rois[key]
            with c1:
                x_min = st.slider(f"X mín {labels[key]}", 0, max(1, w - 2), base["x_min"], key=f"{key}_x0")
            with c2:
                x_max = st.slider(f"X máx {labels[key]}", min(x_min + 2, w), w, base["x_max"], key=f"{key}_x1")
            with c3:
                y_min = st.slider(f"Y mín {labels[key]}", 0, max(1, h - 2), base["y_min"], key=f"{key}_y0")
            with c4:
                y_max = st.slider(f"Y máx {labels[key]}", min(y_min + 2, h), h, base["y_max"], key=f"{key}_y1")
            rois[key] = clamp_roi({"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max}, w, h)

    st.image(draw_rois(img, rois), caption="Vista previa de recortes: verde ECG, azul dZ/dt, naranja fonocardiograma", use_container_width=True)

    with st.expander("2. Parámetros didácticos de detección", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            fono_line = st.slider("Línea horizontal del fonocardiograma para S1/S2", 0.10, 0.95, 0.55, 0.01)
        with c2:
            st.caption("La línea horizontal no intenta diagnosticar: solo ayuda a ver dónde aparecen los ruidos.")
        with c3:
            st.caption("Los cursores finales siempre son los corregidos manualmente por el médico/aprendiz.")

    ecg = digitize_signal(img, rois["ecg"], "ecg_verde")
    dzdt = digitize_signal(img, rois["dzdt"], "dzdt_azul")
    fono = digitize_signal(img, rois["fono"], "fono_naranja")

    if dzdt.empty:
        st.error("No se pudo digitalizar la curva dZ/dt. Ajuste el recorte azul o use una imagen/PDF con mayor resolución.")
        st.stop()
    if ecg.empty:
        st.warning("ECG no detectado con claridad. Igual puede corregir cursores, pero falta la referencia QRS.")
    if fono.empty:
        st.warning("Fonocardiograma no detectado con claridad. Igual puede corregir cursores, pero falta la referencia S1/S2.")

    x_min_common = max([rois[k]["x_min"] for k in rois])
    x_max_common = min([rois[k]["x_max"] for k in rois])
    auto, guide = detect_guides(ecg, dzdt, fono, x_min_common, x_max_common, fono_line)

    st.markdown("<div class='ok'><b>Señales digitalizadas.</b> Ahora corrija B, C, X e Y mirando simultáneamente el ECG, la curva dZ/dt y el fonocardiograma.</div>", unsafe_allow_html=True)

    with st.expander("3. Corrección manual de cursores", expanded=True):
        st.markdown(
            """
            <div class='guide'>
            <b>Guía rápida:</b><br>
            B: posterior al QRS y en el pie del ascenso de dZ/dt.<br>
            C: pico sistólico principal de dZ/dt.<br>
            X: nadir sistólico, validado contra el segundo ruido del fonocardiograma.<br>
            Y: rebote diastólico posterior si es visible.
            </div>
            """,
            unsafe_allow_html=True,
        )
        manual: Dict[str, Dict[str, float]] = {}
        min_slider = int(x_min_common)
        max_slider = int(x_max_common)
        cols = st.columns(4)
        for i, c in enumerate(CURSORS):
            with cols[i]:
                default_x = int(round(auto[c]["x"]))
                val = st.slider(f"Cursor {c}", min_slider, max_slider, min(max(default_x, min_slider), max_slider), key=f"manual_{c}")
                manual[c] = {"x": float(val), "y": nearest_y(dzdt, float(val))}

    chart = plot_three_signals(ecg, dzdt, fono, auto, manual, guide, x_min_common, x_max_common, "Corrección integrada ECG + dZ/dt + fonocardiograma")
    st.image(chart, caption="Vista didáctica sincronizada de las tres señales y cursores corregidos", use_container_width=True)

    cursor_df = build_cursor_table(auto, manual, guide)
    metrics, conclusion = make_conclusion(cursor_df, guide)
    st.subheader("Tabla de corrección")
    st.dataframe(cursor_df, use_container_width=True)
    st.subheader("Conclusión didáctica")
    st.info(conclusion)

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Guardar corrección y aprendizaje", type="primary"):
            sid = save_session(patient_code, str(study_date), condition_label, source_name, page_number, rois, auto, manual, guide, metrics, conclusion)
            st.success(f"Corrección guardada con ID {sid}.")
    with c2:
        st.download_button(
            "Descargar gráfico PNG",
            data=chart.getvalue(),
            file_name=f"{patient_code}_cursores_cgi.png".replace(" ", "_"),
            mime="image/png",
        )

with tab2:
    st.header("Histórico")
    df_hist = sessions_df()
    if df_hist.empty:
        st.info("Todavía no hay correcciones guardadas.")
    else:
        show_cols = ["id", "created_at", "patient_code", "study_date", "condition_label", "source_name", "conclusion"]
        st.dataframe(df_hist[show_cols], use_container_width=True)
        st.download_button(
            "Exportar histórico completo a Excel",
            data=excel_export(),
            file_name="historico_cursores_cgi.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.markdown(
        """
        <div class='card'>
        <b>Qué se simplificó:</b> se retiró el flujo extenso de usuario/administrador y se dejó un módulo único de entrenamiento visual. Esta versión prioriza la corrección de cursores con contexto electrocardiográfico y fonocardiográfico.
        </div>
        """,
        unsafe_allow_html=True,
    )
