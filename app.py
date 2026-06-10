from __future__ import annotations

import io
import json
import sqlite3
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageOps
import matplotlib.pyplot as plt

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

APP_TITLE = "App para Repositorio y aprendizaje en Cardiografía de Impedancia"
APP_SUBTITLE = "Corrección didáctica de cursores B, C, X e Y con ECG + dZ/dt + fonocardiograma"
APP_DEVELOPER = "Desarrollador: Dr. Olano Ricardo Daniel — Cardiólogo Hipertensólogo"
DB_PATH = Path("cgi_cursores.sqlite3")
CURSORS = ["B", "C", "X", "Y"]

st.set_page_config(page_title="CGI cursores", page_icon="🫀", layout="wide")


def apply_css() -> None:
    st.markdown(
        """
        <style>
        .stApp{background:linear-gradient(180deg,#F5FAFD,#FFFFFF)!important;}
        .block-container{max-width:1450px;padding-top:1rem;padding-bottom:2rem;}
        .hero{background:linear-gradient(90deg,#082F49,#075985);border-radius:18px;padding:18px 22px;margin-bottom:14px;color:white;box-shadow:0 10px 24px rgba(8,47,73,.18)}
        .hero h1{margin:0;color:white!important;font-size:1.48rem}.hero p{margin:.25rem 0;color:#E0F2FE}.hero .dev{font-weight:800;color:#BAE6FD;margin-top:.35rem}
        .box{background:white;border:1px solid #D7E3EE;border-radius:14px;padding:12px 14px;margin-bottom:10px;box-shadow:0 4px 12px rgba(15,23,42,.05)}
        .guide{background:#EAF6FF;border:1px solid #BAE6FD;border-radius:14px;padding:12px;color:#075985;margin-bottom:10px;}
        .ok{background:#ECFDF5;border:1px solid #99F6E4;border-radius:14px;padding:12px;color:#064E3B;margin-bottom:10px;}
        .warn{background:#FFF7ED;border:1px solid #FED7AA;border-radius:14px;padding:12px;color:#7C2D12;margin-bottom:10px;}
        .small{font-size:.88rem;color:#556575;}
        .stButton>button,.stDownloadButton>button{background:#075985!important;color:white!important;border-radius:10px!important;border:1px solid #082F49!important;font-weight:800!important;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    con = connect()
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS sesiones(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            paciente TEXT,
            fecha_estudio TEXT,
            condicion TEXT,
            archivo TEXT,
            pagina INTEGER,
            rois_json TEXT,
            auto_json TEXT,
            manual_json TEXT,
            guia_json TEXT,
            conclusion TEXT
        )
        """
    )
    con.commit()
    con.close()


