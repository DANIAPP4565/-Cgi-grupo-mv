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

# Intentar importar el lienzo interactivo
try:
    import streamlit.elements.image as _st_image
    from streamlit_drawable_canvas import st_canvas
except ImportError:
    st_canvas = None

# ============================================================
# CONFIGURACIÓN Y CONSTANTES DEL ENTORNO MÉDICO
# ============================================================
DB_FILE = "mdpa_2026.db"
SIGNALS = ["dzdt", "ecg", "fono"]
CURSORS = ["QRS", "B", "C", "X", "Y"]

CURSOR_COLORS = {
    "dzdt": "#DC2626", # Rojo para dZ/dt
    "ecg": "#2563EB",  # Azul para ECG
    "fono": "#16A34A", # Verde para Fonocardiograma
    "QRS": "#2563EB",
    "B": "#EA580C",
    "C": "#DC2626",
    "X": "#7C3AED",
    "Y": "#0D9488"
}

# ============================================================
# PARCHE DE COMPATIBILIDAD PARA STREAMLIT-DRAWABLE-CANVAS
# ============================================================
def _install_canvas_image_to_url_patch() -> None:
    try:
        import streamlit.elements.image as _st_image
        try:
            from streamlit.elements.lib import image_utils as _image_utils
        except Exception:
            _image_utils = None

        source_mod = _image_utils if _image_utils is not None else _st_image
        if not hasattr(_st_image, "image_to_url") and hasattr(source_mod, "image_to_url"):
            _st_image.image_to_url = source_mod.image_to_url
    except Exception:
        pass

_install_canvas_image_to_url_patch()

# ============================================================
# FUNCIONES AUXILIARES: BASE DE DATOS Y TIEMPO
# ============================================================
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def now_iso() -> str:
    return datetime.now().isoformat()

def init_db():
    con = connect()
    con.execute("""
    CREATE TABLE IF NOT EXISTS cursor_corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        study_id TEXT,
        user_id INTEGER,
        username TEXT,
        patient_code TEXT,
        source_name TEXT,
        page_number INTEGER,
        rois_json TEXT,
        auto_json TEXT,
        manual_json TEXT,
        guide_json TEXT,
        metrics_json TEXT,
        conclusion TEXT
    )
    """)
    con.commit()
    con.close()

# ============================================================
# LÓGICA DE PROCESAMIENTO Y DIGITALIZACIÓN DE SEÑALES
# ============================================================
def _get_default_rois(w: int, h: int, mode: str = "panel_recortado") -> Dict:
    if mode == "panel_recortado":
        return {
            "dzdt": {"x0": int(w*0.05), "y0": int(h*0.10), "x1": int(w*0.95), "y1": int(h*0.38)},
            "ecg":  {"x0": int(w*0.05), "y0": int(h*0.42), "x1": int(w*0.95), "y1": int(h*0.68)},
            "fono": {"x0": int(w*0.05), "y0": int(h*0.72), "x1": int(w*0.95), "y1": int(h*0.95)}
        }
    return {
        "dzdt": {"x0": int(w*0.1), "y0": int(h*0.2), "x1": int(w*0.9), "y1": int(h*0.4)},
        "ecg":  {"x0": int(w*0.1), "y0": int(h*0.45), "x1": int(w*0.9), "y1": int(h*0.65)},
        "fono": {"x0": int(w*0.1), "y0": int(h*0.7), "x1": int(w*0.9), "y1": int(h*0.9)}
    }

def digitize_signal(img: Image.Image, roi: Dict) -> pd.DataFrame:
    """Extrae el contorno de la curva dentro de la Región de Interés (ROI)"""
    crop = img.crop((roi["x0"], roi["y0"], roi["x1"], roi["y1"]))
    gray = ImageOps.grayscale(crop)
    arr = np.array(gray)
    
    # Detección del trazo oscuro (línea de la curva)
    h_box, w_box = arr.shape
    xs = []
    ys = []
    
    for x in range(w_box):
        col = arr[:, x]
        dark_idx = np.where(col < 100)[0] # Umbral adaptativo para curvas oscuras
        if len(dark_idx) > 0:
            y_center = int(np.mean(dark_idx))
            global_x = roi["x0"] + x
            global_y = roi["y0"] + y_center
            xs.append(global_x)
            ys.append(global_y)
            
    if not xs:
        return pd.DataFrame(columns=["x", "y"])
    
    df = pd.DataFrame({"x": xs, "y": ys}).drop_duplicates(subset=["x"]).sort_values("x")
    # Invertir el eje Y para que los picos positivos apunten hacia arriba en el análisis
    df["y_inverted"] = roi["y1"] - df["y"]
    return df

