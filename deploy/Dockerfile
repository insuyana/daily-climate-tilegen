# Imagen de producción de tilegen: lo que corre Airflow en cada tarea.
#
# Reemplaza al checkout pinneado de ~/tilegen-prod: la imagen se construye desde
# un tag y queda congelada, con las dependencias de deploy/requirements-lock.txt.
#
#   git checkout v0.5.0
#   docker build -f deploy/Dockerfile -t tilegen:v0.5.0 --build-arg VERSION=v0.5.0 .
#
# Python 3.12 + zarr 3 A PROPÓSITO: los stores de EDH pasaron a Zarr v3 y zarr 2
# no los lee (los da por inexistentes sin error). Los cubos de suyana-tiles se
# siguen escribiendo en formato v2 (ver _v2_kwargs en tilegen/zarrstore.py).
FROM python:3.12-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="tilegen" \
      org.opencontainers.image.version="${VERSION}"

RUN useradd --create-home --uid 1000 tilegen

WORKDIR /app
COPY deploy/requirements-lock.txt /tmp/requirements-lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements-lock.txt

COPY . /app
RUN pip install --no-cache-dir --no-deps /app \
 && mkdir -p /work \
 && chown -R tilegen /work /app/docs

# config.yaml apunta el workdir a un home de la máquina; acá va a /work, que es
# efímero: con keep_local=false los archivos bajados se borran al terminar.
ENV TILEGEN_WORKDIR=/work \
    TILEGEN_VERSION=${VERSION} \
    PYTHONUNBUFFERED=1

USER tilegen
CMD ["tilegen", "--help"]
