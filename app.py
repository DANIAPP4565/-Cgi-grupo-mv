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

# ============================================================
# COMPATIBILIDAD STREAMLIT + streamlit-drawable-canvas
# ============================================================
# streamlit-drawable-canvas 0.9.3 usa internamente image_to_url con
# firmas antiguas de Streamlit. En Streamlit reciente esa función fue
# movida/modificada y puede fallar con:
# - module 'streamlit.elements.image' has no attribute 'image_to_url'
# - AttributeError: 'int' object has no attribute 'width'
# Este parche crea un puente compatible antes de importar st_canvas.

def _install_canvas_image_to_url_patch() -> None:
    try:
        import inspect
        from types import SimpleNamespace
        import streamlit.elements.image as _st_image

        _image_utils = None
        try:
            from streamlit.elements.lib import image_utils as _image_utils  # Streamlit nuevo
        except Exception:
            _image_utils = None

        # Fuente real de la función. En versiones viejas vive en elements.image;
        # en versiones nuevas vive en elements.lib.image_utils.
        _original_image_to_url = getattr(_st_image, "image_to_url", None)
        if _original_image_to_url is None and _image_utils is not None:
            _original_image_to_url = getattr(_image_utils, "image_to_url", None)
        if _original_image_to_url is None:
            return

        def _layout_config_from_width(width):
            """Convierte el ancho entero antiguo en layout_config.width."""
            if hasattr(width, "width"):
                return width
            try:
                layout_width = int(width) if isinstance(width, (int, float)) else "content"
            except Exception:
                layout_width = "content"
            try:
                from streamlit.elements.lib.layout_utils import LayoutConfig
                try:
                    return LayoutConfig(width=layout_width)
                except Exception:
                    pass
            except Exception:
                pass
            # Respaldo: image_utils suele necesitar, como mínimo, atributo .width.
            return SimpleNamespace(width=layout_width, height=None)

        def _compat_image_to_url(
            image,
            width=None,
            clamp=False,
            channels="RGB",
            output_format="auto",
            image_id="drawable-canvas-bg",
            *args,
            **kwargs,
        ):
            """Acepta firma antigua y firma nueva de Streamlit."""
            # Caso firma nueva: image_to_url(image, layout_config=..., ...)
            layout_config = kwargs.pop("layout_config", None)
            if layout_config is None:
                layout_config = _layout_config_from_width(width)
            elif isinstance(layout_config, (int, float, str)):
                layout_config = _layout_config_from_width(layout_config)

            try:
                sig = inspect.signature(_original_image_to_url)
                expects_layout = "layout_config" in sig.parameters
            except Exception:
                expects_layout = True

            if expects_layout:
                try:
                    return _original_image_to_url(
                        image,
                        layout_config=layout_config,
                        clamp=bool(clamp),
                        channels=channels,
                        output_format=output_format,
                        image_id=str(image_id),
                    )
                except TypeError:
                    # Algunas versiones no aceptan keywords aunque usen layout_config.
                    return _original_image_to_url(image, layout_config, bool(clamp), channels, output_format, str(image_id))
            else:
                # Firma vieja: image_to_url(image, width, clamp, channels, output_format, image_id)
                try:
                    raw_width = getattr(layout_config, "width", width)
                    if raw_width == "content":
                        raw_width = width
                    return _original_image_to_url(image, raw_width, bool(clamp), channels, output_format, str(image_id))
                except AttributeError:
                    raw_width = getattr(layout_config, "width", None) or width
                    return _original_image_to_url(image, raw_width, bool(clamp), channels, output_format, str(image_id))

        # Parchear ambos lugares, porque el componente archivado puede importar
        # desde streamlit.elements.image y Streamlit nuevo desde image_utils.
        _st_image.image_to_url = _compat_image_to_url
        if _image_utils is not None:
            try:
                _image_utils.image_to_url = _compat_image_to_url
            except Exception:
                pass
    except Exception:
        pass

try:
    _install_canvas_image_to_url_patch()
    from streamlit_drawable_canvas import st_canvas
except Exception:
    st_canvas = None

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
ADMIN_USER_DEFAULT = os.getenv("CGI_ADMIN_USER", "olan")
CURSORS = ["QRS", "B", "C", "X", "Y"]
SIGNALS = ["dzdt", "ecg", "fono"]
CURSOR_COLORS = {"QRS": "#DC2626", "B": "#2563EB", "C": "#16A34A", "X": "#EA580C", "Y": "#7C3AED"}

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


