"""Muestra el estado de la base SQLite del visor.

Uso:
    .\\venv\\Scripts\\python.exe check_db.py

No necesita que el servidor esté arrancado.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "instance" / "spherex.db"
TABLES = ("users", "conversations", "messages", "favorites", "preferences")


def main() -> None:
    print(f"Archivo: {DB}")
    if not DB.is_file():
        print("La base todavía no existe.")
        print("Arranca el servidor una vez o ejecuta:")
        print('  .\\venv\\Scripts\\python.exe -c "from spherex import db; db.init()"')
        return

    print(f"Tamaño:  {DB.stat().st_size} bytes")
    with sqlite3.connect(DB) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type='table' order by name"
            )
        ]
        print("Tablas:  " + ", ".join(tables))
        for table in TABLES:
            if table in tables:
                count = conn.execute(f"select count(*) from {table}").fetchone()[0]
                print(f"  {table:<14} {count:>4} filas")

        users = conn.execute(
            "select id, username, email, created_at from users order by id"
        ).fetchall()
        if users:
            print("\nUsuarios registrados:")
            for user in users:
                print(f"  #{user[0]}  {user[1]}  <{user[2]}>  {user[3]}")


if __name__ == "__main__":
    main()