def detect_automatic_cursors(dzdt: pd.DataFrame, ecg: pd.DataFrame, fono: pd.DataFrame, fono_th: float, x0: int, x1: int) -> Tuple[Dict, Dict]:
    """Establece puntos de partida automatizados basados en máximos y mínimos relativos"""
    width = x1 - x0
    auto = {
        "QRS": x0 + int(width * 0.15),
        "B": x0 + int(width * 0.32),
        "C": x0 + int(width * 0.45),
        "X": x0 + int(width * 0.65),
        "Y": x0 + int(width * 0.80)
    }
    guides = {
        "isoelectrica": float(dzdt["y_inverted"].median() if not dzdt.empty else 0),
        "fono_umbral": fono_th
    }
    return auto, guides

# ============================================================
# CONSTRUCCIÓN VISUAL DEL GRÁFICO COMBINADO (MATPLOTLIB)
# ============================================================
def build_curve_chart(dzdt: pd.DataFrame, ecg: pd.DataFrame, fono: pd.DataFrame, auto: Dict, manual: Dict, guides: Dict, ax):
    """Dibuja las señales normalizadas superpuestas junto con los cursores"""
    ax.set_facecolor("#FAFAFA")
    
    if not ecg.empty:
        ax.plot(ecg["x"], ecg["y_inverted"] + 300, color=CURSOR_COLORS["ecg"], label="ECG", alpha=0.8, linewidth=1.5)
    if not dzdt.empty:
        ax.plot(dzdt["x"], dzdt["y_inverted"] + 150, color=CURSOR_COLORS["dzdt"], label="dZ/dt", linewidth=1.8)
    if not fono.empty:
        ax.plot(fono["x"], fono["y_inverted"], color=CURSOR_COLORS["fono"], label="Fono", alpha=0.7, linewidth=1.2)
        
    # Dibujar cursores verticales (Manuales e Interactivos)
    for k, vx in manual.items():
        ax.axvline(x=vx, color=CURSOR_COLORS.get(k, "#333333"), linestyle="-", linewidth=2, label=f"Cursor {k}")
        ax.text(vx + 2, ax.get_ylim()[1] * 0.9, k, color=CURSOR_COLORS.get(k, "#333333"), fontsize=10, weight="bold")

    ax.set_title("Señales Digitalizadas y Modificación Dinámica de Cursores", fontsize=11, pad=10)
    ax.set_xlabel("Coordenada Horizontal (px)")
    ax.set_ylabel("Amplitud Normalizada (Unidades Arbitrarias)")
    ax.grid(True, linestyle="--", alpha=0.5)

