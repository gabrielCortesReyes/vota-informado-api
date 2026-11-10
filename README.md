# 🗳️ Vota Informado API

API REST en JSON con la información de la **Cámara de Diputadas y Diputados de Chile**
(proyectos de ley, votaciones, diputados, sesiones, asistencia y catálogos) y una capa de
**participación ciudadana**: cuentas de usuario, ficha de cada diputado y senador con descripción
de Wikipedia, comentarios, calificaciones de 1 a 5 y encuestas ("¿votaste por él?", "¿volverías a votar?").

- Los datos legislativos se consultan **en vivo** a [opendata.camara.cl](https://opendata.camara.cl/)
  con caché en memoria (1 hora por defecto).
- En la base se guardan los usuarios, el **padrón** de parlamentarios y partidos (para poder
  comentarlos y calificarlos) y la participación.

## Inicio rápido

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abre **http://localhost:8000/docs** (Swagger) para ver y probar todos los endpoints.

Carga el padrón de parlamentarios (partidos, diputados, senadores y Wikipedia) y crea tu administrador:

```bash
python -m app.cli sincronizar            # agrega --sin-wikipedia para ir más rápido
# crea tu cuenta con POST /auth/register y luego:
python -m app.cli admin tu@correo.cl
```

Conviene repetir `sincronizar` cada cierto tiempo (por ejemplo, con Cloud Scheduler llamando a
`POST /admin/sincronizar`), para reflejar cambios de partido o reemplazos.
Sin configuración usa SQLite (`vota_informado.db`). Con Docker y PostgreSQL:

```bash
docker compose up --build
```

Pruebas (no necesitan internet, simulan la Cámara): `pip install -r requirements-dev.txt && pytest -q`

## Endpoints

Todas las rutas de datos son `GET`, públicas, y devuelven el XML de la Cámara convertido a JSON
(mismos nombres de campo: `Id`, `Nombre`, `ApellidoPaterno`, `FechaInicio`, …).
Los campos con atributo, como `<Sexo Valor="0">Femenino</Sexo>`, quedan como `{"Valor": 0, "Nombre": "Femenino"}`.

| Endpoint | Servicio de la Cámara |
|---|---|
| **Proyectos de ley** | |
| `/proyectos/mensajes?anno=2025` | WSLegislativo `retornarMensajesXAnno` |
| `/proyectos/mociones?anno=2025` | WSLegislativo `retornarMocionesXAnno` |
| `/proyectos/{boletin}` | WSLegislativo `retornarProyectoLey` |
| `/proyectos/{boletin}/votaciones` | WSLegislativo `retornarVotacionesXProyectoLey` |
| `/proyectos/{boletin}/documento` | WSLegislativo `retornarDocumentoIngresoProyectoLey` |
| `/proyectos/ley/{numero_ley}` | WSLegislativo `retornarProyectosLeyxNumeroLey` |
| **Votaciones** | |
| `/votaciones?anno=2025` | WSLegislativo `retornarVotacionesXAnno` |
| `/votaciones/{id}` | WSLegislativo `retornarVotacionDetalle` |
| **Legislaturas y períodos** | |
| `/legislaturas`, `/legislaturas/actual` | WSLegislativo `retornarLegislaturas`, `retornarLegislaturaActual` |
| `/periodos`, `/periodos/actual` | WSLegislativo `retornarPeriodosLegislativos`, `retornarPeriodoLegislativoActual` |
| `/materias` | WSLegislativo `retornarMaterias` |
| `/tramites/constitucionales`, `/tramites/reglamentarios` | WSLegislativo `retornarTramites…` |
| **Diputados** | |
| `/diputados` | WSDiputado `retornarDiputados` |
| `/diputados/actuales` | WSDiputado `retornarDiputadosPeriodoActual` |
| `/diputados/periodo/{periodo_id}` | WSDiputado `retornarDiputadosXPeriodo` |
| `/diputados/{id}` | WSDiputado `retornarDiputado` |
| **Sesiones de Sala** | |
| `/sesiones?anno=2025` | WSSala `retornarSesionesXAnno` |
| `/sesiones/legislatura/{id}` | WSSala `retornarSesionesXLegislatura` |
| `/sesiones/{id}` (votaciones y asistencia) | WSSala `retornarSesionAsistencia` |
| **Catálogos** (24) | |
| `/catalogos` (lista) y `/catalogos/{nombre}` | WSComun `retornar…` |

Catálogos: `comunas`, `distritos`, `regiones`, `provincias`, `ministerios`, `partidos-politicos`,
`tipos-asistencia`, `tipos-camara-origen`, `tipos-estado`, `tipos-estado-acuerdos-resoluciones`,
`tipos-estado-sesion-comision`, `tipos-estado-sesion-sala`, `tipos-iniciativa-proyecto-ley`,
`tipos-justificaciones-inasistencia`, `tipos-legislatura`, `tipos-opcion-voto`, `tipos-quorum-votacion`,
`tipos-resultado-votacion`, `tipos-sesion-comision`, `tipos-sesion-sala`, `tipos-sexo`,
`tipos-titular-asistencia`, `tipos-votacion`, `tipos-votacion-proyecto-ley`.

**Cuentas de usuario**

| Método | Ruta | |
|---|---|---|
| POST | `/auth/register` | `{"fiscal_id" (RUT), "names", "last_names", "email", "password" (mín. 8), "phone_number"?, "commune_id"?, "district_vote"?, "voted_parliamentarian_id"?}` → tokens |
| POST | `/auth/login` | `{"username","password"}` → access + refresh token (máx. 5/min) |
| POST | `/auth/refresh-token` | `{"refresh_token"}` → tokens nuevos |
| POST | `/auth/change-password` | `{"new_password"}` (requiere token) |
| POST | `/auth/restore-password` | Clave temporal por WhatsApp (requiere `WPP_ENDPOINT`) |
| GET | `/user/me` | Perfil del usuario |
| GET/POST | `/user/me/preferences` | Comuna (`Numero` de `/catalogos/comunas`) y a quién votó: `district_vote` = `diputado` (con `voted_parliamentarian_id`, que debe ser de su distrito), `otro`, `no_voto` o `no_responde`. Solo cambian los campos enviados |
| GET | `/user/me/distrito` | Distrito del usuario según su comuna, sus diputados y a quién votó |
| GET | `/user/me/distrito/votaciones/{id}` (o `/ultima`) | Cómo votó cada diputado de su distrito; el suyo va primero con `es_mi_diputado` |
| GET/PUT | `/user/me/materias` | Materias que le interesan: `{"materia_ids": [..]}` reemplaza la lista |
| GET | `/user/me/materias/proyectos?limit=&offset=` | Proyectos de ley de sus materias, del más reciente |

**Parlamentarios y participación** (leer es público; escribir requiere token)

| Método | Ruta | |
|---|---|---|
| GET | `/partidos` | Partidos con número de diputados y senadores en ejercicio |
| GET | `/parlamentarios?camara=senador&partido_id=&distrito=&q=` | Buscar diputados y senadores (el partido trae `logo_url`) |
| GET | `/distritos/{n}/diputados` | Diputados en ejercicio de un distrito |
| GET | `/votaciones/{id}/distrito/{n}` | Cómo votaron los diputados de un distrito |
| GET | `/parlamentarios/{id}` | Ficha: partido, región, contacto, descripción y foto de Wikipedia, promedio de calificación y resumen de `asistencia` (diputados) |
| GET | `/parlamentarios/{id}/asistencia?desde=&hasta=` | % de asistencia a la Sala, cada sesión a la que no asistió y su justificación (diputados) |
| GET | `/materias/categorias` | Materias con proyectos guardados y cuántos tiene cada una |
| GET | `/materias/{id}/proyectos?limit=&offset=` | Proyectos de una materia (formato de la Cámara + `Materias` y `Autores`) |
| GET | `/parlamentarios/{id}/materias?desde=` | En qué materias presenta mociones un diputado (`mociones`, `con_materia`, top de materias) |
| GET | `/parlamentarios/{id}/mociones?materia=` | Mociones de las que es autor |
| GET | `/votaciones/{id}/asistencia` | Asistencia a la sesión de esa votación: quién no asistió y su justificación |
| GET | `/asistencia/diputados?orden=desc&partido_id=&distrito=` | Asistencia de todos los diputados en ejercicio, ordenada por % |
| GET/POST | `/parlamentarios/{id}/comentarios` | Ver y dejar comentarios (se muestra "María S.") |
| PUT/DELETE | `/comentarios/{id}` | Editar o borrar un comentario propio |
| PUT | `/parlamentarios/{id}/calificacion` | `{"score": 1-5}`, una por usuario (reenviar la cambia) |
| GET | `/parlamentarios/{id}/calificacion` (y `/mia`) | Promedio y distribución |
| GET | `/encuestas`, `/encuestas/{id}` | Encuestas activas y sus preguntas |
| POST | `/encuestas/{id}/respuestas` | `{"parliamentarian_id", "answers": [{"question_id", "value"}]}` |
| GET | `/encuestas/{id}/resultados?parliamentarian_id=` | % de "sí", promedios y conteos |
| GET | `/encuestas/{id}/mis-respuestas` | Lo que respondió el usuario |

Al arrancar se crea la encuesta **"Evaluación de tu parlamentario"**: ¿Votaste por él/ella?,
¿Volverías a votar por él/ella? y ¿Apruebas su desempeño?. Tipos de pregunta: `si_no`, `escala` (1-5),
`opcion` (con lista de opciones) y `texto` (no se publica en resultados).

**Administración** (usuario con `is_admin`)

| Método | Ruta | |
|---|---|---|
| POST | `/admin/sincronizar?wikipedia=true` | Recarga partidos, diputados, senadores, asistencia y descripciones |
| POST | `/admin/asistencia/sincronizar?desde=2026-03-11&recargar_dias=30` | Carga solo la asistencia a la Sala |
| POST | `/admin/proyectos/sincronizar?desde_anno=2025&limite=500` | Carga mociones y mensajes y pide el detalle (materias y autores) de los que falten |
| POST | `/admin/encuestas` | Crear una encuesta nueva |
| PATCH | `/admin/encuestas/{id}` | `{"active": false}` para cerrarla |
| PATCH | `/admin/comentarios/{id}` | `{"hidden": true}` para ocultar un comentario |
| PATCH | `/admin/partidos/{id}/logo` | Fijar a mano `logo_url`, `logo_license`, `logo_author`, `logo_source_url` |

Rutas con token: header `Authorization: Bearer <access_token>`.
Otras: `/health`, `/cache/clear` (requiere token).

**Materias**: la Cámara no deja buscar proyectos por materia y los listados por año no traen materias
ni autores. La sincronización diaria (`app/proyectos.py`) guarda mociones y mensajes desde
`PROYECTOS_DESDE_ANNO` (2025) y pide el detalle de hasta `PROYECTOS_DETALLES_POR_CORRIDA` (500) proyectos
por corrida; los de los últimos 90 días se releen cada 7 (la Cámara asigna materias después).
Primera carga: `python -m app.cli proyectos --limite 3000` o varias veces `POST /admin/proyectos/sincronizar`.

Errores: `404` si la Cámara no tiene el dato, `502` si la Cámara no responde o rechaza los parámetros.

## Configuración

Ver `.env.example`. Lo importante:

- `APP_SECRET_KEY`: clave JWT, **obligatoria en producción**.
- `DATABASE_URL` o `PGSQL_*`: base de usuarios (si no, SQLite local). Las tablas se crean solas al arrancar.
- `CACHE_TTL_SECONDS`: cuánto se guarda en caché cada respuesta de la Cámara (3600).
- `WIKIPEDIA_USER_AGENT`: identifica la app ante Wikipedia (lo exige su política de uso).

**Fuentes del padrón:** diputados y partidos desde la Cámara (`retornarDiputadosPeriodoActual`,
`retornarPartidosPoliticos`), senadores desde el Senado (`senadores_vigentes.php`) y descripciones desde
Wikipedia en español. Solo se acepta un artículo de Wikipedia si su título tiene el nombre y apellido
del parlamentario y el texto habla de un político chileno; los demás quedan sin descripción. Los textos
de Wikipedia son CC BY-SA 4.0: la app debe mostrar el enlace (`wikipedia_url`) junto a la descripción.

**Distritos:** se usan los 28 distritos vigentes (Ley 20.840). `/catalogos/comunas` agrega a cada comuna su
`Distrito` cruzando `retornarComunas` por nombre con `app/data/comunas_distrito.json` (`retornarDistritos` de la Cámara
trae aún los 60 distritos antiguos). Cada diputado guarda su distrito desde `DiputadoPeriodo/Distrito` si viene y es
1-28; si no, desde `app/data/diputados_distrito.json` (Id de la Cámara → distrito, período 2026-2030). Ambos JSON se
generaron desde Wikipedia ("Distritos electorales de Chile" y "LVII periodo legislativo del Congreso Nacional de Chile").

**Asistencia a la Sala:** desde WSSala. `retornarSesionesXAnno` da las sesiones y `retornarSesionAsistencia` dice,
por diputado, `Asiste` / `No Asiste` y, si no asistió, la `Justificacion` (p. ej. "Licencia médica (Art. 42)", con las
marcas `RebajaAsistencia` y `RebajaQuorum` de la Cámara; catálogo en `/catalogos/tipos-justificaciones-inasistencia`).
Se guardan en las tablas `sala_sessions` y `sala_attendance`, solo sesiones **celebradas** desde `PERIODO_INICIO`
(2026-03-11, inicio del período 2026-2030). La sincronización diaria agrega las sesiones nuevas y vuelve a leer las de
los últimos `ASISTENCIA_RECARGAR_DIAS` (30), porque la Cámara registra justificaciones después. El % es
asistencias / sesiones × 100; una inasistencia es "justificada" si la Cámara le registró una justificación. Cada
inasistencia trae `fuente_url`, la consulta oficial de esa sesión.

**Logos de partidos:** se buscan en Wikipedia en español → Wikidata (propiedad P154, logo vigente, país Chile)
→ Wikimedia Commons, que entrega un PNG de 256 px, la licencia y el autor (`logo_license`, `logo_author`,
`logo_source_url`). La app debe mostrar el crédito y enlazar a `logo_source_url`. Los logos son marcas de sus
partidos; Commons solo guarda los de dominio público o con licencia libre. Si alguno queda mal, se corrige con
`PATCH /admin/partidos/{id}/logo`.

## Despliegue (Cloud Run)

Cloud Run + Cloud SQL + Cloud Scheduler, con scripts en `deploy/`. Guía completa: [deploy/README.md](deploy/README.md).

```bash
./deploy/setup-gcp.sh && ./deploy/deploy.sh && ./deploy/setup-scheduler.sh
```

## Estructura

```
app/
├── main.py          # arma la app y registra las rutas
├── config.py        # variables de entorno
├── database.py      # SQLAlchemy
├── models.py        # usuarios, padrón, comentarios, calificaciones, encuestas
├── schemas.py       # validación de entrada/salida
├── security.py      # contraseñas, JWT, rate limit, rol admin
├── rut.py           # validación de RUT
├── sync.py          # carga del padrón (Cámara, Senado, Wikipedia)
├── asistencia.py    # asistencia a la Sala y justificaciones (WSSala)
├── cli.py           # python -m app.cli sincronizar | asistencia | admin
├── camara/
│   ├── client.py    # única puerta hacia opendata.camara.cl (+ caché)
│   └── xml.py       # XML → JSON
└── routes/          # legislativo, diputados, sala, asistencia, catalogos, parlamentarios,
                     # encuestas, auth, usuario, admin
tests/               # pytest, sin red (Cámara, Senado y Wikipedia simulados)
```

## Historia

Vota Informado nace del proyecto Democracia Inteligente: reemplaza a `democracia-inteligente` (Vota Bien, que copiaba los datos a PostgreSQL con
un programador de tareas) y a `democracia-inteligente-app-api` (usuarios). Se conservan los mismos
nombres de tablas de usuarios y las rutas `/auth/*` y `/user/me*` de la app-api.

## Licencia

MIT, ver [LICENSE](LICENSE).
