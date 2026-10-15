"""DAGs de tilegen: la actualización diaria de los cubos Zarr en s3://suyana-tiles.

Es el crontab de ~/tilegen-prod traducido uno a uno: un DAG por línea, mismos
horarios (UTC), mismas escenas y mismas ventanas. Así, durante la convivencia,
se puede comparar día por día contra cron.log.

Cada tarea corre la imagen `tilegen:<tag>` en un contenedor aparte. Qué tag
corre se decide con la Variable de Airflow `tilegen_image`: promover una versión
es construir la imagen desde el tag y cambiar esa variable (ver deploy/README.md).

Lo que hacía prod-run.sh y cómo queda acá:
  - "solo corre código tageado"  -> la imagen se construye desde un tag.
  - lock por job (flock)         -> max_active_runs=1: si la corrida anterior
                                    sigue viva, la nueva ESPERA en vez de saltearse.
  - aviso a ClickUp si falla     -> on_failure_callback (después de los reintentos).
Y rechunk-run.sh, que se negaba a arrancar si había cualquier tilegen vivo,
queda como el pool `tilegen_s3`: cada tarea de datos ocupa 1 lugar y el rechunk
los ocupa TODOS, así nunca reescribe un cubo mientras otro job lo está llenando.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests
from airflow.providers.docker.operators.docker import DockerOperator
from airflow.sdk import DAG
from airflow.timetables.trigger import CronTriggerTimetable
from docker.types import Mount

# --- escenas -----------------------------------------------------------------
# TIERRA = las 20 escenas terrestres. nino34 queda afuera de chirps, cpc y
# era5-land: es un índice oceánico y ahí esas fuentes dan todo NaN.
TIERRA = [
    "mexico_norte", "mexico_sur", "centroamerica", "antillas_mayores",
    "antillas_menores", "colombia", "venezuela", "guayanas", "ecuador_galapagos",
    "peru", "bolivia", "brasil_amazonia", "brasil_nordeste",
    "brasil_centro_sudeste", "brasil_sur", "paraguay", "uruguay",
    "chile_norte_centro", "argentina_norte", "patagonia",
]
TODAS = TIERRA + ["nino34"]

# era5 va en 3 grupos escalonados y no en uno: cada escena es un pedido a la
# cola del CDS, y en una sola corrida las 21 se serializaban.
ERA5_GRUPOS = {
    "era5-1": TODAS[0:7],
    "era5-2": TODAS[7:14],
    "era5-3": TODAS[14:21],
}
ERA5_VARS = ["t2m_mean", "t2m_max", "t2m_min", "precip_sum", "ssrd_sum",
             "sd_mean", "rsn_mean", "fg10_max"]


def escenas(lista):
    return [a for x in lista for a in ("-x", x)]


def variables(lista):
    return [a for v in lista for a in ("-v", v)]


def desde(dias):
    """-s <fecha>: la ventana hacia atrás, contada desde el momento del disparo."""
    return ["-s", "{{ (dag_run.run_after - macros.timedelta(days=%d)).strftime('%%Y-%%m-%%d') }}" % dias]


# --- jobs de datos -----------------------------------------------------------
# (nombre, cron UTC, argumentos de tilegen). Los porqués de cada ventana están
# en el crontab original y en los yaml de cada dataset; acá va lo mínimo.
JOBS = [
    # cpc: lag 3 días; 45 cubre además cualquier corrida perdida.
    ("cpc", "0 5 * * *", ["run", "-d", "cpc", *escenas(TIERRA), *desde(45)]),
    # chirps: lag 45 días -> ventana de 70 para agarrar lo que se publica tarde.
    ("chirps", "0 6 * * *", ["run", "-d", "chirps", *escenas(TIERRA), *desde(70)]),
    # era5: lag 6 días -> ventana de 16.
    ("era5-1", "30 6 * * *", ["run", "-d", "era5", *variables(ERA5_VARS), *escenas(ERA5_GRUPOS["era5-1"]), *desde(16)]),
    ("era5-2", "15 7 * * *", ["run", "-d", "era5", *variables(ERA5_VARS), *escenas(ERA5_GRUPOS["era5-2"]), *desde(16)]),
    ("era5-3", "0 8 * * *", ["run", "-d", "era5", *variables(ERA5_VARS), *escenas(ERA5_GRUPOS["era5-3"]), *desde(16)]),
    # swvl1 solo existe en el mirror EDH, que publica ~mensual y va ~2 meses
    # atrás: semanal, con ventana de 90 días.
    ("era5-swvl1", "0 9 * * 0", ["run", "-d", "era5", "--source", "era5_edh", "-v", "swvl1", *escenas(TODAS), *desde(90)]),
    ("chirps-v3", "30 9 * * *", ["run", "-d", "chirps-v3", *escenas(TIERRA), *desde(70)]),
    # era5-land: 120 días porque el CDS tiene huecos viejos que llena después.
    ("era5-land", "30 10 * * *", ["run", "-d", "era5-land", *escenas(TIERRA), *desde(120)]),
]

# rechunk: una tanda por día, a las 14h, con techo de 13h. Cortar por tiempo es
# el diseño (sigue donde quedó al día siguiente), así que el 124 de `timeout`
# deja la tarea en "skipped", no en "failed", y no avisa.
RECHUNKS = [
    ("era5", "0 14 * * 1"),
    ("chirps", "0 14 * * 2"),
    ("chirts", "0 14 * * 4"),
    ("chirps-v3", "0 14 * * 5"),
    ("cfs", "0 14 * * 6"),
]
RECHUNK_TECHO = "13h"

POOL = "tilegen_s3"
POOL_SLOTS = 16  # tiene que coincidir con el tamaño del pool (lo crea airflow-init)


# --- aviso a ClickUp ---------------------------------------------------------
CLICKUP_WORKSPACE = "9011058683"
CLICKUP_CANAL = "6-901109639647-8"  # Devops
SECRETS = "/opt/tilegen/secrets"     # montado en los contenedores de Airflow


def avisar_clickup(context):
    """Mismo mensaje que avisar-clickup.sh. Nunca levanta: un aviso roto no
    tiene que tapar la falla que estaba avisando."""
    try:
        with open(f"{SECRETS}/clickup.token") as f:
            token = f.read().strip()
    except OSError:
        print("[aviso] sin token de ClickUp, no aviso")
        return
    ti = context["ti"]
    cuando = datetime.now(timezone.utc).strftime("%d/%m %H:%M")
    texto = (f"🔴 `{ti.dag_id}` **falló** — {cuando} UTC\n"
             f"Los cubos de ese dataset **no se actualizaron hoy**. "
             f"El log está en Airflow: DAG `{ti.dag_id}`, tarea `{ti.task_id}`.")
    try:
        r = requests.post(
            f"https://api.clickup.com/api/v3/workspaces/{CLICKUP_WORKSPACE}"
            f"/chat/channels/{CLICKUP_CANAL}/messages",
            headers={"Authorization": token},
            json={"type": "message", "content_format": "text/md", "content": texto},
            timeout=20,
        )
        print(f"[aviso] ClickUp http {r.status_code}")
    except Exception as e:  # noqa: BLE001
        print(f"[aviso] ClickUp no contestó: {e}")


# --- contenedor --------------------------------------------------------------
# Las rutas de los mounts son del HOST (los contenedores los lanza el docker de
# la máquina, no el de Airflow), por eso vienen por variable de entorno.
HOST_SECRETS = os.environ.get("TILEGEN_HOST_SECRETS_DIR", "/opt/tilegen/secrets")
MOUNTS = [
    Mount(source=f"{HOST_SECRETS}/cdsapirc", target="/home/tilegen/.cdsapirc", type="bind", read_only=True),
    Mount(source=f"{HOST_SECRETS}/netrc", target="/home/tilegen/.netrc", type="bind", read_only=True),
]


def tilegen_task(task_id, command, **kw):
    return DockerOperator(
        task_id=task_id,
        image="{{ var.value.tilegen_image }}",
        command=command,
        mounts=MOUNTS,
        # network_mode=host: las credenciales de S3 salen del rol de la
        # instancia (IMDSv2), y con la red bridge de docker el pedido hace un
        # salto de más y AWS lo rechaza (hop limit 1).
        network_mode="host",
        mount_tmp_dir=False,
        auto_remove="success",
        docker_url="unix://var/run/docker.sock",
        pool=POOL,
        **kw,
    )


default_args = {
    "owner": "tilegen",
    # Un reintento a los 20 min: la cola del CDS falla a veces de forma
    # transitoria. El aviso sale recién si falla también el reintento.
    "retries": 1,
    "retry_delay": timedelta(minutes=20),
    "on_failure_callback": avisar_clickup,
}

COMUN = dict(
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["tilegen"],
)

for nombre, cron, args in JOBS:
    with DAG(
        dag_id=f"tilegen_{nombre.replace('-', '_')}",
        schedule=CronTriggerTimetable(cron, timezone="UTC"),
        description=f"tilegen {' '.join(args[:3])}",
        **COMUN,
    ):
        tilegen_task("run", ["tilegen", *args])

for ds, cron in RECHUNKS:
    with DAG(
        dag_id=f"tilegen_rechunk_{ds.replace('-', '_')}",
        schedule=CronTriggerTimetable(cron, timezone="UTC"),
        description=f"rechunk espacial de {ds} (una tanda, techo {RECHUNK_TECHO})",
        **{**COMUN, "tags": ["tilegen", "rechunk"]},
    ):
        tilegen_task(
            "rechunk",
            ["timeout", "--signal=INT", RECHUNK_TECHO,
             "tilegen", "rechunk", "-d", ds, "--yes", "--keep-old"],
            pool_slots=POOL_SLOTS,
            skip_on_exit_code=124,
            retries=0,
        )

# Cobertura y catálogo: solo lectura de ledgers, sin pool. A las 12h, después
# del bloque de datos y antes del rechunk.
with DAG(
    dag_id="tilegen_cobertura",
    schedule=CronTriggerTimetable("0 12 * * *", timezone="UTC"),
    description="regenera cobertura.html y el catálogo y los sube al bucket",
    **COMUN,
):
    DockerOperator(
        task_id="publicar",
        image="{{ var.value.tilegen_image }}",
        command=["python", "deploy/publicar_docs.py"],
        network_mode="host",
        mount_tmp_dir=False,
        auto_remove="success",
        docker_url="unix://var/run/docker.sock",
    )
