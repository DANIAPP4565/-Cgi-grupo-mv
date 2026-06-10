from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageOps
import matplotlib.pyplot as plt

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

APP_TITLE = "Repositorio CGI para correlación y concordancia interusuario"
APP_SUBTITLE = "Digitalización de informe completo Exxer/Z-Logic + anonimización + Excel por usuario y administrador"
APP_DEVELOPER = "Desarrollador: Dr. Olano Ricardo Daniel — Cardiólogo Hipertensólogo"
DB_PATH = Path("cgi_repositorio_concordancia.sqlite3")
FILES_DIR = Path("archivos_cgi")
FILES_DIR.mkdir(exist_ok=True)
ADMIN_USER = "admin"
ADMIN_PASS_DEFAULT = "admin123"
CURSORS = ["QRS", "B", "C", "X", "Y"]
SIGNALS = ["dzdt", "ecg", "fono"]

st.set_page_config(page_title="Repositorio CGI", page_icon="🫀", layout="wide")

# ============================================================
# ESTILO
# ============================================================

def css() -> None:
    st.markdown(
        """
        <style>
        .stApp{background:linear-gradient(180deg,#F5FAFD,#FFFFFF)!important;}
        .block-container{max-width:1500px;padding-top:1rem;padding-bottom:2rem;}
        .hero{background:linear-gradient(90deg,#082F49,#075985);border-radius:18px;padding:18px 22px;margin-bottom:14px;color:white;box-shadow:0 10px 26px rgba(8,47,73,.18)}
        .hero h1{margin:0;color:white!important;font-size:1.45rem}.hero p{margin:.25rem 0;color:#E0F2FE}.hero .dev{font-weight:800;color:#BAE6FD;margin-top:.35rem}
        .box{background:white;border:1px solid #D7E3EE;border-radius:14px;padding:12px 14px;margin-bottom:10px;box-shadow:0 4px 12px rgba(15,23,42,.05)}
        .guide{background:#EAF6FF;border:1px solid #BAE6FD;border-radius:14px;padding:12px;color:#075985;margin-bottom:10px;}
        .ok{background:#ECFDF5;border:1px solid #99F6E4;border-radius:14px;padding:12px;color:#064E3B;margin-bottom:10px;}
        .warn{background:#FFF7ED;border:1px solid #FED7AA;border-radius:14px;padding:12px;color:#7C2D12;margin-bottom:10px;}
        .danger{background:#FEF2F2;border:1px solid #FECACA;border-radius:14px;padding:12px;color:#7F1D1D;margin-bottom:10px;}
        .small{font-size:.88rem;color:#556575;}
        .stButton>button,.stDownloadButton>button{background:#075985!important;color:white!important;border-radius:10px!important;border:1px solid #082F49!important;font-weight:800!important;}
        </style>
        """,
        unsafe_allow_html=True,
    )

# ============================================================
# SEGURIDAD / DB
# ============================================================

def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest = stored.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
        return secrets.compare_digest(dk, digest)
    except Exception:
        return False


