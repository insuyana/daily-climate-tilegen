"""Regenera la cobertura y el catálogo y los publica en la raíz del bucket.

Es la versión para el contenedor de .work/cobertura-run.sh: mismos generadores,
misma regla. Si un generador falla, sus archivos NO se suben, así queda
publicada la última foto buena en vez de una a medias.

Solo lee ledgers de S3 y escribe 4 archivos en la raíz del bucket; no toca
ningún cubo. Corre desde la raíz del repo (los generadores usan rutas relativas).

Sale con 1 si algo falló, para que Airflow lo marque y avise.
"""
import subprocess
import sys

import boto3

BUCKET = "suyana-tiles"

# generador -> archivos que produce, con su content-type. Sin content-type S3
# los sirve como binario y el navegador los baja en vez de mostrarlos.
GENERADORES = {
    "docs/gen_cobertura.py": {
        "cobertura.html": "text/html; charset=utf-8",
    },
    "docs/gen_catalog.py": {
        "CATALOG.md": "text/markdown; charset=utf-8",
        "catalog.csv": "text/csv; charset=utf-8",
        "catalog.yaml": "text/yaml; charset=utf-8",
    },
}


def main() -> int:
    s3 = boto3.client("s3")
    fallas = 0
    for script, archivos in GENERADORES.items():
        rc = subprocess.run([sys.executable, script]).returncode
        if rc != 0:
            print(f"FALLO {script} exit={rc} (no se suben {', '.join(archivos)})")
            fallas += 1
            continue
        for nombre, ctype in archivos.items():
            try:
                s3.upload_file(f"docs/{nombre}", BUCKET, nombre,
                               ExtraArgs={"ContentType": ctype})
                print(f"subido {nombre}")
            except Exception as e:  # noqa: BLE001 - se reporta y se sigue con el resto
                print(f"FALLO subiendo {nombre}: {e}")
                fallas += 1
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