def get_secret_value(name: str, default: str = "") -> str:
    """Lee credenciales desde variables de entorno o Streamlit Secrets.
    No imprime ni muestra claves en pantalla.
    """
    val = os.getenv(name, "")
    if val:
        return str(val)
    try:
        val = st.secrets.get(name, "")
        if val:
            return str(val)
    except Exception:
        pass
    return default


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
    admin_user = get_secret_value("CGI_ADMIN_USER", ADMIN_USER_DEFAULT)
    admin_pass = get_secret_value("CGI_ADMIN_PASS", "")
    if n == 0 and admin_pass:
        cur.execute(
            "INSERT INTO users(username,password_hash,full_name,matricula,provincia,role,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (admin_user, hash_password(admin_pass), "Administrador", "", "", "admin", 1, now_iso()),
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
            st.markdown(
                "<div class='guide'><b>Acceso de administrador.</b> "
                "Las credenciales se configuran en Secrets o variables de entorno y nunca se muestran en pantalla.</div>",
                unsafe_allow_html=True,
            )
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


def words_json_from_page(page) -> str:
    """Guarda palabras con coordenadas para extracción por filas/columnas.
    Esto es clave en informes Exxer, donde el texto plano puede salir desordenado.
    """
    try:
        words = page.get_text("words") or []
    except Exception:
        words = []
    out = []
    for w in words:
        try:
            x0, y0, x1, y1, txt = w[:5]
            if str(txt).strip():
                out.append({"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "text": str(txt)})
        except Exception:
            continue
    return json.dumps(out, ensure_ascii=False)


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
        words_json = words_json_from_page(p)
        # Primero coordenadas, luego líneas espaciales y texto plano como respaldo.
        text = (
            "=== WORDS_JSON_PDF ===\n" + words_json +
            "\n=== LINEAS_ESPACIALES_PDF ===\n" + spatial_text +
            "\n=== TEXTO_PLANO_PDF ===\n" + plain_text
        ).strip()
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
    # Identificación fisiológica general
    {"codigo": "FC", "nombre": "Frecuencia cardíaca", "unidad": "pulsos/min", "categoria": "General / ritmo"},
    {"codigo": "RR", "nombre": "Intervalo RR", "unidad": "ms", "categoria": "General / ritmo"},
    {"codigo": "PAS", "nombre": "Presión arterial sistólica", "unidad": "mmHg", "categoria": "Presión arterial"},
    {"codigo": "PAD", "nombre": "Presión arterial diastólica", "unidad": "mmHg", "categoria": "Presión arterial"},
    {"codigo": "PAM", "nombre": "Presión arterial media", "unidad": "mmHg", "categoria": "Presión arterial"},
    {"codigo": "PP", "nombre": "Presión de pulso", "unidad": "mmHg", "categoria": "Presión arterial"},

    # Dinámica de fluidos / flujo
    {"codigo": "DS", "nombre": "Descarga sistólica / volumen sistólico", "unidad": "ml/pulso", "categoria": "Dinámica de fluidos"},
    {"codigo": "IDS", "nombre": "Índice de descarga sistólica", "unidad": "ml/pulso/m2", "categoria": "Dinámica de fluidos"},
    {"codigo": "VM", "nombre": "Volumen minuto / gasto cardíaco", "unidad": "L/min", "categoria": "Dinámica de fluidos"},
    {"codigo": "IC", "nombre": "Índice cardíaco", "unidad": "L/min/m2", "categoria": "Dinámica de fluidos"},
    {"codigo": "ITC", "nombre": "Índice de trabajo cardíaco", "unidad": "kg.m/m2", "categoria": "Dinámica de fluidos"},
    {"codigo": "ITS", "nombre": "Índice de trabajo sistólico", "unidad": "g.m/m2/lat", "categoria": "Dinámica de fluidos"},
    {"codigo": "LCW", "nombre": "Trabajo cardíaco izquierdo", "unidad": "kg.m", "categoria": "Dinámica de fluidos"},
    {"codigo": "LCWI", "nombre": "Índice de trabajo cardíaco izquierdo", "unidad": "kg.m/m2", "categoria": "Dinámica de fluidos"},
    {"codigo": "LVSW", "nombre": "Trabajo sistólico ventricular izquierdo", "unidad": "g.m", "categoria": "Dinámica de fluidos"},
    {"codigo": "LVSWI", "nombre": "Índice de trabajo sistólico ventricular izquierdo", "unidad": "g.m/m2", "categoria": "Dinámica de fluidos"},

    # Postcarga / vascular
    {"codigo": "RVS", "nombre": "Resistencia vascular sistémica", "unidad": "dyn.s.cm-5", "categoria": "Postcarga / vascular"},
    {"codigo": "IRV", "nombre": "Índice de resistencia vascular", "unidad": "dyn.s.cm-5.m2", "categoria": "Postcarga / vascular"},
    {"codigo": "CA", "nombre": "Complacencia arterial", "unidad": "ml/mmHg", "categoria": "Postcarga / vascular"},
    {"codigo": "EA", "nombre": "Elastancia arterial efectiva", "unidad": "mmHg/ml", "categoria": "Postcarga / vascular"},
    {"codigo": "EAI", "nombre": "Elastancia arterial efectiva indexada", "unidad": "mmHg/ml/m2", "categoria": "Postcarga / vascular"},
    {"codigo": "TFC", "nombre": "Contenido de fluidos torácicos", "unidad": "kohms(-1)", "categoria": "Volemia / fluidos"},
    {"codigo": "CFT", "nombre": "Contenido de fluidos torácicos", "unidad": "kohms(-1)", "categoria": "Volemia / fluidos"},
    {"codigo": "TFCNR", "nombre": "Contenido de fluidos torácicos normalizado", "unidad": "adimensional", "categoria": "Volemia / fluidos"},
    {"codigo": "CFTNR", "nombre": "Contenido de fluidos torácicos normalizado", "unidad": "adimensional", "categoria": "Volemia / fluidos"},

    # Contractilidad / función sistólica
    {"codigo": "IV", "nombre": "Índice de velocidad", "unidad": "/1000/seg", "categoria": "Contractilidad"},
    {"codigo": "IAC", "nombre": "Índice de aceleración cardíaca", "unidad": "/100/seg2", "categoria": "Contractilidad"},
    {"codigo": "ACI", "nombre": "Índice de aceleración cardíaca", "unidad": "/100/seg2", "categoria": "Contractilidad"},
    {"codigo": "IH", "nombre": "Índice Heather", "unidad": "ohm/s2", "categoria": "Contractilidad"},
    {"codigo": "HI", "nombre": "Heather index", "unidad": "ohm/s2", "categoria": "Contractilidad"},
    {"codigo": "EES", "nombre": "Elastancia ventricular de fin de sístole", "unidad": "mmHg/ml", "categoria": "Contractilidad"},
    {"codigo": "EESI", "nombre": "Elastancia ventricular indexada", "unidad": "mmHg/ml/m2", "categoria": "Contractilidad"},
    {"codigo": "FE_CAPAN", "nombre": "Fracción de eyección estimada por Capan", "unidad": "%", "categoria": "Función cardíaca"},
    {"codigo": "FE", "nombre": "Fracción de eyección", "unidad": "%", "categoria": "Función cardíaca"},

    # Tiempos sistólicos y morfología CGI
    {"codigo": "CTS", "nombre": "Cociente de tiempo sistólico", "unidad": "%", "categoria": "Tiempos sistólicos"},
    {"codigo": "CTE", "nombre": "Cociente de tiempo eyectivo", "unidad": "%", "categoria": "Tiempos sistólicos"},
    {"codigo": "PE", "nombre": "Período expulsivo", "unidad": "ms", "categoria": "Tiempos sistólicos"},
    {"codigo": "PPE", "nombre": "Preperíodo expulsivo", "unidad": "ms", "categoria": "Tiempos sistólicos"},
    {"codigo": "LVET", "nombre": "Tiempo de eyección ventricular izquierda", "unidad": "ms", "categoria": "Tiempos sistólicos"},
    {"codigo": "PEP", "nombre": "Pre-ejection period", "unidad": "ms", "categoria": "Tiempos sistólicos"},

    # Señal de impedancia y equipo
    {"codigo": "DZDT_MAX", "nombre": "dZ/dt máximo", "unidad": "ohm/seg", "categoria": "Señal de impedancia"},
    {"codigo": "DZDT_MIN", "nombre": "dZ/dt mínimo", "unidad": "ohm/seg", "categoria": "Señal de impedancia"},
    {"codigo": "D2ZDT2_MAX", "nombre": "d2Z/dt2 máximo", "unidad": "ohm/seg2", "categoria": "Señal de impedancia"},
    {"codigo": "Z0", "nombre": "Impedancia basal Z0", "unidad": "ohm", "categoria": "Señal de impedancia"},
    {"codigo": "DIST_D", "nombre": "Distancia electrodos D", "unidad": "cm", "categoria": "Equipo / técnica"},
    {"codigo": "DIST_T", "nombre": "Distancia electrodos T", "unidad": "cm", "categoria": "Equipo / técnica"},

    # Acoplamiento ventriculoarterial y métricas derivadas
    {"codigo": "AC", "nombre": "Acoplamiento ventriculoarterial Ea/Ees", "unidad": "relación", "categoria": "Acoplamiento"},
    {"codigo": "EA_EES", "nombre": "Relación Ea/Ees", "unidad": "relación", "categoria": "Acoplamiento"},

    # Antropometría útil para indexación / auditoría
    {"codigo": "PESO", "nombre": "Peso", "unidad": "kg", "categoria": "Antropometría"},
    {"codigo": "TALLA", "nombre": "Talla", "unidad": "cm", "categoria": "Antropometría"},
    {"codigo": "IMC", "nombre": "Índice de masa corporal", "unidad": "kg/m2", "categoria": "Antropometría"},
    {"codigo": "BSA", "nombre": "Superficie corporal", "unidad": "m2", "categoria": "Antropometría"},
]

VARIABLE_ORDER = [v["codigo"] for v in VARIABLES]
VARIABLE_DICT = {v["codigo"]: v for v in VARIABLES}


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
    """Carga un valor en la tabla, protegiendo contra falsos positivos del OCR/PDF.

    Caso crítico corregido: el rótulo Z0 contiene el dígito 0. Si se usa el
    primer número de la línea, puede exportarse 0 en lugar de la impedancia
    basal real, por ejemplo 23.0 o 31.1 ohm.
    """
    if pd.isna(val):
        return
    try:
        fval = float(val)
    except Exception:
        return
    # Validación general de rango fisiológico/técnico cuando la función ya está disponible.
    vf = globals().get("variable_plausible")
    if callable(vf):
        try:
            if not vf(code, fval):
                return
        except Exception:
            pass
    idx = df.index[df["codigo"] == code]
    if len(idx):
        df.loc[idx[0], "valor"] = fval
        df.loc[idx[0], "estado"] = state


def get_df_value(df: pd.DataFrame, code: str):
    idx = df.index[df["codigo"] == code]
    if len(idx) == 0:
        return np.nan
    return pd.to_numeric(pd.Series([df.loc[idx[0], "valor"]]), errors="coerce").iloc[0]


def copy_if_missing(df: pd.DataFrame, src: str, dst: str, state: str = "copiado/sinónimo/revisar") -> None:
    if pd.isna(get_df_value(df, dst)) and not pd.isna(get_df_value(df, src)):
        set_df_value(df, dst, get_df_value(df, src), state)


def complete_derived_variables(df: pd.DataFrame) -> None:
    """Completa variables equivalentes/derivadas para que el Excel siempre tenga columnas hemodinámicas completas."""
    pas, pad = get_df_value(df, "PAS"), get_df_value(df, "PAD")
    if pd.isna(get_df_value(df, "PP")) and not pd.isna(pas) and not pd.isna(pad):
        set_df_value(df, "PP", pas - pad, "derivado PAS-PAD/revisar")
    if pd.isna(get_df_value(df, "PAM")) and not pd.isna(pas) and not pd.isna(pad):
        set_df_value(df, "PAM", pad + (pas - pad) / 3.0, "derivado PA/revisar")

    # Sinónimos frecuentes entre informes y literatura
    copy_if_missing(df, "CFT", "TFC")
    copy_if_missing(df, "TFC", "CFT")
    copy_if_missing(df, "CFTNR", "TFCNR")
    copy_if_missing(df, "TFCNR", "CFTNR")
    copy_if_missing(df, "IAC", "ACI")
    copy_if_missing(df, "ACI", "IAC")
    copy_if_missing(df, "IH", "HI")
    copy_if_missing(df, "HI", "IH")
    copy_if_missing(df, "PE", "LVET")
    copy_if_missing(df, "LVET", "PE")
    copy_if_missing(df, "PPE", "PEP")
    copy_if_missing(df, "PEP", "PPE")
    copy_if_missing(df, "AC", "EA_EES")
    copy_if_missing(df, "EA_EES", "AC")
    copy_if_missing(df, "FE_CAPAN", "FE")

    # Cociente de tiempo sistólico: PPE/PE x 100
    ppe, pe = get_df_value(df, "PPE"), get_df_value(df, "PE")
    if pd.isna(get_df_value(df, "CTS")) and not pd.isna(ppe) and not pd.isna(pe) and pe != 0:
        set_df_value(df, "CTS", (ppe / pe) * 100.0, "derivado PPE/PE/revisar")

    # Acoplamiento si se cargaron Ea y Ees
    ea, ees = get_df_value(df, "EA"), get_df_value(df, "EES")
    if pd.isna(get_df_value(df, "AC")) and not pd.isna(ea) and not pd.isna(ees) and ees != 0:
        val = ea / ees
        set_df_value(df, "AC", val, "derivado Ea/Ees/revisar")
        set_df_value(df, "EA_EES", val, "derivado Ea/Ees/revisar")

    # Antropometría derivada para indexación y exportación.
    peso = get_df_value(df, "PESO")
    talla = get_df_value(df, "TALLA")
    imc = get_df_value(df, "IMC")
    if not pd.isna(talla):
        talla_cm = talla * 100.0 if talla < 3 else talla
    else:
        talla_cm = np.nan
    if pd.isna(get_df_value(df, "TALLA")) is False and not pd.isna(talla_cm):
        # Normaliza a centímetros si el PDF trajo metros, por ejemplo 1.70.
        set_df_value(df, "TALLA", talla_cm, "normalizado cm/revisar")
    if pd.isna(imc) and not pd.isna(peso) and not pd.isna(talla_cm) and talla_cm > 0:
        h_m = talla_cm / 100.0
        set_df_value(df, "IMC", peso / (h_m * h_m), "derivado peso/talla/revisar")
    if pd.isna(peso) and not pd.isna(imc) and not pd.isna(talla_cm) and talla_cm > 0:
        h_m = talla_cm / 100.0
        set_df_value(df, "PESO", imc * h_m * h_m, "derivado IMC/talla/revisar")
    if pd.isna(get_df_value(df, "BSA")) and not pd.isna(peso) and not pd.isna(talla_cm) and peso > 0 and talla_cm > 0:
        # Fórmula de Mosteller: BSA = sqrt(talla(cm) * peso(kg) / 3600).
        set_df_value(df, "BSA", float(np.sqrt((talla_cm * peso) / 3600.0)), "derivado Mosteller/revisar")


def parse_anthropometry_from_text(text: str, df: pd.DataFrame) -> None:
    """Extrae peso, talla, IMC y superficie corporal de textos variables del informe.
    No usa la letra T aislada para talla, porque en Exxer puede significar distancia de electrodos.
    """
    if not text:
        return
    clean = re.sub(r"[\t\r\n]+", " ", text)
    clean = re.sub(r"\s+", " ", clean)
    patterns = {
        "PESO": [r"\bPeso\s*[:=]?\s*(\d{2,3}(?:[\.,]\d+)?)\s*(?:kg|kilos)?\b"],
        "TALLA": [
            r"\bTalla\s*[:=]?\s*(\d{1,3}(?:[\.,]\d+)?)\s*(?:cm|m|mts|metros)?\b",
            r"\bAltura\s*[:=]?\s*(\d{1,3}(?:[\.,]\d+)?)\s*(?:cm|m|mts|metros)?\b",
            r"\bEstatura\s*[:=]?\s*(\d{1,3}(?:[\.,]\d+)?)\s*(?:cm|m|mts|metros)?\b",
        ],
        "IMC": [r"\bIMC\s*[:=]?\s*(\d{1,2}(?:[\.,]\d+)?)\b", r"\bBMI\s*[:=]?\s*(\d{1,2}(?:[\.,]\d+)?)\b"],
        "BSA": [
            r"\bBSA\s*[:=]?\s*(\d(?:[\.,]\d+)?)\b",
            r"Superficie\s+corporal\s*[:=]?\s*(\d(?:[\.,]\d+)?)\b",
            r"\bSC\s*[:=]?\s*(\d(?:[\.,]\d+)?)\b",
        ],
    }
    for code, pats in patterns.items():
        if not pd.isna(get_df_value(df, code)):
            continue
        for pat in pats:
            m = re.search(pat, clean, re.I)
            if m:
                val = to_float(m.group(1))
                if code == "TALLA" and not pd.isna(val) and val < 3:
                    val = val * 100.0
                if variable_plausible(code, val):
                    set_df_value(df, code, val, "extraído/antropometría/revisar")
                    break


def variable_plausible(code: str, value: float) -> bool:
    if pd.isna(value):
        return False
    ranges = {
        "FC": (30, 180), "RR": (250, 2500),
        "PAS": (50, 260), "PAD": (25, 160), "PAM": (35, 190), "PP": (10, 160),
        "DS": (5, 200), "IDS": (1, 150), "VM": (0.3, 25), "IC": (0.2, 12),
        "ITC": (0.1, 20), "ITS": (0.1, 200), "LCW": (0.1, 30), "LCWI": (0.1, 20),
        "LVSW": (1, 300), "LVSWI": (1, 200),
        "RVS": (100, 8000), "IRV": (100, 12000), "CA": (0.03, 20),
        "EA": (0.05, 10), "EAI": (0.05, 10), "EES": (0.05, 20), "EESI": (0.05, 20),
        "AC": (0.05, 5), "EA_EES": (0.05, 5),
        "IV": (1, 300), "IAC": (1, 600), "ACI": (1, 600), "IH": (0.1, 200), "HI": (0.1, 200),
        "FE_CAPAN": (5, 95), "FE": (5, 95),
        "CTS": (1, 100), "CTE": (1, 100), "PE": (50, 600), "PPE": (20, 300), "LVET": (50, 600), "PEP": (20, 300),
        "TFC": (1, 100), "CFT": (1, 100), "TFCNR": (0.05, 5), "CFTNR": (0.05, 5),
        "DZDT_MAX": (0.05, 20), "DZDT_MIN": (-20, -0.01), "D2ZDT2_MAX": (0.05, 200), "Z0": (5, 80),
        "DIST_D": (5, 80), "DIST_T": (5, 80),
        "PESO": (20, 250), "TALLA": (80, 230), "IMC": (10, 80), "BSA": (0.5, 3.5),
    }
    lo, hi = ranges.get(code, (-1e9, 1e9))
    return lo <= float(value) <= hi


def extract_words_json(text: str) -> List[Dict[str, float]]:
    if "=== WORDS_JSON_PDF ===" not in text:
        return []
    block = text.split("=== WORDS_JSON_PDF ===", 1)[1].split("=== LINEAS_ESPACIALES_PDF ===", 1)[0].strip()
    try:
        data = json.loads(block)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def group_words_rows(words: List[Dict[str, float]], y_tol: float = 4.5) -> List[List[Dict[str, float]]]:
    """Agrupa palabras en filas usando coordenada Y, preservando columnas por X."""
    clean = []
    for w in words:
        try:
            txt = str(w.get("text", "")).strip()
            if not txt:
                continue
            w2 = dict(w)
            w2["yc"] = (float(w2["y0"]) + float(w2["y1"])) / 2.0
            clean.append(w2)
        except Exception:
            pass
    clean.sort(key=lambda z: (z["yc"], float(z.get("x0", 0))))
    rows: List[List[Dict[str, float]]] = []
    centers: List[float] = []
    for w in clean:
        if not rows or abs(w["yc"] - centers[-1]) > y_tol:
            rows.append([w]); centers.append(w["yc"])
        else:
            rows[-1].append(w)
            centers[-1] = sum(x["yc"] for x in rows[-1]) / len(rows[-1])
    for r in rows:
        r.sort(key=lambda z: float(z.get("x0", 0)))
    return rows


def row_text(row: List[Dict[str, float]]) -> str:
    return re.sub(r"\s+", " ", " ".join(str(w.get("text", "")) for w in row)).strip()


def numeric_tokens_after(row: List[Dict[str, float]], x_start: float) -> List[Tuple[float, float]]:
    vals = []
    for w in row:
        if float(w.get("x0", 0)) <= x_start:
            continue
        txt = str(w.get("text", ""))
        for m in re.finditer(r"-?\d+(?:[\.,]\d+)?", txt):
            vals.append((float(w.get("x0", 0)), to_float(m.group(0))))
    vals.sort(key=lambda t: t[0])
    return vals


def parse_words_by_position(words: List[Dict[str, float]], df: pd.DataFrame, meta: Dict[str, str]) -> None:
    """Extractor principal para PDF nativo Exxer: usa coordenadas, no texto plano.\n    Evita confundir valores reales con referencias de barras y evita cargar observaciones como medicación.\n    """
    if not words:
        return
    rows = group_words_rows(words)
    code_list = [c for c in VARIABLE_ORDER if c not in ["PAS", "PAD", "PAM", "PP", "PE", "PPE", "RR", "DZDT_MAX", "Z0", "DIST_D", "DIST_T"]]
    for row in rows:
        txt = row_text(row)
        # Presión arterial compuesta.
        if any(str(w.get("text", "")).strip().upper() == "PA" for w in row) or re.search(r"Sist[óo]lica/Diast[óo]lica", txt, re.I):
            m = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})\s*\(?\s*(\d{2,3})?\s*\)?", txt)
            if m:
                set_df_value(df, "PAS", to_float(m.group(1)), "extraído/pdf-coordenadas/revisar")
                set_df_value(df, "PAD", to_float(m.group(2)), "extraído/pdf-coordenadas/revisar")
                if m.group(3):
                    set_df_value(df, "PAM", to_float(m.group(3)), "extraído/pdf-coordenadas/revisar")
        # Variables de tabla izquierda.
        for code in code_list:
            code_words = [w for w in row if str(w.get("text", "")).strip().upper() == code]
            if not code_words:
                continue
            x_code = min(float(w.get("x1", 0)) for w in code_words)
            nums = numeric_tokens_after(row, x_code)
            # Para evitar tomar referencias de barras, se elige el primer número plausible después del código.
            for _, val in nums:
                if variable_plausible(code, val):
                    set_df_value(df, code, val, "extraído/pdf-coordenadas/revisar")
                    break
            if code == "CTS":
                m = re.search(r"(\d+(?:[\.,]\d+)?)\s*%?\s*\(?\s*(\d+(?:[\.,]\d+)?)\s*/\s*(\d+(?:[\.,]\d+)?)", txt)
                if m:
                    set_df_value(df, "CTS", to_float(m.group(1)), "extraído/pdf-coordenadas/revisar")
                    set_df_value(df, "PPE", to_float(m.group(2)), "extraído/pdf-coordenadas/revisar")
                    set_df_value(df, "PE", to_float(m.group(3)), "extraído/pdf-coordenadas/revisar")
        # Panel derecho: suele estar en filas RR / PE / PPE / dz/dt / Z0.
        for code in ["RR", "PE", "PPE"]:
            code_words = [w for w in row if str(w.get("text", "")).strip().upper() == code]
            for cw in code_words:
                for _, val in numeric_tokens_after(row, float(cw.get("x1", 0))):
                    if variable_plausible(code, val):
                        set_df_value(df, code, val, "extraído/pdf-coordenadas/revisar")
                        break
        if re.search(r"dZ/dt|dz/dt|dZdt|dzdt", txt, re.I):
            nums = [to_float(x) for x in re.findall(r"-?\d+(?:[\.,]\d+)?", txt)]
            nums = [x for x in nums if variable_plausible("DZDT_MAX", x)]
            if nums:
                set_df_value(df, "DZDT_MAX", nums[-1], "extraído/pdf-coordenadas/revisar")
        if re.search(r"\bZ\s*[0O]\b", txt, re.I):
            # No tomar el 0 del rótulo Z0. Se toma exclusivamente el número posterior a Z0/ZO.
            mz0 = re.search(r"\bZ\s*[0O]\b\s*[:=]?\s*(\d+(?:[\.,]\d+)?)", txt, re.I)
            if mz0:
                set_df_value(df, "Z0", to_float(mz0.group(1)), "extraído/pdf-coordenadas/revisar")
        if re.search(r"Dist\.?\s*e/?\s*electrodos|electrodos|D\s*:", txt, re.I):
            md = re.search(r"\bD\s*[:=]\s*(\d+(?:[\.,]\d+)?)", txt, re.I)
            mt = re.search(r"\bT\s*[:=]\s*(\d+(?:[\.,]\d+)?)", txt, re.I)
            if md: set_df_value(df, "DIST_D", to_float(md.group(1)), "extraído/pdf-coordenadas/revisar")
            if mt: set_df_value(df, "DIST_T", to_float(mt.group(1)), "extraído/pdf-coordenadas/revisar")
        # Medicación: tomar sólo la fila donde aparece medicación, antes del panel gráfico o valores clínicos.
        if re.search(r"Medicaci[oó]n", txt, re.I):
            med = re.sub(r".*Medicaci[oó]n", "", txt, flags=re.I).strip()
            med = re.sub(r"\b(RR|PE|PPE|dz/dt|Z0|Dist\.).*", "", med, flags=re.I).strip()
            if med:
                meta["medication"] = med[:180]