def init_db() -> None:
    con = connect()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            matricula TEXT,
            provincia TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS studies(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            patient_code TEXT NOT NULL,
            patient_key_basis TEXT,
            study_date TEXT,
            condition_label TEXT,
            source_name TEXT,
            page_number INTEGER,
            medication TEXT,
            observations TEXT,
            extracted_text TEXT,
            variables_json TEXT NOT NULL,
            image_path TEXT,
            notes TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cursor_corrections(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            study_id INTEGER,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            patient_code TEXT NOT NULL,
            source_name TEXT,
            page_number INTEGER,
            rois_json TEXT,
            auto_json TEXT,
            manual_json TEXT,
            guide_json TEXT,
            metrics_json TEXT,
            conclusion TEXT,
            FOREIGN KEY(study_id) REFERENCES studies(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    con.commit()
    n = cur.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if n == 0:
        cur.execute(
            "INSERT INTO users(username,password_hash,full_name,matricula,provincia,role,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (ADMIN_USER, hash_password(ADMIN_PASS_DEFAULT), "Administrador", "", "", "admin", 1, now_iso()),
        )
        con.commit()
    con.close()


def get_user(username: str):
    con = connect()
    row = con.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (username.strip(),)).fetchone()
    con.close()
    return row


def create_user(username: str, password: str, full_name: str, matricula: str, provincia: str) -> Tuple[bool, str]:
    username = username.strip()
    if len(username) < 3 or len(password) < 6 or not full_name.strip() or not matricula.strip() or not provincia.strip():
        return False, "Complete usuario, contraseña de al menos 6 caracteres, nombre, matrícula y provincia."
    try:
        con = connect()
        con.execute(
            "INSERT INTO users(username,password_hash,full_name,matricula,provincia,role,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (username, hash_password(password), full_name.strip(), matricula.strip(), provincia.strip(), "user", 1, now_iso()),
        )
        con.commit()
        con.close()
        return True, "Usuario registrado. Ya puede ingresar."
    except sqlite3.IntegrityError:
        return False, "Ese usuario ya existe."


def login_ui() -> None:
    st.markdown(f"<div class='hero'><h1>{APP_TITLE}</h1><p>{APP_SUBTITLE}</p><div class='dev'>{APP_DEVELOPER}</div></div>", unsafe_allow_html=True)
    st.markdown("<div class='guide'><b>Ingreso protegido.</b> Cada operador queda identificado para el análisis de correlación y concordancia interusuario. Los Excel exportan código anónimo, no nombre del paciente.</div>", unsafe_allow_html=True)
    t1, t2 = st.tabs(["Ingresar", "Registrar usuario"])
    with t1:
        c1, c2 = st.columns(2)
        with c1:
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            if st.button("Ingresar", type="primary"):
                row = get_user(username)
                if row and row["active"] and verify_password(password, row["password_hash"]):
                    st.session_state.user = dict(row)
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos.")
        with c2:
            st.info(f"Primer ingreso de administrador: usuario `{ADMIN_USER}` / contraseña `{ADMIN_PASS_DEFAULT}`. Luego cambie esa clave en producción.")
    with t2:
        c1, c2, c3 = st.columns(3)
        with c1:
            new_user = st.text_input("Nuevo usuario")
            new_pass = st.text_input("Nueva contraseña", type="password")
        with c2:
            full_name = st.text_input("Nombre del operador")
            matricula = st.text_input("Matrícula profesional")
        with c3:
            provincia = st.selectbox("Provincia", ["", "Buenos Aires", "CABA", "Catamarca", "Chaco", "Chubut", "Córdoba", "Corrientes", "Entre Ríos", "Formosa", "Jujuy", "La Pampa", "La Rioja", "Mendoza", "Misiones", "Neuquén", "Río Negro", "Salta", "San Juan", "San Luis", "Santa Cruz", "Santa Fe", "Santiago del Estero", "Tierra del Fuego", "Tucumán"])
        if st.button("Registrar"):
            ok, msg = create_user(new_user, new_pass, full_name, matricula, provincia)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

# ============================================================
# PDF / IMAGEN / TEXTO
# ============================================================

def spatial_text_from_page(page) -> str:
    """Reconstruye líneas por coordenadas del PDF.
    Esto es clave para informes Exxer/Z-Logic, porque p.get_text('text') suele mezclar
    encabezados, barras de referencia y nombres de variables en una sola línea.
    """
    try:
        words = page.get_text("words") or []
    except Exception:
        return ""
    if not words:
        return ""
    # word tuple: x0,y0,x1,y1,text,block,line,word
    groups = {}
    for w in words:
        x0, y0, x1, y1, txt, block, line, word = w[:8]
        key = (int(block), int(line))
        groups.setdefault(key, []).append((float(x0), float(y0), str(txt)))
    line_items = []
    for key, vals in groups.items():
        vals = sorted(vals, key=lambda t: t[0])
        y = sum(v[1] for v in vals) / max(1, len(vals))
        text = " ".join(v[2] for v in vals)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            line_items.append((y, min(v[0] for v in vals), text))
    line_items.sort(key=lambda t: (t[0], t[1]))
    return "\n".join(t[2] for t in line_items)


def render_pdf_page(pdf_bytes: bytes, page: int, zoom: float) -> Tuple[Image.Image, int, str]:
    if fitz is None:
        raise RuntimeError("Falta PyMuPDF. Agregue PyMuPDF al requirements.txt.")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        total = int(doc.page_count)
        page = int(max(0, min(page, total - 1)))
        p = doc.load_page(page)
        pix = p.get_pixmap(matrix=fitz.Matrix(float(zoom), float(zoom)), alpha=False)
        plain_text = p.get_text("text") or ""
        spatial_text = spatial_text_from_page(p)
        # Primero líneas espaciales; después texto plano como respaldo.
        text = ("=== LINEAS_ESPACIALES_PDF ===\n" + spatial_text + "\n=== TEXTO_PLANO_PDF ===\n" + plain_text).strip()
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), total, text
    finally:
        doc.close()


def open_uploaded(uploaded) -> Tuple[Image.Image, int, str, str]:
    raw = uploaded.getvalue()
    suffix = Path(uploaded.name).suffix.lower()
    if suffix == ".pdf":
        if fitz is None:
            raise RuntimeError("No se pudo importar PyMuPDF.")
        doc = fitz.open(stream=raw, filetype="pdf")
        total = int(doc.page_count)
        doc.close()
        col1, col2 = st.columns(2)
        with col1:
            page = st.number_input("Página del informe a cargar", min_value=1, max_value=max(1, total), value=min(2, max(1, total)), step=1)
        with col2:
            zoom = st.slider("Resolución PDF", 1.5, 4.0, 2.5, 0.25)
        img, _, text = render_pdf_page(raw, int(page) - 1, float(zoom))
        return img.convert("RGB"), int(page), uploaded.name, text
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    return img, 1, uploaded.name, ""

# ============================================================
# ANONIMIZACION
# ============================================================

def normalize_key(s: str) -> str:
    s = (s or "").strip().upper()
    s = re.sub(r"\s+", " ", s)
    return s


def patient_code_from(name: str, dni: str, birthdate: str, study_date: str) -> Tuple[str, str]:
    basis = "|".join([normalize_key(name), normalize_key(dni), normalize_key(birthdate), normalize_key(study_date)])
    if basis.replace("|", "") == "":
        basis = secrets.token_hex(8)
    digest = hashlib.sha256(("CGI_REPO_2026|" + basis).encode("utf-8")).hexdigest().upper()
    return f"CGI-{digest[:10]}", basis

# ============================================================
# EXTRACCION DE VARIABLES DEL INFORME
# ============================================================

VARIABLES: List[Dict[str, str]] = [
    {"codigo": "FC", "nombre": "Frecuencia Cardíaca", "unidad": "pulsos/min"},
    {"codigo": "PAS", "nombre": "Presión sistólica", "unidad": "mmHg"},
    {"codigo": "PAD", "nombre": "Presión diastólica", "unidad": "mmHg"},
    {"codigo": "PAM", "nombre": "Presión media", "unidad": "mmHg"},
    {"codigo": "DS", "nombre": "Descarga Sistólica", "unidad": "ml/pulso"},
    {"codigo": "IDS", "nombre": "Índice de Descarga Sistólica", "unidad": "ml/pulso/m2"},
    {"codigo": "VM", "nombre": "Volumen Minuto", "unidad": "L/min"},
    {"codigo": "IC", "nombre": "Índice Cardíaco", "unidad": "L/min/m2"},
    {"codigo": "RVS", "nombre": "Resistencia Vascular Sistémica", "unidad": "dyn.s.cm-5"},
    {"codigo": "IRV", "nombre": "Índice de Resistencia Vascular", "unidad": "dyn.s.cm-5.m2"},
    {"codigo": "CA", "nombre": "Complacencia Arterial", "unidad": "ml/mmHg"},
    {"codigo": "IV", "nombre": "Índice de Velocidad", "unidad": "/1000/seg"},
    {"codigo": "IAC", "nombre": "Índice de Aceleración Cardíaca", "unidad": "/100/seg2"},
    {"codigo": "CTS", "nombre": "Cociente de Tiempo Sistólico", "unidad": "%"},
    {"codigo": "PE", "nombre": "Período expulsivo", "unidad": "ms"},
    {"codigo": "PPE", "nombre": "Preperíodo expulsivo", "unidad": "ms"},
    {"codigo": "ITC", "nombre": "Índice de Trabajo Cardíaco", "unidad": "kg.m/m2"},
    {"codigo": "CFT", "nombre": "Contenido de Fluidos Torácicos", "unidad": "kohms(-1)"},
    {"codigo": "RR", "nombre": "Intervalo RR", "unidad": "ms"},
    {"codigo": "DZDT_MAX", "nombre": "dZ/dt máximo", "unidad": "ohm/seg"},
    {"codigo": "Z0", "nombre": "Impedancia basal Z0", "unidad": "ohm"},
    {"codigo": "DIST_D", "nombre": "Distancia electrodos D", "unidad": "cm"},
    {"codigo": "DIST_T", "nombre": "Distancia electrodos T", "unidad": "cm"},
]


def empty_variables_df() -> pd.DataFrame:
    return pd.DataFrame([{**v, "valor": np.nan, "estado": "manual/revisar"} for v in VARIABLES])


def to_float(x: str):
    if x is None:
        return np.nan
    x = str(x).strip().replace(",", ".")
    x = re.sub(r"[^0-9.\-]", "", x)
    try:
        return float(x)
    except Exception:
        return np.nan


def first_number_in(s: str):
    m = re.search(r"-?\d+(?:[\.,]\d+)?", s or "")
    return to_float(m.group(0)) if m else np.nan


def set_df_value(df: pd.DataFrame, code: str, val, state: str = "extraído/revisar") -> None:
    if pd.isna(val):
        return
    idx = df.index[df["codigo"] == code]
    if len(idx):
        df.loc[idx[0], "valor"] = float(val)
        df.loc[idx[0], "estado"] = state


def parse_variable_lines(lines: List[str], df: pd.DataFrame) -> None:
    """Extrae valores cuando el PDF conserva una línea por variable.
    Ejemplos esperados:
    FC Frecuencia Cardíaca 57 pulsos/min
    PA Sistólica/Diastólica (Media) 125/76 (92) mmHg
    CTS Cociente de Tiempo Sistólico 39% (120/305)
    """
    aliases = {
        "FC": ["FC", "Frecuencia Card"],
        "DS": ["DS", "Descarga Sist"],
        "IDS": ["IDS", "Indice de Descarga", "Índice de Descarga"],
        "VM": ["VM", "Volumen Minuto"],
        "IC": ["IC", "Indice Card", "Índice Card"],
        "RVS": ["RVS", "Resistencia Vascular Sist"],
        "IRV": ["IRV", "Indice de Resistencia", "Índice de Resistencia"],
        "CA": ["CA", "Complacencia Arterial"],
        "IV": ["IV", "Indice de Velocidad", "Índice de Velocidad"],
        "IAC": ["IAC", "Indice de Aceler", "Índice de Aceler"],
        "ITC": ["ITC", "Indice de Trabajo", "Índice de Trabajo"],
        "CFT": ["CFT", "Contenido de Fluidos"],
    }
    used_line_idx = set()
    for i, raw in enumerate(lines):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        # Presión arterial compuesta
        if re.search(r"^(PA\b|.*Sist[óo]lica/Diast[óo]lica)", line, re.I):
            m = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})(?:\s*\(?\s*(\d{2,3})\s*\)?)?", line)
            if m:
                set_df_value(df, "PAS", to_float(m.group(1)))
                set_df_value(df, "PAD", to_float(m.group(2)))
                if m.group(3):
                    set_df_value(df, "PAM", to_float(m.group(3)))
                used_line_idx.add(i)
                continue
        # CTS compuesto
        if re.search(r"^(CTS\b|.*Cociente de Tiempo Sist)", line, re.I):
            m = re.search(r"(\d+(?:[\.,]\d+)?)\s*%?\s*(?:\(?\s*(\d+(?:[\.,]\d+)?)\s*/\s*(\d+(?:[\.,]\d+)?)\s*\)?)?", line)
            if m:
                set_df_value(df, "CTS", to_float(m.group(1)))
                if m.group(2): set_df_value(df, "PPE", to_float(m.group(2)))
                if m.group(3): set_df_value(df, "PE", to_float(m.group(3)))
                used_line_idx.add(i)
                continue
        for code, keys in aliases.items():
            if any(re.search(r"(^|\s)" + re.escape(k) + r"(\b|\s)", line, re.I) for k in keys):
                # tomar números de la línea; el primero suele ser el VALOR del estudio.
                nums = re.findall(r"-?\d+(?:[\.,]\d+)?", line)
                if nums:
                    set_df_value(df, code, to_float(nums[0]))
                    used_line_idx.add(i)
                    break


