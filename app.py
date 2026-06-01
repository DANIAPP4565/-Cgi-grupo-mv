# ============================================================
# APP PARA REPOSITORIO Y APRENDIZAJE EN CARDIOGRAFIA DE IMPEDANCIA
# Grupo de trabajo de Mecanica Vascular 2026
# Rediseño integrado:
# - Usuarios, login, registro, roles y administrador
# - Procesos hemodinamicos didacticos previos
# - Digitalizacion de curva de impedancia desde PDF original del estudio o imagen real
# - Deteccion automatica de cursores B, C, X, Y
# - Correccion manual por medico/aprendiz
# - Aprendizaje incremental por usuario y global mediante deltas auto-manual
# - Exportacion Excel por usuario y global
# - Registro corregido con st.form y validacion campo por campo
# ============================================================

from __future__ import annotations

import hashlib
import io
import json
import secrets
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

# Evita que Streamlit muestre objetos internos DeltaGenerator por “magic commands”.
try:
    st.set_option("runner.magicEnabled", False)
except Exception:
    pass
from PIL import Image, ImageOps

try:
    import fitz  # PyMuPDF: renderiza PDF a imagen para digitalizar la curva original
except Exception:  # pragma: no cover
    fitz = None

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None

# ============================================================
# CONFIGURACION GENERAL
# ============================================================

st.set_page_config(
    page_title="Repositorio y aprendizaje en Cardiografía de Impedancia",
    page_icon="🫀",
    layout="wide",
)

APP_TITLE = "App para Repositorio y aprendizaje en Cardiografía de Impedancia"
APP_SUBTITLE = "Grupo de trabajo de Mecánica Vascular 2026"
DB_PATH = Path("cgi_didactica_usuarios.sqlite3")

PROVINCIAS_ARGENTINA = [
    "Ciudad Autónoma de Buenos Aires", "Buenos Aires", "Catamarca", "Chaco", "Chubut",
    "Córdoba", "Corrientes", "Entre Ríos", "Formosa", "Jujuy", "La Pampa",
    "La Rioja", "Mendoza", "Misiones", "Neuquén", "Río Negro", "Salta",
    "San Juan", "San Luis", "Santa Cruz", "Santa Fe", "Santiago del Estero",
    "Tierra del Fuego", "Tucumán"
]

CURSOR_NAMES = ["B", "C", "X", "Y"]
CURSOR_CLINICAL_HELP = {
    "B": "Apertura valvular aórtica / inicio del ascenso sistólico de dZ/dt. Se ubica en el pie de la onda antes del pico C.",
    "C": "Pico sistólico principal de dZ/dt. Representa la máxima velocidad de cambio de impedancia asociada al flujo aórtico.",
    "X": "Nadir sistólico posterior al pico C. Punto de cierre/fin del componente sistólico principal.",
    "Y": "Onda diastólica o rebote posterior al punto X, si está visible. Ayuda a caracterizar morfología diastólica.",
}

# ============================================================
# ESTILO
# ============================================================

def aplicar_estilos() -> None:
    st.markdown(
        """
        <style>
        :root{
            --azul:#082F49; --azul2:#0B4F7A; --celeste:#EAF6FF; --line:#D7E3EE;
            --txt:#102033; --muted:#5B6B7D; --ok:#047857; --warn:#B45309; --bad:#B91C1C;
        }
        .stApp{background:linear-gradient(180deg,#F2F7FB,#FFFFFF)!important;}
        .block-container{max-width:1420px;padding-top:1.0rem;padding-bottom:2.5rem;}
        h1,h2,h3{color:var(--azul)!important;font-weight:800!important;}
        .hero{background:linear-gradient(90deg,#082F49,#0B4F7A);color:#fff;border-radius:18px;padding:20px 24px;margin-bottom:18px;box-shadow:0 12px 28px rgba(8,47,73,.18)}
        .hero h1{color:#fff!important;margin:0;font-size:1.55rem}.hero p{color:#E0F2FE!important;margin:4px 0 0 0}
        .card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin-bottom:14px;box-shadow:0 4px 14px rgba(15,23,42,.06)}
        .metric-box{background:#FFFFFF;border:1px solid #D7E3EE;border-radius:14px;padding:12px 14px;margin-bottom:10px;}
        .small-muted{color:#5B6B7D!important;font-size:.90rem;}
        .okbox{background:#ECFDF5;border:1px solid #99F6E4;border-radius:14px;padding:12px;color:#064E3B;}
        .warnbox{background:#FFF7ED;border:1px solid #FED7AA;border-radius:14px;padding:12px;color:#7C2D12;}
        .badbox{background:#FEF2F2;border:1px solid #FECACA;border-radius:14px;padding:12px;color:#7F1D1D;}
        .infobox{background:#EAF6FF;border:1px solid #BAE6FD;border-radius:14px;padding:12px;color:#075985;}
        .stButton>button,.stDownloadButton>button{background:#0B4F7A!important;color:#fff!important;border-radius:10px!important;border:1px solid #082F49!important;font-weight:800!important;}
        .stButton>button *,.stDownloadButton>button *{color:#fff!important;}
        </style>
        """,
        unsafe_allow_html=True,
    )

aplicar_estilos()
st.markdown(f"<div class='hero'><h1>{APP_TITLE}</h1><p>{APP_SUBTITLE}</p></div>", unsafe_allow_html=True)

