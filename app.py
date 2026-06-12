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


def admin_exists() -> bool:
    con = connect()
    row = con.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND active=1").fetchone()
    con.close()
    return bool(row and int(row["n"]) > 0)


def generate_password(length: int = 16) -> str:
    """Genera una clave fuerte, legible y no almacenada en texto plano."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%&*"
    return "".join(secrets.choice(alphabet) for _ in range(max(12, int(length))))


def normalize_setup_token(token: str) -> str:
    """Permite CREAR-ADMIN, CREAR ADMIN o crear_admin sin errores de guion/espacio."""
    return re.sub(r"[^A-Z0-9]+", "", str(token or "").upper())


def setup_token_ok(typed_token: str, configured_token: str) -> bool:
    """Valida token de instalación tolerando guiones, espacios y mayúsculas/minúsculas."""
    cfg = normalize_setup_token(configured_token)
    typed = normalize_setup_token(typed_token)
    if not cfg:
        return True
    return bool(typed and secrets.compare_digest(typed, cfg))


def create_admin_user(username: str, password: str, full_name: str = "Administrador") -> Tuple[bool, str]:
    username = (username or "").strip()
    if len(username) < 3 or len(password) < 8:
        return False, "El usuario debe tener al menos 3 caracteres y la clave de administrador al menos 8."
    try:
        con = connect()
        con.execute(
            "INSERT INTO users(username,password_hash,full_name,matricula,provincia,role,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (username, hash_password(password), full_name.strip() or "Administrador", "ADMIN", "", "admin", 1, now_iso()),
        )
        con.commit()
        con.close()
        return True, "Administrador creado. La clave solo se mostró una vez."
    except sqlite3.IntegrityError:
        con = connect()
        row = con.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
        if not row:
            con.close()
            return False, "No se pudo crear el administrador."
        con.execute(
            "UPDATE users SET password_hash=?, role='admin', active=1 WHERE id=?",
            (hash_password(password), int(row["id"])),
        )
        con.commit()
        con.close()
        return True, "Usuario existente convertido en administrador. La clave solo se mostró una vez."


def set_user_password(username: str, password: str) -> Tuple[bool, str]:
    username = (username or "").strip()
    if len(password) < 8:
        return False, "La clave debe tener al menos 8 caracteres."
    con = connect()
    row = con.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
    if not row:
        con.close()
        return False, "Usuario no encontrado."
    con.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), int(row["id"])))
    con.commit()
    con.close()
    return True, "Clave actualizada. No queda visible para otros usuarios."


def set_user_role(username: str, role: str, active: int = 1) -> Tuple[bool, str]:
    username = (username or "").strip()
    role = "admin" if role == "admin" else "user"
    con = connect()
    row = con.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
    if not row:
        con.close()
        return False, "Usuario no encontrado."
    con.execute("UPDATE users SET role=?, active=? WHERE id=?", (role, int(active), int(row["id"])))
    con.commit()
    con.close()
    return True, f"Usuario actualizado como {role}."


def admin_password_ui(current_user: dict) -> None:
    """Panel exclusivo del administrador para crear o redefinir claves.
    Las claves se guardan hasheadas; no se listan ni se exportan.
    """
    st.markdown("<div class='guide'><b>Seguridad:</b> las claves se guardan con hash PBKDF2. Los usuarios comunes no ven este panel y nunca se exporta ninguna contraseña.</div>", unsafe_allow_html=True)

    con = connect()
    users_df = pd.read_sql_query("SELECT username, full_name, role, active FROM users ORDER BY username", con)
    con.close()
    all_users = users_df["username"].tolist() if not users_df.empty else []
    admin_users = users_df.loc[users_df["role"].eq("admin"), "username"].tolist() if not users_df.empty else []
    default_admin = current_user.get("username", "") if current_user.get("username", "") in all_users else (admin_users[0] if admin_users else "")

    st.subheader("Clave de administrador")
    tgen, tmanual, tnew = st.tabs(["Generar clave segura", "Definir clave manual", "Crear/convertir administrador"])

    with tgen:
        target = st.selectbox("Administrador a modificar", admin_users or [default_admin], index=0, key="admin_pwd_target_generate")
        length = st.slider("Longitud", 12, 28, 16, 1, key="admin_pwd_len")
        st.caption("La clave se mostrará una sola vez en esta pantalla del administrador. Guárdela en un gestor de claves.")
        if st.button("Generar nueva clave de administrador", type="primary", key="btn_generate_admin_pwd"):
            new_pass = generate_password(int(length))
            ok, msg = set_user_password(target, new_pass)
            if ok:
                st.success(msg)
                st.code(new_pass, language=None)
                st.session_state["admin_password_shown_once_at"] = now_iso()
            else:
                st.error(msg)

    with tmanual:
        target2 = st.selectbox("Usuario administrador", admin_users or [default_admin], index=0, key="admin_pwd_target_manual")
        p1 = st.text_input("Nueva clave", type="password", key="admin_pwd_manual_1")
        p2 = st.text_input("Repetir nueva clave", type="password", key="admin_pwd_manual_2")
        if st.button("Guardar clave manual", type="primary", key="btn_manual_admin_pwd"):
            if p1 != p2:
                st.error("Las claves no coinciden.")
            else:
                ok, msg = set_user_password(target2, p1)
                st.success(msg) if ok else st.error(msg)

    with tnew:
        c1, c2 = st.columns(2)
        with c1:
            new_admin = st.text_input("Usuario a crear o convertir en administrador", key="new_admin_user")
            new_admin_name = st.text_input("Nombre visible", value="Administrador", key="new_admin_name")
        with c2:
            create_mode = st.radio("Clave", ["Generar automática", "Escribir manual"], horizontal=True, key="new_admin_mode")
            manual_admin_pass = ""
            manual_admin_pass_2 = ""
            if create_mode == "Escribir manual":
                manual_admin_pass = st.text_input("Clave", type="password", key="new_admin_pass_1")
                manual_admin_pass_2 = st.text_input("Repetir clave", type="password", key="new_admin_pass_2")
        if st.button("Crear/actualizar administrador", type="primary", key="btn_create_admin"):
            if create_mode == "Generar automática":
                new_pass = generate_password(16)
            else:
                if manual_admin_pass != manual_admin_pass_2:
                    st.error("Las claves no coinciden.")
                    new_pass = ""
                else:
                    new_pass = manual_admin_pass
            if new_pass:
                ok, msg = create_admin_user(new_admin, new_pass, new_admin_name)
                if ok:
                    st.success(msg)
                    if create_mode == "Generar automática":
                        st.code(new_pass, language=None)
                else:
                    st.error(msg)

    st.markdown("#### Roles de usuarios")
    with st.container():
        if all_users:
            u = st.selectbox("Usuario", all_users, key="role_user_select")
            selected = users_df[users_df["username"].eq(u)].iloc[0]
            role = st.selectbox("Rol", ["user", "admin"], index=1 if selected["role"] == "admin" else 0, key="role_select")
            active = st.checkbox("Usuario activo", value=bool(selected["active"]), key="role_active")
            if st.button("Actualizar rol/estado", key="btn_update_role"):
                ok, msg = set_user_role(u, role, 1 if active else 0)
                st.success(msg) if ok else st.error(msg)
                if u == current_user.get("username"):
                    st.session_state.user["role"] = role


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
            if not admin_exists():
                with st.expander("Crear administrador inicial", expanded=True):
                    setup_token_cfg = get_secret_value("CGI_SETUP_TOKEN", "")
                    if setup_token_cfg:
                        st.caption("Instalación protegida: ingrese el token definido en Secrets como CGI_SETUP_TOKEN. Acepta CREAR-ADMIN o CREAR ADMIN.")
                        setup_token = st.text_input("Token de instalación", type="password", key="setup_token")
                    else:
                        setup_token = ""
                        st.info("No hay administrador creado. Cree el administrador inicial. No necesita escribir token.")

                    admin_user_init = st.text_input("Usuario administrador inicial", value=get_secret_value("CGI_ADMIN_USER", ADMIN_USER_DEFAULT), key="setup_admin_user")
                    admin_pass_mode = st.radio("Clave inicial", ["Generar automática", "Escribir manual"], horizontal=True, key="setup_admin_mode")
                    init_pass = ""
                    init_pass_2 = ""
                    if admin_pass_mode == "Escribir manual":
                        init_pass = st.text_input("Clave administrador", type="password", key="setup_admin_pass1")
                        init_pass_2 = st.text_input("Repetir clave", type="password", key="setup_admin_pass2")

                    if st.button("Crear administrador inicial", type="primary", key="btn_setup_admin"):
                        if not setup_token_ok(setup_token, setup_token_cfg):
                            st.error("Token de instalación incorrecto. Escriba exactamente CREAR-ADMIN o CREAR ADMIN, según lo configurado en Secrets.")
                        else:
                            if admin_pass_mode == "Generar automática":
                                new_admin_pass = generate_password(16)
                            else:
                                if len(init_pass) < 8:
                                    st.error("La clave de administrador debe tener al menos 8 caracteres.")
                                    new_admin_pass = ""
                                elif init_pass != init_pass_2:
                                    st.error("Las claves no coinciden.")
                                    new_admin_pass = ""
                                else:
                                    new_admin_pass = init_pass
                            if new_admin_pass:
                                ok, msg = create_admin_user(admin_user_init, new_admin_pass, "Administrador")
                                if ok:
                                    st.success(msg)
                                    if admin_pass_mode == "Generar automática":
                                        st.warning("Copie esta clave ahora. Por seguridad no se volverá a mostrar.")
                                        st.code(new_admin_pass, language=None)
                                    st.session_state.user = dict(get_user(admin_user_init))
                                    st.info("Administrador creado. Ahora toque cualquier control o recargue la página para entrar al panel administrador.")
                                    st.stop()
                                else:
                                    st.error(msg)
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
    """Máscara de tinta para las curvas.

    La versión anterior tomaba la mediana de todos los píxeles oscuros/azules por
    columna. Eso puede deformar la señal cuando dentro del ROI entran grilla,
    letras, ejes o marcas. Esta máscara conserva tinta azul/negra del trazado y
    descarta la grilla clara; luego digitize_signal elige una trayectoria continua.
    """
    arr = rgb.astype(np.int16)
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]
    gray = 0.299 * r + 0.587 * g + 0.114 * b
    maxc = np.maximum.reduce([r, g, b])
    minc = np.minimum.reduce([r, g, b])
    sat = maxc - minc

    # Trazos frecuentes del informe: azul oscuro o negro/gris oscuro.
    blue_ink = (b > r + 8) & (b >= g - 8) & (gray < 230) & (sat > 10)
    dark_ink = (gray < 118) & (sat > 2)
    colored_ink = (gray < 175) & (sat > 28)

    # La grilla suele ser clara y con baja saturación; si se incluye, la curva
    # resultante se parece a una línea horizontal o toma saltos falsos.
    light_grid = (gray > 155) & (sat < 24)
    return (blue_ink | dark_ink | colored_ink) & (~light_grid)


def _ink_score(crop: np.ndarray) -> np.ndarray:
    arr = crop.astype(np.float32)
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]
    gray = 0.299 * r + 0.587 * g + 0.114 * b
    maxc = np.maximum.reduce([r, g, b])
    minc = np.minimum.reduce([r, g, b])
    sat = maxc - minc
    # Más alto = más probable que sea el trazo real.
    return ((255.0 - gray) / 255.0) + 0.30 * (sat / 255.0) + 0.15 * np.maximum(b - r, 0) / 255.0


def _column_candidates(mask: np.ndarray, score: np.ndarray, max_candidates: int = 8) -> List[Tuple[int, List[Tuple[float, float]]]]:
    """Devuelve candidatos de trazo por columna: [(x, [(fila, fuerza), ...])]."""
    ch, cw = mask.shape[:2]
    out: List[Tuple[int, List[Tuple[float, float]]]] = []
    max_dense = max(8, int(ch * 0.42))
    min_run = 1
    max_run = max(14, int(ch * 0.18))
    for cx in range(cw):
        rows = np.where(mask[:, cx])[0]
        if len(rows) == 0 or len(rows) > max_dense:
            # Columna demasiado densa: suele ser borde, letra, eje o grilla vertical.
            continue
        splits = np.where(np.diff(rows) > 1)[0] + 1
        runs = np.split(rows, splits)
        cands: List[Tuple[float, float]] = []
        for run in runs:
            if len(run) < min_run or len(run) > max_run:
                continue
            sc = score[run, cx].astype(float)
            if np.nansum(sc) <= 0:
                y = float(np.nanmean(run))
            else:
                y = float(np.average(run, weights=np.maximum(sc, 1e-6)))
            strength = float(np.nanmean(sc)) + 0.035 * min(len(run), 8)
            # Penalización leve si el candidato está pegado al borde del ROI.
            if y < ch * 0.03 or y > ch * 0.97:
                strength -= 0.25
            cands.append((y, strength))
        if cands:
            cands = sorted(cands, key=lambda t: t[1], reverse=True)[:max_candidates]
            out.append((cx, cands))
    return out


def _choose_continuous_path(columns: List[Tuple[int, List[Tuple[float, float]]]], height: int) -> List[Tuple[int, float]]:
    """Selecciona la trayectoria más continua entre candidatos de tinta.

    Esto evita el error típico de la mediana por columna: cuando hay grilla, texto o
    ejes dentro del ROI, el punto central de todos los píxeles no coincide con la
    curva. El algoritmo premia continuidad horizontal y penaliza saltos bruscos.
    """
    if not columns:
        return []
    dp: List[List[float]] = []
    back: List[List[int]] = []
    prev_x, prev_cands = columns[0]
    first_scores = []
    for y, strength in prev_cands:
        edge_penalty = 0.20 if (y < height * 0.04 or y > height * 0.96) else 0.0
        first_scores.append(1.25 + strength - edge_penalty)
    dp.append(first_scores)
    back.append([-1] * len(prev_cands))

    for ci in range(1, len(columns)):
        x, cands = columns[ci]
        gap = max(1, x - prev_x)
        new_scores: List[float] = []
        new_back: List[int] = []
        for y, strength in cands:
            best_score = None
            best_j = -1
            for j, (yprev, _) in enumerate(prev_cands):
                jump = abs(float(y) - float(yprev))
                # Se permite que la curva cambie, pero se penalizan saltos verticales
                # incompatibles con una señal continua.
                jump_penalty = 0.020 * jump + 0.060 * max(0.0, jump / max(1, gap) - 1.8)
                gap_penalty = 0.010 * min(gap, 35)
                val = dp[-1][j] - jump_penalty - gap_penalty
                if best_score is None or val > best_score:
                    best_score = val
                    best_j = j
            # Permitir reinicio si hubo un gran blanco, pero con menor puntaje.
            restart = 1.0 + strength - 0.15 * min(gap, 20)
            if best_score is None or restart > best_score + 0.6:
                best_score = restart
                best_j = -1
            edge_penalty = 0.25 if (y < height * 0.03 or y > height * 0.97) else 0.0
            new_scores.append(float(best_score + 1.15 + strength - edge_penalty))
            new_back.append(best_j)
        dp.append(new_scores)
        back.append(new_back)
        prev_x, prev_cands = x, cands

    # Backtracking desde la mejor columna final.
    idx = int(np.argmax(dp[-1]))
    selected: List[Tuple[int, float]] = []
    for ci in range(len(columns) - 1, -1, -1):
        x, cands = columns[ci]
        if idx < 0 or idx >= len(cands):
            break
        selected.append((x, float(cands[idx][0])))
        idx = back[ci][idx]
        if idx == -1 and ci > 0:
            # Buscar el mejor reinicio previo para no perder una señal larga si hubo blanco.
            # Se corta aquí porque la porción posterior ya es la más confiable.
            break
    selected.reverse()

    # Si el último segmento quedó demasiado corto, usar el segmento continuo más largo.
    if len(selected) < max(12, int(0.10 * len(columns))):
        # Respaldo: elegir el candidato más fuerte por columna y suavizar por mediana móvil.
        selected = [(x, sorted(cands, key=lambda t: t[1], reverse=True)[0][0]) for x, cands in columns]
    return selected


def digitize_signal(img: Image.Image, roi: Dict[str, int], smooth_frac: float = 0.003) -> pd.DataFrame:
    """Digitaliza una señal manteniendo la morfología original del ROI.

    Cambios clave frente a la versión previa:
    1) selecciona una trayectoria continua del trazo, no la mediana de todo lo oscuro;
    2) elimina mejor grilla/texto/ejes;
    3) normaliza contra la altura del ROI, no por min-máx de la señal, para que la
       amplitud visual sea más parecida a la curva original.
    """
    rgb = np.asarray(img.convert("RGB"))
    w, h = img.size
    r = clamp_roi(roi, w, h)
    crop = rgb[r["y0"]:r["y1"], r["x0"]:r["x1"], :]
    ch, cw = crop.shape[:2]
    if ch < 5 or cw < 5:
        return pd.DataFrame(columns=["x", "y_pixel", "y_norm", "y_smooth"])

    mask = make_mask(crop)
    # Evita que los bordes del recorte entren como señal.
    ym = max(2, int(ch * 0.025))
    xm = max(2, int(cw * 0.006))
    mask[:ym, :] = False
    mask[-ym:, :] = False
    mask[:, :xm] = False
    mask[:, -xm:] = False

    score = _ink_score(crop)
    columns = _column_candidates(mask, score)
    path = _choose_continuous_path(columns, ch)
    if len(path) < 10:
        return pd.DataFrame(columns=["x", "y_pixel", "y_norm", "y_smooth"])

    local_x = np.array([p[0] for p in path], dtype=int)
    local_y = np.array([p[1] for p in path], dtype=float)

    # Interpolación limitada de pequeños huecos para que la curva no quede dentada.
    s = pd.Series(local_y, index=local_x).groupby(level=0).median().sort_index()
    full_index = np.arange(int(s.index.min()), int(s.index.max()) + 1)
    s_full = s.reindex(full_index).interpolate(limit=18, limit_direction="both")
    s_full = s_full.dropna()
    if len(s_full) < 10:
        return pd.DataFrame(columns=["x", "y_pixel", "y_norm", "y_smooth"])

    y_abs = r["y0"] + s_full.to_numpy(float)
    x_abs = r["x0"] + s_full.index.to_numpy(float)

    # Suavizado mínimo: preserva picos y morfología, sólo reduce serrucho por pixelado.
    win = max(3, int(len(y_abs) * smooth_frac))
    y_s = smooth(y_abs, win)

    roi_h = max(1.0, float(r["y1"] - r["y0"]))
    # Normalización por altura real del ROI: arriba=1, abajo=0. No estira por min-máx.
    y_norm = 1.0 - ((y_s - float(r["y0"])) / roi_h)
    y_norm = np.clip(y_norm, 0.0, 1.0)

    df = pd.DataFrame({"x": x_abs, "y_pixel": y_abs, "y_smooth": y_s, "y_norm": y_norm})
    return df


def draw_digitization_overlay(img: Image.Image, rois: dict, curves: Dict[str, pd.DataFrame]) -> Image.Image:
    """Vista de control: superpone el trazo digitalizado sobre la imagen original."""
    out = img.copy().convert("RGB")
    d = ImageDraw.Draw(out)
    colors = {"dzdt": "red", "ecg": "red", "fono": "red"}
    roi_colors = {"dzdt": "blue", "ecg": "green", "fono": "orange"}
    names = {"dzdt": "dZ/dt", "ecg": "ECG", "fono": "Fono"}
    for key, roi in rois.items():
        r = clamp_roi(roi, *out.size)
        d.rectangle([r["x0"], r["y0"], r["x1"], r["y1"]], outline=roi_colors.get(key, "yellow"), width=max(2, out.size[0] // 650))
        d.text((r["x0"] + 4, r["y0"] + 4), names.get(key, key), fill=roi_colors.get(key, "yellow"))
        df = curves.get(key, pd.DataFrame())
        if df is not None and not df.empty and "y_smooth" in df.columns:
            pts = list(zip(df["x"].to_numpy(float), df["y_smooth"].to_numpy(float)))
            if len(pts) >= 2:
                # Reducir puntos para no generar imágenes pesadas.
                step = max(1, len(pts) // 1400)
                pts2 = [(int(round(x)), int(round(y))) for x, y in pts[::step]]
                d.line(pts2, fill=colors.get(key, "red"), width=max(2, out.size[0] // 900))
    return out

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


def _set_signal_ylim(ax, values: np.ndarray, pad: float = 0.12) -> None:
    """Eje Y estable para que las curvas no queden aplastadas ni exageradas."""
    try:
        y = np.asarray(values, dtype=float)
        y = y[np.isfinite(y)]
        if y.size < 3:
            ax.set_ylim(-0.10, 1.10)
            return
        lo = float(np.nanpercentile(y, 1))
        hi = float(np.nanpercentile(y, 99))
        if not np.isfinite(lo) or not np.isfinite(hi) or abs(hi - lo) < 1e-6:
            lo, hi = -0.10, 1.10
        rng = hi - lo
        ax.set_ylim(lo - pad * rng, hi + pad * rng)
    except Exception:
        ax.set_ylim(-0.10, 1.10)


def build_curve_chart(dzdt: pd.DataFrame, ecg: pd.DataFrame, fono: pd.DataFrame, auto: dict, manual: dict, guide: dict, x0: float, x1: float) -> dict:
    """Construye el gráfico de corrección de cursores con ejes optimizados.

    Cambio V4:
    - Reemplaza el gráfico único muy ancho por tres ejes apilados y alineados.
    - Aumenta la altura útil para que dZ/dt, ECG y fono no se vean aplastados.
    - Mantiene el mismo eje X en los tres paneles para corregir QRS/B/C/X/Y.
    - Calcula la conversión píxel↔dato usando el eje superior, compartido por todos.
    """
    dpi = 155
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(11.8, 8.9),
        dpi=dpi,
        sharex=True,
        gridspec_kw={"height_ratios": [1.25, 1.0, 0.95], "hspace": 0.10},
    )
    ax_dzdt, ax_ecg, ax_fono = axes
    signal_axes = {
        "dzdt": ax_dzdt,
        "ecg": ax_ecg,
        "fono": ax_fono,
    }

    # Curvas principales: cada una con su propio eje Y, sin estiramiento artificial común.
    if not dzdt.empty:
        xd = dzdt["x"].to_numpy(float)
        yd = dzdt["y_norm"].to_numpy(float)
        ax_dzdt.plot(xd, yd, linewidth=2.3, label="dZ/dt / impedancia")
        _set_signal_ylim(ax_dzdt, yd)
    else:
        ax_dzdt.set_ylim(-0.10, 1.10)

    if not ecg.empty:
        xe = ecg["x"].to_numpy(float)
        ye = ecg["y_norm"].to_numpy(float)
        ax_ecg.plot(xe, ye, linewidth=1.9, label="ECG")
        _set_signal_ylim(ax_ecg, ye)
    else:
        ax_ecg.set_ylim(-0.10, 1.10)

    if not fono.empty:
        xf = fono["x"].to_numpy(float)
        yf = fono["y_norm"].to_numpy(float)
        ax_fono.plot(xf, yf, linewidth=1.9, label="Fonocardiograma")
        _set_signal_ylim(ax_fono, yf)
    else:
        ax_fono.set_ylim(-0.10, 1.10)

    # Línea horizontal del fonocardiograma sólo en su eje.
    try:
        ax_fono.axhline(float(guide.get("fono_line", 0.55)), linestyle="--", linewidth=1.2, label="Línea fono")
    except Exception:
        pass

    # Guías automáticas de referencia.
    qrs_peak = guide.get("qrs_peak", np.nan)
    if np.isfinite(qrs_peak):
        ax_ecg.axvline(float(qrs_peak), linestyle="-.", linewidth=1.1)
        ax_ecg.text(float(qrs_peak), 0.98, "QRS pico", transform=ax_ecg.get_xaxis_transform(), rotation=90, ha="center", va="top", fontsize=8)
    for k, lab in [("s1", "S1"), ("s2", "S2")]:
        v = guide.get(k, np.nan)
        if np.isfinite(v):
            ax_fono.axvline(float(v), linestyle="-.", linewidth=1.0)
            ax_fono.text(float(v), 0.98, lab, transform=ax_fono.get_xaxis_transform(), rotation=90, ha="center", va="top", fontsize=8)

    # Cursores: líneas tenues en todos los paneles y etiqueta fuerte en el panel más relevante.
    for c in CURSORS:
        color = CURSOR_COLORS.get(c)
        try:
            ax_target = ax_ecg if c == "QRS" else ax_dzdt
            auto_x = float(auto[c]["x"])
            manual_x = float(manual[c]["x"])
        except Exception:
            continue
        for ax in axes:
            ax.axvline(auto_x, linestyle=":", linewidth=0.9, color=color, alpha=0.55)
            ax.axvline(manual_x, linestyle="--", linewidth=1.8, color=color, alpha=0.95)
        ax_target.text(
            manual_x,
            0.97,
            c,
            transform=ax_target.get_xaxis_transform(),
            rotation=90,
            ha="center",
            va="top",
            fontsize=10,
            fontweight="bold",
            color=color,
            bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": color, "alpha": 0.85},
        )

    # Estética y ejes.
    labels = [(ax_dzdt, "dZ/dt"), (ax_ecg, "ECG"), (ax_fono, "Fono")]
    for ax, lab in labels:
        ax.set_ylabel(lab, rotation=0, labelpad=32, fontsize=10, fontweight="bold", va="center")
        ax.grid(True, axis="x", alpha=0.22)
        ax.grid(True, axis="y", alpha=0.12)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(axis="y", labelsize=8)
        ax.tick_params(axis="x", labelsize=8)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.88)

    ax_dzdt.set_title("Corrección opcional de cursores — ejes optimizados", fontsize=13, fontweight="bold", pad=10)
    ax_fono.set_xlabel("Posición horizontal digitalizada del informe (px)", fontsize=10)
    ax_dzdt.set_xlim(float(x0), float(x1))

    # Ticks X más legibles: evita exceso de marcas cuando el ROI es ancho.
    try:
        span = max(1.0, float(x1) - float(x0))
        step = max(25.0, round(span / 8.0 / 10.0) * 10.0)
        ticks = np.arange(np.ceil(float(x0) / step) * step, float(x1) + 1, step)
        ax_fono.set_xticks(ticks)
    except Exception:
        pass

    fig.subplots_adjust(left=0.085, right=0.985, top=0.94, bottom=0.075)
    fig.canvas.draw()
    width, height = fig.canvas.get_width_height()

    # Usamos el eje superior para mapear x↔píxel. Al compartir x, los tres paneles quedan alineados.
    x0_pix = float(ax_dzdt.transData.transform((float(x0), 0))[0])
    x1_pix = float(ax_dzdt.transData.transform((float(x1), 0))[0])

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
    bg = img.resize((disp_w, disp_h)).convert("RGB") if scale != 1.0 else img.convert("RGB")
    safe_key = _safe_canvas_key(key_prefix)

    st.caption("Arrastre cada línea vertical de color sobre el gráfico. QRS se corrige sobre ECG; B, C, X e Y sobre dZ/dt. No hace falta escribir coordenadas.")
    st.markdown(
        " ".join([f"<span style='display:inline-block;margin-right:12px;color:{CURSOR_COLORS[c]};font-weight:700'>{c}</span>" for c in CURSORS]),
        unsafe_allow_html=True,
    )

    positions = {c: float(min(max(cursor_x.get(c, chart_meta["x0"]), chart_meta["x0"]), chart_meta["x1"])) for c in CURSORS}
    if st_canvas is not None:
        try:
            initial_objects = []
            for c in CURSORS:
                xpix = data_x_to_canvas_px(positions[c], chart_meta) * scale
                initial_objects.append({
                    "type": "rect",
                    "left": max(0.0, float(xpix) - 4.0),
                    "top": 0.0,
                    "width": 8.0,
                    "height": float(disp_h),
                    "fill": "rgba(255,255,255,0.01)",
                    "stroke": CURSOR_COLORS.get(c, "#111827"),
                    "strokeWidth": 3,
                    "strokeUniform": True,
                    "name": c,
                    "cursor_key": c,
                    "selectable": True,
                    "evented": True,
                    "hasControls": False,
                    "lockMovementY": True,
                    "lockScalingX": True,
                    "lockScalingY": True,
                    "lockRotation": True,
                    "hoverCursor": "ew-resize",
                    "moveCursor": "ew-resize",
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
                display_toolbar=False,
                update_streamlit=True,
                key=f"{safe_key}_drawable_cursor_canvas",
            )
            objects = []
            if canvas_result is not None and getattr(canvas_result, "json_data", None):
                if isinstance(canvas_result.json_data.get("objects"), list):
                    objects = canvas_result.json_data["objects"]
            named = {}
            for obj in objects:
                k = obj.get("cursor_key") or obj.get("name")
                if k in CURSORS:
                    named[k] = obj
            for i, c in enumerate(CURSORS):
                obj = named.get(c) or (objects[i] if i < len(objects) else {})
                left = float(obj.get("left", max(0.0, (data_x_to_canvas_px(positions[c], chart_meta) * scale) - 4.0)))
                width = float(obj.get("width", 8.0)) * float(obj.get("scaleX", 1.0))
                center_px = (left + width / 2.0) / scale
                positions[c] = canvas_px_to_data_x(center_px, chart_meta)
            return positions
        except Exception as e:
            st.warning(
                "No se pudo abrir el selector gráfico de cursores por incompatibilidad de versión. "
                "Se habilita un respaldo numérico para no interrumpir la carga. "
                f"Detalle técnico: {type(e).__name__}: {e}"
            )

    st.markdown("#### Respaldo numérico si el gráfico no responde")
    st.info("Use este respaldo solo si el componente gráfico no funciona en el navegador o en Streamlit Cloud.")
    cols = st.columns(5)
    for i, c in enumerate(CURSORS):
        with cols[i]:
            positions[c] = float(st.number_input(f"Cursor {c}", value=float(positions[c]), min_value=float(chart_meta["x0"]), max_value=float(chart_meta["x1"]), step=1.0, key=f"{safe_key}_{c}_fallback"))
    return positions


# ============================================================
# EDITOR GRÁFICO NATIVO ROBUSTO (HTML/JS COMPONENT)
# ============================================================
# Reemplaza el uso problemático de streamlit-drawable-canvas en la segunda sección.
# Mantiene la corrección DESDE el gráfico mediante un componente propio sin dependencias
# Python externas. La selección queda restringida a la señal/cursor elegido para evitar
# que se mueva el borde o cursor equivocado.

import base64

_CGI_DRAG_COMPONENT = None


def _pil_to_data_url(img: Image.Image) -> str:
    bio = io.BytesIO()
    img.convert("RGB").save(bio, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(bio.getvalue()).decode("ascii")


def _ensure_cgi_drag_component() -> Path:
    comp_dir = Path(__file__).parent / "cgi_drag_component"
    comp_dir.mkdir(exist_ok=True)
    index_path = comp_dir / "index.html"
    html = '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8" />\n<meta name="viewport" content="width=device-width, initial-scale=1" />\n<style>\n  html, body { margin:0; padding:0; font-family: Inter, Arial, sans-serif; background: transparent; }\n  #wrap { width:100%; box-sizing:border-box; }\n  #bar { display:flex; align-items:center; gap:10px; margin: 0 0 6px 0; color:#0f172a; font-size:14px; }\n  #badge { font-weight:800; padding:5px 8px; border-radius:999px; background:#e0f2fe; color:#075985; border:1px solid #7dd3fc; }\n  #hint { color:#475569; }\n  #canvas { display:block; border:1px solid #cbd5e1; border-radius:12px; background:#fff; max-width:100%; touch-action:none; cursor:crosshair; }\n</style>\n</head>\n<body>\n<div id="wrap">\n  <div id="bar"><span id="badge">Editor gráfico</span><span id="hint">Arrastre sobre la imagen.</span></div>\n  <canvas id="canvas"></canvas>\n</div>\n<script>\n(function(){\n  const Streamlit = {\n    ready: () => window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1}, "*"),\n    height: (h) => window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: h}, "*"),\n    value: (v) => window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:setComponentValue", value: v, dataType: "json"}, "*")\n  };\n\n  const canvas = document.getElementById("canvas");\n  const ctx = canvas.getContext("2d");\n  const badge = document.getElementById("badge");\n  const hint = document.getElementById("hint");\n  let args = null;\n  let image = new Image();\n  let scale = 1;\n  let dragging = false;\n  let drag = null;\n  let rois = {};\n  let cursorX = {};\n\n  function clone(o){ return JSON.parse(JSON.stringify(o || {})); }\n  function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }\n  function dist(a,b,c,d){ return Math.hypot(a-c,b-d); }\n  function cssColor(hex, alpha){\n    if(!hex || hex[0] !== \'#\') return hex || \'#111827\';\n    const r=parseInt(hex.slice(1,3),16), g=parseInt(hex.slice(3,5),16), b=parseInt(hex.slice(5,7),16);\n    return `rgba(${r},${g},${b},${alpha})`;\n  }\n  function pointer(ev){\n    const rect = canvas.getBoundingClientRect();\n    const t = ev.touches && ev.touches.length ? ev.touches[0] : ev;\n    return {x:(t.clientX-rect.left)*(canvas.width/rect.width), y:(t.clientY-rect.top)*(canvas.height/rect.height)};\n  }\n  function fitCanvas(){\n    const maxW = Number(args.displayMaxWidth || 1100);\n    const iw = Number(args.imageWidth || image.naturalWidth || 1);\n    const ih = Number(args.imageHeight || image.naturalHeight || 1);\n    const available = Math.max(300, Math.min(maxW, window.innerWidth - 8));\n    scale = Math.min(1, available / Math.max(1, iw));\n    canvas.width = Math.max(1, Math.round(iw * scale));\n    canvas.height = Math.max(1, Math.round(ih * scale));\n    canvas.style.width = canvas.width + "px";\n    canvas.style.height = canvas.height + "px";\n    Streamlit.height(canvas.height + 46);\n  }\n  function draw(){\n    if(!args) return;\n    fitCanvas();\n    ctx.clearRect(0,0,canvas.width,canvas.height);\n    try { ctx.drawImage(image, 0, 0, canvas.width, canvas.height); }\n    catch(e) { ctx.fillStyle = "#fff"; ctx.fillRect(0,0,canvas.width,canvas.height); }\n    if(args.mode === "rois") drawRois();\n    if(args.mode === "cursors") drawCursors();\n  }\n  function drawRois(){\n    const active = args.activeSignal;\n    const colors = args.colors || {};\n    const labels = args.labels || {};\n    for(const k of (args.signals || Object.keys(rois))){\n      const r = rois[k]; if(!r) continue;\n      const x0=r.x0*scale, x1=r.x1*scale, y0=r.y0*scale, y1=r.y1*scale;\n      const color = colors[k] || \'#2563eb\';\n      const isActive = k === active;\n      ctx.save();\n      ctx.strokeStyle = color;\n      ctx.lineWidth = isActive ? 4 : 2;\n      ctx.setLineDash(isActive ? [] : [6,4]);\n      ctx.fillStyle = cssColor(color, isActive ? 0.06 : 0.025);\n      ctx.fillRect(x0,y0,x1-x0,y1-y0);\n      ctx.strokeRect(x0,y0,x1-x0,y1-y0);\n      ctx.setLineDash([]);\n      ctx.fillStyle = color;\n      ctx.font = "bold 13px Arial";\n      ctx.fillText((labels[k] || k), x0+8, Math.max(16, y0+18));\n      if(isActive){\n        const pts = [[x0,y0],[x1,y0],[x0,y1],[x1,y1],[(x0+x1)/2,y0],[(x0+x1)/2,y1],[x0,(y0+y1)/2],[x1,(y0+y1)/2]];\n        ctx.fillStyle = \'#ffffff\'; ctx.strokeStyle = color; ctx.lineWidth = 2;\n        for(const [px,py] of pts){ ctx.beginPath(); ctx.rect(px-5, py-5, 10, 10); ctx.fill(); ctx.stroke(); }\n      }\n      ctx.restore();\n    }\n    badge.textContent = "Bordes: " + ((args.labels || {})[args.activeSignal] || args.activeSignal || "");\n    hint.textContent = "Acción: " + humanAction(args.activeAction || "auto") + ". Arrastre y suelte.";\n  }\n  function humanAction(a){\n    return ({auto:\'borde/esquina más cercano\', mover:\'mover rectángulo\', izquierdo:\'borde izquierdo\', derecho:\'borde derecho\', superior:\'borde superior\', inferior:\'borde inferior\', esquina_sup_izq:\'esquina superior izquierda\', esquina_sup_der:\'esquina superior derecha\', esquina_inf_izq:\'esquina inferior izquierda\', esquina_inf_der:\'esquina inferior derecha\'})[a] || a;\n  }\n  function chooseAutoHandle(r, x, y){\n    const x0=r.x0*scale, x1=r.x1*scale, y0=r.y0*scale, y1=r.y1*scale;\n    const candidates = [\n      [\'esquina_sup_izq\', x0, y0], [\'esquina_sup_der\', x1, y0], [\'esquina_inf_izq\', x0, y1], [\'esquina_inf_der\', x1, y1],\n      [\'izquierdo\', x0, y], [\'derecho\', x1, y], [\'superior\', x, y0], [\'inferior\', x, y1]\n    ];\n    let best=\'mover\', bd=1e9;\n    for(const [name, px, py] of candidates){ const d=dist(x,y,px,py); if(d<bd){bd=d; best=name;} }\n    if(bd < 22) return best;\n    if(x>=x0 && x<=x1 && y>=y0 && y<=y1) return \'mover\';\n    return best;\n  }\n  function startRoi(p){\n    const k = args.activeSignal;\n    if(!k || !rois[k]) return;\n    const r = rois[k];\n    let action = args.activeAction || \'auto\';\n    if(action === \'auto\') action = chooseAutoHandle(r, p.x, p.y);\n    dragging = true;\n    drag = {mode:\'rois\', key:k, action:action, startX:p.x/scale, startY:p.y/scale, startR:clone(r)};\n    applyRoiDrag(p);\n  }\n  function applyRoiDrag(p){\n    if(!drag || drag.mode !== \'rois\') return;\n    const iw = Number(args.imageWidth || 1), ih=Number(args.imageHeight || 1);\n    const r = clone(drag.startR);\n    const x = clamp(p.x/scale, 0, iw), y = clamp(p.y/scale, 0, ih);\n    const dx = x - drag.startX, dy = y - drag.startY;\n    let nr = clone(r);\n    const minSize = 8;\n    switch(drag.action){\n      case \'mover\': {\n        const ww=r.x1-r.x0, hh=r.y1-r.y0;\n        nr.x0=clamp(r.x0+dx,0,iw-ww); nr.x1=nr.x0+ww; nr.y0=clamp(r.y0+dy,0,ih-hh); nr.y1=nr.y0+hh; break;\n      }\n      case \'izquierdo\': nr.x0=clamp(x,0,r.x1-minSize); break;\n      case \'derecho\': nr.x1=clamp(x,r.x0+minSize,iw); break;\n      case \'superior\': nr.y0=clamp(y,0,r.y1-minSize); break;\n      case \'inferior\': nr.y1=clamp(y,r.y0+minSize,ih); break;\n      case \'esquina_sup_izq\': nr.x0=clamp(x,0,r.x1-minSize); nr.y0=clamp(y,0,r.y1-minSize); break;\n      case \'esquina_sup_der\': nr.x1=clamp(x,r.x0+minSize,iw); nr.y0=clamp(y,0,r.y1-minSize); break;\n      case \'esquina_inf_izq\': nr.x0=clamp(x,0,r.x1-minSize); nr.y1=clamp(y,r.y0+minSize,ih); break;\n      case \'esquina_inf_der\': nr.x1=clamp(x,r.x0+minSize,iw); nr.y1=clamp(y,r.y0+minSize,ih); break;\n    }\n    for(const q of [\'x0\',\'x1\',\'y0\',\'y1\']) nr[q]=Math.round(nr[q]);\n    rois[drag.key]=nr;\n    draw();\n  }\n  function emitRois(){ Streamlit.value({rois: rois, edited: args.activeSignal, action: args.activeAction, t: Date.now()}); }\n\n  function dataXToPx(xv){\n    const cm=args.chartMeta || {}; const x0=Number(cm.x0||0), x1=Number(cm.x1||1);\n    const p0=Number(cm.data_x0_pix||0)*scale, p1=Number(cm.data_x1_pix||canvas.width)*scale;\n    if(Math.abs(x1-x0)<1e-9) return p0;\n    return p0 + ((Number(xv)-x0)/(x1-x0))*(p1-p0);\n  }\n  function pxToDataX(px){\n    const cm=args.chartMeta || {}; const x0=Number(cm.x0||0), x1=Number(cm.x1||1);\n    const p0=Number(cm.data_x0_pix||0)*scale, p1=Number(cm.data_x1_pix||canvas.width)*scale;\n    if(Math.abs(p1-p0)<1e-9) return x0;\n    return clamp(x0 + ((px-p0)/(p1-p0))*(x1-x0), x0, x1);\n  }\n  function drawCursors(){\n    const colors=args.colors || {};\n    for(const c of (args.cursors || Object.keys(cursorX))){\n      const xp=dataXToPx(cursorX[c]);\n      const color=colors[c] || \'#111827\';\n      const active = c === args.activeCursor;\n      ctx.save();\n      ctx.strokeStyle=color; ctx.lineWidth=active ? 4 : 2; ctx.setLineDash(active ? [] : [6,4]);\n      ctx.beginPath(); ctx.moveTo(xp,0); ctx.lineTo(xp,canvas.height); ctx.stroke(); ctx.setLineDash([]);\n      ctx.fillStyle=color; ctx.font=\'bold 14px Arial\'; ctx.fillText(c, Math.min(canvas.width-30, xp+6), active ? 20 : 40);\n      if(active){ ctx.fillStyle=cssColor(color,0.12); ctx.fillRect(xp-10,0,20,canvas.height); }\n      ctx.restore();\n    }\n    badge.textContent = "Cursor activo: " + (args.activeCursor || "");\n    hint.textContent = "Haga clic o arrastre horizontalmente sobre el gráfico y suelte.";\n  }\n  function startCursor(p){\n    const c=args.activeCursor; if(!c) return;\n    dragging=true; drag={mode:\'cursors\', key:c};\n    cursorX[c] = pxToDataX(p.x);\n    draw();\n  }\n  function applyCursorDrag(p){\n    if(!drag || drag.mode !== \'cursors\') return;\n    cursorX[drag.key] = pxToDataX(p.x);\n    draw();\n  }\n  function emitCursors(){ Streamlit.value({cursor_x: cursorX, edited: args.activeCursor, t: Date.now()}); }\n\n  function onDown(ev){ ev.preventDefault(); const p=pointer(ev); if(args.mode===\'rois\') startRoi(p); else if(args.mode===\'cursors\') startCursor(p); }\n  function onMove(ev){ if(!dragging) return; ev.preventDefault(); const p=pointer(ev); if(drag.mode===\'rois\') applyRoiDrag(p); else applyCursorDrag(p); }\n  function onUp(ev){ if(!dragging) return; ev.preventDefault(); if(drag && drag.mode===\'rois\') emitRois(); if(drag && drag.mode===\'cursors\') emitCursors(); dragging=false; drag=null; }\n  canvas.addEventListener(\'mousedown\', onDown); window.addEventListener(\'mousemove\', onMove); window.addEventListener(\'mouseup\', onUp);\n  canvas.addEventListener(\'touchstart\', onDown, {passive:false}); window.addEventListener(\'touchmove\', onMove, {passive:false}); window.addEventListener(\'touchend\', onUp, {passive:false});\n  window.addEventListener(\'resize\', () => draw());\n\n  function render(newArgs){\n    args = newArgs || {};\n    if(args.mode === \'rois\') { rois = clone(args.rois || {}); }\n    if(args.mode === \'cursors\') { cursorX = clone(args.cursorX || {}); }\n    image.onload = () => draw();\n    if(image.src !== args.image) image.src = args.image || \'\';\n    else draw();\n  }\n  window.addEventListener(\'message\', function(event){\n    if(!event.data || event.data.type !== \'streamlit:render\') return;\n    render(event.data.args || {});\n  });\n  Streamlit.ready();\n  Streamlit.height(520);\n})();\n</script>\n</body>\n</html>\n'
    if (not index_path.exists()) or index_path.read_text(encoding="utf-8") != html:
        index_path.write_text(html, encoding="utf-8")
    return comp_dir


def _cgi_drag_component(**kwargs):
    global _CGI_DRAG_COMPONENT
    if _CGI_DRAG_COMPONENT is None:
        import streamlit.components.v1 as components
        _CGI_DRAG_COMPONENT = components.declare_component(
            "cgi_drag_component",
            path=str(_ensure_cgi_drag_component()),
        )
    return _CGI_DRAG_COMPONENT(**kwargs)


def canvas_select_rois(img: Image.Image, rois_default: dict, key_prefix: str = "roi_canvas") -> dict:
    """Editor gráfico real para ROI: señal y borde elegidos, arrastre en la imagen."""
    w, h = img.size
    rois_default = {k: clamp_roi(v, w, h) for k, v in rois_default.items()}
    colors = {"dzdt": "#1D4ED8", "ecg": "#16A34A", "fono": "#EA580C"}
    names = {"dzdt": "dZ/dt / impedancia", "ecg": "ECG", "fono": "Fonocardiograma"}
    safe_key = _safe_canvas_key(key_prefix)
    state_key = f"{safe_key}_rois_component_state"
    if state_key not in st.session_state or not _rois_are_valid(st.session_state.get(state_key), w, h):
        st.session_state[state_key] = rois_default
    current_rois = {k: clamp_roi(v, w, h) for k, v in st.session_state[state_key].items()}

    st.markdown(
        "<div class='guide'><b>Corrección gráfica de bordes.</b> Seleccione la señal y el borde. "
        "Luego arrastre directamente sobre la imagen. Sólo se mueve la señal activa, por eso no debería correrse un borde equivocado.</div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns([1.25, 1.6, 1])
    with c1:
        active_signal = st.radio(
            "Señal activa",
            SIGNALS,
            format_func=lambda x: names.get(x, x),
            horizontal=False,
            key=f"{safe_key}_active_signal",
        )
    with c2:
        active_action = st.selectbox(
            "Qué mover",
            [
                "auto", "mover", "izquierdo", "derecho", "superior", "inferior",
                "esquina_sup_izq", "esquina_sup_der", "esquina_inf_izq", "esquina_inf_der",
            ],
            format_func=lambda x: {
                "auto": "Borde/esquina más cercano",
                "mover": "Mover rectángulo completo",
                "izquierdo": "Borde izquierdo",
                "derecho": "Borde derecho",
                "superior": "Borde superior",
                "inferior": "Borde inferior",
                "esquina_sup_izq": "Esquina superior izquierda",
                "esquina_sup_der": "Esquina superior derecha",
                "esquina_inf_izq": "Esquina inferior izquierda",
                "esquina_inf_der": "Esquina inferior derecha",
            }.get(x, x),
            key=f"{safe_key}_active_action",
        )
    with c3:
        if st.button("Restablecer bordes", key=f"{safe_key}_reset_component_rois"):
            st.session_state[state_key] = rois_default
            current_rois = rois_default

    default_value = {"rois": current_rois}
    value = _cgi_drag_component(
        mode="rois",
        image=_pil_to_data_url(img),
        imageWidth=int(w),
        imageHeight=int(h),
        rois=current_rois,
        signals=SIGNALS,
        colors=colors,
        labels=names,
        activeSignal=active_signal,
        activeAction=active_action,
        displayMaxWidth=1120,
        key=f"{safe_key}_roi_drag_component",
        default=default_value,
    )
    if isinstance(value, dict) and isinstance(value.get("rois"), dict):
        rois = {k: clamp_roi(value["rois"].get(k, current_rois[k]), w, h) for k in SIGNALS}
    else:
        rois = current_rois
    st.session_state[state_key] = rois
    st.image(draw_rois(img, rois), caption="Sectores finales activos para digitalizar", use_container_width=True)
    return rois


def graph_adjust_cursors(chart_meta: dict, cursor_x: dict, key_prefix: str = "cursor_canvas") -> dict:
    """Editor gráfico real para QRS/B/C/X/Y: se elige un cursor y se arrastra en el gráfico."""
    img = Image.open(io.BytesIO(chart_meta["image_bytes"])).convert("RGB")
    w, h = img.size
    safe_key = _safe_canvas_key(key_prefix)
    state_key = f"{safe_key}_cursor_component_state"
    positions = {c: float(min(max(cursor_x.get(c, chart_meta["x0"]), chart_meta["x0"]), chart_meta["x1"])) for c in CURSORS}
    if state_key in st.session_state and isinstance(st.session_state[state_key], dict):
        for c in CURSORS:
            if c in st.session_state[state_key]:
                positions[c] = float(min(max(st.session_state[state_key][c], chart_meta["x0"]), chart_meta["x1"]))

    st.markdown(
        "<div class='guide'><b>Corrección gráfica de cursores.</b> Seleccione QRS, B, C, X o Y. "
        "Luego haga clic y arrastre la línea vertical sobre el gráfico. Sólo se mueve el cursor activo.</div>",
        unsafe_allow_html=True,
    )
    active_cursor = st.radio(
        "Cursor activo",
        CURSORS,
        horizontal=True,
        key=f"{safe_key}_active_cursor",
    )

    component_chart_meta = {
        "x0": float(chart_meta["x0"]),
        "x1": float(chart_meta["x1"]),
        "data_x0_pix": float(chart_meta["data_x0_pix"]),
        "data_x1_pix": float(chart_meta["data_x1_pix"]),
    }
    default_value = {"cursor_x": positions}
    value = _cgi_drag_component(
        mode="cursors",
        image=_pil_to_data_url(img),
        imageWidth=int(w),
        imageHeight=int(h),
        cursorX=positions,
        cursors=CURSORS,
        colors=CURSOR_COLORS,
        activeCursor=active_cursor,
        chartMeta=component_chart_meta,
        displayMaxWidth=1180,
        key=f"{safe_key}_cursor_drag_component",
        default=default_value,
    )
    if isinstance(value, dict) and isinstance(value.get("cursor_x"), dict):
        for c in CURSORS:
            if c in value["cursor_x"]:
                positions[c] = float(min(max(float(value["cursor_x"][c]), chart_meta["x0"]), chart_meta["x1"]))
    st.session_state[state_key] = positions
    return positions

# ============================================================
# EDITOR GRÁFICO V2 ROBUSTO (pointer events, sin redimensionar durante arrastre)
# ============================================================
# Esta versión reemplaza el editor anterior porque algunos navegadores/iframes de
# Streamlit Cloud no procesan bien mousemove/touchmove cuando el canvas se redimensiona
# durante el arrastre. Usa Pointer Events + setPointerCapture y emite el valor sólo al soltar.

_CGI_DRAG_COMPONENT_V2 = None


def _ensure_cgi_drag_component_v2() -> Path:
    comp_dir = Path(__file__).parent / "cgi_drag_component_v2"
    comp_dir.mkdir(exist_ok=True)
    index_path = comp_dir / "index.html"
    html = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  html, body { margin:0; padding:0; overflow:hidden; font-family: Inter, Arial, sans-serif; background: transparent; }
  #wrap { width:100%; box-sizing:border-box; }
  #bar { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:0 0 7px 0; color:#0f172a; font-size:14px; }
  #badge { font-weight:900; padding:5px 9px; border-radius:999px; background:#e0f2fe; color:#075985; border:1px solid #7dd3fc; }
  #hint { color:#334155; font-weight:600; }
  #canvas { display:block; border:2px solid #94a3b8; border-radius:12px; background:#fff; max-width:100%; touch-action:none; cursor:crosshair; user-select:none; }
</style>
</head>
<body>
<div id="wrap">
  <div id="bar"><span id="badge">Editor gráfico</span><span id="hint">Arrastre el borde/cursor activo y suelte.</span></div>
  <canvas id="canvas"></canvas>
</div>
<script>
(function(){
  const Streamlit = {
    ready: function(){ window.parent.postMessage({isStreamlitMessage:true, type:"streamlit:componentReady", apiVersion:1}, "*"); },
    height: function(h){ window.parent.postMessage({isStreamlitMessage:true, type:"streamlit:setFrameHeight", height:h}, "*"); },
    value: function(v){ window.parent.postMessage({isStreamlitMessage:true, type:"streamlit:setComponentValue", value:v, dataType:"json"}, "*"); }
  };
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const badge = document.getElementById("badge");
  const hint = document.getElementById("hint");
  let args = {};
  let image = new Image();
  let scale = 1;
  let rois = {};
  let cursorX = {};
  let dragging = false;
  let drag = null;
  let lastCanvasW = 0, lastCanvasH = 0;

  function clone(o){ return JSON.parse(JSON.stringify(o || {})); }
  function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }
  function dist(x1,y1,x2,y2){ return Math.hypot(x1-x2, y1-y2); }
  function color(hex, alpha){
    if(!hex || hex[0] !== '#') return hex || '#111827';
    const r=parseInt(hex.slice(1,3),16), g=parseInt(hex.slice(3,5),16), b=parseInt(hex.slice(5,7),16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  function getPoint(ev){
    const rect = canvas.getBoundingClientRect();
    return {x:(ev.clientX-rect.left)*(canvas.width/Math.max(1,rect.width)), y:(ev.clientY-rect.top)*(canvas.height/Math.max(1,rect.height))};
  }
  function setupCanvas(){
    const iw = Number(args.imageWidth || image.naturalWidth || 1);
    const ih = Number(args.imageHeight || image.naturalHeight || 1);
    const maxW = Number(args.displayMaxWidth || 1120);
    const available = Math.max(320, Math.min(maxW, window.innerWidth - 12));
    scale = Math.min(1, available / Math.max(1, iw));
    const cw = Math.max(1, Math.round(iw * scale));
    const ch = Math.max(1, Math.round(ih * scale));
    if(cw !== lastCanvasW || ch !== lastCanvasH){
      canvas.width = cw;
      canvas.height = ch;
      canvas.style.width = cw + "px";
      canvas.style.height = ch + "px";
      lastCanvasW = cw; lastCanvasH = ch;
      Streamlit.height(ch + 48);
    }
  }
  function draw(){
    ctx.clearRect(0,0,canvas.width,canvas.height);
    try { ctx.drawImage(image, 0, 0, canvas.width, canvas.height); }
    catch(e) { ctx.fillStyle="#fff"; ctx.fillRect(0,0,canvas.width,canvas.height); }
    if(args.mode === "rois") drawRois();
    else if(args.mode === "cursors") drawCursors();
  }
  function humanAction(a){
    const map = {auto:"borde/esquina cercano", mover:"mover rectángulo", izquierdo:"borde izquierdo", derecho:"borde derecho", superior:"borde superior", inferior:"borde inferior", esquina_sup_izq:"esquina superior izquierda", esquina_sup_der:"esquina superior derecha", esquina_inf_izq:"esquina inferior izquierda", esquina_inf_der:"esquina inferior derecha"};
    return map[a] || a;
  }
  function drawRois(){
    const active = args.activeSignal;
    const colors = args.colors || {};
    const labels = args.labels || {};
    const order = args.signals || Object.keys(rois);
    for(const k of order){
      const r = rois[k]; if(!r) continue;
      const x0=r.x0*scale, x1=r.x1*scale, y0=r.y0*scale, y1=r.y1*scale;
      const c = colors[k] || "#2563eb";
      const act = k === active;
      ctx.save();
      ctx.strokeStyle = c;
      ctx.lineWidth = act ? 5 : 2;
      ctx.setLineDash(act ? [] : [6,4]);
      ctx.fillStyle = color(c, act ? 0.08 : 0.025);
      ctx.fillRect(x0, y0, x1-x0, y1-y0);
      ctx.strokeRect(x0, y0, x1-x0, y1-y0);
      ctx.setLineDash([]);
      ctx.font = "bold 14px Arial";
      ctx.fillStyle = c;
      ctx.fillText(labels[k] || k, x0 + 8, Math.max(18, y0 + 20));
      if(act){
        const handles = [[x0,y0],[x1,y0],[x0,y1],[x1,y1],[(x0+x1)/2,y0],[(x0+x1)/2,y1],[x0,(y0+y1)/2],[x1,(y0+y1)/2]];
        ctx.fillStyle="#fff"; ctx.strokeStyle=c; ctx.lineWidth=3;
        for(const [hx,hy] of handles){ ctx.beginPath(); ctx.arc(hx, hy, 7, 0, Math.PI*2); ctx.fill(); ctx.stroke(); }
      }
      ctx.restore();
    }
    badge.textContent = "Bordes: " + ((args.labels||{})[active] || active || "");
    hint.textContent = "Mover: " + humanAction(args.activeAction || "auto") + ". Arrastre sobre la imagen y suelte.";
  }
  function pickAuto(r, px, py){
    const x0=r.x0*scale, x1=r.x1*scale, y0=r.y0*scale, y1=r.y1*scale;
    const list = [
      ["esquina_sup_izq", x0, y0], ["esquina_sup_der", x1, y0], ["esquina_inf_izq", x0, y1], ["esquina_inf_der", x1, y1],
      ["izquierdo", x0, py], ["derecho", x1, py], ["superior", px, y0], ["inferior", px, y1]
    ];
    let best="mover", bd=999999;
    for(const [name,x,y] of list){ const d=dist(px,py,x,y); if(d<bd){bd=d; best=name;} }
    if(bd <= 35) return best;
    if(px>=x0 && px<=x1 && py>=y0 && py<=y1) return "mover";
    return best;
  }
  function startRoi(p){
    const k = args.activeSignal;
    if(!k || !rois[k]) return;
    let action = args.activeAction || "auto";
    if(action === "auto") action = pickAuto(rois[k], p.x, p.y);
    dragging = true;
    drag = {mode:"rois", key:k, action:action, sx:p.x/scale, sy:p.y/scale, base:clone(rois[k])};
    moveRoi(p);
  }
  function moveRoi(p){
    if(!drag || drag.mode !== "rois") return;
    const iw=Number(args.imageWidth||1), ih=Number(args.imageHeight||1);
    const minSize = 10;
    const base = drag.base;
    const x = clamp(p.x/scale, 0, iw);
    const y = clamp(p.y/scale, 0, ih);
    const dx = x - drag.sx;
    const dy = y - drag.sy;
    let nr = clone(base);
    if(drag.action === "mover"){
      const ww=base.x1-base.x0, hh=base.y1-base.y0;
      nr.x0 = clamp(base.x0+dx,0,iw-ww); nr.x1 = nr.x0 + ww;
      nr.y0 = clamp(base.y0+dy,0,ih-hh); nr.y1 = nr.y0 + hh;
    } else {
      if(drag.action.includes("izq") || drag.action === "izquierdo") nr.x0 = clamp(x,0,base.x1-minSize);
      if(drag.action.includes("der") || drag.action === "derecho") nr.x1 = clamp(x,base.x0+minSize,iw);
      if(drag.action.includes("sup") || drag.action === "superior") nr.y0 = clamp(y,0,base.y1-minSize);
      if(drag.action.includes("inf") || drag.action === "inferior") nr.y1 = clamp(y,base.y0+minSize,ih);
    }
    for(const q of ["x0","x1","y0","y1"]) nr[q] = Math.round(nr[q]);
    rois[drag.key] = nr;
    draw();
  }
  function emitRois(){ Streamlit.value({rois: rois, edited: drag ? drag.key : args.activeSignal, action: drag ? drag.action : args.activeAction, t: Date.now()}); }

  function dataXToPx(xv){
    const cm=args.chartMeta || {};
    const x0=Number(cm.x0||0), x1=Number(cm.x1||1);
    const p0=Number(cm.data_x0_pix||0)*scale, p1=Number(cm.data_x1_pix||canvas.width)*scale;
    if(Math.abs(x1-x0)<1e-9) return p0;
    return p0 + ((Number(xv)-x0)/(x1-x0))*(p1-p0);
  }
  function pxToDataX(px){
    const cm=args.chartMeta || {};
    const x0=Number(cm.x0||0), x1=Number(cm.x1||1);
    const p0=Number(cm.data_x0_pix||0)*scale, p1=Number(cm.data_x1_pix||canvas.width)*scale;
    if(Math.abs(p1-p0)<1e-9) return x0;
    return clamp(x0 + ((px-p0)/(p1-p0))*(x1-x0), Math.min(x0,x1), Math.max(x0,x1));
  }
  function drawCursors(){
    const colors = args.colors || {};
    const cursors = args.cursors || Object.keys(cursorX);
    for(const c of cursors){
      const xp = dataXToPx(cursorX[c]);
      const col = colors[c] || "#111827";
      const act = c === args.activeCursor;
      ctx.save();
      if(act){ ctx.fillStyle=color(col,0.12); ctx.fillRect(xp-12,0,24,canvas.height); }
      ctx.strokeStyle=col; ctx.lineWidth=act?5:2; ctx.setLineDash(act?[]:[6,4]);
      ctx.beginPath(); ctx.moveTo(xp,0); ctx.lineTo(xp,canvas.height); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle=col; ctx.font="bold 15px Arial"; ctx.fillText(c, Math.min(canvas.width-34, xp+7), act?21:42);
      ctx.restore();
    }
    badge.textContent = "Cursor: " + (args.activeCursor || "");
    hint.textContent = "Haga clic o arrastre la línea vertical del cursor activo.";
  }
  function startCursor(p){
    const c = args.activeCursor;
    if(!c) return;
    dragging = true;
    drag = {mode:"cursors", key:c};
    moveCursor(p);
  }
  function moveCursor(p){
    if(!drag || drag.mode !== "cursors") return;
    cursorX[drag.key] = pxToDataX(p.x);
    draw();
  }
  function emitCursors(){ Streamlit.value({cursor_x: cursorX, edited: drag ? drag.key : args.activeCursor, t: Date.now()}); }

  function onPointerDown(ev){
    ev.preventDefault();
    try { canvas.setPointerCapture(ev.pointerId); } catch(e) {}
    const p = getPoint(ev);
    if(args.mode === "rois") startRoi(p);
    else if(args.mode === "cursors") startCursor(p);
  }
  function onPointerMove(ev){
    if(!dragging) return;
    ev.preventDefault();
    const p = getPoint(ev);
    if(drag.mode === "rois") moveRoi(p); else moveCursor(p);
  }
  function onPointerUp(ev){
    if(!dragging) return;
    ev.preventDefault();
    if(drag && drag.mode === "rois") emitRois();
    if(drag && drag.mode === "cursors") emitCursors();
    dragging = false;
    drag = null;
    try { canvas.releasePointerCapture(ev.pointerId); } catch(e) {}
  }
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", onPointerUp);
  window.addEventListener("resize", function(){ setupCanvas(); draw(); });

  function render(newArgs){
    args = newArgs || {};
    if(args.mode === "rois") rois = clone(args.rois || {});
    if(args.mode === "cursors") cursorX = clone(args.cursorX || {});
    image.onload = function(){ setupCanvas(); draw(); };
    if(image.src !== args.image){ image.src = args.image || ""; }
    else { setupCanvas(); draw(); }
  }
  window.addEventListener("message", function(event){
    if(!event.data || event.data.type !== "streamlit:render") return;
    render(event.data.args || {});
  });
  Streamlit.ready();
  Streamlit.height(520);
})();
</script>
</body>
</html>"""
    if (not index_path.exists()) or index_path.read_text(encoding="utf-8") != html:
        index_path.write_text(html, encoding="utf-8")
    return comp_dir