def parse_right_panel_text(text: str, df: pd.DataFrame) -> None:
    """Extrae variables del panel derecho: RR, PE, PPE, dZ/dt Imax, Z0, D/T."""
    clean = re.sub(r"[\t\r]+", " ", text or " ")
    clean = re.sub(r" +", " ", clean)
    patterns = {
        "RR": r"\bRR\s*(\d+(?:[\.,]\d+)?)",
        "PE": r"\bPE\s*(\d+(?:[\.,]\d+)?)",
        "PPE": r"\bPPE\s*(\d+(?:[\.,]\d+)?)",
        "DZDT_MAX": r"(?:dZ/dt|dz/dt|dZdt|dzdt)\s*(?:Imax|lmax|max)?\s*(\d+(?:[\.,]\d+)?)",
        "Z0": r"\bZ0\s*(\d+(?:[\.,]\d+)?)",
        "DIST_D": r"\bD\s*:\s*(\d+(?:[\.,]\d+)?)\s*cm",
        "DIST_T": r"\bT\s*:\s*(\d+(?:[\.,]\d+)?)\s*cm",
    }
    for code, pat in patterns.items():
        m = re.search(pat, clean, re.I)
        if m:
            set_df_value(df, code, to_float(m.group(1)))


def fallback_sequence_parse(text: str, df: pd.DataFrame) -> None:
    """Respaldo para PDFs que exportan los códigos juntos y los valores juntos.
    Busca una secuencia numérica compatible con la tabla principal del CGI.
    """
    # Evita texto del panel derecho para no mezclar RR/PE con tabla izquierda.
    clean = re.sub(r"[\t\r\n]+", " ", text or " ")
    clean = re.sub(r" +", " ", clean)
    # Ventana después de encabezados típicos y antes del panel/observaciones.
    m = re.search(r"(?:FC\s+PA\s+DS\s+IDS\s+VM\s+IC|PAR[ÁA]METRO\s+VALOR).*?(?:Observaciones|Sistema no invasivo|$)", clean, re.I)
    segment = m.group(0) if m else clean
    nums = [to_float(x) for x in re.findall(r"-?\d+(?:[\.,]\d+)?", segment)]
    nums = [x for x in nums if not pd.isna(x)]
    # En informes Exxer, los primeros valores clínicos suelen seguir este orden.
    # Se aplican filtros de plausibilidad para no cargar referencias de barras.
    candidates = {}
    for i in range(0, max(0, len(nums) - 12)):
        a = nums[i:i+14]
        # FC, PAS, PAD, PAM, DS, IDS, VM, IC, RVS, IRV, CA, IV, IAC, CTS
        if (35 <= a[0] <= 140 and 60 <= a[1] <= 240 and 30 <= a[2] <= 140 and
            40 <= a[3] <= 160 and 5 <= a[4] <= 200 and 2 <= a[5] <= 120 and
            0.5 <= a[6] <= 20 and 0.5 <= a[7] <= 10 and 200 <= a[8] <= 6000 and
            300 <= a[9] <= 9000):
            candidates = dict(zip(["FC","PAS","PAD","PAM","DS","IDS","VM","IC","RVS","IRV","CA","IV","IAC","CTS"], a[:14]))
            break
    for code, val in candidates.items():
        set_df_value(df, code, val, "extraído/secuencia/revisar")


