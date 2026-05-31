# ============================================================
# APP PARA REPOSITORIO Y APRENDIZAJE EN CARDIOGRAFIA DE IMPEDANCIA
# Version simplificada para aprendices medicos
# Autor: Dr. Ricardo Daniel Olano
# ============================================================
# Funciones:
# - Registro de usuarios con matricula profesional y provincia argentina
# - Login con usuario y contraseña
# - Rol administrador
# - Carga didactica de datos hemodinamicos basicos
# - Calculo de BMI, BSA, PAM, IC, IRV, FE Capan, Ea, Ees, Ea/Ees
# - Diagnostico por dominios: flujo, funcion cardiaca, contractilidad,
#   rendimiento cardiovascular y volemia
# - Guardado de procesos por usuario en SQLite
# - Exportacion a Excel por usuario
# - Exportacion global a Excel por administrador
# ============================================================

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

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
        .block-container{max-width:1320px;padding-top:1.0rem;padding-bottom:2.5rem;}
        h1,h2,h3{color:var(--azul)!important;font-weight:800!important;}
        .hero{background:linear-gradient(90deg,#082F49,#0B4F7A);color:#fff;border-radius:18px;padding:20px 24px;margin-bottom:18px;box-shadow:0 12px 28px rgba(8,47,73,.18)}
        .hero h1{color:#fff!important;margin:0;font-size:1.55rem}.hero p{color:#E0F2FE!important;margin:4px 0 0 0}
        .card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin-bottom:14px;box-shadow:0 4px 14px rgba(15,23,42,.06)}
        .pill{display:inline-block;padding:4px 10px;border-radius:999px;font-weight:700;font-size:.82rem;margin-right:6px;}
        .pill-ok{background:#ECFDF5;color:#065F46;border:1px solid #99F6E4}.pill-warn{background:#FFF7ED;color:#9A3412;border:1px solid #FED7AA}.pill-bad{background:#FEF2F2;color:#991B1B;border:1px solid #FECACA}.pill-info{background:#EAF6FF;color:#075985;border:1px solid #BAE6FD}
        .metric-box{background:#FFFFFF;border:1px solid #D7E3EE;border-radius:14px;padding:12px 14px;margin-bottom:10px;}
        .small-muted{color:#5B6B7D!important;font-size:.90rem;}
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
    conn.commit()

    # Usuario administrador inicial. En produccion, configurar en st.secrets.
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
    if not all([username, email, nombre, matricula_tipo, matricula_numero, provincia, password]):
        return False, "Completar todos los campos."
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
            (username.strip(), email.strip().lower(), nombre.strip(), matricula_tipo, matricula_numero.strip(), provincia, "user", salt, hp, datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()
        conn.close()
        return True, "Usuario registrado correctamente. Ya puede iniciar sesión."
    except sqlite3.IntegrityError:
        return False, "El usuario o email ya existe."
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


def save_process(user_id: int, patient_code: str, fecha_estudio: str, inputs: Dict[str, Any], metrics: Dict[str, Any], domains: Dict[str, Any], conclusion: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO processes
        (user_id, created_at, patient_code, fecha_estudio, input_json, metrics_json, domains_json, conclusion)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            user_id,
            datetime.now().isoformat(timespec="seconds"),
            patient_code,
            fecha_estudio,
            json.dumps(inputs, ensure_ascii=False),
            json.dumps(metrics, ensure_ascii=False),
            json.dumps(domains, ensure_ascii=False),
            conclusion,
        )
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

# ============================================================
# CALCULOS HEMODINAMICOS SIMPLES
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
    peso = num(inputs.get("peso"))
    talla = num(inputs.get("talla"))
    pas = num(inputs.get("pas"))
    pad = num(inputs.get("pad"))
    fc = num(inputs.get("fc"))
    ds = num(inputs.get("ds"))
    co_in = num(inputs.get("co"))
    rvs = num(inputs.get("rvs"))
    cft = num(inputs.get("cft"))
    iv = num(inputs.get("iv"))
    ih = num(inputs.get("ih"))
    iac = num(inputs.get("iac"))
    itc = num(inputs.get("itc"))
    pep = num(inputs.get("pep"))
    lvet = num(inputs.get("lvet"))

    bsa = bsa_mosteller(talla, peso)
    bmi = bmi_calc(talla, peso)
    pam = map_calc(pas, pad)
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
        "CFT_kohm_inv": cft,
        "IV": iv, "IH": ih, "IAC": iac, "ITC": itc,
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

    # Dominio contractilidad por conjunto IV, IH, IAC, ITC y CTS.
    contract_scores = []
    def add_contract(v, low, high):
        if v is None:
            return
        if v < low:
            contract_scores.append(-1)
        elif v > high:
            contract_scores.append(1)
        else:
            contract_scores.append(0)
    add_contract(iv, 35, 65)
    add_contract(iac, 70, 150)
    add_contract(itc, 3.0, 5.5)
    if ih is not None:
        # IH no siempre comparte escala entre equipos. Rango didactico operativo.
        add_contract(ih, 0.15, 0.45)
    if cts is not None:
        if cts > 0.50:
            contract_scores.append(-1)
        elif cts < 0.25:
            contract_scores.append(1)
        else:
            contract_scores.append(0)
    if contract_scores:
        mean_score = float(np.mean(contract_scores))
        if mean_score <= -0.35:
            contractilidad = "contractilidad global disminuida"
        elif mean_score >= 0.35:
            contractilidad = "contractilidad global aumentada"
        elif any(s < 0 for s in contract_scores) and any(s > 0 for s in contract_scores):
            contractilidad = "contractilidad mixta o discordante"
        else:
            contractilidad = "contractilidad global conservada"
    else:
        contractilidad = "No clasificable"

    rendimiento = "No clasificable"
    if ac is not None:
        if ac < 1.0:
            rendimiento = "acoplamiento ventrículo-arterial óptimo"
        elif ac <= 1.3:
            rendimiento = "acoplamiento subóptimo"
        else:
            rendimiento = "desacoplamiento ventrículo-arterial"

    volemia = "No clasificable"
    if cft is not None:
        if cft < 41:
            volemia = "hipovolemia o bajo contenido de fluido torácico"
        elif cft <= 56:
            volemia = "normovolemia"
        else:
            volemia = "hipervolemia o aumento de fluido torácico"

    domains = {
        "Flujo": flujo_txt,
        "Función cardíaca": funcion,
        "Contractilidad": contractilidad,
        "Rendimiento cardiovascular": rendimiento,
        "Volemia": volemia,
    }

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
    unidades = {
        "Peso_kg": "kg", "Talla_cm": "cm", "BMI": "kg/m²", "BSA_m2": "m²",
        "PAS": "mmHg", "PAD": "mmHg", "PAM": "mmHg", "FC": "lpm",
        "DS_ml": "mL/lat", "IDS_ml_m2": "mL/lat/m²", "CO_VM_L_min": "L/min", "IC_L_min_m2": "L/min/m²",
        "RVS_dyn_s_cm5": "dyn·s·cm⁻5", "IRV_dyn_s_cm5_m2": "dyn·s·cm⁻5·m²",
        "CFT_kohm_inv": "kohm⁻1", "PEP_ms": "ms", "LVET_ms": "ms", "CTS_PEP_LVET": "relación",
        "FE_Capan_%": "%", "Ea": "mmHg/mL", "Ees": "mmHg/mL", "Acoplamiento_Ea_Ees": "relación",
    }
    for k, v in metrics.items():
        if v is None:
            continue
        dec = 1
        if k in ["PAS", "PAD", "PAM", "FC", "RVS_dyn_s_cm5", "IRV_dyn_s_cm5_m2", "PEP_ms", "LVET_ms", "IV", "IAC"]:
            dec = 0
        if k in ["Ea", "Ees", "Acoplamiento_Ea_Ees", "CTS_PEP_LVET"]:
            dec = 2
        rows.append({"Métrica": k, "Valor": round(float(v), dec), "Unidad": unidades.get(k, "")})
    return pd.DataFrame(rows)

# ============================================================
# EXCEL EXPORT
# ============================================================

def expand_process_df(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    resumen = []
    metricas = []
    dominios = []
    for _, r in df.iterrows():
        base = {
            "process_id": r["id"], "created_at": r["created_at"], "patient_code": r["patient_code"],
            "fecha_estudio": r["fecha_estudio"], "username": r.get("username"), "email": r.get("email"),
            "nombre_usuario": r.get("nombre"), "matricula_tipo": r.get("matricula_tipo"),
            "matricula_numero": r.get("matricula_numero"), "provincia": r.get("provincia"),
        }
        inputs = json.loads(r["input_json"])
        mets = json.loads(r["metrics_json"])
        doms = json.loads(r["domains_json"])
        resumen.append({**base, "conclusion": r["conclusion"]})
        for k, v in inputs.items():
            metricas.append({**base, "origen": "dato_ingresado", "variable": k, "valor": v})
        for k, v in mets.items():
            metricas.append({**base, "origen": "metrica_calculada", "variable": k, "valor": v})
        for k, v in doms.items():
            dominios.append({**base, "dominio": k, "interpretacion": v})
    return pd.DataFrame(resumen), pd.DataFrame(metricas), pd.DataFrame(dominios)


def to_excel_bytes(df_processes: pd.DataFrame) -> bytes:
    resumen, metricas, dominios = expand_process_df(df_processes)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        resumen.to_excel(writer, index=False, sheet_name="Procesos")
        metricas.to_excel(writer, index=False, sheet_name="Datos_y_metricas")
        dominios.to_excel(writer, index=False, sheet_name="Dominios")
        pd.DataFrame([
            {"Campo": "Descripción", "Valor": "Exportación de procesos guardados en CGI Didáctica"},
            {"Campo": "Fecha_exportacion", "Valor": datetime.now().isoformat(timespec="seconds")},
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
        u = st.text_input("Usuario o email", key="login_user")
        p = st.text_input("Contraseña", type="password", key="login_pass")
        if st.button("Ingresar", key="btn_login"):
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
        if st.button("Crear usuario", key="btn_register"):
            ok, msg = create_user(username, email, nombre, matricula_tipo, matricula_numero, provincia, password)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

# ============================================================
# APP PRINCIPAL
# ============================================================

user = st.session_state.get("user")
if user is None:
    login_register_ui()
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

st.markdown("<div class='card'>Esta versión está diseñada para aprendizaje: muestra los cálculos esenciales, clasifica por dominios y guarda cada proceso bajo el usuario que lo realiza.</div>", unsafe_allow_html=True)

menu = st.sidebar.radio("Módulo", ["Nuevo proceso", "Mis procesos", "Exportar mis procesos", "Administrador"] if user["role"] == "admin" else ["Nuevo proceso", "Mis procesos", "Exportar mis procesos"])

if menu == "Nuevo proceso":
    st.header("Nuevo proceso didáctico CGI")
    st.caption("Los campos con datos faltantes quedan como no clasificables. Complete solo lo disponible.")
    with st.form("form_proceso"):
        st.subheader("1. Identificación del caso")
        c1, c2, c3 = st.columns(3)
        with c1:
            patient_code = st.text_input("Código del paciente / iniciales", value="CASO-001")
        with c2:
            fecha_estudio = st.date_input("Fecha del estudio", value=date.today())
        with c3:
            contexto = st.selectbox("Contexto", ["Basal / acostado", "Control evolutivo", "Entrenamiento", "Otro"])

        st.subheader("2. Antropometría y presión arterial")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            peso = st.number_input("Peso (kg)", min_value=20.0, max_value=250.0, value=80.0, step=0.5)
        with c2:
            talla = st.number_input("Talla (cm)", min_value=100.0, max_value=230.0, value=170.0, step=1.0)
        with c3:
            pas = st.number_input("PAS (mmHg)", min_value=70.0, max_value=260.0, value=130.0, step=1.0)
        with c4:
            pad = st.number_input("PAD (mmHg)", min_value=35.0, max_value=160.0, value=80.0, step=1.0)

        st.subheader("3. Flujo y resistencias")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            fc = st.number_input("FC (lpm)", min_value=30.0, max_value=180.0, value=75.0, step=1.0)
        with c2:
            ds = st.number_input("DS / Volumen sistólico (mL)", min_value=10.0, max_value=200.0, value=60.0, step=0.5)
        with c3:
            co = st.number_input("VM/CO si viene del equipo (L/min, opcional)", min_value=0.0, max_value=20.0, value=0.0, step=0.1)
        with c4:
            rvs = st.number_input("RVS (dyn·s·cm⁻5)", min_value=0.0, max_value=5000.0, value=1400.0, step=10.0)

        st.subheader("4. Contractilidad, volemia y tiempos")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            cft = st.number_input("CFT/TFC", min_value=0.0, max_value=120.0, value=49.0, step=0.1)
        with c2:
            iv = st.number_input("IV", min_value=0.0, max_value=200.0, value=46.0, step=1.0)
        with c3:
            iac = st.number_input("IAC", min_value=0.0, max_value=300.0, value=74.0, step=1.0)
        with c4:
            itc = st.number_input("ITC", min_value=0.0, max_value=15.0, value=3.0, step=0.1)
        c1, c2, c3 = st.columns(3)
        with c1:
            ih = st.number_input("IH / Heather", min_value=0.0, max_value=10.0, value=0.30, step=0.01)
        with c2:
            pep = st.number_input("PEP / PPE (ms)", min_value=20.0, max_value=250.0, value=80.0, step=1.0)
        with c3:
            lvet = st.number_input("LVET / PE (ms)", min_value=100.0, max_value=600.0, value=280.0, step=1.0)

        submitted = st.form_submit_button("Calcular y mostrar interpretación")

    if submitted:
        inputs = {
            "patient_code": patient_code, "fecha_estudio": str(fecha_estudio), "contexto": contexto,
            "peso": peso, "talla": talla, "pas": pas, "pad": pad, "fc": fc, "ds": ds,
            "co": None if co == 0 else co, "rvs": None if rvs == 0 else rvs,
            "cft": None if cft == 0 else cft, "iv": None if iv == 0 else iv,
            "ih": None if ih == 0 else ih, "iac": None if iac == 0 else iac,
            "itc": None if itc == 0 else itc, "pep": pep, "lvet": lvet,
        }
        metrics, domains, conclusion = calcular_todo(inputs)
        st.session_state["last_inputs"] = inputs
        st.session_state["last_metrics"] = metrics
        st.session_state["last_domains"] = domains
        st.session_state["last_conclusion"] = conclusion
        st.session_state["last_patient_code"] = patient_code
        st.session_state["last_fecha_estudio"] = str(fecha_estudio)

    if "last_metrics" in st.session_state:
        metrics = st.session_state["last_metrics"]
        domains = st.session_state["last_domains"]
        conclusion = st.session_state["last_conclusion"]
        st.subheader("Métricas calculadas")
        st.dataframe(df_metricas(metrics), use_container_width=True)
        st.subheader("Diagnóstico por dominios")
        cols = st.columns(5)
        for col, (dom, txt) in zip(cols, domains.items()):
            with col:
                st.markdown(f"<div class='metric-box'><b>{dom}</b><br><span class='small-muted'>{txt}</span></div>", unsafe_allow_html=True)
        st.subheader("Conclusión didáctica")
        st.info(conclusion)
        if st.button("Guardar proceso en mi usuario"):
            pid = save_process(
                int(user["id"]),
                st.session_state.get("last_patient_code", ""),
                st.session_state.get("last_fecha_estudio", ""),
                st.session_state["last_inputs"],
                metrics,
                domains,
                conclusion,
            )
            st.success(f"Proceso guardado con ID {pid}.")

elif menu == "Mis procesos":
    st.header("Mis procesos guardados")
    dfp = load_processes(int(user["id"]))
    if dfp.empty:
        st.info("Todavía no hay procesos guardados.")
    else:
        show = dfp[["id", "created_at", "patient_code", "fecha_estudio", "conclusion"]].copy()
        st.dataframe(show, use_container_width=True)

elif menu == "Exportar mis procesos":
    st.header("Exportar mis procesos a Excel")
    dfp = load_processes(int(user["id"]))
    if dfp.empty:
        st.info("No hay procesos para exportar.")
    else:
        xls = to_excel_bytes(dfp)
        st.download_button(
            "Descargar Excel de mis procesos",
            data=xls,
            file_name=f"procesos_cgi_{user['username']}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.dataframe(dfp[["id", "created_at", "patient_code", "fecha_estudio", "conclusion"]], use_container_width=True)

elif menu == "Administrador":
    if user["role"] != "admin":
        st.error("Acceso restringido.")
        st.stop()
    st.header("Panel administrador")
    st.caption("Permite exportar todos los procesos de todos los usuarios.")
    df_all = load_processes(None)
    if df_all.empty:
        st.info("No hay procesos guardados por usuarios.")
    else:
        st.subheader("Procesos de todos los usuarios")
        st.dataframe(df_all[["id", "created_at", "username", "nombre", "matricula_tipo", "matricula_numero", "provincia", "patient_code", "fecha_estudio", "conclusion"]], use_container_width=True)
        xls = to_excel_bytes(df_all)
        st.download_button(
            "Descargar Excel global de todos los usuarios",
            data=xls,
            file_name=f"procesos_cgi_todos_los_usuarios_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
