# tilegen en Airflow

Reemplaza al crontab de `jupyter-daniela` (`~/tilegen-prod`). Los mismos jobs, a
los mismos horarios, pero corriendo como un usuario de servicio (`tilegen`) y
con una interfaz web donde todo el equipo ve qué corrió, qué falló y el log de
cada tarea, y puede relanzar con un clic.

```
deploy/
  Dockerfile               imagen tilegen:<tag> (Python 3.12, zarr 3)
  requirements-lock.txt    versiones exactas de la imagen
  publicar_docs.py         cobertura + catálogo -> bucket (antes .work/cobertura-run.sh)
  airflow/
    docker-compose.yml     Airflow 3 (LocalExecutor + Postgres)
    .env.example           configuración; copiar a .env
    dags/tilegen.py        un DAG por línea del crontab
```

## Cómo funciona

- Airflow corre en docker-compose en esta misma máquina, como `tilegen`.
- Cada tarea lanza un contenedor de la imagen `tilegen:<tag>`. Qué tag corre lo
  decide la Variable de Airflow `tilegen_image`.
- Las credenciales de S3 salen del rol de la instancia (como hoy). Las de CDS,
  EDH y ClickUp son archivos en `TILEGEN_SECRETS_DIR`, legibles solo por `tilegen`.
- Si una tarea falla dos veces seguidas (hay un reintento a los 20 min), avisa
  al canal Devops de ClickUp, igual que `avisar-clickup.sh`.

| Antes (cron + wrappers)                          | Ahora (Airflow)                                   |
|--------------------------------------------------|---------------------------------------------------|
| checkout pinneado a un tag                       | imagen construida desde un tag                    |
| `flock` por job: si sigue viva, la nueva **se saltea** | `max_active_runs=1`: la nueva **espera**   |
| rechunk aborta si hay otro tilegen vivo          | pool `tilegen_s3`: el rechunk lo ocupa entero     |
| rechunk cortado por `timeout` (124) no avisa     | igual: queda en *skipped*, sin aviso              |
| sin reintentos                                   | 1 reintento a los 20 min                          |
| log en `~/tilegen-prod/log/cron.log`             | log por tarea en la interfaz                      |

## 1. Lo que hace alguien con sudo (una sola vez)

```bash
# Docker
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker

# usuario de servicio
sudo useradd --create-home --shell /bin/bash tilegen
sudo usermod -aG docker tilegen

# que la persona a cargo pueda operar como tilegen (ejemplo: jupyter-daniela)
echo 'jupyter-daniela ALL=(tilegen) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/tilegen
sudo chmod 440 /etc/sudoers.d/tilegen
```

**Nota sobre el acceso a docker:** estar en el grupo `docker` equivale a ser root
en la máquina. Por eso se agrega solo `tilegen`, no las personas.

**Nota sobre IMDS:** las tareas usan `network_mode=host` para llegar al rol de
la instancia. Si se cambiara a la red bridge, hay que subir el hop limit de
IMDSv2 a 2 (`aws ec2 modify-instance-metadata-options --http-put-response-hop-limit 2`).

## 2. Instalación (como `tilegen`)

```bash
sudo -iu tilegen

# código y credenciales
git clone https://github.com/dbrisaro/tile-generation-module.git ~/tile_generation_module
mkdir -m 700 ~/secrets
#   copiar ahí: cdsapirc, netrc (solo la entrada de earthdatahub), clickup.token
chmod 600 ~/secrets/*

# imagen desde un tag
cd ~/tile_generation_module
git checkout v0.5.0
docker build -f deploy/Dockerfile -t tilegen:v0.5.0 --build-arg VERSION=v0.5.0 .

# Airflow
cd deploy/airflow
cp .env.example .env && chmod 600 .env    # completar
mkdir -p logs
docker compose up -d
docker compose ps                           # todo "running"; airflow-init "exited (0)"
```

Antes de prender nada, probar la imagen a mano contra S3 (solo lectura):

```bash
docker run --rm --network host tilegen:v0.5.0 tilegen verify -d era5 -v sd_mean -x patagonia
```

## 3. Acceso a la interfaz

La interfaz escucha solo en `127.0.0.1:8080`. Para entrar, desde tu máquina:

```bash
ssh -L 8080:localhost:8080 <usuario>@<esta-máquina>
# y abrir http://localhost:8080
```

Usuarios: el admin del `.env` crea los del equipo en *Security > List Users*.
Si se quiere entrar sin túnel, hay que abrir el puerto en el security group
(limitado a las IPs del equipo) y poner HTTPS adelante: es una decisión aparte.

## 4. Convivencia con el cron y corte

Los DAGs nacen **pausados**. Se pasan de a uno:

1. Elegir un job (empezar por `tilegen_cpc`, que es barato).
2. Comentar su línea en `crontab -e` de `jupyter-daniela`.
3. Prender el DAG en la interfaz y dispararlo a mano una vez (*Trigger*).
4. Al día siguiente, confirmar que corrió solo y con `tilegen verify` que el
   dato avanzó.
5. Siguiente job.

**Nunca prender un DAG sin comentar su línea de cron**: los dos escribirían el
mismo cubo a la vez y los locks de uno no ven al otro.

Cuando estén todos: borrar el crontab de `jupyter-daniela` (dejar una copia en
`~/tilegen-prod/crontab.bak`). `~/tilegen-prod` queda como respaldo un tiempo
y después se borra.

**Rollback:** pausar el DAG y descomentar la línea del cron.

## 5. Promover una versión nueva

```bash
sudo -iu tilegen
cd ~/tile_generation_module && git fetch --tags && git checkout v0.5.1
docker build -f deploy/Dockerfile -t tilegen:v0.5.1 --build-arg VERSION=v0.5.1 .
```

Después, en la interfaz: *Admin > Variables > tilegen_image* = `tilegen:v0.5.1`.
Las corridas siguientes usan la imagen nueva; para volver atrás se pone el tag
anterior.

Si cambian los DAGs (`deploy/airflow/dags/`), alcanza con el `git checkout`:
Airflow relee la carpeta solo.

## Lo que NO cubre todavía

- **Corridas manuales fuera de Airflow** (un backfill largo en tmux) no ocupan
  el pool `tilegen_s3`, así que el rechunk no las ve. Mejor lanzarlas como DAG o
  no lanzarlas los días de rechunk.
- **Los backfills `@reboot`** del crontab (`.work/ensure_backfills.sh`) no se
  migran: son corridas puntuales, no parte del servicio.
- **El vigilante externo** (Healthchecks) sigue haciendo falta: si la máquina
  está apagada, Airflow tampoco avisa.