def parse_report_text(text: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
    df = empty_variables_df()
    meta = {"medication": "", "observations": ""}
    if not text or not text.strip():
        return df, meta

    raw_lines = [l.strip() for l in text.splitlines() if l.strip()]
    # Prioriza líneas espaciales reconstruidas; son más confiables para la tabla.
    if "=== LINEAS_ESPACIALES_PDF ===" in text:
        spatial = text.split("=== LINEAS_ESPACIALES_PDF ===", 1)[1].split("=== TEXTO_PLANO_PDF ===", 1)[0]
        spatial_lines = [l.strip() for l in spatial.splitlines() if l.strip()]
    else:
        spatial_lines = raw_lines

    parse_variable_lines(spatial_lines, df)
    parse_right_panel_text(text, df)
    fallback_sequence_parse(text, df)

    joined = "\n".join(raw_lines)
    flat = re.sub(r"\s+", " ", joined)
    m = re.search(r"Medicaci[oó]n\s+(.+?)(?:Observaciones|Dist\.|D:\s*\d|$)", flat, re.I)
    if m:
        meta["medication"] = re.sub(r"\s+", " ", m.group(1)).strip()[:180]
    m = re.search(r"Observaciones\s+(.+?)(?:www\.|$)", flat, re.I)
    if m:
        meta["observations"] = re.sub(r"\s+", " ", m.group(1)).strip()[:500]

    # Si se cargaron valores, marcar como validado visualmente pendiente de revisión.
    # El operador puede editar antes de guardar.
    return df, meta

# ============================================================
# DIGITALIZACION OPCIONAL DE CURVAS
# ============================================================

def clamp_roi(r: Dict[str, int], w: int, h: int) -> Dict[str, int]:
    x0 = int(max(0, min(r["x0"], w - 3)))
    x1 = int(max(x0 + 3, min(r["x1"], w)))
    y0 = int(max(0, min(r["y0"], h - 3)))
    y1 = int(max(y0 + 3, min(r["y1"], h)))
    return {"x0": x0, "x1": x1, "y0": y0, "y1": y1}


def default_rois(w: int, h: int, preset: str) -> Dict[str, Dict[str, int]]:
    if preset == "panel_recortado":
        x0, x1 = int(w * 0.03), int(w * 0.70)
        return {
            "dzdt": {"x0": x0, "x1": x1, "y0": int(h * 0.035), "y1": int(h * 0.335)},
            "ecg":  {"x0": x0, "x1": x1, "y0": int(h * 0.365), "y1": int(h * 0.640)},
            "fono": {"x0": x0, "x1": x1, "y0": int(h * 0.680), "y1": int(h * 0.965)},
        }
    if preset == "tiras_inferiores":
        x0, x1 = int(w * 0.05), int(w * 0.94)
        return {
            "dzdt": {"x0": x0, "x1": x1, "y0": int(h * 0.84), "y1": int(h * 0.98)},
            "ecg":  {"x0": x0, "x1": x1, "y0": int(h * 0.74), "y1": int(h * 0.84)},
            "fono": {"x0": x0, "x1": x1, "y0": int(h * 0.60), "y1": int(h * 0.72)},
        }
    x0, x1 = int(w * 0.725), int(w * 0.890)
    return {
        "dzdt": {"x0": x0, "x1": x1, "y0": int(h * 0.135), "y1": int(h * 0.405)},
        "ecg":  {"x0": x0, "x1": x1, "y0": int(h * 0.440), "y1": int(h * 0.610)},
        "fono": {"x0": x0, "x1": x1, "y0": int(h * 0.605), "y1": int(h * 0.730)},
    }


def draw_rois(img: Image.Image, rois: dict) -> Image.Image:
    out = img.copy().convert("RGB")
    d = ImageDraw.Draw(out)
    colors = {"dzdt": "blue", "ecg": "green", "fono": "orange"}
    names = {"dzdt": "dZ/dt", "ecg": "ECG", "fono": "Fono"}
    for key, roi in rois.items():
        r = clamp_roi(roi, *out.size)
        d.rectangle([r["x0"], r["y0"], r["x1"], r["y1"]], outline=colors[key], width=max(3, out.size[0] // 400))
        d.text((r["x0"] + 5, r["y0"] + 5), names[key], fill=colors[key])
    return out


def smooth(y: np.ndarray, window: int = 7) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    if len(y) < 5:
        return y
    window = max(3, int(window))
    if window % 2 == 0:
        window += 1
    window = min(window, len(y) if len(y) % 2 else len(y) - 1)
    if window < 3:
        return y
    pad = window // 2
    yy = np.pad(y, (pad, pad), mode="edge")
    return np.convolve(yy, np.ones(window) / window, mode="valid")


def make_mask(rgb: np.ndarray) -> np.ndarray:
    r = rgb[:, :, 0].astype(int)
    g = rgb[:, :, 1].astype(int)
    b = rgb[:, :, 2].astype(int)
    blue = (b > 65) & (b > r + 5) & (b > g - 8) & (r < 180) & (g < 180)
    dark_blue = (b > 40) & (r < 125) & (g < 135) & (b >= r) & (b >= g - 15)
    dark = (r < 95) & (g < 95) & (b < 150)
    return blue | dark_blue | dark


def digitize_signal(img: Image.Image, roi: Dict[str, int], smooth_frac: float = 0.012) -> pd.DataFrame:
    rgb = np.asarray(img.convert("RGB"))
    w, h = img.size
    r = clamp_roi(roi, w, h)
    crop = rgb[r["y0"]:r["y1"], r["x0"]:r["x1"], :]
    ch, cw = crop.shape[:2]
    if ch < 5 or cw < 5:
        return pd.DataFrame(columns=["x", "y_pixel", "y_norm"])
    mask = make_mask(crop)
    # evita bordes del recorte
    m = max(2, int(ch * 0.04))
    mask[:m, :] = False
    mask[-m:, :] = False
    mask[:, :2] = False
    mask[:, -2:] = False
    xs, ys = [], []
    max_dense = max(4, int(ch * 0.36))
    for cx in range(cw):
        rows = np.where(mask[:, cx])[0]
        if len(rows) == 0 or len(rows) > max_dense:
            continue
        # cluster más delgado: mediana de los píxeles más oscuros/azules del trazo
        xs.append(float(r["x0"] + cx))
        ys.append(float(r["y0"] + np.median(rows)))
    if len(xs) < 10:
        return pd.DataFrame(columns=["x", "y_pixel", "y_norm"])
    df = pd.DataFrame({"x": xs, "y_pixel": ys}).groupby("x", as_index=False)["y_pixel"].median()
    y = df["y_pixel"].to_numpy(float)
    y_s = smooth(y, max(3, int(len(y) * smooth_frac)))
    # normalización sin truncar picos
    amp = r["y1"] - y_s
    amin, amax = float(np.nanmin(amp)), float(np.nanmax(amp))
    yn = (amp - amin) / (amax - amin) if amax > amin else np.zeros_like(amp)
    df["y_smooth"] = y_s
    df["y_norm"] = yn
    return df


def interp_y(df: pd.DataFrame, x_value: float) -> float:
    if df.empty:
        return 0.5
    idx = int(np.nanargmin(np.abs(df["x"].to_numpy(float) - float(x_value))))
    return float(df.iloc[idx]["y_norm"])


def detect_auto(ecg: pd.DataFrame, dzdt: pd.DataFrame, fono: pd.DataFrame, x0: float, x1: float, fono_line: float) -> Tuple[dict, dict]:
    auto = {c: {"x": float((x0 + x1) / 2), "y": 0.5} for c in CURSORS}
    guide = {"qrs_peak": np.nan, "s1": np.nan, "s2": np.nan, "fono_line": float(fono_line)}
    if not ecg.empty:
        x = ecg["x"].to_numpy(float)
        y = smooth(ecg["y_norm"].to_numpy(float), max(5, int(len(ecg) * 0.02)))
        peak = int(np.argmax(y))
        guide["qrs_peak"] = float(x[peak])
        base = np.nanmedian(y[:max(5, peak)]) if peak > 5 else np.nanmedian(y)
        thr = base + 0.20 * (float(np.nanmax(y)) - base)
        start = peak
        for i in range(peak, 0, -1):
            if y[i] <= thr:
                start = i
                break
        auto["QRS"] = {"x": float(x[start]), "y": interp_y(ecg, float(x[start]))}
    if not dzdt.empty:
        x = dzdt["x"].to_numpy(float)
        y = smooth(dzdt["y_norm"].to_numpy(float), max(5, int(len(dzdt) * 0.02)))
        cidx = int(np.argmax(y))
        left0 = 0
        if np.isfinite(auto["QRS"]["x"]):
            left0 = max(0, min(cidx - 3, int(np.searchsorted(x, auto["QRS"]["x"]))))
        seg = y[left0:max(left0 + 3, cidx)]
        bidx = left0 + int(np.argmax(np.gradient(seg))) if len(seg) >= 4 else max(0, cidx - int(len(x) * 0.06))
        post = y[cidx + 1:]
        xidx = cidx + 1 + int(np.argmin(post)) if len(post) >= 4 else min(len(y) - 1, cidx + int(len(y) * 0.15))
        post2 = y[xidx + 1:]
        yidx = xidx + 1 + int(np.argmax(post2)) if len(post2) >= 4 else min(len(y) - 1, xidx + int(len(y) * 0.08))
        for name, idx in {"B": bidx, "C": cidx, "X": xidx, "Y": yidx}.items():
            auto[name] = {"x": float(x[idx]), "y": interp_y(dzdt, float(x[idx]))}
    if not fono.empty:
        x = fono["x"].to_numpy(float)
        y = smooth(fono["y_norm"].to_numpy(float), max(5, int(len(fono) * 0.02)))
        above = y >= fono_line
        groups, start = [], None
        for i, v in enumerate(above):
            if v and start is None:
                start = i
            if start is not None and ((not v) or i == len(above) - 1):
                end = i if not v else i + 1
                if end - start >= 3:
                    groups.append((start, end))
                start = None
        centers = [float(np.nanmean(x[a:b])) for a, b in groups]
        if len(centers) > 0:
            guide["s1"] = centers[0]
        if len(centers) > 1:
            guide["s2"] = centers[1]
    return auto, guide


def plot_curves(dzdt: pd.DataFrame, ecg: pd.DataFrame, fono: pd.DataFrame, auto: dict, manual: dict, guide: dict, x0: float, x1: float) -> bytes:
    fig, ax = plt.subplots(figsize=(14.5, 5.8))
    for df, offset, label, lw in [(dzdt, 2.4, "dZ/dt / impedancia", 2.2), (ecg, 1.2, "ECG", 1.8), (fono, 0.0, "Fonocardiograma", 1.8)]:
        if not df.empty:
            x = df["x"].to_numpy(float)
            y = df["y_norm"].to_numpy(float) + offset
            ax.plot(x, y, linewidth=lw, label=label)
    ax.hlines(float(guide.get("fono_line", 0.55)), x0, x1, linestyles="--", linewidth=1.2, label="Línea horizontal fono")
    for k, lab in [("qrs_peak", "QRS pico"), ("s1", "S1"), ("s2", "S2")]:
        v = guide.get(k, np.nan)
        if np.isfinite(v):
            ax.axvline(float(v), linestyle="-.", linewidth=1.1)
            ax.text(float(v), 3.45 if k == "qrs_peak" else 0.88, lab, rotation=90, ha="center", va="bottom", fontsize=8)
    for c in CURSORS:
        ax.axvline(float(auto[c]["x"]), linestyle=":", linewidth=1.1)
        ax.axvline(float(manual[c]["x"]), linestyle="--", linewidth=2.0)
        ax.text(float(manual[c]["x"]), 2.10 if c in ["B", "C", "X", "Y"] else 1.05, c, rotation=90, ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_title("Corrección opcional integrada de cursores")
    ax.set_xlim(x0, x1)
    ax.set_ylim(-0.15, 3.75)
    ax.set_yticks([0.5, 1.7, 2.9])
    ax.set_yticklabels(["Fono", "ECG", "dZ/dt"])
    ax.grid(True, alpha=0.22)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    bio = io.BytesIO()
    fig.savefig(bio, format="png", dpi=170, bbox_inches="tight")
    plt.close(fig)
    bio.seek(0)
    return bio.getvalue()

# ============================================================
# GUARDADO / EXPORTACION
# ============================================================

def save_image(img: Image.Image, prefix: str) -> str:
    fname = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path = FILES_DIR / fname
    img.save(path)
    return str(path)


def variables_df_to_json(df: pd.DataFrame) -> str:
    out = df.copy()
    out["valor"] = pd.to_numeric(out["valor"], errors="coerce")
    return out.to_json(orient="records", force_ascii=False)


def save_study(user: dict, patient_code: str, key_basis: str, study_date: str, condition: str, source: str, page: int,
               medication: str, observations: str, text: str, variables_df: pd.DataFrame, img: Image.Image, notes: str) -> int:
    image_path = save_image(img, patient_code)
    con = connect()
    cur = con.execute(
        """
        INSERT INTO studies(created_at,user_id,username,patient_code,patient_key_basis,study_date,condition_label,source_name,page_number,medication,observations,extracted_text,variables_json,image_path,notes)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (now_iso(), user["id"], user["username"], patient_code, key_basis, study_date, condition, source, page, medication, observations, text, variables_df_to_json(variables_df), image_path, notes),
    )
    con.commit()
    sid = int(cur.lastrowid)
    con.close()
    return sid


def save_cursor_correction(user: dict, study_id, patient_code: str, source: str, page: int, rois: dict, auto: dict, manual: dict, guide: dict, metrics: dict, conclusion: str) -> int:
    con = connect()
    cur = con.execute(
        """
        INSERT INTO cursor_corrections(created_at,study_id,user_id,username,patient_code,source_name,page_number,rois_json,auto_json,manual_json,guide_json,metrics_json,conclusion)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (now_iso(), study_id, user["id"], user["username"], patient_code, source, page, json.dumps(rois, ensure_ascii=False), json.dumps(auto, ensure_ascii=False), json.dumps(manual, ensure_ascii=False), json.dumps(guide, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False), conclusion),
    )
    con.commit()
    cid = int(cur.lastrowid)
    con.close()
    return cid


def studies_df(scope_user: dict | None = None) -> pd.DataFrame:
    con = connect()
    q = "SELECT id,created_at,username,patient_code,study_date,condition_label,source_name,page_number,medication,observations,variables_json,notes FROM studies"
    params = []
    if scope_user is not None and scope_user.get("role") != "admin":
        q += " WHERE user_id=?"
        params.append(scope_user["id"])
    q += " ORDER BY created_at DESC"
    df = pd.read_sql_query(q, con, params=params)
    con.close()
    return df


def cursor_df(scope_user: dict | None = None) -> pd.DataFrame:
    con = connect()
    q = "SELECT * FROM cursor_corrections"
    params = []
    if scope_user is not None and scope_user.get("role") != "admin":
        q += " WHERE user_id=?"
        params.append(scope_user["id"])
    q += " ORDER BY created_at DESC"
    df = pd.read_sql_query(q, con, params=params)
    con.close()
    return df


def export_excel(scope_user: dict, only_current_user: bool = True) -> bytes:
    user_filter = scope_user if only_current_user else None
    df = studies_df(user_filter)
    cdf = cursor_df(user_filter)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        if df.empty:
            pd.DataFrame().to_excel(writer, index=False, sheet_name="estudios")
            pd.DataFrame().to_excel(writer, index=False, sheet_name="variables_largo")
            pd.DataFrame().to_excel(writer, index=False, sheet_name="variables_ancho")
        else:
            base = df.drop(columns=["variables_json"], errors="ignore")
            base.to_excel(writer, index=False, sheet_name="estudios")
            long_rows = []
            wide_rows = []
            for _, r in df.iterrows():
                vars_df = pd.read_json(io.StringIO(r["variables_json"])) if r.get("variables_json") else pd.DataFrame()
                if not vars_df.empty:
                    v2 = vars_df.copy()
                    v2.insert(0, "study_id", r["id"])
                    v2.insert(1, "created_at", r["created_at"])
                    v2.insert(2, "username", r["username"])
                    v2.insert(3, "patient_code", r["patient_code"])
                    v2.insert(4, "condition_label", r["condition_label"])
                    long_rows.append(v2)
                    wide = {"study_id": r["id"], "created_at": r["created_at"], "username": r["username"], "patient_code": r["patient_code"], "study_date": r["study_date"], "condition_label": r["condition_label"]}
                    for _, vv in vars_df.iterrows():
                        wide[str(vv["codigo"])] = vv.get("valor", np.nan)
                    wide_rows.append(wide)
            pd.concat(long_rows, ignore_index=True).to_excel(writer, index=False, sheet_name="variables_largo") if long_rows else pd.DataFrame().to_excel(writer, index=False, sheet_name="variables_largo")
            pd.DataFrame(wide_rows).to_excel(writer, index=False, sheet_name="variables_ancho")
        if cdf.empty:
            pd.DataFrame().to_excel(writer, index=False, sheet_name="cursores")
        else:
            rows = []
            for _, r in cdf.iterrows():
                try:
                    auto = json.loads(r["auto_json"] or "{}")
                    manual = json.loads(r["manual_json"] or "{}")
                    for cur in CURSORS:
                        rows.append({
                            "correction_id": r["id"], "study_id": r["study_id"], "created_at": r["created_at"], "username": r["username"], "patient_code": r["patient_code"],
                            "cursor": cur, "auto_x": auto.get(cur, {}).get("x", np.nan), "manual_x": manual.get(cur, {}).get("x", np.nan),
                            "delta_x": manual.get(cur, {}).get("x", np.nan) - auto.get(cur, {}).get("x", np.nan) if cur in manual and cur in auto else np.nan,
                        })
                except Exception:
                    pass
            pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="cursores")
    bio.seek(0)
    return bio.getvalue()

# ============================================================
# INTERFAZ
# ============================================================

def app_main() -> None:
    css()
    init_db()
    if "user" not in st.session_state:
        login_ui()
        return
    user = st.session_state.user
    st.markdown(f"<div class='hero'><h1>{APP_TITLE}</h1><p>{APP_SUBTITLE}</p><div class='dev'>{APP_DEVELOPER}</div></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        st.markdown(f"<div class='ok'><b>Operador:</b> {user['username']} | <b>Rol:</b> {user['role']} | <b>Objetivo:</b> repositorio anónimo para concordancia interusuario.</div>", unsafe_allow_html=True)
    with c2:
        if st.button("Cerrar sesión"):
            st.session_state.pop("user", None)
            st.rerun()
    with c3:
        st.caption("Los nombres de pacientes no se exportan.")

    tab1, tab2, tab3, tab4 = st.tabs(["1. Cargar informe completo", "2. Corrección opcional de curvas", "3. Mis Excel", "4. Administración"])

    with tab1:
        st.markdown("<div class='guide'><b>Función principal:</b> cargar la hoja completa del informe, extraer/corregir variables y guardar un registro anónimo en Excel. La corrección de curvas queda como módulo opcional aparte.</div>", unsafe_allow_html=True)
        uploaded = st.file_uploader("Subir PDF o imagen del informe completo", type=["pdf", "png", "jpg", "jpeg"], key="main_upload")
        if uploaded is not None:
            try:
                img, page, source, text = open_uploaded(uploaded)
                st.image(img, caption="Hoja completa renderizada", use_container_width=True)
                parsed_df, meta = parse_report_text(text)
                with st.expander("Texto extraído del PDF", expanded=False):
                    if text.strip():
                        st.text_area("Texto", value=text, height=220)
                    else:
                        st.warning("No se extrajo texto. Si el PDF es imagen escaneada, cargue/corrija las variables manualmente.")
                st.subheader("Datos para anonimización")
                a1, a2, a3, a4 = st.columns(4)
                with a1:
                    patient_name = st.text_input("Nombre del paciente — no se exporta", key="pname")
                with a2:
                    dni = st.text_input("DNI/Documento — no se exporta", key="pdni")
                with a3:
                    birthdate = st.text_input("Fecha nacimiento — no se exporta", placeholder="dd/mm/aaaa", key="pbirth")
                with a4:
                    study_date = st.date_input("Fecha del estudio", value=date.today(), key="pstudydate")
                patient_code, key_basis = patient_code_from(patient_name, dni, birthdate, str(study_date))
                st.success(f"Código anónimo generado: {patient_code}")

                b1, b2, b3 = st.columns(3)
                with b1:
                    condition = st.selectbox("Condición del estudio", ["Basal / acostado / cinta", "Parado", "Spot", "Otro"], key="condition")
                with b2:
                    medication = st.text_input("Medicación", value=meta.get("medication", ""), key="med")
                with b3:
                    notes = st.text_input("Notas internas del operador", key="notes")
                observations = st.text_area("Observaciones del informe", value=meta.get("observations", ""), height=80, key="obs")

                st.subheader("Variables del informe")
                st.caption("Revise y corrija los valores. Los campos exportados usan el código anónimo del paciente y el usuario operador.")
                edited = st.data_editor(
                    parsed_df,
                    num_rows="fixed",
                    use_container_width=True,
                    column_config={
                        "codigo": st.column_config.TextColumn("Variable", disabled=True),
                        "nombre": st.column_config.TextColumn("Nombre", disabled=True),
                        "unidad": st.column_config.TextColumn("Unidad", disabled=True),
                        "valor": st.column_config.NumberColumn("Valor corregido", format="%.3f"),
                        "estado": st.column_config.SelectboxColumn("Estado", options=["extraído/revisar", "manual/revisar", "validado"]),
                    },
                    key="vars_editor",
                )
                if st.button("Guardar informe completo en repositorio", type="primary"):
                    sid = save_study(user, patient_code, key_basis, str(study_date), condition, source, int(page), medication, observations, text, edited, img, notes)
                    st.session_state.last_study_id = sid
                    st.session_state.last_patient_code = patient_code
                    st.session_state.last_image = img
                    st.session_state.last_source = source
                    st.session_state.last_page = int(page)
                    st.success(f"Informe guardado con ID {sid}. Código anónimo: {patient_code}")
            except Exception as exc:
                st.error(f"No se pudo procesar el informe: {exc}")
                st.exception(exc)
        else:
            st.info("Suba un PDF o imagen de informe completo para comenzar.")

    with tab2:
        st.markdown("<div class='guide'><b>Módulo opcional:</b> corrección de curvas y cursores QRS, B, C, X e Y. Use este módulo solo cuando quiera validar morfología o entrenar operadores.</div>", unsafe_allow_html=True)
        uploaded2 = st.file_uploader("Subir PDF/imagen para corrección opcional de curvas", type=["pdf", "png", "jpg", "jpeg"], key="curves_upload")
        use_last = False
        if "last_image" in st.session_state:
            use_last = st.checkbox("Usar la última hoja cargada en el módulo principal", value=True)
        if uploaded2 is not None or use_last:
            try:
                if use_last:
                    img2 = st.session_state.last_image
                    page2 = st.session_state.get("last_page", 1)
                    source2 = st.session_state.get("last_source", "última hoja")
                    patient_code2 = st.session_state.get("last_patient_code", "SIN-CODIGO")
                    study_id2 = st.session_state.get("last_study_id", None)
                else:
                    img2, page2, source2, _ = open_uploaded(uploaded2)
                    patient_code2 = st.text_input("Código anónimo para vincular corrección", value="SIN-CODIGO")
                    study_id2 = None
                preset_label = st.selectbox("Área inicial de digitalización", ["Panel derecho del informe completo", "Imagen ya recortada del panel derecho", "Tiras largas inferiores"], key="preset_curves")
                preset = {"Panel derecho del informe completo": "panel_derecho", "Imagen ya recortada del panel derecho": "panel_recortado", "Tiras largas inferiores": "tiras_inferiores"}[preset_label]
                w, h = img2.size
                base_rois = default_rois(w, h, preset)
                with st.expander("Ajustar recortes", expanded=True):
                    rois = {}
                    names = {"dzdt": "dZ/dt / impedancia", "ecg": "ECG", "fono": "Fonocardiograma"}
                    for key in SIGNALS:
                        st.markdown(f"**{names[key]}**")
                        b = base_rois[key]
                        c1, c2, c3, c4 = st.columns(4)
                        with c1:
                            x0 = st.slider(f"X mín {key}", 0, max(1, w - 3), b["x0"], key=f"{key}_x0")
                        with c2:
                            x1 = st.slider(f"X máx {key}", x0 + 3, w, max(x0 + 3, b["x1"]), key=f"{key}_x1")
                        with c3:
                            y0 = st.slider(f"Y mín {key}", 0, max(1, h - 3), b["y0"], key=f"{key}_y0")
                        with c4:
                            y1 = st.slider(f"Y máx {key}", y0 + 3, h, max(y0 + 3, b["y1"]), key=f"{key}_y1")
                        rois[key] = clamp_roi({"x0": x0, "x1": x1, "y0": y0, "y1": y1}, w, h)
                st.image(draw_rois(img2, rois), caption="Recortes sobre la hoja", use_container_width=True)
                fono_line = st.slider("Línea horizontal fono", 0.10, 0.95, 0.55, 0.01)
                dzdt = digitize_signal(img2, rois["dzdt"])
                ecg = digitize_signal(img2, rois["ecg"])
                fono = digitize_signal(img2, rois["fono"])
                st.write({"puntos_dzdt": len(dzdt), "puntos_ecg": len(ecg), "puntos_fono": len(fono)})
                if dzdt.empty:
                    st.error("No se detectó dZ/dt. Ajuste el recorte azul para que contenga solamente la curva.")
                else:
                    x0c = max(rois[k]["x0"] for k in SIGNALS)
                    x1c = min(rois[k]["x1"] for k in SIGNALS)
                    auto, guide = detect_auto(ecg, dzdt, fono, x0c, x1c, fono_line)
                    manual = {}
                    cols = st.columns(5)
                    for i, cur in enumerate(CURSORS):
                        with cols[i]:
                            val = st.slider(f"Cursor {cur}", int(x0c), int(x1c), int(min(max(auto[cur]["x"], x0c), x1c)), key=f"cursor_{cur}")
                            target_df = ecg if cur == "QRS" else dzdt
                            manual[cur] = {"x": float(val), "y": interp_y(target_df, float(val))}
                    chart = plot_curves(dzdt, ecg, fono, auto, manual, guide, x0c, x1c)
                    st.image(chart, caption="Curvas digitalizadas: arriba dZ/dt, medio ECG, abajo fonocardiograma", use_container_width=True)
                    deltas = [abs(manual[c]["x"] - auto[c]["x"]) for c in CURSORS]
                    metrics = {"error_medio_px": float(np.mean(deltas)), "puntos_dzdt": len(dzdt), "puntos_ecg": len(ecg), "puntos_fono": len(fono)}
                    conclusion = "Corrección opcional de cursores realizada con dZ/dt arriba, ECG medio y fonocardiograma abajo."
                    if st.button("Guardar corrección opcional de cursores", type="primary"):
                        cid = save_cursor_correction(user, study_id2, patient_code2, source2, int(page2), rois, auto, manual, guide, metrics, conclusion)
                        st.success(f"Corrección guardada con ID {cid}.")
            except Exception as exc:
                st.error(f"Falló el módulo opcional de curvas: {exc}")
                st.exception(exc)
        else:
            st.info("Suba un archivo o use la última hoja cargada en el módulo principal.")

    with tab3:
        st.subheader("Mis estudios guardados")
        df = studies_df(user)
        st.dataframe(df.drop(columns=["variables_json"], errors="ignore"), use_container_width=True)
        st.download_button("Descargar mi Excel", data=export_excel(user, only_current_user=True), file_name=f"cgi_excel_{user['username']}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with tab4:
        if user.get("role") != "admin":
            st.warning("Solo el administrador puede acceder a todos los registros.")
        else:
            st.subheader("Administrador: todos los usuarios y todos los Excel")
            con = connect()
            udf = pd.read_sql_query("SELECT id,username,full_name,matricula,provincia,role,active,created_at FROM users ORDER BY created_at DESC", con)
            con.close()
            st.dataframe(udf, use_container_width=True)
            all_df = studies_df(None)
            st.dataframe(all_df.drop(columns=["variables_json"], errors="ignore"), use_container_width=True)
            st.download_button("Descargar Excel administrador completo", data=export_excel(user, only_current_user=False), file_name="cgi_excel_administrador_todos_los_usuarios.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    try:
        app_main()
    except Exception as exc:
        css()
        st.error("La aplicación encontró un error controlado.")
        st.code(traceback.format_exc())