# ============================================================
# SEGURIDAD Y BASE DE DATOS
# ============================================================

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 150_000)
    return salt, digest.hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    _, candidate = hash_password(password, salt)
    return secrets.compare_digest(candidate, password_hash)


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            matricula_tipo TEXT NOT NULL,
            matricula_numero TEXT NOT NULL,
            provincia TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            patient_code TEXT,
            fecha_estudio TEXT,
            input_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            domains_json TEXT NOT NULL,
            conclusion TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS curve_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            patient_code TEXT,
            fecha_estudio TEXT,
            contexto TEXT,
            image_name TEXT,
            image_width INTEGER,
            image_height INTEGER,
            roi_json TEXT NOT NULL,
            auto_json TEXT NOT NULL,
            manual_json TEXT NOT NULL,
            deltas_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            conclusion TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cursor_learning (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            curve_session_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            cursor TEXT NOT NULL,
            auto_x REAL NOT NULL,
            manual_x REAL NOT NULL,
            delta_x REAL NOT NULL,
            delta_x_fraction REAL NOT NULL,
            auto_y REAL,
            manual_y REAL,
            delta_y REAL,
            quality_score REAL,
            FOREIGN KEY(curve_session_id) REFERENCES curve_sessions(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()

    admin_user = st.secrets.get("ADMIN_USER", "admin") if hasattr(st, "secrets") else "admin"
    admin_email = st.secrets.get("ADMIN_EMAIL", "admin@cgi.local") if hasattr(st, "secrets") else "admin@cgi.local"
    admin_pass = st.secrets.get("ADMIN_PASSWORD", "admin123") if hasattr(st, "secrets") else "admin123"
    cur.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
    if cur.fetchone() is None:
        salt, hp = hash_password(admin_pass)
        cur.execute(
            """
            INSERT OR IGNORE INTO users
            (username,email,nombre,matricula_tipo,matricula_numero,provincia,role,salt,password_hash,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (admin_user, admin_email, "Administrador", "Nacional", "ADMIN", "Buenos Aires", "admin", salt, hp, datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()
    conn.close()


init_db()


def create_user(username: str, email: str, nombre: str, matricula_tipo: str, matricula_numero: str, provincia: str, password: str) -> Tuple[bool, str]:
    """Crea un usuario con validación robusta.

    Corrige el falso mensaje "Completar todos los campos" cuando Streamlit entrega
    valores con espacios, selectbox no inicializado o claves de estado inconsistentes.
    """
    username = (username or "").strip()
    email = (email or "").strip().lower()
    nombre = (nombre or "").strip()
    matricula_tipo = (matricula_tipo or "").strip()
    matricula_numero = (matricula_numero or "").strip()
    provincia = (provincia or "").strip()
    password = password or ""

    faltantes = []
    if not username:
        faltantes.append("Usuario")
    if not email:
        faltantes.append("Email")
    if not nombre:
        faltantes.append("Nombre y apellido")
    if not matricula_tipo:
        faltantes.append("Tipo de matrícula")
    if not matricula_numero:
        faltantes.append("Número de matrícula")
    if not provincia:
        faltantes.append("Provincia")
    if not password:
        faltantes.append("Contraseña")

    if faltantes:
        return False, "Faltan completar: " + ", ".join(faltantes) + "."
    if "@" not in email or "." not in email.split("@")[-1]:
        return False, "Ingrese un email válido."
    if len(password) < 6:
        return False, "La contraseña debe tener al menos 6 caracteres."

    salt, hp = hash_password(password)
    try:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO users
            (username,email,nombre,matricula_tipo,matricula_numero,provincia,role,salt,password_hash,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (username, email, nombre, matricula_tipo, matricula_numero, provincia, "user", salt, hp, datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()
        conn.close()
        return True, "Usuario registrado correctamente. Ya puede iniciar sesión."
    except sqlite3.IntegrityError:
        return False, "El usuario o email ya existe. Use otro usuario/email o ingrese con su cuenta."
    except Exception as e:
        return False, f"No se pudo registrar: {e}"

def authenticate(username_or_email: str, password: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM users WHERE username=? OR email=?",
        (username_or_email.strip(), username_or_email.strip().lower())
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    if verify_password(password, row["salt"], row["password_hash"]):
        return dict(row)
    return None

# ============================================================
# UTILIDADES DB Y EXPORTACION
# ============================================================

def save_process(user_id: int, patient_code: str, fecha_estudio: str, inputs: Dict[str, Any], metrics: Dict[str, Any], domains: Dict[str, Any], conclusion: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO processes
        (user_id, created_at, patient_code, fecha_estudio, input_json, metrics_json, domains_json, conclusion)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (user_id, datetime.now().isoformat(timespec="seconds"), patient_code, fecha_estudio,
         json.dumps(inputs, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False),
         json.dumps(domains, ensure_ascii=False), conclusion)
    )
    conn.commit()
    new_id = int(cur.lastrowid)
    conn.close()
    return new_id


def load_processes(user_id: Optional[int] = None) -> pd.DataFrame:
    conn = get_conn()
    if user_id is None:
        q = """
        SELECT p.*, u.username, u.email, u.nombre, u.matricula_tipo, u.matricula_numero, u.provincia
        FROM processes p JOIN users u ON p.user_id=u.id
        ORDER BY p.created_at DESC
        """
        df = pd.read_sql_query(q, conn)
    else:
        q = """
        SELECT p.*, u.username, u.email, u.nombre, u.matricula_tipo, u.matricula_numero, u.provincia
        FROM processes p JOIN users u ON p.user_id=u.id
        WHERE p.user_id=? ORDER BY p.created_at DESC
        """
        df = pd.read_sql_query(q, conn, params=(user_id,))
    conn.close()
    return df


def save_curve_session(
    user_id: int,
    patient_code: str,
    fecha_estudio: str,
    contexto: str,
    image_name: str,
    image_size: Tuple[int, int],
    roi: Dict[str, Any],
    auto: Dict[str, Any],
    manual: Dict[str, Any],
    metrics: Dict[str, Any],
    conclusion: str,
) -> int:
    x_span = max(1.0, float(roi.get("x_max", image_size[0]) - roi.get("x_min", 0)))
    deltas: Dict[str, Dict[str, float]] = {}
    for cur in CURSOR_NAMES:
        ax = float(auto[cur]["x"])
        ay = float(auto[cur]["y"])
        mx = float(manual[cur]["x"])
        my = float(manual[cur]["y"])
        deltas[cur] = {
            "auto_x": ax, "manual_x": mx, "delta_x": mx - ax, "delta_x_fraction": (mx - ax) / x_span,
            "auto_y": ay, "manual_y": my, "delta_y": my - ay,
        }
    conn = get_conn()
    curdb = conn.cursor()
    curdb.execute(
        """
        INSERT INTO curve_sessions
        (user_id, created_at, patient_code, fecha_estudio, contexto, image_name, image_width, image_height,
         roi_json, auto_json, manual_json, deltas_json, metrics_json, conclusion)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            user_id, datetime.now().isoformat(timespec="seconds"), patient_code, fecha_estudio, contexto,
            image_name, image_size[0], image_size[1], json.dumps(roi, ensure_ascii=False),
            json.dumps(auto, ensure_ascii=False), json.dumps(manual, ensure_ascii=False),
            json.dumps(deltas, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False), conclusion,
        )
    )
    sid = int(curdb.lastrowid)
    for cname, d in deltas.items():
        curdb.execute(
            """
            INSERT INTO cursor_learning
            (curve_session_id, user_id, created_at, cursor, auto_x, manual_x, delta_x, delta_x_fraction, auto_y, manual_y, delta_y, quality_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sid, user_id, datetime.now().isoformat(timespec="seconds"), cname,
                d["auto_x"], d["manual_x"], d["delta_x"], d["delta_x_fraction"],
                d["auto_y"], d["manual_y"], d["delta_y"], metrics.get("quality_score"),
            )
        )
    conn.commit()
    conn.close()
    return sid


def load_curve_sessions(user_id: Optional[int] = None) -> pd.DataFrame:
    conn = get_conn()
    if user_id is None:
        q = """
        SELECT c.*, u.username, u.email, u.nombre, u.matricula_tipo, u.matricula_numero, u.provincia
        FROM curve_sessions c JOIN users u ON c.user_id=u.id
        ORDER BY c.created_at DESC
        """
        df = pd.read_sql_query(q, conn)
    else:
        q = """
        SELECT c.*, u.username, u.email, u.nombre, u.matricula_tipo, u.matricula_numero, u.provincia
        FROM curve_sessions c JOIN users u ON c.user_id=u.id
        WHERE c.user_id=? ORDER BY c.created_at DESC
        """
        df = pd.read_sql_query(q, conn, params=(user_id,))
    conn.close()
    return df


def load_learning(user_id: Optional[int] = None) -> pd.DataFrame:
    conn = get_conn()
    if user_id is None:
        q = """
        SELECT l.*, u.username, u.nombre, u.matricula_numero, c.patient_code, c.fecha_estudio, c.contexto
        FROM cursor_learning l
        JOIN users u ON l.user_id=u.id
        JOIN curve_sessions c ON l.curve_session_id=c.id
        ORDER BY l.created_at DESC
        """
        df = pd.read_sql_query(q, conn)
    else:
        q = """
        SELECT l.*, u.username, u.nombre, u.matricula_numero, c.patient_code, c.fecha_estudio, c.contexto
        FROM cursor_learning l
        JOIN users u ON l.user_id=u.id
        JOIN curve_sessions c ON l.curve_session_id=c.id
        WHERE l.user_id=? ORDER BY l.created_at DESC
        """
        df = pd.read_sql_query(q, conn, params=(user_id,))
    conn.close()
    return df


def learning_offsets(user_id: Optional[int] = None, use_global: bool = True) -> Dict[str, float]:
    df = load_learning(None if use_global else user_id)
    offsets = {c: 0.0 for c in CURSOR_NAMES}
    if df.empty:
        return offsets
    for c in CURSOR_NAMES:
        vals = pd.to_numeric(df.loc[df["cursor"] == c, "delta_x_fraction"], errors="coerce").dropna()
        if len(vals) >= 3:
            offsets[c] = float(vals.median())
        elif len(vals) > 0:
            offsets[c] = float(vals.mean())
    return offsets

# ============================================================
# CALCULOS HEMODINAMICOS DIDACTICOS PREVIOS
# ============================================================

def num(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def bsa_mosteller(talla_cm: Optional[float], peso_kg: Optional[float]) -> Optional[float]:
    if talla_cm and peso_kg and talla_cm > 0 and peso_kg > 0:
        return float(np.sqrt((talla_cm * peso_kg) / 3600.0))
    return None


def bmi_calc(talla_cm: Optional[float], peso_kg: Optional[float]) -> Optional[float]:
    if talla_cm and peso_kg and talla_cm > 0:
        return float(peso_kg / ((talla_cm / 100.0) ** 2))
    return None


def map_calc(pas: Optional[float], pad: Optional[float]) -> Optional[float]:
    if pas is not None and pad is not None:
        return float((pas + 2 * pad) / 3.0)
    return None


def fe_capan(pep_ms: Optional[float], lvet_ms: Optional[float]) -> Optional[float]:
    if pep_ms and lvet_ms and lvet_ms > 0:
        fe = 0.84 - 0.64 * (pep_ms / lvet_ms)
        return float(fe * 100.0)
    return None


def ea_calc(pas: Optional[float], sv_ml: Optional[float]) -> Optional[float]:
    if pas and sv_ml and sv_ml > 0:
        return float((pas * 0.9) / sv_ml)
    return None


def end_avg_chen(tnd: Optional[float]) -> Optional[float]:
    if tnd is None or not (0.05 <= tnd <= 0.60):
        return None
    t = tnd
    return float(0.35695 - 7.2266*t + 74.249*t**2 - 307.39*t**3 + 684.54*t**4 - 856.92*t**5 + 571.95*t**6 - 159.1*t**7)


def ees_chen(pas: Optional[float], pad: Optional[float], sv_ml: Optional[float], pep_ms: Optional[float], lvet_ms: Optional[float]) -> Optional[float]:
    if None in [pas, pad, sv_ml, pep_ms, lvet_ms] or sv_ml <= 0 or lvet_ms <= 0:
        return None
    ef = fe_capan(pep_ms, lvet_ms)
    if ef is None:
        return None
    ef_frac = ef / 100.0
    pes = pas * 0.9
    tnd = pep_ms / (pep_ms + lvet_ms)
    end_avg = end_avg_chen(tnd)
    if end_avg is None or pes <= 0:
        return None
    end_est = 0.0275 - 0.165*ef_frac + 0.3656*(pad / pes) + 0.515*end_avg
    if end_est <= 0:
        return None
    ees = (pad - end_est * pes) / (end_est * sv_ml)
    return float(ees) if ees > 0 else None


def classify_range(value: Optional[float], low: float, high: float, low_label: str, normal_label: str, high_label: str) -> str:
    if value is None:
        return "No clasificable"
    if value < low:
        return low_label
    if value > high:
        return high_label
    return normal_label


def calcular_todo(inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    peso = num(inputs.get("peso")); talla = num(inputs.get("talla")); pas = num(inputs.get("pas")); pad = num(inputs.get("pad"))
    fc = num(inputs.get("fc")); ds = num(inputs.get("ds")); co_in = num(inputs.get("co")); rvs = num(inputs.get("rvs"))
    cft = num(inputs.get("cft")); iv = num(inputs.get("iv")); ih = num(inputs.get("ih")); iac = num(inputs.get("iac")); itc = num(inputs.get("itc"))
    pep = num(inputs.get("pep")); lvet = num(inputs.get("lvet"))

    bsa = bsa_mosteller(talla, peso); bmi = bmi_calc(talla, peso); pam = map_calc(pas, pad)
    co = co_in if co_in is not None else (ds * fc / 1000.0 if ds and fc else None)
    ic = co / bsa if co and bsa else None
    ids = ds / bsa if ds and bsa else None
    irv = rvs * bsa if rvs and bsa else None
    fe = fe_capan(pep, lvet)
    cts = pep / lvet if pep and lvet and lvet > 0 else None
    ea = ea_calc(pas, ds)
    ees = ees_chen(pas, pad, ds, pep, lvet)
    ac = ea / ees if ea and ees and ees > 0 else None

    metrics = {
        "Peso_kg": peso, "Talla_cm": talla, "BMI": bmi, "BSA_m2": bsa,
        "PAS": pas, "PAD": pad, "PAM": pam, "FC": fc,
        "DS_ml": ds, "IDS_ml_m2": ids, "CO_VM_L_min": co, "IC_L_min_m2": ic,
        "RVS_dyn_s_cm5": rvs, "IRV_dyn_s_cm5_m2": irv,
        "CFT_kohm_inv": cft, "IV": iv, "IH": ih, "IAC": iac, "ITC": itc,
        "PEP_ms": pep, "LVET_ms": lvet, "CTS_PEP_LVET": cts,
        "FE_Capan_%": fe, "Ea": ea, "Ees": ees, "Acoplamiento_Ea_Ees": ac,
    }

    flujo = []
    if ic is not None:
        flujo.append(classify_range(ic, 2.5, 4.2, "bajo flujo", "flujo conservado", "alto flujo"))
    if ids is not None:
        flujo.append(classify_range(ids, 30, 50, "IDS bajo", "IDS conservado", "IDS alto"))
    if irv is not None:
        flujo.append(classify_range(irv, 1300, 2500, "IRV baja", "IRV normal", "IRV elevada"))
    flujo_txt = "; ".join(flujo) if flujo else "No clasificable"

    funcion = "No clasificable"
    if fe is not None:
        funcion = "función cardíaca disminuida" if fe < 50 else "función cardíaca conservada"

    contract_scores: List[int] = []
    def add_contract(v: Optional[float], low: float, high: float) -> None:
        if v is None:
            return
        if v < low:
            contract_scores.append(-1)
        elif v > high:
            contract_scores.append(1)
        else:
            contract_scores.append(0)
    add_contract(iv, 35, 65); add_contract(iac, 70, 150); add_contract(itc, 3.0, 5.5)
    if ih is not None: add_contract(ih, 0.15, 0.45)
    if cts is not None:
        contract_scores.append(-1 if cts > 0.50 else (1 if cts < 0.25 else 0))
    if contract_scores:
        mean_score = float(np.mean(contract_scores))
        if mean_score <= -0.35: contractilidad = "contractilidad global disminuida"
        elif mean_score >= 0.35: contractilidad = "contractilidad global aumentada"
        elif any(s < 0 for s in contract_scores) and any(s > 0 for s in contract_scores): contractilidad = "contractilidad mixta o discordante"
        else: contractilidad = "contractilidad global conservada"
    else:
        contractilidad = "No clasificable"

    rendimiento = "No clasificable"
    if ac is not None:
        rendimiento = "acoplamiento ventrículo-arterial óptimo" if ac < 1.0 else ("acoplamiento subóptimo" if ac <= 1.3 else "desacoplamiento ventrículo-arterial")
    volemia = "No clasificable"
    if cft is not None:
        volemia = "hipovolemia o bajo contenido de fluido torácico" if cft < 41 else ("normovolemia" if cft <= 56 else "hipervolemia o aumento de fluido torácico")

    domains = {"Flujo": flujo_txt, "Función cardíaca": funcion, "Contractilidad": contractilidad, "Rendimiento cardiovascular": rendimiento, "Volemia": volemia}
    conclusion = (
        f"El análisis didáctico por dominios muestra: {flujo_txt}. "
        f"La función cardíaca se clasifica como {funcion}. "
        f"El dominio contractilidad, integrado por IV, IH, IAC, ITC y relación PEP/LVET, sugiere {contractilidad}. "
        f"El rendimiento cardiovascular corresponde a {rendimiento}. "
        f"El dominio de volemia sugiere {volemia}."
    )
    return metrics, domains, conclusion


def df_metricas(metrics: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    unidades = {"Peso_kg":"kg", "Talla_cm":"cm", "BMI":"kg/m²", "BSA_m2":"m²", "PAS":"mmHg", "PAD":"mmHg", "PAM":"mmHg", "FC":"lpm", "DS_ml":"mL/lat", "IDS_ml_m2":"mL/lat/m²", "CO_VM_L_min":"L/min", "IC_L_min_m2":"L/min/m²", "RVS_dyn_s_cm5":"dyn·s·cm⁻5", "IRV_dyn_s_cm5_m2":"dyn·s·cm⁻5·m²", "CFT_kohm_inv":"kohm⁻1", "PEP_ms":"ms", "LVET_ms":"ms", "CTS_PEP_LVET":"relación", "FE_Capan_%":"%", "Ea":"mmHg/mL", "Ees":"mmHg/mL", "Acoplamiento_Ea_Ees":"relación"}
    for k, v in metrics.items():
        if v is None: continue
        dec = 1
        if k in ["PAS", "PAD", "PAM", "FC", "RVS_dyn_s_cm5", "IRV_dyn_s_cm5_m2", "PEP_ms", "LVET_ms", "IV", "IAC"]: dec = 0
        if k in ["Ea", "Ees", "Acoplamiento_Ea_Ees", "CTS_PEP_LVET"]: dec = 2
        rows.append({"Métrica": k, "Valor": round(float(v), dec), "Unidad": unidades.get(k, "")})
    return pd.DataFrame(rows)

# ============================================================
# DIGITALIZACION DE CURVA CGI Y CURSORES
# ============================================================

def smooth_signal(y: np.ndarray, window: int = 9) -> np.ndarray:
    if len(y) < 5:
        return y.astype(float)
    window = int(max(3, window))
    if window % 2 == 0:
        window += 1
    window = min(window, len(y) if len(y) % 2 == 1 else len(y) - 1)
    if window < 3:
        return y.astype(float)
    kernel = np.ones(window) / window
    pad = window // 2
    yy = np.pad(y.astype(float), (pad, pad), mode="edge")
    return np.convolve(yy, kernel, mode="valid")



def render_pdf_page_to_image(pdf_bytes: bytes, page_index: int = 0, zoom: float = 2.5) -> Image.Image:
    """Convierte una página del PDF original del estudio en imagen RGB para recorte/digitalización.

    Permite que el usuario cargue directamente el PDF descargado del equipo, como en el flujo previo.
    """
    if fitz is None:
        raise RuntimeError("Falta PyMuPDF. Agregue `PyMuPDF` al requirements.txt para cargar estudios en PDF.")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if doc.page_count == 0:
            raise ValueError("El PDF no contiene páginas renderizables.")
        page_index = max(0, min(int(page_index), doc.page_count - 1))
        page = doc.load_page(page_index)
        matrix = fitz.Matrix(float(zoom), float(zoom))
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return img
    finally:
        doc.close()


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Devuelve la cantidad de páginas del PDF original del estudio."""
    if fitz is None:
        raise RuntimeError("Falta PyMuPDF. Agregue `PyMuPDF` al requirements.txt para cargar estudios en PDF.")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return int(doc.page_count)
    finally:
        doc.close()

def digitize_curve_from_image(
    img: Image.Image,
    roi: Dict[str, int],
    threshold_percentile: int = 35,
    invert_y: bool = True,
    min_pixels_per_column: int = 1,
    method: str = "seguimiento_continuo",
    start_y_fraction: float = 0.35,
    max_jump_px: int = 18,
    max_pixels_per_column_fraction: float = 0.35,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Digitaliza la curva seleccionada dentro del ROI.

    Cambio clave v6:
    - Ya no usa la mediana de todos los píxeles oscuros por columna como método principal,
      porque eso mezcla grilla, texto y otras señales.
    - El modo `seguimiento_continuo` sigue un único trazo, comenzando cerca de la banda vertical
      elegida por el usuario. Esto permite digitalizar solo el sector dZ/dt superior señalado.
    """
    gray = ImageOps.grayscale(img)
    arr = np.array(gray)
    h, w = arr.shape
    x0 = max(0, min(int(roi["x_min"]), w - 1)); x1 = max(x0 + 2, min(int(roi["x_max"]), w))
    y0 = max(0, min(int(roi["y_min"]), h - 1)); y1 = max(y0 + 2, min(int(roi["y_max"]), h))
    crop = arr[y0:y1, x0:x1]
    crop_h, crop_w = crop.shape

    # Detecta tinta oscura/coloreada. El percentil bajo evita incorporar grilla clara.
    thr = np.percentile(crop, threshold_percentile)
    mask = crop <= thr

    xs: List[int] = []
    ys: List[float] = []

    if method == "mediana_columna":
        # Método previo: se mantiene como alternativa, pero puede mezclar varias curvas si el ROI es amplio.
        for cx in range(mask.shape[1]):
            rows = np.where(mask[:, cx])[0]
            if len(rows) >= min_pixels_per_column:
                xs.append(x0 + cx)
                ys.append(float(np.median(rows) + y0))
    else:
        # Método nuevo: seguimiento de un único trazo continuo.
        max_dense = max(8, int(crop_h * float(max_pixels_per_column_fraction)))
        target_y = int(np.clip(float(start_y_fraction), 0.0, 1.0) * max(1, crop_h - 1))
        last_y: Optional[float] = None

        # Primer anclaje: columna con candidato cercano a la banda inicial.
        start_col = None
        start_row = None
        for cx in range(mask.shape[1]):
            rows = np.where(mask[:, cx])[0]
            if len(rows) < min_pixels_per_column or len(rows) > max_dense:
                continue
            r = float(rows[np.argmin(np.abs(rows - target_y))])
            start_col = cx; start_row = r
            break

        if start_col is None:
            raise ValueError("No se encontró un punto inicial de curva. Ajuste el recorte vertical o aumente la sensibilidad.")

        # Recorre hacia la derecha siguiendo el candidato más cercano al punto anterior.
        last_y = start_row
        for cx in range(start_col, mask.shape[1]):
            rows = np.where(mask[:, cx])[0]
            if len(rows) < min_pixels_per_column or len(rows) > max_dense:
                continue
            # Elimina candidatos demasiado alejados del trazo anterior. Evita saltos hacia grilla/textos/otras señales.
            diffs = np.abs(rows.astype(float) - float(last_y))
            k = int(np.argmin(diffs))
            if float(diffs[k]) <= float(max_jump_px):
                r = float(rows[k])
                xs.append(x0 + cx)
                ys.append(r + y0)
                last_y = r
            else:
                # Permite pequeños huecos, pero no cambia de señal.
                continue

        # Si el primer punto no empezó en el borde izquierdo, recorre hacia la izquierda desde el anclaje.
        left_xs: List[int] = []
        left_ys: List[float] = []
        last_y = start_row
        for cx in range(start_col - 1, -1, -1):
            rows = np.where(mask[:, cx])[0]
            if len(rows) < min_pixels_per_column or len(rows) > max_dense:
                continue
            diffs = np.abs(rows.astype(float) - float(last_y))
            k = int(np.argmin(diffs))
            if float(diffs[k]) <= float(max_jump_px):
                r = float(rows[k])
                left_xs.append(x0 + cx)
                left_ys.append(r + y0)
                last_y = r
        if left_xs:
            xs = list(reversed(left_xs)) + xs
            ys = list(reversed(left_ys)) + ys

    if len(xs) < 12:
        raise ValueError("No se pudo digitalizar una curva suficiente. Ajuste el recorte al sector dZ/dt superior señalado, aumente sensibilidad o aumente el salto máximo permitido.")

    x = np.array(xs, dtype=float)
    y_pix = np.array(ys, dtype=float)

    # Ordena y elimina duplicados por columna para evitar artefactos.
    order = np.argsort(x)
    x = x[order]; y_pix = y_pix[order]
    df_tmp = pd.DataFrame({"x_pixel": x, "y_pixel": y_pix}).groupby("x_pixel", as_index=False)["y_pixel"].median()
    x = df_tmp["x_pixel"].to_numpy(dtype=float)
    y_pix = df_tmp["y_pixel"].to_numpy(dtype=float)

    y_s = smooth_signal(y_pix, window=max(5, int(len(y_pix) * 0.012)))
    amp = (y1 - y_s) if invert_y else (y_s - y0)
    amp = amp - np.nanmin(amp)
    if np.nanmax(amp) > 0:
        amp = amp / np.nanmax(amp)

    df = pd.DataFrame({"x_pixel": x, "y_pixel": y_pix, "y_smooth_pixel": y_s, "amplitude_norm": amp})
    quality = {
        "points_detected": int(len(df)),
        "coverage_fraction": float((df["x_pixel"].max() - df["x_pixel"].min()) / max(1, (x1 - x0))),
        "threshold_value": float(thr),
        "method": method,
        "roi_width": int(x1 - x0),
        "roi_height": int(y1 - y0),
    }
    return df, quality


def nearest_curve_y(curve_df: pd.DataFrame, x_value: float) -> float:
    idx = int(np.argmin(np.abs(curve_df["x_pixel"].to_numpy() - x_value)))
    return float(curve_df.iloc[idx]["y_smooth_pixel"])


def detect_cursors(curve_df: pd.DataFrame, roi: Dict[str, int], offsets: Optional[Dict[str, float]] = None) -> Dict[str, Dict[str, float]]:
    offsets = offsets or {c: 0.0 for c in CURSOR_NAMES}
    x = curve_df["x_pixel"].to_numpy(dtype=float)
    y = curve_df["amplitude_norm"].to_numpy(dtype=float)
    y_s = smooth_signal(y, max(5, int(len(y) * 0.025)))
    n = len(y_s)
    x_span = max(1.0, float(roi["x_max"] - roi["x_min"]))

    c_idx = int(np.argmax(y_s))
    left0 = max(0, int(c_idx * 0.05))
    left = y_s[left0:max(c_idx, left0 + 2)]
    if len(left) >= 4:
        dy = np.gradient(left)
        # B: mayor aceleracion/pendiente previa al pico C, desplazado apenas hacia la izquierda del ascenso.
        b_rel = int(np.argmax(dy))
        b_idx = min(max(left0 + b_rel - 2, 0), c_idx)
    else:
        b_idx = max(0, int(c_idx * 0.55))

    post_c = y_s[c_idx + 1:]
    if len(post_c) >= 6:
        x_rel = int(np.argmin(post_c))
        x_idx = c_idx + 1 + x_rel
    else:
        x_idx = min(n - 1, c_idx + max(1, n // 5))

    post_x = y_s[x_idx + 1:]
    if len(post_x) >= 6:
        y_rel = int(np.argmax(post_x))
        y_idx = x_idx + 1 + y_rel
    else:
        y_idx = min(n - 1, x_idx + max(1, n // 8))

    raw = {"B": b_idx, "C": c_idx, "X": x_idx, "Y": y_idx}
    result: Dict[str, Dict[str, float]] = {}
    for cname, idx in raw.items():
        learned_x = float(x[idx] + offsets.get(cname, 0.0) * x_span)
        learned_x = min(max(learned_x, float(x.min())), float(x.max()))
        result[cname] = {"x": learned_x, "y": nearest_curve_y(curve_df, learned_x), "index": float(idx)}
    return result


def cursor_metrics(auto: Dict[str, Any], manual: Dict[str, Any], roi: Dict[str, int]) -> Dict[str, Any]:
    x_span = max(1.0, float(roi["x_max"] - roi["x_min"]))
    rows = []
    errors = []
    for c in CURSOR_NAMES:
        dx = float(manual[c]["x"] - auto[c]["x"])
        dy = float(manual[c]["y"] - auto[c]["y"])
        err = abs(dx) / x_span * 100.0
        errors.append(err)
        rows.append({"Cursor": c, "Auto_x": auto[c]["x"], "Manual_x": manual[c]["x"], "Delta_x_px": dx, "Error_x_%_ROI": err, "Delta_y_px": dy})
    mae = float(np.mean(errors)) if errors else None
    quality_score = None if mae is None else max(0.0, 100.0 - mae)
    return {"rows": rows, "mae_x_percent_roi": mae, "quality_score": quality_score}


def interpret_curve_learning(metrics: Dict[str, Any]) -> str:
    mae = metrics.get("mae_x_percent_roi")
    q = metrics.get("quality_score")
    if mae is None:
        return "No fue posible estimar la calidad de la colocación automática de cursores."
    if mae <= 3:
        nivel = "muy buena concordancia"
    elif mae <= 7:
        nivel = "concordancia aceptable"
    elif mae <= 12:
        nivel = "concordancia moderada, con necesidad de mayor entrenamiento"
    else:
        nivel = "baja concordancia inicial; se recomienda seguir acumulando correcciones manuales"
    return (
        f"La comparación entre cursores automáticos y cursores corregidos manualmente muestra {nivel}. "
        f"El error absoluto medio horizontal fue {mae:.1f}% del ancho útil de la curva y el puntaje operativo de calidad fue {q:.1f}/100. "
        "Cada corrección guardada se incorpora a la tabla de aprendizaje como delta auto-manual, permitiendo que la app ajuste progresivamente la posición esperada de B, C, X e Y."
    )


def plot_curve_with_cursors(curve_df: pd.DataFrame, auto: Dict[str, Any], manual: Optional[Dict[str, Any]] = None, title: str = "Curva digitalizada") -> Optional[io.BytesIO]:
    if plt is None:
        return None
    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(curve_df["x_pixel"], curve_df["amplitude_norm"], linewidth=2, label="Curva CGI digitalizada")
    for cname in CURSOR_NAMES:
        ax.axvline(auto[cname]["x"], linestyle="--", linewidth=1.5, label=f"{cname} auto")
        ax.text(auto[cname]["x"], 1.02, f"{cname}A", rotation=90, va="bottom", ha="center", fontsize=9)
    if manual:
        for cname in CURSOR_NAMES:
            ax.axvline(manual[cname]["x"], linestyle=":", linewidth=2.0, label=f"{cname} manual")
            ax.text(manual[cname]["x"], -0.07, f"{cname}M", rotation=90, va="top", ha="center", fontsize=9)
    ax.set_title(title)
    ax.set_xlabel("Tiempo relativo / píxel horizontal")
    ax.set_ylabel("Amplitud normalizada")
    ax.set_ylim(-0.12, 1.12)
    ax.grid(True, alpha=0.25)
    handles, labels = ax.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    ax.legend(unique.values(), unique.keys(), loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=8)
    fig.tight_layout()
    bio = io.BytesIO()
    fig.savefig(bio, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    bio.seek(0)
    return bio

# ============================================================
# EXCEL EXPORT
# ============================================================

def expand_process_df(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    resumen = []; metricas = []; dominios = []
    for _, r in df.iterrows():
        base = {"process_id": r["id"], "created_at": r["created_at"], "patient_code": r["patient_code"], "fecha_estudio": r["fecha_estudio"], "username": r.get("username"), "email": r.get("email"), "nombre_usuario": r.get("nombre"), "matricula_tipo": r.get("matricula_tipo"), "matricula_numero": r.get("matricula_numero"), "provincia": r.get("provincia")}
        inputs = json.loads(r["input_json"]); mets = json.loads(r["metrics_json"]); doms = json.loads(r["domains_json"])
        resumen.append({**base, "conclusion": r["conclusion"]})
        for k, v in inputs.items(): metricas.append({**base, "origen": "dato_ingresado", "variable": k, "valor": v})
        for k, v in mets.items(): metricas.append({**base, "origen": "metrica_calculada", "variable": k, "valor": v})
        for k, v in doms.items(): dominios.append({**base, "dominio": k, "interpretacion": v})
    return pd.DataFrame(resumen), pd.DataFrame(metricas), pd.DataFrame(dominios)


def expand_curve_df(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    sesiones = []; cursores = []; metricas = []
    for _, r in df.iterrows():
        base = {"curve_session_id": r["id"], "created_at": r["created_at"], "patient_code": r["patient_code"], "fecha_estudio": r["fecha_estudio"], "contexto": r["contexto"], "image_name": r["image_name"], "username": r.get("username"), "email": r.get("email"), "nombre_usuario": r.get("nombre"), "matricula_tipo": r.get("matricula_tipo"), "matricula_numero": r.get("matricula_numero"), "provincia": r.get("provincia")}
        roi = json.loads(r["roi_json"]); auto = json.loads(r["auto_json"]); manual = json.loads(r["manual_json"]); deltas = json.loads(r["deltas_json"]); mets = json.loads(r["metrics_json"])
        sesiones.append({**base, **{f"roi_{k}": v for k, v in roi.items()}, "conclusion": r["conclusion"]})
        for c in CURSOR_NAMES:
            cursores.append({**base, "cursor": c, **{f"auto_{k}": v for k, v in auto[c].items()}, **{f"manual_{k}": v for k, v in manual[c].items()}, **deltas[c]})
        for k, v in mets.items():
            if k != "rows": metricas.append({**base, "variable": k, "valor": v})
    return pd.DataFrame(sesiones), pd.DataFrame(cursores), pd.DataFrame(metricas)


def to_excel_bytes(df_processes: pd.DataFrame, df_curves: Optional[pd.DataFrame] = None, df_learning: Optional[pd.DataFrame] = None) -> bytes:
    resumen, metricas, dominios = expand_process_df(df_processes)
    curve_ses, curve_cur, curve_met = expand_curve_df(df_curves if df_curves is not None else pd.DataFrame())
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        resumen.to_excel(writer, index=False, sheet_name="Procesos_hemodinamicos")
        metricas.to_excel(writer, index=False, sheet_name="Datos_y_metricas")
        dominios.to_excel(writer, index=False, sheet_name="Dominios")
        curve_ses.to_excel(writer, index=False, sheet_name="Curvas_sesiones")
        curve_cur.to_excel(writer, index=False, sheet_name="Curvas_cursores")
        curve_met.to_excel(writer, index=False, sheet_name="Curvas_metricas")
        if df_learning is not None and not df_learning.empty:
            df_learning.to_excel(writer, index=False, sheet_name="Aprendizaje_deltas")
        pd.DataFrame([
            {"Campo":"Descripción", "Valor":"Exportación de procesos y aprendizaje en cardiografía de impedancia"},
            {"Campo":"Fecha_exportacion", "Valor":datetime.now().isoformat(timespec="seconds")},
            {"Campo":"Uso", "Valor":"La hoja Aprendizaje_deltas conserva la diferencia entre cursor automático y cursor corregido manualmente."},
        ]).to_excel(writer, index=False, sheet_name="README")
    return bio.getvalue()

# ============================================================
# AUTENTICACION UI
# ============================================================

if "user" not in st.session_state:
    st.session_state["user"] = None


def login_register_ui() -> None:
    tab_login, tab_reg = st.tabs(["Ingresar", "Registrarse"])

    with tab_login:
        st.subheader("Ingreso de usuario")
        with st.form("login_form", clear_on_submit=False):
            u = st.text_input("Usuario o email", key="login_user")
            p = st.text_input("Contraseña", type="password", key="login_pass")
            login_submit = st.form_submit_button("Ingresar")
        if login_submit:
            user = authenticate(u, p)
            if user:
                st.session_state["user"] = user
                st.success("Ingreso correcto.")
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos.")
        st.info("Administrador inicial si no configuró secretos: usuario `admin`, contraseña `admin123`. Cambiar en producción.")

    with tab_reg:
        st.subheader("Registro de aprendiz médico")
        st.caption("Todos los campos son obligatorios. La validación ahora informa exactamente qué campo falta.")
        with st.form("registro_usuario_form", clear_on_submit=False):
            c1, c2 = st.columns(2)
            with c1:
                username = st.text_input("Usuario", key="reg_user")
                email = st.text_input("Email", key="reg_email")
                nombre = st.text_input("Nombre y apellido", key="reg_nombre")
            with c2:
                matricula_tipo = st.selectbox("Tipo de matrícula", ["Provincial", "Nacional"], key="reg_mat_tipo")
                matricula_numero = st.text_input("Número de matrícula", key="reg_mat_num")
                provincia = st.selectbox("Provincia de Argentina", PROVINCIAS_ARGENTINA, key="reg_prov")
            password = st.text_input("Contraseña", type="password", key="reg_pass")
            submitted_register = st.form_submit_button("Crear usuario")

        if submitted_register:
            ok, msg = create_user(username, email, nombre, matricula_tipo, matricula_numero, provincia, password)
            if ok:
                st.success(msg)
                st.info("Ahora vaya a la pestaña Ingresar y acceda con su usuario o email.")
            else:
                st.error(msg)
                with st.expander("Verificación de campos recibidos", expanded=False):
                    st.write({
                        "Usuario": bool((username or "").strip()),
                        "Email": bool((email or "").strip()),
                        "Nombre": bool((nombre or "").strip()),
                        "Tipo de matrícula": bool((matricula_tipo or "").strip()),
                        "Número de matrícula": bool((matricula_numero or "").strip()),
                        "Provincia": bool((provincia or "").strip()),
                        "Contraseña": bool(password),
                    })
    return None

def render_login_register() -> None:
    """Renderiza login/registro sin devolver objetos visibles a Streamlit."""
    login_register_ui()
    return None

# ============================================================
# UI MODULOS
# ============================================================

def ui_nuevo_proceso(user: Dict[str, Any]) -> None:
    st.header("Nuevo proceso didáctico CGI")
    st.caption("Conserva el módulo previo de carga manual de variables, cálculo de métricas y diagnóstico por dominios.")
    with st.form("form_proceso"):
        st.subheader("1. Identificación del caso")
        c1, c2, c3 = st.columns(3)
        with c1: patient_code = st.text_input("Código del paciente / iniciales", value="CASO-001")
        with c2: fecha_estudio = st.date_input("Fecha del estudio", value=date.today())
        with c3: contexto = st.selectbox("Contexto", ["Basal / acostado", "Control evolutivo", "Entrenamiento", "Otro"])
        st.subheader("2. Antropometría y presión arterial")
        c1, c2, c3, c4 = st.columns(4)
        with c1: peso = st.number_input("Peso (kg)", min_value=20.0, max_value=250.0, value=80.0, step=0.5)
        with c2: talla = st.number_input("Talla (cm)", min_value=100.0, max_value=230.0, value=170.0, step=1.0)
        with c3: pas = st.number_input("PAS (mmHg)", min_value=70.0, max_value=260.0, value=130.0, step=1.0)
        with c4: pad = st.number_input("PAD (mmHg)", min_value=35.0, max_value=160.0, value=80.0, step=1.0)
        st.subheader("3. Flujo y resistencias")
        c1, c2, c3, c4 = st.columns(4)
        with c1: fc = st.number_input("FC (lpm)", min_value=30.0, max_value=180.0, value=75.0, step=1.0)
        with c2: ds = st.number_input("DS / Volumen sistólico (mL)", min_value=10.0, max_value=200.0, value=60.0, step=0.5)
        with c3: co = st.number_input("VM/CO si viene del equipo (L/min, opcional)", min_value=0.0, max_value=20.0, value=0.0, step=0.1)
        with c4: rvs = st.number_input("RVS (dyn·s·cm⁻5)", min_value=0.0, max_value=5000.0, value=1400.0, step=10.0)
        st.subheader("4. Contractilidad, volemia y tiempos")
        c1, c2, c3, c4 = st.columns(4)
        with c1: cft = st.number_input("CFT/TFC", min_value=0.0, max_value=120.0, value=49.0, step=0.1)
        with c2: iv = st.number_input("IV", min_value=0.0, max_value=200.0, value=46.0, step=1.0)
        with c3: iac = st.number_input("IAC", min_value=0.0, max_value=300.0, value=74.0, step=1.0)
        with c4: itc = st.number_input("ITC", min_value=0.0, max_value=15.0, value=3.0, step=0.1)
        c1, c2, c3 = st.columns(3)
        with c1: ih = st.number_input("IH / Heather", min_value=0.0, max_value=10.0, value=0.30, step=0.01)
        with c2: pep = st.number_input("PEP / PPE (ms)", min_value=20.0, max_value=250.0, value=80.0, step=1.0)
        with c3: lvet = st.number_input("LVET / PE (ms)", min_value=100.0, max_value=600.0, value=280.0, step=1.0)
        submitted = st.form_submit_button("Calcular y mostrar interpretación")
    if submitted:
        inputs = {"patient_code": patient_code, "fecha_estudio": str(fecha_estudio), "contexto": contexto, "peso": peso, "talla": talla, "pas": pas, "pad": pad, "fc": fc, "ds": ds, "co": None if co == 0 else co, "rvs": None if rvs == 0 else rvs, "cft": None if cft == 0 else cft, "iv": None if iv == 0 else iv, "ih": None if ih == 0 else ih, "iac": None if iac == 0 else iac, "itc": None if itc == 0 else itc, "pep": pep, "lvet": lvet}
        metrics, domains, conclusion = calcular_todo(inputs)
        st.session_state["last_inputs"] = inputs; st.session_state["last_metrics"] = metrics; st.session_state["last_domains"] = domains; st.session_state["last_conclusion"] = conclusion; st.session_state["last_patient_code"] = patient_code; st.session_state["last_fecha_estudio"] = str(fecha_estudio)
    if "last_metrics" in st.session_state:
        metrics = st.session_state["last_metrics"]; domains = st.session_state["last_domains"]; conclusion = st.session_state["last_conclusion"]
        st.subheader("Métricas calculadas")
        st.dataframe(df_metricas(metrics), use_container_width=True)
        st.subheader("Diagnóstico por dominios")
        cols = st.columns(5)
        for col, (dom, txt) in zip(cols, domains.items()):
            with col: st.markdown(f"<div class='metric-box'><b>{dom}</b><br><span class='small-muted'>{txt}</span></div>", unsafe_allow_html=True)
        st.subheader("Conclusión didáctica")
        st.info(conclusion)
        if st.button("Guardar proceso en mi usuario"):
            pid = save_process(int(user["id"]), st.session_state.get("last_patient_code", ""), st.session_state.get("last_fecha_estudio", ""), st.session_state["last_inputs"], metrics, domains, conclusion)
            st.success(f"Proceso guardado con ID {pid}.")


def ui_digitalizacion(user: Dict[str, Any]) -> None:
    st.header("Digitalizar curva de impedancia desde PDF del estudio y entrenar cursores")
    st.markdown("<div class='card'>Flujo recuperado: el usuario carga el <b>PDF original descargado del estudio</b> —o una imagen si solo tiene captura— → selecciona la página donde está la curva de impedancia → recorta la zona útil → la app digitaliza la señal → coloca B, C, X e Y automáticamente → el médico corrige → se guardan los deltas para aprendizaje.</div>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1.1, 1, 1])
    with c1: patient_code = st.text_input("Código del paciente / estudio", value="CURVA-001")
    with c2: fecha_estudio = st.date_input("Fecha del estudio", value=date.today(), key="fecha_curva")
    with c3: contexto = st.selectbox("Condición", ["Basal / acostado / cinta", "Spot", "Parado", "Control evolutivo", "Entrenamiento"], key="contexto_curva")

    uploaded = st.file_uploader(
        "Cargar PDF original del estudio CGI o imagen de la curva",
        type=["pdf", "png", "jpg", "jpeg"],
        help="Preferido: PDF descargado del equipo con la curva de impedancia. Alternativa: imagen/captura PNG o JPG."
    )
    use_global_learning = st.checkbox("Aplicar aprendizaje acumulado global para ajustar cursores automáticos", value=True)
    if uploaded is None:
        st.info("Cargue el PDF original del estudio donde se vea la curva dZ/dt. También puede cargar una imagen/captura si no dispone del PDF.")
        return

    uploaded_bytes = uploaded.getvalue()
    file_ext = Path(uploaded.name).suffix.lower()
    source_label = uploaded.name

    if file_ext == ".pdf":
        if fitz is None:
            st.error("Para cargar estudios en PDF falta instalar PyMuPDF. Agregue `PyMuPDF` al requirements.txt y reinicie la app.")
            return
        try:
            n_pages = count_pdf_pages(uploaded_bytes)
        except Exception as e:
            st.error(f"No se pudo leer el PDF: {e}")
            return
        st.markdown("<div class='infobox'><b>PDF cargado correctamente.</b> Seleccione la página donde se observa la curva de impedancia. En estudios de 4 páginas suele estar en la página de trazados/curvas.</div>", unsafe_allow_html=True)
        cpdf1, cpdf2 = st.columns([1, 1])
        with cpdf1:
            page_number = st.number_input("Página del PDF a usar", min_value=1, max_value=max(1, n_pages), value=min(2, max(1, n_pages)), step=1)
        with cpdf2:
            zoom_pdf = st.slider("Resolución de renderizado del PDF", 1.5, 4.0, 2.5, 0.25, help="Más resolución mejora la digitalización, pero puede hacer más pesada la app.")
        try:
            img = render_pdf_page_to_image(uploaded_bytes, int(page_number) - 1, float(zoom_pdf)).convert("RGB")
            source_label = f"{uploaded.name} | página {int(page_number)}"
        except Exception as e:
            st.error(f"No se pudo convertir la página del PDF a imagen: {e}")
            return
    else:
        try:
            img = Image.open(io.BytesIO(uploaded_bytes)).convert("RGB")
        except Exception as e:
            st.error(f"No se pudo abrir la imagen: {e}")
            return

    w, h = img.size
    st.image(img, caption=f"Fuente cargada: {source_label} ({w} x {h} px)", use_container_width=True)

    with st.expander("1. Recorte de región útil de la curva", expanded=True):
        st.caption("IMPORTANTE: recorte solo el sector de la señal dZ/dt superior señalado. No incluya el texto de mediciones de la derecha ni las señales inferiores, porque alteran la digitalización.")
        st.markdown("<div class='infobox'><b>Modo recomendado:</b> dejar dentro del recorte solo la curva superior dZ/dt. Luego usar el método <b>seguimiento continuo de trazo</b>.</div>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        with c1: x_min = st.slider("X mínimo del sector dZ/dt", 0, max(1, w - 2), int(w * 0.02))
        with c2: x_max = st.slider("X máximo del sector dZ/dt", min(x_min + 2, w), w, int(w * 0.72))
        with c3: y_min = st.slider("Y mínimo del sector dZ/dt", 0, max(1, h - 2), int(h * 0.06))
        with c4: y_max = st.slider("Y máximo del sector dZ/dt", min(y_min + 2, h), h, int(h * 0.42))

        roi_preview = img.crop((int(x_min), int(y_min), int(x_max), int(y_max)))
        st.image(roi_preview, caption="Vista previa: este debe ser SOLO el sector de curva dZ/dt a digitalizar", use_container_width=True)

        c1, c2, c3 = st.columns(3)
        with c1:
            threshold_percentile = st.slider("Sensibilidad de detección del trazo", 3, 70, 22, help="Más bajo evita grilla/textos. Si no detecta la curva, subir gradualmente.")
        with c2:
            invert_y = st.checkbox("Invertir eje Y para que el ascenso sea positivo", value=True)
        with c3:
            min_pixels_col = st.slider("Píxeles mínimos por columna", 1, 8, 1)

        c1, c2, c3 = st.columns(3)
        with c1:
            method_label = st.selectbox("Método de digitalización", ["Seguimiento continuo del trazo seleccionado", "Mediana por columna (método previo)"], index=0)
        with c2:
            start_y_fraction = st.slider("Altura inicial esperada del trazo", 0.05, 0.95, 0.38, 0.01, help="0=arriba del recorte, 1=abajo. Ubicar cerca del inicio de la curva superior.")
        with c3:
            max_jump_px = st.slider("Salto máximo permitido entre puntos", 4, 80, 22, help="Aumentar si la curva tiene ascensos/descensos bruscos; bajar si salta a otra señal.")
        max_dense_fraction = st.slider("Excluir columnas demasiado densas por grilla/texto", 0.10, 0.90, 0.35, 0.05)

    roi = {"x_min": int(x_min), "x_max": int(x_max), "y_min": int(y_min), "y_max": int(y_max), "threshold_percentile": int(threshold_percentile), "invert_y": bool(invert_y)}
    method = "seguimiento_continuo" if method_label.startswith("Seguimiento") else "mediana_columna"

    try:
        curve_df, q = digitize_curve_from_image(
            img, roi, threshold_percentile, invert_y, min_pixels_col,
            method=method,
            start_y_fraction=float(start_y_fraction),
            max_jump_px=int(max_jump_px),
            max_pixels_per_column_fraction=float(max_dense_fraction),
        )
        offsets = learning_offsets(int(user["id"]), use_global=use_global_learning)
        auto = detect_cursors(curve_df, roi, offsets=offsets)
    except Exception as e:
        st.error(f"No se pudo digitalizar la curva: {e}")
        return

    st.success(f"Curva digitalizada: {q['points_detected']} puntos detectados, cobertura {q['coverage_fraction']*100:.1f}% del ancho recortado.")
    if any(abs(v) > 0 for v in offsets.values()):
        st.markdown("<div class='infobox'><b>Aprendizaje aplicado:</b> se ajustó la posición automática según la mediana histórica de correcciones auto-manual.</div>", unsafe_allow_html=True)

    plot_auto = plot_curve_with_cursors(curve_df, auto, None, "Cursores automáticos sobre curva digitalizada")
    if plot_auto:
        st.image(plot_auto, use_container_width=True)
    else:
        st.line_chart(curve_df.set_index("x_pixel")[["amplitude_norm"]])

    with st.expander("2. Corrección manual de cursores B, C, X e Y", expanded=True):
        st.caption("Mueva cada cursor hasta el punto validado por criterio médico. La coordenada Y se toma automáticamente sobre la curva suavizada.")
        manual: Dict[str, Dict[str, float]] = {}
        minx = int(curve_df["x_pixel"].min()); maxx = int(curve_df["x_pixel"].max())
        for cname in CURSOR_NAMES:
            st.markdown(f"**Cursor {cname}** — {CURSOR_CLINICAL_HELP[cname]}")
            default_x = int(round(auto[cname]["x"]))
            x_val = st.slider(f"Posición manual {cname}", minx, maxx, min(max(default_x, minx), maxx), key=f"manual_{cname}")
            manual[cname] = {"x": float(x_val), "y": nearest_curve_y(curve_df, float(x_val))}

    metrics = cursor_metrics(auto, manual, roi)
    conclusion = interpret_curve_learning(metrics)
    df_err = pd.DataFrame(metrics["rows"])
    st.subheader("Comparación automático vs corrección manual")
    st.dataframe(df_err.round(2), use_container_width=True)
    st.info(conclusion)

    plot_manual = plot_curve_with_cursors(curve_df, auto, manual, "Cursores automáticos y cursores corregidos manualmente")
    if plot_manual:
        st.image(plot_manual, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Guardar curva, corrección manual y aprendizaje", type="primary"):
            sid = save_curve_session(int(user["id"]), patient_code, str(fecha_estudio), contexto, source_label, (w, h), roi, auto, manual, {**metrics, **q, "learning_offsets_applied": offsets, "source_file": uploaded.name}, conclusion)
            st.success(f"Curva guardada con ID {sid}. Los deltas B, C, X e Y quedaron incorporados al aprendizaje.")
    with c2:
        out_df = pd.DataFrame({"x_pixel": curve_df["x_pixel"], "y_pixel": curve_df["y_pixel"], "amplitude_norm": curve_df["amplitude_norm"]})
        csv = out_df.to_csv(index=False).encode("utf-8")
        st.download_button("Descargar curva digitalizada CSV", data=csv, file_name=f"curva_digitalizada_{patient_code}.csv", mime="text/csv")


def ui_mis_procesos(user: Dict[str, Any]) -> None:
    st.header("Mis procesos guardados")
    tab1, tab2, tab3 = st.tabs(["Procesos hemodinámicos", "Curvas digitalizadas", "Aprendizaje de cursores"])
    with tab1:
        dfp = load_processes(int(user["id"]))
        if dfp.empty: st.info("Todavía no hay procesos hemodinámicos guardados.")
        else: st.dataframe(dfp[["id", "created_at", "patient_code", "fecha_estudio", "conclusion"]], use_container_width=True)
    with tab2:
        dfc = load_curve_sessions(int(user["id"]))
        if dfc.empty: st.info("Todavía no hay curvas digitalizadas guardadas.")
        else: st.dataframe(dfc[["id", "created_at", "patient_code", "fecha_estudio", "contexto", "image_name", "conclusion"]], use_container_width=True)
    with tab3:
        dfl = load_learning(int(user["id"]))
        if dfl.empty: st.info("Todavía no hay correcciones manuales guardadas para aprendizaje.")
        else:
            st.dataframe(dfl, use_container_width=True)
            resumen = dfl.groupby("cursor")["delta_x_fraction"].agg(["count", "mean", "median", "std"]).reset_index()
            st.subheader("Resumen de aprendizaje por cursor")
            st.dataframe(resumen, use_container_width=True)


def ui_exportar(user: Dict[str, Any]) -> None:
    st.header("Exportar mis procesos a Excel")
    dfp = load_processes(int(user["id"])); dfc = load_curve_sessions(int(user["id"])); dfl = load_learning(int(user["id"]))
    if dfp.empty and dfc.empty:
        st.info("No hay procesos ni curvas para exportar.")
        return
    xls = to_excel_bytes(dfp, dfc, dfl)
    st.download_button("Descargar Excel integral de mis procesos", data=xls, file_name=f"cgi_repositorio_{user['username']}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("Incluye procesos hemodinámicos, curvas digitalizadas, cursores automáticos/manuales y deltas de aprendizaje.")


def ui_admin(user: Dict[str, Any]) -> None:
    if user["role"] != "admin":
        st.error("Acceso restringido.")
        return
    st.header("Panel administrador")
    tab1, tab2, tab3 = st.tabs(["Todos los procesos", "Todas las curvas", "Aprendizaje global"])
    df_all = load_processes(None); df_curves = load_curve_sessions(None); df_learning = load_learning(None)
    with tab1:
        if df_all.empty: st.info("No hay procesos guardados.")
        else: st.dataframe(df_all[["id", "created_at", "username", "nombre", "matricula_tipo", "matricula_numero", "provincia", "patient_code", "fecha_estudio", "conclusion"]], use_container_width=True)
    with tab2:
        if df_curves.empty: st.info("No hay curvas guardadas.")
        else: st.dataframe(df_curves[["id", "created_at", "username", "nombre", "patient_code", "fecha_estudio", "contexto", "image_name", "conclusion"]], use_container_width=True)
    with tab3:
        if df_learning.empty: st.info("No hay deltas de aprendizaje guardados.")
        else:
            st.dataframe(df_learning, use_container_width=True)
            st.subheader("Modelo operativo actual: offset mediano por cursor")
            resumen = df_learning.groupby("cursor")["delta_x_fraction"].agg(["count", "mean", "median", "std"]).reset_index()
            st.dataframe(resumen, use_container_width=True)
    if not (df_all.empty and df_curves.empty):
        xls = to_excel_bytes(df_all, df_curves, df_learning)
        st.download_button("Descargar Excel global de todos los usuarios", data=xls, file_name=f"cgi_global_todos_los_usuarios_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ============================================================
# APP PRINCIPAL
# ============================================================

user = st.session_state.get("user")
if user is None:
    render_login_register()
    st.stop()

with st.sidebar:
    st.markdown("### Sesión")
    st.write(f"**{user['nombre']}**")
    st.write(f"Usuario: `{user['username']}`")
    st.write(f"Rol: `{user['role']}`")
    st.write(f"Matrícula: {user['matricula_tipo']} {user['matricula_numero']}")
    st.write(f"Provincia: {user['provincia']}")
    if st.button("Cerrar sesión"):
        st.session_state["user"] = None
        st.rerun()

st.markdown("<div class='card'>La app conserva el ingreso por usuarios y suma el módulo de digitalización de curva CGI para corrección de cursores y aprendizaje progresivo por deltas auto-manual.</div>", unsafe_allow_html=True)

items = ["Digitalizar curva y entrenar", "Nuevo proceso hemodinámico", "Mis procesos", "Exportar mis procesos"]
if user["role"] == "admin":
    items.append("Administrador")
menu = st.sidebar.radio("Módulo", items)

if menu == "Digitalizar curva y entrenar":
    ui_digitalizacion(user)
elif menu == "Nuevo proceso hemodinámico":
    ui_nuevo_proceso(user)
elif menu == "Mis procesos":
    ui_mis_procesos(user)
elif menu == "Exportar mis procesos":
    ui_exportar(user)
elif menu == "Administrador":
    ui_admin(user)
