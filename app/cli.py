"""Comandos de administración.

  python -m app.cli sincronizar [--sin-wikipedia]   Carga partidos, diputados, senadores y Wikipedia
  python -m app.cli asistencia [AAAA-MM-DD]        Carga la asistencia a la Sala (desde el inicio del período)
  python -m app.cli patrimonio [--forzar]          Carga las declaraciones de patrimonio (InfoProbidad)
  python -m app.cli proyectos [AAAA] [--limite N]  Carga proyectos de ley con sus materias y autores
  python -m app.cli admin correo@ejemplo.cl         Da rol de administrador a un usuario
"""
import asyncio
import sys

from app.database import SessionLocal, init_db
from app.models import UserCredentials
from app.asistencia import sincronizar_asistencia
from app.patrimonio import sincronizar_patrimonio
from app.proyectos import sincronizar_proyectos
from app.sync import sincronizar_todo


def main(args: list[str]) -> None:
    init_db()
    db = SessionLocal()
    try:
        if args[:1] == ["sincronizar"]:
            print(asyncio.run(sincronizar_todo(db, wikipedia="--sin-wikipedia" not in args)))
        elif args[:1] == ["asistencia"]:
            print(asyncio.run(sincronizar_asistencia(db, desde=args[1] if len(args) > 1 else None)))
        elif args[:1] == ["patrimonio"]:
            print(asyncio.run(sincronizar_patrimonio(db, forzar="--forzar" in args)))
        elif args[:1] == ["proyectos"]:
            anno = next((int(a) for a in args[1:2] if a.isdigit()), None)
            limite = int(args[args.index("--limite") + 1]) if "--limite" in args else 500
            print(asyncio.run(sincronizar_proyectos(db, desde_anno=anno, limite=limite)))
        elif args[:1] == ["admin"] and len(args) == 2:
            cred = db.query(UserCredentials).filter(UserCredentials.username == args[1].lower()).first()
            if not cred:
                sys.exit(f"No existe el usuario {args[1]}")
            cred.user.is_admin = True
            db.commit()
            print(f"{args[1]} ahora es administrador")
        else:
            print(__doc__)
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