def _cgi_drag_component_v2(**kwargs):
    global _CGI_DRAG_COMPONENT_V2
    if _CGI_DRAG_COMPONENT_V2 is None:
        import streamlit.components.v1 as components
        _CGI_DRAG_COMPONENT_V2 = components.declare_component(
            "cgi_drag_component_v2",
            path=str(_ensure_cgi_drag_component_v2()),
        )
    return _CGI_DRAG_COMPONENT_V2(**kwargs)


def canvas_select_rois(img: Image.Image, rois_default: dict, key_prefix: str = "roi_canvas") -> dict:
    """Editor gráfico V2 para ROI, con arrastre real dentro de un canvas propio."""
    w, h = img.size
    rois_default = {k: clamp_roi(v, w, h) for k, v in rois_default.items()}
    colors = {"dzdt": "#1D4ED8", "ecg": "#16A34A", "fono": "#EA580C"}
    names = {"dzdt": "dZ/dt / impedancia", "ecg": "ECG", "fono": "Fonocardiograma"}
    safe_key = _safe_canvas_key(key_prefix)
    state_key = f"{safe_key}_rois_v2_state"
    if state_key not in st.session_state or not _rois_are_valid(st.session_state.get(state_key), w, h):
        st.session_state[state_key] = rois_default
    current_rois = {k: clamp_roi(v, w, h) for k, v in st.session_state[state_key].items()}

    st.markdown(
        "<div class='guide'><b>Editor real de bordes.</b> La imagen interactiva está justo debajo. "
        "Seleccione señal y borde, arrastre dentro de esa imagen y suelte. "
        "La vista final de abajo es sólo confirmatoria.</div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns([1.2, 1.7, 1])
    with c1:
        active_signal = st.radio(
            "Señal activa",
            SIGNALS,
            format_func=lambda x: names.get(x, x),
            key=f"{safe_key}_v2_active_signal",
        )
    with c2:
        active_action = st.selectbox(
            "Qué mover",
            ["auto", "mover", "izquierdo", "derecho", "superior", "inferior", "esquina_sup_izq", "esquina_sup_der", "esquina_inf_izq", "esquina_inf_der"],
            format_func=lambda x: {
                "auto": "Borde/esquina más cercano",
                "mover": "Mover rectángulo completo",
                "izquierdo": "Borde izquierdo",
                "derecho": "Borde derecho",
                "superior": "Borde superior",
                "inferior": "Borde inferior",
                "esquina_sup_izq": "Esquina superior izquierda",
                "esquina_sup_der": "Esquina superior derecha",
                "esquina_inf_izq": "Esquina inferior izquierda",
                "esquina_inf_der": "Esquina inferior derecha",
            }.get(x, x),
            key=f"{safe_key}_v2_active_action",
        )
    with c3:
        if st.button("Restablecer bordes", key=f"{safe_key}_v2_reset_rois"):
            st.session_state[state_key] = rois_default
            current_rois = rois_default

    value = _cgi_drag_component_v2(
        mode="rois",
        image=_pil_to_data_url(img),
        imageWidth=int(w),
        imageHeight=int(h),
        rois=current_rois,
        signals=SIGNALS,
        colors=colors,
        labels=names,
        activeSignal=active_signal,
        activeAction=active_action,
        displayMaxWidth=1120,
        key=f"{safe_key}_roi_drag_component_v2",
        default={"rois": current_rois},
    )
    if isinstance(value, dict) and isinstance(value.get("rois"), dict):
        rois = {k: clamp_roi(value["rois"].get(k, current_rois[k]), w, h) for k in SIGNALS}
        if rois != current_rois:
            st.session_state[f"{safe_key}_last_roi_edit"] = f"Último cambio aplicado: {value.get('edited', active_signal)} / {value.get('action', active_action)}"
    else:
        rois = current_rois
    st.session_state[state_key] = rois
    if st.session_state.get(f"{safe_key}_last_roi_edit"):
        st.success(st.session_state[f"{safe_key}_last_roi_edit"])
    st.image(draw_rois(img, rois), caption="Vista final confirmatoria: sectores activos para digitalizar", use_container_width=True)
    return rois


def graph_adjust_cursors(chart_meta: dict, cursor_x: dict, key_prefix: str = "cursor_canvas") -> dict:
    """Editor gráfico V2 para cursores QRS/B/C/X/Y."""
    img = Image.open(io.BytesIO(chart_meta["image_bytes"])).convert("RGB")
    w, h = img.size
    safe_key = _safe_canvas_key(key_prefix)
    state_key = f"{safe_key}_cursor_v2_state"
    positions = {c: float(min(max(cursor_x.get(c, chart_meta["x0"]), chart_meta["x0"]), chart_meta["x1"])) for c in CURSORS}
    if state_key in st.session_state and isinstance(st.session_state[state_key], dict):
        for c in CURSORS:
            if c in st.session_state[state_key]:
                positions[c] = float(min(max(st.session_state[state_key][c], chart_meta["x0"]), chart_meta["x1"]))

    st.markdown(
        "<div class='guide'><b>Editor real de cursores.</b> Seleccione QRS, B, C, X o Y. "
        "Luego haga clic o arrastre la línea dentro del gráfico interactivo y suelte.</div>",
        unsafe_allow_html=True,
    )
    active_cursor = st.radio("Cursor activo", CURSORS, horizontal=True, key=f"{safe_key}_v2_active_cursor")
    component_chart_meta = {
        "x0": float(chart_meta["x0"]),
        "x1": float(chart_meta["x1"]),
        "data_x0_pix": float(chart_meta["data_x0_pix"]),
        "data_x1_pix": float(chart_meta["data_x1_pix"]),
    }
    value = _cgi_drag_component_v2(
        mode="cursors",
        image=_pil_to_data_url(img),
        imageWidth=int(w),
        imageHeight=int(h),
        cursorX=positions,
        cursors=CURSORS,
        colors=CURSOR_COLORS,
        activeCursor=active_cursor,
        chartMeta=component_chart_meta,
        displayMaxWidth=1060,
        key=f"{safe_key}_cursor_drag_component_v2",
        default={"cursor_x": positions},
    )
    if isinstance(value, dict) and isinstance(value.get("cursor_x"), dict):
        new_positions = positions.copy()
        for c in CURSORS:
            if c in value["cursor_x"]:
                new_positions[c] = float(min(max(float(value["cursor_x"][c]), chart_meta["x0"]), chart_meta["x1"]))
        if new_positions != positions:
            st.session_state[f"{safe_key}_last_cursor_edit"] = f"Último cursor aplicado: {value.get('edited', active_cursor)}"
        positions = new_positions
    st.session_state[state_key] = positions
    if st.session_state.get(f"{safe_key}_last_cursor_edit"):
        st.success(st.session_state[f"{safe_key}_last_cursor_edit"])
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
                st.image(
                    draw_digitization_overlay(img2, rois, {"dzdt": dzdt, "ecg": ecg, "fono": fono}),
                    caption="Control de calidad: trazo rojo digitalizado superpuesto sobre la curva original. Si no coincide, ajuste levemente el ROI para excluir texto/ejes/grilla intensa.",
                    use_container_width=True,
                )
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
                    manual_x_final = {cur: float(min(max(adjusted_x.get(cur, auto[cur]["x"]), x0c), x1c)) for cur in CURSORS}
                    with st.expander("Respaldo numérico opcional", expanded=False):
                        st.caption("Dejar cerrado para trabajar todo desde el gráfico. Abrir solo si el navegador no permite arrastrar algún cursor.")
                        cols = st.columns(5)
                        for i, cur in enumerate(CURSORS):
                            with cols[i]:
                                manual_x_final[cur] = float(st.number_input(f"Cursor {cur}", min_value=float(x0c), max_value=float(x1c), value=float(manual_x_final[cur]), step=1.0, key=f"cursor_num_{cur}_{source2}_{page2}_{preset}"))
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
            st.markdown("#### Crear o redefinir clave de administrador")
            with st.container():
                admin_password_ui(user)
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