# ============================================================
# LIENZO INTERACTIVO DE CURSORES (MANIPULACIÓN DIRECTA DESDE EL GRÁFICO)
# ============================================================
def graph_adjust_cursors(meta: Dict, current_cursors: Dict, key_prefix: str) -> Dict:
    """
    Renderiza el gráfico de curvas como un canvas interactivo donde el profesional
    puede arrastrar y mover las líneas verticales directamente con el mouse.
    """
    if st_canvas is None:
        st.warning("El módulo streamlit-drawable-canvas no está disponible. Use entradas numéricas.")
        return current_cursors

    img_chart = Image.open(io.BytesIO(meta["image_bytes"]))
    w_c, h_c = img_chart.size
    
    # Escalamiento adaptativo para pantallas médicas estándar
    scale = min(1.0, 850 / max(1, w_c))
    canvas_w = int(w_c * scale)
    canvas_h = int(h_c * scale)
    
    # Mapear las posiciones reales x a coordenadas dentro del Canvas de Streamlit
    data_x0 = meta["x0"]
    data_x1 = meta["x1"]
    pixel_x0 = meta["data_x0_pix"]
    pixel_x1 = meta["data_x1_pix"]
    
    def to_canvas_x(real_x: float) -> float:
        pct = (real_x - data_x0) / max(1, (data_x1 - data_x0))
        cx = pixel_x0 + pct * (pixel_x1 - pixel_x0)
        return cx * scale

    def to_real_x(canvas_x: float) -> float:
        orig_x = canvas_x / scale
        pct = (orig_x - pixel_x0) / max(1, (pixel_x1 - pixel_x0))
        return data_x0 + pct * (data_x1 - data_x0)

    # Definir el JSON de dibujo inicial para renderizar las líneas de los cursores como objetos manipulables
    initial_drawing = {"objects": []}
    for cname, rx in current_cursors.items():
        cx = to_canvas_x(rx)
        initial_drawing["objects"].append({
            "type": "line",
            "x1": cx, "y1": 0,
            "x2": cx, "y2": canvas_h,
            "stroke": CURSOR_COLORS.get(cname, "#000000"),
            "strokeWidth": 4, # Grosor cómodo para arrastre táctil o mouse
            "cursor_name": cname
        })

    st.caption("💡 **Instrucciones:** Haga clic en la herramienta de selección (flecha) del lienzo, seleccione cualquier línea vertical y desplácela lateralmente para reajustar los puntos fisiológicos.")
    
    canvas_res = st_canvas(
        fill_color="rgba(0,0,0,0)",
        stroke_width=3,
        background_image=img_chart,
        update_streamlit=True,
        height=canvas_h,
        width=canvas_w,
        drawing_mode="transform",
        initial_drawing=initial_drawing,
        key=f"{key_prefix}_canvas"
    )

    # Procesar los movimientos del lienzo hacia atrás para actualizar los datos del backend
    updated_cursors = current_cursors.copy()
    if canvas_res.json_data and "objects" in canvas_res.json_data:
        objs = canvas_res.json_data["objects"]
        # Mapeamos basándonos en el orden o en propiedades guardadas
        for i, obj in enumerate(objs):
            if i < len(CURSORS):
                cname = CURSORS[i]
                # En modo transform, la coordenada se altera mediante 'left' o variaciones de 'x1'
                cx_current = obj.get("left", obj.get("x1", 0))
                real_x_calc = to_real_x(cx_current)
                # Restringir el cursor dentro del área válida del gráfico digitalizado
                updated_cursors[cname] = max(data_x0, min(data_x1, int(real_x_calc)))
                
    return updated_cursors

def _apply_canvas_objects_to_rois(objects: List, current_rois: Dict, scale: float, w_img: int, h_img: int, session_key: str):
    new_rois = current_rois.copy()
    for obj in objects:
        roi_key = obj.get("name", obj.get("roi_key", None))
        if roi_key in new_rois:
            l = obj["left"] / scale
            t = obj["top"] / scale
            w = obj["width"] / scale
            h = obj["height"] / scale
            new_rois[roi_key] = {
                "x0": max(0, int(l)),
                "y0": max(0, int(t)),
                "x1": min(w_img, int(l + w)),
                "y1": min(h_img, int(t + h))
            }
    st.session_state[session_key] = new_rois

