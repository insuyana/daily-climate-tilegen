"""Mapa comparativo: las 21 escenas de scenes.yaml contra las 23 del equipo.

    python3 docs/make_mapa_comparativo.py

Las cajas nuevas vienen de docs/scene_boxes_equipo.csv, que es una copia de
s3://suyana-dev-pricing/geometries/scene_boxes.parquet (23 filas, leido el
28-10-2026). Se guarda en CSV y no en parquet para no pedir geopandas solo
para dibujar un mapa; para refrescarlo:

    AWS_PROFILE=suyana-dev python3 -c "import geopandas as gpd; \
        gpd.read_parquet('s3://suyana-dev-pricing/geometries/scene_boxes.parquet') \
        [['scene','minx','miny','maxx','maxy']].to_csv( \
        'docs/scene_boxes_equipo.csv', index=False)"

El rol del server no llega a ese bucket: hace falta el perfil suyana-dev.
"""
import csv
import sys
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import yaml
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
VIEJO = "#2E6FA3"
NUEVO = "#C4622D"
# nino34 vive en el Pacifico central y es identica en los dos juegos: dibujarla
# obligaria a abrir el mapa 100 grados al oeste por una caja que no cambia.
FUERA = {"nino34"}


def base(ax, extent):
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#f2efe9", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#fcfdfe", zorder=0)
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), lw=0.4,
                   edgecolor="#9a9a9a", zorder=1)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), lw=0.3,
                   edgecolor="#b5b5b5", zorder=1)
    ax.spines["geo"].set_linewidth(0.4)
    ax.spines["geo"].set_edgecolor("#bbbbbb")


def caja(ax, bounds, color, lw=1.2, ls="-", relleno=0.0, z=4):
    w, s, e, n = bounds
    if relleno:
        ax.add_patch(mpatches.Rectangle(
            (w, s), e - w, n - s, transform=ccrs.PlateCarree(),
            facecolor=color, alpha=relleno, edgecolor="none", zorder=z - 1))
    ax.add_patch(mpatches.Rectangle(
        (w, s), e - w, n - s, transform=ccrs.PlateCarree(),
        facecolor="none", edgecolor=color, lw=lw, ls=ls, zorder=z))


def etiqueta(ax, bounds, texto, color, abajo=False, ext=None):
    """Las viejas rotulan arriba y las nuevas abajo, si no los nombres se pisan
    donde las dos cajas comparten esquina. En los zooms la caja puede salirse
    del recuadro, asi que el rotulo se trae adentro en vez de quedar colgando."""
    w, s, e, n = bounds
    y, va = (s + 0.5, "bottom") if abajo else (n - 0.5, "top")
    x = w + 0.4
    if ext:
        x = min(max(x, ext[0] + 0.4), ext[1] - 0.4)
        y = min(max(y, ext[2] + 0.6), ext[3] - 0.6)
    ax.text(x, y, texto, transform=ccrs.PlateCarree(),
            ha="left", va=va, fontsize=6.0, color=color, zorder=7,
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.75))


viejas = {k: tuple(v["bbox"])
          for k, v in yaml.safe_load(
              (ROOT / "tilegen" / "conf" / "scenes.yaml").read_text())["scenes"].items()
          if k not in FUERA}
CAJAS_NUEVAS = ROOT / "docs" / "scene_boxes_equipo.csv"
nuevas = {r["scene"]: (float(r["minx"]), float(r["miny"]),
                       float(r["maxx"]), float(r["maxy"]))
          for r in csv.DictReader(CAJAS_NUEVAS.open()) if r["scene"] not in FUERA}

EXT = [-124, -28, -58, 35]
fig = plt.figure(figsize=(15.5, 10.5))
gs = fig.add_gridspec(2, 2, height_ratios=[1.45, 1.0], hspace=0.08, wspace=0.06)

ax1 = fig.add_subplot(gs[0, 0], projection=ccrs.PlateCarree())
base(ax1, EXT)
for nombre, b in viejas.items():
    caja(ax1, b, VIEJO, relleno=0.07)
    etiqueta(ax1, b, nombre, "#1b4666")
ax1.set_title(f"Antes: {len(viejas) + 1} escenas de scenes.yaml\n"
              "cajas en grados enteros, con mar agregado a mano",
              fontsize=10.5, color="#333333", pad=8)

ax2 = fig.add_subplot(gs[0, 1], projection=ccrs.PlateCarree())
base(ax2, EXT)
for nombre, b in nuevas.items():
    caja(ax2, b, NUEVO, relleno=0.07)
    etiqueta(ax2, b, nombre, "#8a3f17")
ax2.set_title(f"Ahora: {len(nuevas) + 1} escenas del equipo\n"
              "cajas al 1/4 de grado, pegadas al borde de los paises",
              fontsize=10.5, color="#333333", pad=8)

# zooms: solo las cajas de la zona, si no es una maraña de lineas
ZOOMS = [
    ("Brasil: de 4 regiones a 5, y cambian todos los nombres",
     [-76, -27, -26, 9],
     ["brasil_amazonia", "brasil_nordeste", "brasil_centro_sudeste", "brasil_sur"],
     ["brasil_norte", "brasil_nordeste", "brasil_centro_oeste", "brasil_sudeste",
      "brasil_sur"]),
    ("Cono sur: patagonia era compartida y ahora se parte por pais",
     [-84, -58, -50, -15],
     ["chile_norte_centro", "argentina_norte", "patagonia"],
     ["chile_norte", "chile_sur", "argentina_norte", "argentina_sur"]),
]
for col, (titulo, ext, v_sel, n_sel) in enumerate(ZOOMS):
    ax = fig.add_subplot(gs[1, col], projection=ccrs.PlateCarree())
    base(ax, ext)
    for nombre in v_sel:
        caja(ax, viejas[nombre], VIEJO, lw=1.4)
        etiqueta(ax, viejas[nombre], nombre, "#1b4666", ext=ext)
    for nombre in n_sel:
        caja(ax, nuevas[nombre], NUEVO, lw=1.4, ls=(0, (4, 2)))
        etiqueta(ax, nuevas[nombre], nombre, "#8a3f17", abajo=True, ext=ext)
    ax.set_title(titulo, fontsize=9.5, color="#333333", pad=6)

fig.legend(handles=[
    Line2D([], [], color=VIEJO, lw=1.6, label=f"escenas de hoy ({len(viejas) + 1}, con nino34)"),
    Line2D([], [], color=NUEVO, lw=1.6, ls=(0, (4, 2)),
           label=f"escenas propuestas ({len(nuevas) + 1}, con nino34)")],
    loc="lower center", ncol=2, fontsize=9.5, frameon=False, bbox_to_anchor=(0.5, 0.012))
fig.text(0.5, -0.004,
         "nino34 (170W-120W, 5S-5N) es identica en los dos juegos y queda fuera del mapa.",
         ha="center", fontsize=8, color="#777777")

out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "escenas_viejas_vs_nuevas.png"
fig.savefig(out, dpi=170, bbox_inches="tight")
print(f"guardado {out}")