def parse_variable_lines(lines: List[str], df: pd.DataFrame) -> None:
    """Extrae valores cuando el PDF conserva una línea por variable.
    Ejemplos esperados:
    FC Frecuencia Cardíaca 57 pulsos/min
    PA Sistólica/Diastólica (Media) 125/76 (92) mmHg
    CTS Cociente de Tiempo Sistólico 39% (120/305)
    """
    aliases = {
        "FC": ["FC", "Frecuencia Card"],
        "DS": ["DS", "Descarga Sist", "Volumen Sist"],
        "IDS": ["IDS", "Indice de Descarga", "Índice de Descarga", "Stroke Index"],
        "VM": ["VM", "Volumen Minuto", "Gasto Cardi", "Cardiac Output"],
        "IC": ["IC", "Indice Card", "Índice Card", "Cardiac Index"],
        "RVS": ["RVS", "Resistencia Vascular Sist", "SVR"],
        "IRV": ["IRV", "Indice de Resistencia", "Índice de Resistencia", "SVRI"],
        "CA": ["CA", "Complacencia Arterial", "Arterial Compliance"],
        "EA": ["Ea", "Elastancia Arterial", "Elastancia arterial efectiva"],
        "EES": ["Ees", "Elastancia Ventricular", "fin de sístole", "End Systolic Elastance"],
        "AC": ["Ea/Ees", "Acoplamiento", "Ventriculoarterial"],
        "IV": ["IV", "Indice de Velocidad", "Índice de Velocidad", "Velocity Index"],
        "IAC": ["IAC", "ACI", "Indice de Aceler", "Índice de Aceler", "Acceleration Index"],
        "IH": ["IH", "HI", "Heather"],
        "FE_CAPAN": ["FE Capan", "Fracción de Eyección Capan", "Fraccion de Eyeccion Capan"],
        "FE": ["FE", "Fracción de Eyección", "Fraccion de Eyeccion", "Ejection Fraction"],
        "CTS": ["CTS", "Cociente de Tiempo Sist"],
        "CTE": ["CTE", "Cociente de Tiempo Eyect"],
        "ITC": ["ITC", "Indice de Trabajo", "Índice de Trabajo", "Cardiac Work Index"],
        "ITS": ["ITS", "Indice de Trabajo Sist", "Índice de Trabajo Sist", "Stroke Work Index"],
        "CFT": ["CFT", "TFC", "Contenido de Fluidos", "Thoracic Fluid"],
        "TFCNR": ["TFCNR", "CFTNR", "normalizado"],
        "Z0": ["Z0", "Impedancia basal"],
        "DZDT_MAX": ["dz/dt", "dZ/dt", "dZdt"],
        "PESO": ["Peso"],
        "TALLA": ["Talla", "Altura"],
        "IMC": ["IMC", "BMI"],
        "BSA": ["BSA", "Superficie corporal"],
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
                # Z0 requiere tratamiento especial: el dígito del rótulo NO es el valor.
                if code == "Z0":
                    mz0 = re.search(r"\bZ\s*[0O]\b\s*[:=]?\s*(\d+(?:[\.,]\d+)?)", line, re.I)
                    if mz0:
                        set_df_value(df, "Z0", to_float(mz0.group(1)), "extraído/línea-Z0/revisar")
                        used_line_idx.add(i)
                    break
                # Tomar el primer número plausible de la línea, evitando referencias de barras o rótulos.
                nums = [to_float(x) for x in re.findall(r"-?\d+(?:[\.,]\d+)?", line)]
                nums = [x for x in nums if variable_plausible(code, x)]
                if nums:
                    set_df_value(df, code, nums[0])
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
        "Z0": r"\bZ\s*[0O]\b\s*[:=]?\s*(\d+(?:[\.,]\d+)?)",
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

    words = extract_words_json(text)
    parse_words_by_position(words, df, meta)
    parse_variable_lines(spatial_lines, df)
    parse_right_panel_text(text, df)
    fallback_sequence_parse(text, df)
    parse_anthropometry_from_text(text, df)

    joined = "\n".join(raw_lines)
    flat = re.sub(r"\s+", " ", joined)
    if not meta.get("medication"):
        m = re.search(r"Medicaci[oó]n\s+(.+?)(?:\bRR\b|\bPE\b|\bPPE\b|dz/dt|Z0|Dist\.|Observaciones|$)", flat, re.I)
        if m:
            med = re.sub(r"\s+", " ", m.group(1)).strip()
            # Evita que la tabla de variables entre en Medicación si el PDF concatenó columnas.
            med = re.sub(r"\b(PA|FC|DS|IDS|VM|IC|RVS|IRV|CA|IV|IAC|CTS|ITC|CFT)\b.*", "", med, flags=re.I).strip()
            meta["medication"] = med[:180]
    m = re.search(r"Observaciones\s+(.+?)(?:www\.|$)", flat, re.I)
    if m:
        obs = re.sub(r"\s+", " ", m.group(1)).strip()
        # Si se pegó todo el informe, mantener sólo observación breve real.
        if len(obs) > 500 or re.search(r"PAR[ÁA]METRO|VALOR|Frecuencia Card", obs, re.I):
            obs = ""
        meta["observations"] = obs[:500]

    # Completar variables equivalentes y derivadas para que el Excel tenga todas las columnas hemodinámicas.
    complete_derived_variables(df)

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



def _safe_canvas_key(raw: str) -> str:
    """Evita claves largas o con caracteres problemáticos para componentes."""
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", str(raw))[:120]


def _rois_are_valid(rois: dict, w: int, h: int) -> bool:
    try:
        return all(k in rois and set(["x0", "x1", "y0", "y1"]).issubset(rois[k]) for k in SIGNALS)
    except Exception:
        return False


def _fabric_rect_from_roi(key: str, roi: dict, scale: float, color: str) -> dict:
    return {
        "type": "rect",
        "left": float(roi["x0"] * scale),
        "top": float(roi["y0"] * scale),
        "width": float((roi["x1"] - roi["x0"]) * scale),
        "height": float((roi["y1"] - roi["y0"]) * scale),
        "scaleX": 1.0,
        "scaleY": 1.0,
        "angle": 0,
        "fill": "rgba(0,0,0,0)",
        "stroke": color,
        "strokeWidth": 4,
        "strokeUniform": True,
        "transparentCorners": False,
        "cornerColor": color,
        "cornerStrokeColor": color,
        "borderColor": color,
        "name": key,
        "roi_key": key,
        "selectable": True,
        "evented": True,
        "hasControls": True,
        "lockRotation": True,
    }


def _object_to_roi(obj: dict, fallback: dict, scale: float, w: int, h: int) -> dict:
    """Convierte un rectángulo Fabric.js en coordenadas reales de la imagen."""
    try:
        left = float(obj.get("left", fallback["x0"] * scale))
        top = float(obj.get("top", fallback["y0"] * scale))
        width = float(obj.get("width", (fallback["x1"] - fallback["x0"]) * scale)) * float(obj.get("scaleX", 1.0))
        height = float(obj.get("height", (fallback["y1"] - fallback["y0"]) * scale)) * float(obj.get("scaleY", 1.0))
        # Si el usuario invirtió el rectángulo al estirar, normalizar bordes.
        x0 = min(left, left + width) / scale
        x1 = max(left, left + width) / scale
        y0 = min(top, top + height) / scale
        y1 = max(top, top + height) / scale
        return clamp_roi({"x0": int(round(x0)), "x1": int(round(x1)), "y0": int(round(y0)), "y1": int(round(y1))}, w, h)
    except Exception:
        return clamp_roi(fallback, w, h)


def canvas_select_rois(img: Image.Image, rois_default: dict, key_prefix: str = "roi_canvas") -> dict:
    """Permite ajustar los sectores moviendo bordes sobre la imagen.

    Uso: hacer clic en el rectángulo, arrastrarlo completo o tomar un borde/esquina
    para cambiar su tamaño. Los cambios quedan guardados en st.session_state para
    que no se pierdan al mover cursores o actualizar otros controles.
    """
    w, h = img.size
    rois_default = {k: clamp_roi(v, w, h) for k, v in rois_default.items()}
    colors = {"dzdt": "#1D4ED8", "ecg": "#16A34A", "fono": "#EA580C"}
    names = {"dzdt": "dZ/dt / impedancia", "ecg": "ECG", "fono": "Fonocardiograma"}
    safe_key = _safe_canvas_key(key_prefix)
    state_key = f"{safe_key}_rois_state"

    if state_key not in st.session_state or not _rois_are_valid(st.session_state.get(state_key), w, h):
        st.session_state[state_key] = rois_default
    current_rois = {k: clamp_roi(v, w, h) for k, v in st.session_state[state_key].items()}

    cinfo, creset = st.columns([4, 1])
    with cinfo:
        st.caption("Ajuste los sectores desde la imagen: clic sobre el rectángulo, arrastre para moverlo o tome sus bordes/esquinas para cambiar tamaño. Azul=dZ/dt, verde=ECG, naranja=fono.")
    with creset:
        if st.button("Restablecer bordes", key=f"{safe_key}_reset_rois"):
            st.session_state[state_key] = rois_default
            st.rerun()

    max_display_w = 1150
    scale = min(1.0, max_display_w / max(1, w))
    disp_w = max(1, int(w * scale))
    disp_h = max(1, int(h * scale))
    bg = img.resize((disp_w, disp_h)).convert("RGB") if scale != 1.0 else img.convert("RGB")

    if st_canvas is not None:
        try:
            initial_objects = [_fabric_rect_from_roi(key, current_rois[key], scale, colors[key]) for key in SIGNALS]
            canvas_result = st_canvas(
                fill_color="rgba(0, 0, 0, 0)",
                stroke_width=4,
                stroke_color="#1D4ED8",
                background_image=bg,
                height=disp_h,
                width=disp_w,
                drawing_mode="transform",
                initial_drawing={"version": "5.2.4", "objects": initial_objects},
                display_toolbar=False,
                update_streamlit=True,
                key=f"{safe_key}_drawable_canvas",
            )

            objects = []
            if canvas_result is not None and getattr(canvas_result, "json_data", None):
                maybe_objects = canvas_result.json_data.get("objects", [])
                if isinstance(maybe_objects, list):
                    objects = maybe_objects

            # Mapear por nombre cuando Fabric conserva roi_key/name; si no, por orden.
            named = {}
            for obj in objects:
                k = obj.get("roi_key") or obj.get("name")
                if k in SIGNALS:
                    named[k] = obj

            rois = {}
            for i, key in enumerate(SIGNALS):
                obj = named.get(key)
                if obj is None and i < len(objects):
                    obj = objects[i]
                rois[key] = _object_to_roi(obj or {}, current_rois[key], scale, w, h)

            st.session_state[state_key] = rois
            st.image(draw_rois(img, rois), caption="Sectores finales aplicados para digitalizar", use_container_width=True)
            st.markdown(
                "<div class='ok'><b>Bordes activos:</b> los rectángulos seleccionados se usarán para digitalizar las curvas. "
                "Si no detecta puntos, agrande levemente el rectángulo de esa señal sin incluir texto ni ejes.</div>",
                unsafe_allow_html=True,
            )
            return rois
        except Exception as e:
            st.warning(
                "No se pudo abrir el selector gráfico de bordes. "
                "La app continúa en modo seguro con edición numérica de bordes. "
                f"Detalle técnico: {type(e).__name__}: {e}"
            )

    st.info("Modo seguro: edite los bordes de cada sector con campos numéricos. La app no se detiene y los cambios quedan aplicados.")
    rois = {}
    st.image(draw_rois(img, current_rois), caption="Sectores iniciales/sugeridos", use_container_width=True)
    for key in SIGNALS:
        b = current_rois[key]
        st.markdown(f"**{names[key]}**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            x0 = st.number_input(f"Borde izquierdo {key}", 0, max(1, w - 3), int(b["x0"]), key=f"{safe_key}_{key}_x0_num")
        with c2:
            x1_default = int(max(int(x0) + 3, b["x1"]))
            x1 = st.number_input(f"Borde derecho {key}", int(x0) + 3, w, min(w, x1_default), key=f"{safe_key}_{key}_x1_num")
        with c3:
            y0 = st.number_input(f"Borde superior {key}", 0, max(1, h - 3), int(b["y0"]), key=f"{safe_key}_{key}_y0_num")
        with c4:
            y1_default = int(max(int(y0) + 3, b["y1"]))
            y1 = st.number_input(f"Borde inferior {key}", int(y0) + 3, h, min(h, y1_default), key=f"{safe_key}_{key}_y1_num")
        rois[key] = clamp_roi({"x0": int(x0), "x1": int(x1), "y0": int(y0), "y1": int(y1)}, w, h)
    st.session_state[state_key] = rois
    st.image(draw_rois(img, rois), caption="Sectores finales aplicados para digitalizar", use_container_width=True)
    return rois


def correction_delta_rows(cdf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if cdf is None or cdf.empty:
        return pd.DataFrame(columns=[
            "correction_id", "study_id", "created_at", "username", "patient_code", "source_name", "page_number",
            "cursor", "auto_x", "corregido_x", "delta_corregido_x_px", "auto_y", "corregido_y", "delta_corregido_y_norm",
            "abs_delta_x_px", "rois_json", "metrics_json", "conclusion"
        ])
    for _, r in cdf.iterrows():
        try:
            auto = json.loads(r.get("auto_json", "{}") or "{}")
            manual = json.loads(r.get("manual_json", "{}") or "{}")
            for cur in CURSORS:
                ax = auto.get(cur, {}).get("x", np.nan)
                mx = manual.get(cur, {}).get("x", np.nan)
                ay = auto.get(cur, {}).get("y", np.nan)
                my = manual.get(cur, {}).get("y", np.nan)
                dx = (mx - ax) if pd.notna(mx) and pd.notna(ax) else np.nan
                dy = (my - ay) if pd.notna(my) and pd.notna(ay) else np.nan
                rows.append({
                    "correction_id": r.get("id", np.nan),
                    "study_id": r.get("study_id", np.nan),
                    "created_at": r.get("created_at", ""),
                    "username": r.get("username", ""),
                    "patient_code": r.get("patient_code", ""),
                    "source_name": r.get("source_name", ""),
                    "page_number": r.get("page_number", ""),
                    "cursor": cur,
                    "auto_x": ax,
                    "corregido_x": mx,
                    "delta_corregido_x_px": dx,
                    "auto_y": ay,
                    "corregido_y": my,
                    "delta_corregido_y_norm": dy,
                    "abs_delta_x_px": abs(dx) if pd.notna(dx) else np.nan,
                    "rois_json": r.get("rois_json", ""),
                    "metrics_json": r.get("metrics_json", ""),
                    "conclusion": r.get("conclusion", ""),
                })
        except Exception:
            continue
    return pd.DataFrame(rows)


def export_cursor_delta_excel(scope_user: dict, only_current_user: bool = True) -> bytes:
    user_filter = scope_user if only_current_user else None
    cdf = cursor_df(user_filter)
    delta = correction_delta_rows(cdf)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        delta.to_excel(writer, index=False, sheet_name="delta_correccion_cursores")
        if cdf is None or cdf.empty:
            pd.DataFrame().to_excel(writer, index=False, sheet_name="correcciones_crudas")
        else:
            drop_identifying_filename_columns(cdf).to_excel(writer, index=False, sheet_name="correcciones_crudas")
        resumen_cols = ["correction_id", "created_at", "username", "patient_code"]
        if not delta.empty:
            resumen = delta.groupby(resumen_cols, dropna=False).agg(
                error_medio_px=("abs_delta_x_px", "mean"),
                error_max_px=("abs_delta_x_px", "max"),
                n_cursores=("cursor", "count"),
            ).reset_index()
        else:
            resumen = pd.DataFrame(columns=resumen_cols + ["error_medio_px", "error_max_px", "n_cursores"])
        resumen.to_excel(writer, index=False, sheet_name="resumen_delta")
    bio.seek(0)
    return bio.getvalue()


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


def build_curve_chart(dzdt: pd.DataFrame, ecg: pd.DataFrame, fono: pd.DataFrame, auto: dict, manual: dict, guide: dict, x0: float, x1: float) -> dict:
    dpi = 170
    fig, ax = plt.subplots(figsize=(14.5, 5.8), dpi=dpi)
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
        ax.axvline(float(auto[c]["x"]), linestyle=":", linewidth=1.1, color=CURSOR_COLORS.get(c))
        ax.axvline(float(manual[c]["x"]), linestyle="--", linewidth=2.0, color=CURSOR_COLORS.get(c))
        ax.text(float(manual[c]["x"]), 2.10 if c in ["B", "C", "X", "Y"] else 1.05, c, rotation=90, ha="center", va="bottom", fontsize=10, fontweight="bold", color=CURSOR_COLORS.get(c))
    ax.set_title("Corrección opcional integrada de cursores")
    ax.set_xlim(x0, x1)
    ax.set_ylim(-0.15, 3.75)
    ax.set_yticks([0.5, 1.7, 2.9])
    ax.set_yticklabels(["Fono", "ECG", "dZ/dt"])
    ax.grid(True, alpha=0.22)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.canvas.draw()
    width, height = fig.canvas.get_width_height()
    x0_pix = float(ax.transData.transform((x0, 0))[0])
    x1_pix = float(ax.transData.transform((x1, 0))[0])
    bio = io.BytesIO()
    fig.savefig(bio, format="png", dpi=dpi)
    plt.close(fig)
    bio.seek(0)
    return {
        "image_bytes": bio.getvalue(),
        "width": int(width),
        "height": int(height),
        "data_x0_pix": x0_pix,
        "data_x1_pix": x1_pix,
        "x0": float(x0),
        "x1": float(x1),
    }


def plot_curves(dzdt: pd.DataFrame, ecg: pd.DataFrame, fono: pd.DataFrame, auto: dict, manual: dict, guide: dict, x0: float, x1: float) -> bytes:
    return build_curve_chart(dzdt, ecg, fono, auto, manual, guide, x0, x1)["image_bytes"]


def data_x_to_canvas_px(x_value: float, chart_meta: dict) -> float:
    x0, x1 = float(chart_meta["x0"]), float(chart_meta["x1"])
    p0, p1 = float(chart_meta["data_x0_pix"]), float(chart_meta["data_x1_pix"])
    if abs(x1 - x0) < 1e-9:
        return p0
    return p0 + ((float(x_value) - x0) / (x1 - x0)) * (p1 - p0)


def canvas_px_to_data_x(px_value: float, chart_meta: dict) -> float:
    x0, x1 = float(chart_meta["x0"]), float(chart_meta["x1"])
    p0, p1 = float(chart_meta["data_x0_pix"]), float(chart_meta["data_x1_pix"])
    if abs(p1 - p0) < 1e-9:
        return x0
    val = x0 + ((float(px_value) - p0) / (p1 - p0)) * (x1 - x0)
    return float(min(max(val, x0), x1))


def graph_adjust_cursors(chart_meta: dict, cursor_x: dict, key_prefix: str = "cursor_canvas") -> dict:
    """Permite mover QRS, B, C, X e Y directamente sobre el gráfico digitalizado."""
    img = Image.open(io.BytesIO(chart_meta["image_bytes"])).convert("RGB")
    w, h = img.size
    max_display_w = 1200
    scale = min(1.0, max_display_w / max(1, w))
    disp_w = int(w * scale)
    disp_h = int(h * scale)
    bg = img.resize((disp_w, disp_h)) if scale != 1.0 else img

    st.caption("Arrastre las líneas de colores sobre el gráfico para corregir el inicio de QRS y los puntos B, C, X, Y. Luego, si lo desea, afine el valor exacto debajo.")
    st.markdown(
        " ".join([f"<span style='display:inline-block;margin-right:12px;color:{CURSOR_COLORS[c]};font-weight:700'>{c}</span>" for c in CURSORS]),
        unsafe_allow_html=True,
    )

    positions = {c: float(cursor_x.get(c, chart_meta["x0"])) for c in CURSORS}
    if st_canvas is not None:
        try:
            initial_objects = []
            for c in CURSORS:
                xpix = data_x_to_canvas_px(positions[c], chart_meta) * scale
                initial_objects.append({
                    "type": "rect",
                    "left": max(0.0, float(xpix) - 3.0),
                    "top": 0.0,
                    "width": 6.0,
                    "height": float(disp_h),
                    "fill": "rgba(0,0,0,0.02)",
                    "stroke": CURSOR_COLORS.get(c, "#111827"),
                    "strokeWidth": 3,
                    "name": c,
                    "selectable": True,
                    "hasControls": True,
                    "lockMovementY": True,
                    "lockScalingY": True,
                    "lockRotation": True,
                })
            canvas_result = st_canvas(
                fill_color="rgba(0, 0, 0, 0)",
                stroke_width=3,
                stroke_color="#111827",
                background_image=bg,
                height=disp_h,
                width=disp_w,
                drawing_mode="transform",
                initial_drawing={"version": "5.2.4", "objects": initial_objects},
                key=key_prefix,
            )
            objects = []
            if canvas_result.json_data and isinstance(canvas_result.json_data.get("objects"), list):
                objects = canvas_result.json_data["objects"]
            for i, c in enumerate(CURSORS):
                obj = objects[i] if i < len(objects) else {}
                left = float(obj.get("left", max(0.0, (data_x_to_canvas_px(positions[c], chart_meta) * scale) - 3.0)))
                width = float(obj.get("width", 6.0)) * float(obj.get("scaleX", 1.0))
                center_px = (left + width / 2.0) / scale
                positions[c] = canvas_px_to_data_x(center_px, chart_meta)
            return positions
        except Exception as e:
            st.warning(
                "No se pudo abrir el selector gráfico de cursores por incompatibilidad de versión. "
                "La app continúa con corrección manual de respaldo. "
                f"Detalle técnico: {type(e).__name__}: {e}"
            )

    st.info("Modo seguro: ajuste los cursores con campos numéricos. Si el componente gráfico no responde, igualmente puede corregir todos los puntos.")
    cols = st.columns(5)
    for i, c in enumerate(CURSORS):
        with cols[i]:
            positions[c] = float(st.number_input(f"Cursor {c}", value=float(positions[c]), min_value=float(chart_meta["x0"]), max_value=float(chart_meta["x1"]), step=1.0, key=f"{key_prefix}_{c}_fallback"))
    return positions

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




def drop_identifying_filename_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Retira columnas que puedan contener nombre/apellido del paciente, especialmente el nombre del archivo original."""
    if df is None or df.empty:
        return df
    private_cols = [c for c in ["source_name", "patient_key_basis", "extracted_text", "image_path"] if c in df.columns]
    return df.drop(columns=private_cols, errors="ignore")


def study_row_wide(r: pd.Series) -> dict:
    """Devuelve una fila con metadatos + TODAS las variables como columnas.
    Esta es la hoja principal para análisis: no obliga al usuario a buscar las variables en otra pestaña.
    """
    meta_cols = ["id", "created_at", "username", "patient_code", "study_date", "condition_label", "page_number", "medication", "observations", "notes"]
    out = {c: r.get(c, "") for c in meta_cols if c in r.index}
    try:
        raw_vars = pd.read_json(io.StringIO(r.get("variables_json", ""))) if r.get("variables_json") else pd.DataFrame()
    except Exception:
        raw_vars = pd.DataFrame()
    vars_df = normalize_variables_for_export(raw_vars)
    value_map = dict(zip(vars_df["codigo"].astype(str), vars_df["valor"]))
    state_map = dict(zip(vars_df["codigo"].astype(str), vars_df["estado"]))
    for code in VARIABLE_ORDER:
        out[code] = value_map.get(code, np.nan)
    # columnas auxiliares de auditoría: cuántas variables detectadas/cargadas
    out["n_variables_con_valor"] = int(pd.Series([out.get(c, np.nan) for c in VARIABLE_ORDER]).notna().sum())
    out["n_variables_totales"] = len(VARIABLE_ORDER)
    out["variables_con_valor"] = ", ".join([c for c in VARIABLE_ORDER if pd.notna(out.get(c, np.nan))])
    return out


def studies_wide_df(scope_user: dict | None = None) -> pd.DataFrame:
    """Tabla principal visible y exportable: cada estudio en una fila + todas las variables CGI."""
    df = studies_df(scope_user)
    if df.empty:
        return pd.DataFrame(columns=["id", "created_at", "username", "patient_code", "study_date", "condition_label", "page_number", "medication", "observations", "notes"] + VARIABLE_ORDER + ["n_variables_con_valor", "n_variables_totales", "variables_con_valor"])
    rows = [study_row_wide(r) for _, r in df.iterrows()]
    return drop_identifying_filename_columns(pd.DataFrame(rows))

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


def normalize_variables_for_export(vars_df: pd.DataFrame) -> pd.DataFrame:
    """Garantiza que cada estudio exporte TODAS las variables definidas, aunque algunas no hayan sido detectadas."""
    base = pd.DataFrame([{**v, "valor": np.nan, "estado": "no detectado/manual"} for v in VARIABLES])
    if vars_df is None or vars_df.empty:
        return base
    tmp = vars_df.copy()
    if "codigo" not in tmp.columns:
        return base
    for col in ["nombre", "unidad", "categoria", "valor", "estado"]:
        if col not in tmp.columns:
            tmp[col] = np.nan if col == "valor" else ""
    for _, r in tmp.iterrows():
        code = str(r.get("codigo", "")).strip()
        if code not in VARIABLE_DICT:
            # conservar variables no previstas en una fila adicional
            extra = {
                "codigo": code,
                "nombre": r.get("nombre", code),
                "unidad": r.get("unidad", ""),
                "categoria": r.get("categoria", "No clasificada"),
                "valor": pd.to_numeric(pd.Series([r.get("valor", np.nan)]), errors="coerce").iloc[0],
                "estado": r.get("estado", "importado") or "importado",
            }
            base = pd.concat([base, pd.DataFrame([extra])], ignore_index=True)
            continue
        idx = base.index[base["codigo"] == code]
        if len(idx):
            i = idx[0]
            val = pd.to_numeric(pd.Series([r.get("valor", np.nan)]), errors="coerce").iloc[0]
            base.loc[i, "valor"] = val
            base.loc[i, "estado"] = r.get("estado", "") or base.loc[i, "estado"]
    complete_derived_variables(base)
    return base


def export_excel(scope_user: dict, only_current_user: bool = True) -> bytes:
    user_filter = scope_user if only_current_user else None
    df = studies_df(user_filter)
    cdf = cursor_df(user_filter)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        # Diccionario maestro: documenta qué debe tener SIEMPRE cada Excel.
        pd.DataFrame(VARIABLES).to_excel(writer, index=False, sheet_name="diccionario_variables")

        if df.empty:
            pd.DataFrame().to_excel(writer, index=False, sheet_name="estudios")
            pd.DataFrame([{**v, "study_id": "", "created_at": "", "username": "", "patient_code": "", "condition_label": "", "valor": np.nan, "estado": ""} for v in VARIABLES]).to_excel(writer, index=False, sheet_name="variables_largo_todas")
            pd.DataFrame(columns=["study_id", "created_at", "username", "patient_code", "study_date", "condition_label"] + VARIABLE_ORDER).to_excel(writer, index=False, sheet_name="variables_ancho_todas")
            pd.DataFrame(columns=["study_id", "created_at", "username", "patient_code", "condition_label"] + [v["codigo"] for v in VARIABLES if v["categoria"] in ["Dinámica de fluidos", "Postcarga / vascular", "Contractilidad", "Función cardíaca", "Volemia / fluidos", "Acoplamiento", "Tiempos sistólicos"]]).to_excel(writer, index=False, sheet_name="hemodinamicas")
        else:
            # Hoja principal: metadatos + TODAS las variables como columnas.
            # Así, al abrir el Excel, el usuario no ve solo los datos administrativos.
            base = drop_identifying_filename_columns(pd.DataFrame([study_row_wide(r) for _, r in df.iterrows()]))
            base.to_excel(writer, index=False, sheet_name="estudios")
            long_rows = []
            wide_rows = []
            hemo_rows = []
            hemo_codes = [v["codigo"] for v in VARIABLES if v["categoria"] in ["Dinámica de fluidos", "Postcarga / vascular", "Contractilidad", "Función cardíaca", "Volemia / fluidos", "Acoplamiento", "Tiempos sistólicos"]]
            for _, r in df.iterrows():
                try:
                    raw_vars = pd.read_json(io.StringIO(r["variables_json"])) if r.get("variables_json") else pd.DataFrame()
                except Exception:
                    raw_vars = pd.DataFrame()
                vars_df = normalize_variables_for_export(raw_vars)

                v2 = vars_df.copy()
                v2.insert(0, "study_id", r["id"])
                v2.insert(1, "created_at", r["created_at"])
                v2.insert(2, "username", r["username"])
                v2.insert(3, "patient_code", r["patient_code"])
                v2.insert(4, "condition_label", r["condition_label"])
                long_rows.append(v2)

                wide = {
                    "study_id": r["id"],
                    "created_at": r["created_at"],
                    "username": r["username"],
                    "patient_code": r["patient_code"],
                    "study_date": r["study_date"],
                    "condition_label": r["condition_label"],
                }
                value_map = dict(zip(vars_df["codigo"].astype(str), vars_df["valor"]))
                for code in VARIABLE_ORDER:
                    wide[code] = value_map.get(code, np.nan)
                wide_rows.append(wide)

                hemo = {k: wide.get(k, np.nan) for k in ["study_id", "created_at", "username", "patient_code", "condition_label"]}
                for code in hemo_codes:
                    hemo[code] = value_map.get(code, np.nan)
                hemo_rows.append(hemo)

            pd.concat(long_rows, ignore_index=True).to_excel(writer, index=False, sheet_name="variables_largo_todas")
            pd.DataFrame(wide_rows, columns=["study_id", "created_at", "username", "patient_code", "study_date", "condition_label"] + VARIABLE_ORDER).to_excel(writer, index=False, sheet_name="variables_ancho_todas")
            pd.DataFrame(hemo_rows).to_excel(writer, index=False, sheet_name="hemodinamicas")

        # Corrección opcional: hoja específica para auditoría/aprendizaje.
        # Registra automático vs corregido y el delta corregido por cursor.
        delta_cursor = correction_delta_rows(cdf)
        delta_cursor.to_excel(writer, index=False, sheet_name="delta_correccion_cursores")
        if cdf.empty:
            pd.DataFrame().to_excel(writer, index=False, sheet_name="correcciones_crudas")
        else:
            drop_identifying_filename_columns(cdf).to_excel(writer, index=False, sheet_name="correcciones_crudas")
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
        st.caption("Los nombres de pacientes y el nombre del archivo original no se exportan.")

    tab1, tab2, tab3, tab4 = st.tabs(["1. Cargar informe completo", "2. Corrección opcional de curvas", "3. Mis Excel", "4. Administración"])

    with tab1:
        st.markdown("<div class='guide'><b>Función principal:</b> cargar la hoja completa del informe, extraer/corregir variables y guardar un registro anónimo en Excel. La corrección de curvas queda como módulo opcional aparte.</div>", unsafe_allow_html=True)
        uploaded = st.file_uploader("Subir PDF o imagen del informe completo", type=["pdf", "png", "jpg", "jpeg"], key="main_upload")
        if uploaded is not None:
            try:
                img, page, source, text = open_uploaded(uploaded)
                st.image(img, caption="Hoja completa renderizada", use_container_width=True)
                parsed_df, meta = parse_report_text(text)
                n_extraidos = int(parsed_df["valor"].notna().sum())
                if n_extraidos == 0:
                    st.error("No se pudieron extraer variables numéricas del PDF. Verifique que el PDF tenga texto seleccionable; si es una imagen escaneada, cargue los valores manualmente o use un PDF nativo.")
                else:
                    st.success(f"Variables numéricas detectadas automáticamente: {n_extraidos}/{len(parsed_df)}")
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

                st.subheader("Antropometría para indexación")
                st.caption("Se exporta en el Excel y permite calcular IMC y superficie corporal. Si el PDF no la trae, cargarla manualmente aquí.")
                complete_derived_variables(parsed_df)
                p0 = get_df_value(parsed_df, "PESO")
                t0 = get_df_value(parsed_df, "TALLA")
                i0 = get_df_value(parsed_df, "IMC")
                b0 = get_df_value(parsed_df, "BSA")
                ant1, ant2, ant3, ant4 = st.columns(4)
                with ant1:
                    peso_in = st.number_input("Peso (kg)", min_value=0.0, max_value=250.0, value=float(p0) if not pd.isna(p0) else 0.0, step=0.1, key="ant_peso")
                with ant2:
                    talla_in = st.number_input("Talla (cm)", min_value=0.0, max_value=230.0, value=float(t0) if not pd.isna(t0) else 0.0, step=0.5, key="ant_talla")
                with ant3:
                    imc_in = st.number_input("IMC (kg/m²)", min_value=0.0, max_value=80.0, value=float(i0) if not pd.isna(i0) else 0.0, step=0.1, key="ant_imc")
                with ant4:
                    bsa_in = st.number_input("Superficie corporal/BSA (m²)", min_value=0.0, max_value=3.5, value=float(b0) if not pd.isna(b0) else 0.0, step=0.01, key="ant_bsa")
                if peso_in > 0:
                    set_df_value(parsed_df, "PESO", peso_in, "manual/antropometría")
                if talla_in > 0:
                    set_df_value(parsed_df, "TALLA", talla_in, "manual/antropometría")
                if imc_in > 0:
                    set_df_value(parsed_df, "IMC", imc_in, "manual/antropometría")
                if bsa_in > 0:
                    set_df_value(parsed_df, "BSA", bsa_in, "manual/antropometría")
                complete_derived_variables(parsed_df)

                st.subheader("Variables del informe")
                st.caption("Revise y corrija los valores. Los campos exportados usan el código anónimo del paciente y el usuario operador.")
                edited = st.data_editor(
                    parsed_df,
                    num_rows="fixed",
                    use_container_width=True,
                    column_config={
                        "codigo": st.column_config.TextColumn("Variable", disabled=True),
                        "nombre": st.column_config.TextColumn("Nombre", disabled=True),
                        "categoria": st.column_config.TextColumn("Categoría", disabled=True),
                        "unidad": st.column_config.TextColumn("Unidad", disabled=True),
                        "valor": st.column_config.NumberColumn("Valor corregido", format="%.3f"),
                        "estado": st.column_config.SelectboxColumn("Estado", options=["extraído/revisar", "extraído/antropometría/revisar", "manual/antropometría", "derivado peso/talla/revisar", "derivado IMC/talla/revisar", "derivado Mosteller/revisar", "manual/revisar", "validado"]),
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
                with st.expander("Seleccionar sectores gráficos moviendo bordes", expanded=True):
                    rois = canvas_select_rois(img2, base_rois, key_prefix=f"roi_canvas_{source2}_{page2}_{preset}")
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
                    cursor_state_key = f"manual_cursor_x::{source2}::{page2}::{preset}"
                    manual_x_init = st.session_state.get(cursor_state_key, {c: float(min(max(auto[c]["x"], x0c), x1c)) for c in CURSORS})
                    manual_preview = {c: {"x": float(manual_x_init.get(c, auto[c]["x"])), "y": interp_y(ecg if c == "QRS" else dzdt, float(manual_x_init.get(c, auto[c]["x"]))) } for c in CURSORS}
                    chart_meta = build_curve_chart(dzdt, ecg, fono, auto, manual_preview, guide, x0c, x1c)
                    with st.expander("Mover cursores directamente sobre el gráfico", expanded=True):
                        adjusted_x = graph_adjust_cursors(chart_meta, manual_x_init, key_prefix=f"cursor_canvas_{source2}_{page2}_{preset}")
                    st.markdown("**Ajuste fino numérico (opcional)**")
                    cols = st.columns(5)
                    manual_x_final = {}
                    for i, cur in enumerate(CURSORS):
                        with cols[i]:
                            manual_x_final[cur] = float(st.number_input(f"Cursor {cur}", min_value=float(x0c), max_value=float(x1c), value=float(adjusted_x.get(cur, auto[cur]["x"])), step=1.0, key=f"cursor_num_{cur}_{source2}_{page2}_{preset}"))
                    st.session_state[cursor_state_key] = manual_x_final
                    manual = {}
                    for cur in CURSORS:
                        target_df = ecg if cur == "QRS" else dzdt
                        manual[cur] = {"x": float(manual_x_final[cur]), "y": interp_y(target_df, float(manual_x_final[cur]))}
                    chart = plot_curves(dzdt, ecg, fono, auto, manual, guide, x0c, x1c)
                    st.image(chart, caption="Curvas digitalizadas: arriba dZ/dt, medio ECG, abajo fonocardiograma. Los cursores corregidos pueden moverse directamente sobre el gráfico.", use_container_width=True)
                    deltas = [abs(manual[c]["x"] - auto[c]["x"]) for c in CURSORS]
                    metrics = {"error_medio_px": float(np.mean(deltas)), "puntos_dzdt": len(dzdt), "puntos_ecg": len(ecg), "puntos_fono": len(fono)}
                    conclusion = "Corrección opcional de cursores realizada con dZ/dt arriba, ECG medio y fonocardiograma abajo."
                    if st.button("Guardar corrección opcional de cursores", type="primary"):
                        cid = save_cursor_correction(user, study_id2, patient_code2, source2, int(page2), rois, auto, manual, guide, metrics, conclusion)
                        st.success(f"Corrección guardada con ID {cid}. El Excel registra automático, corregido y delta corregido por cursor.")
                    st.download_button(
                        "Descargar Excel de delta de corrección",
                        data=export_cursor_delta_excel(user, only_current_user=True),
                        file_name=f"delta_correccion_cursores_{user['username']}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            except Exception as exc:
                st.error(f"Falló el módulo opcional de curvas: {exc}")
                st.exception(exc)
        else:
            st.info("Suba un archivo o use la última hoja cargada en el módulo principal.")

    with tab3:
        st.subheader("Mis estudios guardados")
        df = studies_df(user)
        df_wide = studies_wide_df(user)
        st.caption("Vista principal: cada fila es un estudio y las columnas incluyen TODAS las variables CGI, incluidas las hemodinámicas.")
        st.dataframe(df_wide, use_container_width=True)
        st.download_button("Descargar mi Excel completo", data=export_excel(user, only_current_user=True), file_name=f"cgi_excel_completo_{user['username']}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("Descargar Excel delta corrección de cursores", data=export_cursor_delta_excel(user, only_current_user=True), file_name=f"delta_correccion_cursores_{user['username']}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

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
            all_wide = studies_wide_df(None)
            st.caption("Vista administrador: todos los usuarios, cada estudio con TODAS las variables CGI como columnas.")
            st.dataframe(all_wide, use_container_width=True)
            st.download_button("Descargar Excel administrador completo", data=export_excel(user, only_current_user=False), file_name="cgi_excel_administrador_todos_los_usuarios_COMPLETO.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            st.download_button("Descargar Excel administrador delta cursores", data=export_cursor_delta_excel(user, only_current_user=False), file_name="delta_correccion_cursores_ADMIN.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    try:
        app_main()
    except Exception as exc:
        css()
        st.error("La aplicación encontró un error controlado.")
        st.code(traceback.format_exc())