def save_session(paciente: str, fecha: str, condicion: str, archivo: str, pagina: int,
                 rois: dict, auto: dict, manual: dict, guia: dict, conclusion: str) -> int:
    con = connect()
    cur = con.execute(
        """
        INSERT INTO sesiones(created_at,paciente,fecha_estudio,condicion,archivo,pagina,rois_json,auto_json,manual_json,guia_json,conclusion)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            datetime.now().isoformat(timespec="seconds"), paciente, fecha, condicion, archivo, pagina,
            json.dumps(rois, ensure_ascii=False), json.dumps(auto, ensure_ascii=False),
            json.dumps(manual, ensure_ascii=False), json.dumps(guia, ensure_ascii=False), conclusion,
        ),
    )
    con.commit()
    sid = int(cur.lastrowid)
    con.close()
    return sid


def load_sessions() -> pd.DataFrame:
    con = connect()
    try:
        return pd.read_sql_query("SELECT * FROM sesiones ORDER BY created_at DESC", con)
    finally:
        con.close()


def render_pdf_page(pdf_bytes: bytes, page: int, zoom: float) -> Tuple[Image.Image, int]:
    if fitz is None:
        raise RuntimeError("No se pudo importar PyMuPDF. Revise requirements.txt y runtime.txt.")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        total = doc.page_count
        page = int(max(0, min(page, total - 1)))
        pix = doc.load_page(page).get_pixmap(matrix=fitz.Matrix(float(zoom), float(zoom)), alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), total
    finally:
        doc.close()


def open_upload(uploaded) -> Tuple[Image.Image, int, str]:
    raw = uploaded.getvalue()
    suffix = Path(uploaded.name).suffix.lower()
    if suffix == ".pdf":
        if fitz is None:
            st.error("Falta PyMuPDF. En Streamlit Cloud agregue requirements.txt y runtime.txt del ZIP corregido.")
            st.stop()
        doc = fitz.open(stream=raw, filetype="pdf")
        total = doc.page_count
        doc.close()
        col1, col2 = st.columns(2)
        with col1:
            page = st.number_input("Página con ECG + dZ/dt + fonocardiograma", min_value=1, max_value=max(1, total), value=min(2, max(1, total)), step=1)
        with col2:
            zoom = st.slider("Resolución de conversión del PDF", 1.5, 4.0, 2.5, 0.25)
        img, _ = render_pdf_page(raw, int(page) - 1, float(zoom))
        return img.convert("RGB"), int(page), uploaded.name
    return Image.open(io.BytesIO(raw)).convert("RGB"), 1, uploaded.name


def default_rois(w: int, h: int, preset: str = "panel_derecho") -> Dict[str, Dict[str, int]]:
    """Recortes iniciales.

    preset="panel_derecho" corrige el error señalado: la digitalización debe tomarse
    del panel pequeño de la derecha del informe Exxer, marcado en amarillo por el usuario,
    no de la tira larga inferior ni de las tablas.
    """
    if preset == "panel_derecho":
        # Área amarilla derecha del informe: x 72-97 % de la página.
        x0, x1 = int(w * 0.725), int(w * 0.965)
        return {
            # En ese panel la curva dZ/dt está arriba, el ECG en el medio y el fono abajo.
            "dzdt": {"x0": x0, "x1": x1, "y0": int(h * 0.135), "y1": int(h * 0.405)},
            "ecg":  {"x0": x0, "x1": x1, "y0": int(h * 0.440), "y1": int(h * 0.610)},
            "fono": {"x0": x0, "x1": x1, "y0": int(h * 0.605), "y1": int(h * 0.730)},
        }

    # Opción alternativa para trazados largos inferiores, si se quiere entrenar sobre toda la tira.
    x0, x1 = int(w * 0.05), int(w * 0.94)
    return {
        "ecg": {"x0": x0, "x1": x1, "y0": int(h * 0.74), "y1": int(h * 0.84)},
        "dzdt": {"x0": x0, "x1": x1, "y0": int(h * 0.84), "y1": int(h * 0.98)},
        "fono": {"x0": x0, "x1": x1, "y0": int(h * 0.60), "y1": int(h * 0.72)},
    }


def clamp_roi(r: Dict[str, int], w: int, h: int) -> Dict[str, int]:
    x0 = int(max(0, min(r["x0"], w - 3)))
    x1 = int(max(x0 + 3, min(r["x1"], w)))
    y0 = int(max(0, min(r["y0"], h - 3)))
    y1 = int(max(y0 + 3, min(r["y1"], h)))
    return {"x0": x0, "x1": x1, "y0": y0, "y1": y1}


def draw_rois(img: Image.Image, rois: dict) -> Image.Image:
    out = img.copy().convert("RGB")
    d = ImageDraw.Draw(out)
    colors = {"ecg": "green", "dzdt": "blue", "fono": "orange"}
    names = {"ecg": "ECG", "dzdt": "dZ/dt", "fono": "Fonocardiograma"}
    for key, roi in rois.items():
        r = clamp_roi(roi, *out.size)
        d.rectangle([r["x0"], r["y0"], r["x1"], r["y1"]], outline=colors[key], width=max(3, out.size[0] // 400))
        d.text((r["x0"] + 6, r["y0"] + 6), names[key], fill=colors[key])
    return out


def smooth(y: np.ndarray, window: int = 9) -> np.ndarray:
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


def make_mask(rgb: np.ndarray, mode: str) -> np.ndarray:
    r = rgb[:, :, 0].astype(int)
    g = rgb[:, :, 1].astype(int)
    b = rgb[:, :, 2].astype(int)
    if mode == "ecg":
        mask = (g > r + 8) & (g > b + 3) & (r < 230) & (b < 230)
    elif mode == "dzdt":
        mask = (b > r + 4) & (b > g + 1) & (r < 235) & (g < 235)
    elif mode == "fono":
        mask = (r > g + 4) & (g > b + 3) & (r > 100) & (b < 210)
    else:
        gray = np.array(ImageOps.grayscale(Image.fromarray(rgb.astype("uint8"))))
        mask = gray <= np.percentile(gray, 35)
    if int(mask.sum()) < 50:
        gray = np.array(ImageOps.grayscale(Image.fromarray(rgb.astype("uint8"))))
        mask = gray <= np.percentile(gray, 30)
    return mask


def digitize(img: Image.Image, roi: Dict[str, int], mode: str) -> pd.DataFrame:
    arr = np.asarray(img.convert("RGB"))
    r = clamp_roi(roi, *img.size)
    crop = arr[r["y0"]:r["y1"], r["x0"]:r["x1"], :]
    if crop.size == 0:
        return pd.DataFrame(columns=["x", "y", "yn"])
    mask = make_mask(crop, mode)
    ch, cw = mask.shape
    max_dense = max(5, int(ch * 0.50))
    xs, ys = [], []
    for cx in range(cw):
        rows = np.where(mask[:, cx])[0]
        if 0 < len(rows) <= max_dense:
            xs.append(r["x0"] + cx)
            ys.append(r["y0"] + float(np.median(rows)))
    if len(xs) < 8:
        return pd.DataFrame(columns=["x", "y", "yn"])
    df = pd.DataFrame({"x": xs, "y": ys}).groupby("x", as_index=False)["y"].median()
    y = smooth(df["y"].to_numpy(float), max(5, int(len(df) * 0.018)))
    yn = r["y1"] - y
    yn = yn - np.nanmin(yn)
    den = np.nanmax(yn)
    if den > 0:
        yn = yn / den
    df["y"] = y
    df["yn"] = yn
    return df


def interp(df: pd.DataFrame, x: np.ndarray) -> np.ndarray:
    if df.empty or len(df) < 2:
        return np.full_like(x, np.nan, dtype=float)
    return np.interp(x, df["x"].to_numpy(float), df["yn"].to_numpy(float), left=np.nan, right=np.nan)


def near_y(df: pd.DataFrame, xval: float) -> float:
    if df.empty:
        return 0.5
    idx = int(np.nanargmin(np.abs(df["x"].to_numpy(float) - float(xval))))
    return float(df.iloc[idx]["yn"])


def detect(ecg: pd.DataFrame, dzdt: pd.DataFrame, fono: pd.DataFrame, xmin: float, xmax: float, fono_line: float) -> Tuple[dict, dict]:
    auto = {c: {"x": float(xmin + (i + 1) * (xmax - xmin) / 5), "y": 0.5} for i, c in enumerate(CURSORS)}
    guia = {"qrs": np.nan, "s1": np.nan, "s2": np.nan, "fono_line": float(fono_line)}
    if not ecg.empty:
        e = ecg.dropna()
        if len(e):
            guia["qrs"] = float(e.iloc[int(np.nanargmax(e["yn"].to_numpy(float)))]["x"])
    if not fono.empty:
        f = fono.dropna()
        y = smooth(f["yn"].to_numpy(float), max(5, int(len(f) * 0.025)))
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
        centers = [float(np.nanmean(f["x"].to_numpy(float)[a:b])) for a, b in groups]
        if len(centers) >= 1:
            guia["s1"] = centers[0]
        if len(centers) >= 2:
            guia["s2"] = centers[1]
    if not dzdt.empty and len(dzdt) >= 5:
        d = dzdt.dropna()
        x = d["x"].to_numpy(float)
        y = smooth(d["yn"].to_numpy(float), max(5, int(len(d) * 0.025)))
        ci = int(np.nanargmax(y))
        bi = max(0, ci - max(3, len(y) // 10))
        if ci > 5:
            seg = y[:ci]
            grad = np.gradient(seg)
            bi = int(np.nanargmax(grad))
        xi = min(len(y) - 1, ci + max(3, len(y) // 8))
        post = y[ci + 1:]
        if len(post) >= 4:
            xi = ci + 1 + int(np.nanargmin(post))
        yi = min(len(y) - 1, xi + max(3, len(y) // 10))
        post2 = y[xi + 1:]
        if len(post2) >= 4:
            yi = xi + 1 + int(np.nanargmax(post2))
        for name, idx in {"B": bi, "C": ci, "X": xi, "Y": yi}.items():
            auto[name] = {"x": float(x[idx]), "y": near_y(dzdt, float(x[idx]))}
    return auto, guia


def cursor_table(auto: dict, manual: dict, guia: dict) -> pd.DataFrame:
    rows = []
    for c in CURSORS:
        mx = float(manual[c]["x"])
        ax = float(auto[c]["x"])
        if c == "B": crit = "Pie de ascenso de dZ/dt, después del QRS."
        elif c == "C": crit = "Pico sistólico principal de dZ/dt."
        elif c == "X": crit = "Nadir sistólico; validar con S2 del fonocardiograma."
        else: crit = "Rebote diastólico posterior si es visible."
        rows.append({"Cursor": c, "Auto_x": round(ax, 1), "Manual_x": round(mx, 1), "Delta_px": round(mx - ax, 1), "Criterio": crit})
    return pd.DataFrame(rows)


def plot_signals(ecg: pd.DataFrame, dzdt: pd.DataFrame, fono: pd.DataFrame, auto: dict, manual: dict, guia: dict, xmin: float, xmax: float) -> bytes:
    x = np.linspace(xmin, xmax, 900)
    fig, ax = plt.subplots(figsize=(14, 6.5))
    ax.plot(x, interp(ecg, x) + 2.4, linewidth=1.6, label="ECG")
    ax.plot(x, interp(dzdt, x) + 1.2, linewidth=2.1, label="dZ/dt")
    ax.plot(x, interp(fono, x), linewidth=1.6, label="Fonocardiograma")
    ax.hlines(float(guia.get("fono_line", 0.55)), xmin, xmax, linestyles="--", linewidth=1.2, label="Línea horizontal fono")
    for key, txt, yy in [("qrs", "QRS", 3.45), ("s1", "S1", 0.88), ("s2", "S2", 0.88)]:
        val = guia.get(key, np.nan)
        if np.isfinite(val):
            ax.axvline(float(val), linestyle="-.", linewidth=1.1)
            ax.text(float(val), yy, txt, rotation=90, ha="center", va="bottom", fontsize=9)
    for c in CURSORS:
        ax.axvline(float(auto[c]["x"]), linestyle=":", linewidth=1.2)
        ax.axvline(float(manual[c]["x"]), linestyle="--", linewidth=2.0)
        ax.text(float(manual[c]["x"]), 1.05, c, rotation=90, ha="center", va="bottom", fontsize=11, fontweight="bold")
    ax.set_yticks([0.5, 1.7, 2.9])
    ax.set_yticklabels(["Fono", "dZ/dt", "ECG"])
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(-0.15, 3.75)
    ax.grid(True, alpha=0.22)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title("Corrección integrada de cursores")
    fig.tight_layout()
    bio = io.BytesIO()
    fig.savefig(bio, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    return bio.getvalue()


def sessions_excel() -> bytes:
    df = load_sessions()
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="sesiones")
        rows = []
        for _, r in df.iterrows():
            try:
                tab = cursor_table(json.loads(r["auto_json"]), json.loads(r["manual_json"]), json.loads(r["guia_json"]))
                tab.insert(0, "session_id", r["id"])
                tab.insert(1, "paciente", r["paciente"])
                rows.append(tab)
            except Exception:
                pass
        pd.concat(rows, ignore_index=True).to_excel(writer, index=False, sheet_name="cursores") if rows else pd.DataFrame().to_excel(writer, index=False, sheet_name="cursores")
    return bio.getvalue()


def main() -> None:
    apply_css()
    init_db()
    st.markdown(f"<div class='hero'><h1>{APP_TITLE}</h1><p>{APP_SUBTITLE}</p><div class='dev'>{APP_DEVELOPER}</div></div>", unsafe_allow_html=True)
    st.markdown("<div class='guide'><b>Propósito:</b> entrenar la corrección de cursores sobre una vista sincronizada. El área inicial queda configurada en el panel derecho del informe marcado en amarillo: dZ/dt arriba, ECG al medio y fonocardiograma abajo. El ECG orienta QRS/B, dZ/dt define B-C-X-Y y el fonocardiograma aporta referencia S1/S2 con una línea horizontal.</div>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["Corrección", "Histórico"])

    with tab1:
        c1, c2, c3 = st.columns([1.1, 1, 1])
        with c1:
            paciente = st.text_input("Paciente / código", value="CASO-001")
        with c2:
            fecha = st.date_input("Fecha del estudio", value=date.today())
        with c3:
            condicion = st.selectbox("Condición", ["Basal / acostado / cinta", "Parado", "Spot", "Entrenamiento"])

        uploaded = st.file_uploader("Cargar PDF o imagen", type=["pdf", "png", "jpg", "jpeg"])
        if uploaded is None:
            st.info("Cargue un PDF o imagen. La app no se detiene: el histórico queda disponible en la otra pestaña.")
        else:
            img, pagina, archivo = open_upload(uploaded)
            w, h = img.size
            preset = st.selectbox(
                "Área inicial de digitalización",
                [
                    "Panel derecho marcado en amarillo / informe Exxer",
                    "Tiras largas inferiores",
                ],
                index=0,
                help="Para este caso use el panel derecho: allí están sincronizados dZ/dt, ECG y fono.",
            )
            preset_key = "panel_derecho" if preset.startswith("Panel derecho") else "tiras_inferiores"
            base = default_rois(w, h, preset_key)
            st.image(draw_rois(img, base), caption="Recortes iniciales sugeridos sobre el área correcta", use_container_width=True)

            rois = {}
            with st.expander("Ajustar recortes", expanded=True):
                for key, label in [("ecg", "ECG"), ("dzdt", "dZ/dt"), ("fono", "Fonocardiograma")]:
                    st.markdown(f"**{label}**")
                    b = base[key]
                    a, bcol, c, d = st.columns(4)
                    with a:
                        x0 = st.slider(f"X inicial {label}", 0, max(3, w - 3), int(b["x0"]), key=f"{key}_x0")
                    with bcol:
                        x1_min = min(w, int(x0) + 3)
                        x1_default = max(x1_min, int(b["x1"]))
                        x1 = st.slider(f"X final {label}", x1_min, w, x1_default, key=f"{key}_x1")
                    with c:
                        y0 = st.slider(f"Y inicial {label}", 0, max(3, h - 3), int(b["y0"]), key=f"{key}_y0")
                    with d:
                        y1_min = min(h, int(y0) + 3)
                        y1_default = max(y1_min, int(b["y1"]))
                        y1 = st.slider(f"Y final {label}", y1_min, h, y1_default, key=f"{key}_y1")
                    rois[key] = clamp_roi({"x0": int(x0), "x1": int(x1), "y0": int(y0), "y1": int(y1)}, w, h)
            st.image(draw_rois(img, rois), caption="Recortes ajustados", use_container_width=True)

            fono_line = st.slider("Línea horizontal del fonocardiograma", 0.10, 0.95, 0.55, 0.01)
            ecg = digitize(img, rois["ecg"], "ecg")
            dzdt = digitize(img, rois["dzdt"], "dzdt")
            fono = digitize(img, rois["fono"], "fono")

            st.write(f"Puntos detectados: ECG {len(ecg)} | dZ/dt {len(dzdt)} | Fono {len(fono)}")
            if dzdt.empty:
                st.error("No se detectó dZ/dt en el recorte azul. Ajuste el recorte o suba una imagen con mayor resolución.")
                return
            if ecg.empty:
                st.warning("ECG no detectado claramente: se puede corregir, pero sin referencia QRS automática.")
            if fono.empty:
                st.warning("Fonocardiograma no detectado claramente: se puede corregir, pero sin S1/S2 automático.")

            xmin = float(max(rois[k]["x0"] for k in rois))
            xmax = float(min(rois[k]["x1"] for k in rois))
            if xmax <= xmin + 10:
                st.error("Los tres recortes no comparten suficiente eje X. Alinee los X inicial/final de ECG, dZ/dt y fono.")
                return

            auto, guia = detect(ecg, dzdt, fono, xmin, xmax, fono_line)
            st.markdown("<div class='ok'><b>Listo:</b> ahora corrija manualmente B, C, X e Y.</div>", unsafe_allow_html=True)

            manual = {}
            cols = st.columns(4)
            for i, cur in enumerate(CURSORS):
                with cols[i]:
                    default = int(round(float(auto[cur]["x"])))
                    val = st.slider(f"Cursor {cur}", int(xmin), int(xmax), min(max(default, int(xmin)), int(xmax)), key=f"manual_{cur}")
                    manual[cur] = {"x": float(val), "y": near_y(dzdt, float(val))}

            chart = plot_signals(ecg, dzdt, fono, auto, manual, guia, xmin, xmax)
            st.image(chart, caption="ECG + dZ/dt + fonocardiograma con cursores", use_container_width=True)
            tab = cursor_table(auto, manual, guia)
            st.dataframe(tab, use_container_width=True)
            mae = float(pd.to_numeric(tab["Delta_px"], errors="coerce").abs().mean())
            conclusion = (
                "Corrección didáctica integrada. B se valida después del QRS y en el pie de ascenso de dZ/dt; "
                "C corresponde al pico sistólico; X al nadir sistólico en relación con S2; Y al rebote diastólico si es visible. "
                f"Error medio automático-manual: {mae:.1f} px."
            )
            st.info(conclusion)

            a, b = st.columns(2)
            with a:
                if st.button("Guardar corrección", type="primary"):
                    sid = save_session(paciente, str(fecha), condicion, archivo, pagina, rois, auto, manual, guia, conclusion)
                    st.success(f"Corrección guardada con ID {sid}.")
            with b:
                st.download_button("Descargar gráfico PNG", data=chart, file_name=f"{paciente}_cursores.png".replace(" ", "_"), mime="image/png")

    with tab2:
        st.subheader("Histórico")
        df = load_sessions()
        if df.empty:
            st.info("Sin sesiones guardadas todavía.")
        else:
            st.dataframe(df[["id", "created_at", "paciente", "fecha_estudio", "condicion", "archivo", "conclusion"]], use_container_width=True)
            st.download_button("Exportar histórico Excel", data=sessions_excel(), file_name="historico_cursores_cgi.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


try:
    main()
except Exception as exc:
    st.error("La app encontró un error controlado. Copie este detalle si vuelve a ocurrir.")
    st.exception(exc)
    st.code(traceback.format_exc())
