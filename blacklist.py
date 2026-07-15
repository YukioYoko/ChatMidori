"""
blacklist.py
-------------
Lista negra de números que dejaron plantada a la doctora.

Los números en esta lista pueden seguir agendando, pero se les exige un
depósito previo para confirmar la cita (ver conversation_manager.py y
business_config.py).

Almacenamiento:
  - Archivo local "blacklist.json" (editable a mano o vía los endpoints
    /admin/blacklist de main.py).
  - Variable de entorno BLACKLIST_NUMBERS (números separados por coma).
    Útil en hosting con disco efímero (Render), donde el archivo se
    borra en cada redeploy: los números "semilla" de la variable siempre
    se conservan, y los agregados por endpoint viven hasta el siguiente
    redeploy.

Formato de blacklist.json:
    {
      "+5213312345678": {"motivo": "No asistió el 2026-07-10", "agregado": "2026-07-11"}
    }
"""

import os
import json
import logging
from datetime import date

logger = logging.getLogger("blacklist")

BLACKLIST_FILE = "blacklist.json"
BLACKLIST_ENV_VAR = "BLACKLIST_NUMBERS"


def _normalizar(numero: str) -> str:
    """
    Normaliza un número para comparaciones consistentes:
    quita el prefijo "whatsapp:", espacios, guiones y paréntesis.
    "+52 1 33 1234-5678" y "whatsapp:+5213312345678" quedan iguales.
    """
    limpio = numero.replace("whatsapp:", "")
    for ch in " -()":
        limpio = limpio.replace(ch, "")
    if not limpio.startswith("+"):
        limpio = "+" + limpio
    return limpio


def _cargar_archivo() -> dict:
    if not os.path.exists(BLACKLIST_FILE):
        return {}
    try:
        with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("No se pudo leer %s: %s", BLACKLIST_FILE, exc)
        return {}


def _guardar_archivo(data: dict) -> None:
    try:
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.error("No se pudo escribir %s: %s", BLACKLIST_FILE, exc)


def _numeros_de_env() -> set[str]:
    crudo = os.environ.get(BLACKLIST_ENV_VAR, "")
    return {_normalizar(n) for n in crudo.split(",") if n.strip()}


def is_blacklisted(phone_number: str) -> bool:
    """True si el número está en la lista negra (archivo o variable de entorno)."""
    numero = _normalizar(phone_number)
    if numero in _numeros_de_env():
        return True
    return numero in _cargar_archivo()


def add(phone_number: str, motivo: str = "") -> dict:
    """Agrega un número a la lista negra. Devuelve la entrada creada."""
    numero = _normalizar(phone_number)
    data = _cargar_archivo()
    entrada = {"motivo": motivo, "agregado": date.today().isoformat()}
    data[numero] = entrada
    _guardar_archivo(data)
    logger.info("Número %s agregado a la lista negra (%s)", numero, motivo)
    return {numero: entrada}


def remove(phone_number: str) -> bool:
    """Quita un número de la lista negra. True si existía."""
    numero = _normalizar(phone_number)
    data = _cargar_archivo()
    if numero in data:
        del data[numero]
        _guardar_archivo(data)
        logger.info("Número %s quitado de la lista negra", numero)
        return True
    if numero in _numeros_de_env():
        logger.warning(
            "El número %s viene de la variable de entorno %s; quítalo de ahí "
            "directamente (no se puede borrar desde el código).",
            numero, BLACKLIST_ENV_VAR,
        )
    return False


def list_all() -> dict:
    """Devuelve todos los números en lista negra con su información."""
    data = dict(_cargar_archivo())
    for numero in _numeros_de_env():
        data.setdefault(numero, {"motivo": "(definido en variable de entorno)", "agregado": "-"})
    return data