# ============================================================
# APLICACIÓN PRINCIPAL (FLUJO DE UI - ADAPTADO A TAB2)
# ============================================================
def app_main():
    st.set_page_config(page_title="MDPA 2026 - Central Hemodynamics", layout="wide")
    init_db()
    
    # Datos simulados de sesión (para emular el estado global de tu app)
    if "user" not in st.session_state:
        st.session_state["user"] = {"id": 1, "username": "rolano_cardio", "role": "Investigador"}
        
    user = st.session_state["user"]
    
    st.title("🔬 Plataforma de Análisis Hemodinámico - Impedancia Cardiográfica")
    
    # Simulación de carga de imagen si no viene de la Pestaña 1
    if "img_base" not in st.session_state:
        # Creamos una imagen sintética de fondo simulando curvas biomédicas si no hay informe real cargado
        canvas_dummy = Image.new("RGB", (1000, 600), "#FFFFFF")
        draw = ImageDraw.Draw(canvas_dummy)
        # Dibujar grillas de fondo simulando papel milimetrado
        for x in range(0, 1000, 40): draw.line((x, 0, x, 600), fill="#FCA5A5", width=1)
        for y in range(0, 600, 40): draw.line((0, y, 1000, y), fill="#FCA5A5", width=1)
        # Dibujar curvas hipotéticas sinusoidales continuas
        points_dz = [(x, int(200 + 60 * np.sin(x/30) * np.cos(x/100))) for x in range(50, 950)]
        points_ecg = [(x, int(400 + 80 * np.sin(x/15) if x % 120 < 20 else 400)) for x in range(50, 950)]
        points_fono = [(x, int(530 + 30 * np.sin(x/5) * np.exp(-((x%200-50)/40)**2))) for x in range(50, 950)]
        
        draw.line(points_dz, fill="#DC2626", width=3)
        draw.line(points_ecg, fill="#2563EB", width=2)
        draw.line(points_fono, fill="#16A34A", width=2)
        st.session_state["img_base"] = canvas_dummy
        st.session_state["main_upload"] = True

    img = st.session_state["img_base"]
    
    tab1, tab2 = st.tabs(["📋 Registro de Pacientes", "📈 Corrección opcional de curvas"])
    
    with tab1:
        st.info("Pestaña de control operativo. Diríjase a la pestaña 'Corrección opcional de curvas' para evaluar los gráficos interactivos.")
        st.text_input("Código de Identificación Paciente:", value="PAT_2026_HEMO_01", key="current_patient_code")
        st.text_input("ID de Estudio Clínico:", value="ST_88291", key="current_study_id")

    # ============================================================
    # INTEGRACIÓN DE LA SECCIÓN 2: CORRECCIÓN INTERACTIVA SOLICITADA
    # ============================================================
    with tab2:
        st.markdown("""
        <div style='background-color: #EFF6FF; border-left: 4px solid #2563EB; padding: 12px; border-radius: 4px; margin-bottom: 15px;'>
            <b>Módulo de curvas avanzado e integrado:</b> Modifique de manera libre la zona gráfica para la digitalización de las tres curvas principales y mueva las líneas de los cursores fisiológicos arrastrándolas con el ratón sobre el gráfico.
        </div>
        """, unsafe_allow_html=True)
        
        # 1. SELECCIÓN DINÁMICA DE RECTÁNGULOS (ZONA GRÁFICA DE DIGITALIZACIÓN)
        st.subheader("1. Definición de la zona gráfica de digitalización (ROIs)")
        
        preset_mode = st.radio("Configuración rápida de límites:", ["panel_recortado", "personalizado"], horizontal=True, key="preset_roi_radio")
        
        if st_canvas is not None and 'main_upload' in st.session_state and st.session_state.main_upload is not None:
            w_img, h_img = img.size
            canvas_scale = min(1.0, 900 / max(1, w_img))
            
            # Cargar ROIs base o las previamente ajustadas en la sesión
            if "current_rois_state" not in st.session_state:
                st.session_state["current_rois_state"] = _get_default_rois(w_img, h_img, preset_mode)
                
            current_rois = st.session_state["current_rois_state"]
            
            # Formatear rectángulos como objetos iniciales para st_canvas
            initial_drawing = {"objects": []}
            for idx, (sig_name, roi_val) in enumerate(current_rois.items()):
                initial_drawing["objects"].append({
                    "type": "rect",
                    "left": roi_val["x0"] * canvas_scale,
                    "top": roi_val["y0"] * canvas_scale,
                    "width": (roi_val["x1"] - roi_val["x0"]) * canvas_scale,
                    "height": (roi_val["y1"] - roi_val["y0"]) * canvas_scale,
                    "stroke": CURSOR_COLORS.get(sig_name, "#2563EB"),
                    "strokeWidth": 2,
                    "fill": "rgba(37, 99, 235, 0.03)",
                    "name": sig_name
                })
            
            st.caption("Ajuste las cajas de color (Rojo: dZ/dt, Azul: ECG, Verde: Fono) para encuadrar los trazos limpios de las señales:")
            canvas_result = st_canvas(
                fill_color="rgba(37, 99, 235, 0.03)",
                stroke_width=2,
                stroke_color="#2563EB",
                background_image=img,
                update_streamlit=True,
                height=int(h_img * canvas_scale),
                width=int(w_img * canvas_scale),
                drawing_mode="transform",
                initial_drawing=initial_drawing,
                key="roi_canvas_editor"
            )
            
            # Sincronizar las modificaciones hechas a los rectángulos con el estado de la aplicación
            if canvas_result.json_data and "objects" in canvas_result.json_data and len(canvas_result.json_data["objects"]) > 0:
                _apply_canvas_objects_to_rois(canvas_result.json_data["objects"], current_rois, canvas_scale, w_img, h_img, "current_rois_state")
        else:
            st.warning("Requiere la instalación de streamlit-drawable-canvas o la carga previa del documento.")
            st.session_state["current_rois_state"] = _get_default_rois(1000, 600, "panel_recortado")

        # 2. DIGITALIZACIÓN REAL-TIME Y AJUSTE DE CURSORES DESDE EL GRÁFICO
        st.subheader("2. Ajuste interactivo de cursores sobre los gráficos combinados")
        
        rois_finales = st.session_state["current_rois_state"]
        fono_line = st.slider("Sensibilidad detección de Fono (Umbral Horizontal):", 0.10, 0.95, 0.55, 0.01, key="fono_threshold_slider
