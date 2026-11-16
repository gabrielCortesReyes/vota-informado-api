# Solicitud de acceso a la información: gastos de los diputados

Texto para la Cámara de Diputadas y Diputados, según la Ley N° 20.285 sobre Acceso a la Información Pública.

- **Dónde enviarla:** formulario en https://www.camara.cl/transparencia/FormularioinfLeyTransparencia.aspx
- **Plazo de respuesta:** 20 días hábiles, prorrogables por 10 (artículo 14).
- **Si no responden o la deniegan:** amparo ante el Consejo para la Transparencia dentro de 15 días hábiles (artículo 24).

Completa los datos entre corchetes antes de enviarla.

---

**Solicitante:** [Nombre completo], [RUT], [correo electrónico], en representación de [Softmind / a título personal].

**Información solicitada:**

Solicito copia, **en formato electrónico reutilizable (CSV o Excel)**, de la siguiente información que la Cámara publica en Transparencia Activa por cada diputada y diputado, para el período legislativo 2026-2030 (desde marzo de 2026 hasta el último mes publicado):

1. **Gastos operacionales**, por diputado, mes y categoría, con el monto en pesos. Las categorías son las que publica la Cámara: traspaso a asignación de personal de apoyo, telefonía, traslación, difusión, actividades con la comunidad, arriendo de inmueble, consumos básicos, equipamiento, materiales de oficina, correspondencia, mantención y reparaciones, habilitación de sedes, seguros, oficinas virtuales, servicios web, almacenamiento, oficina móvil, servicios menores y otras.
2. **Pasajes aéreos nacionales**, por diputado y mes: cantidad de tramos y monto en pesos (y, si existe, el detalle por viaje: fecha, origen y destino).

Para cada registro pido, si es posible, el **identificador del diputado que usa la Cámara** (el mismo `prmId` de las fichas de camara.cl), su nombre, el año, el mes, la categoría y el monto.

**Forma de entrega:** por correo electrónico, como archivo CSV o Excel. Esta información ya es pública en camara.cl, pero solo como páginas por diputado y por mes, lo que impide revisarla en conjunto. Por eso pido que se entregue en un archivo con todos los diputados (artículo 17 de la Ley N° 20.285 y principio de facilitación del artículo 11, letra f).

**Solicitud adicional:** pido que estos datos se publiquen también de forma periódica en el portal de Datos Abiertos de la Cámara (opendata.camara.cl), junto a los servicios de votaciones, sesiones y comisiones que ya existen.

**Finalidad (opcional):** mostrar a la ciudadanía, en la aplicación Vota Informado, cómo usa cada diputado sus asignaciones parlamentarias, con la Cámara como fuente.

---

## Al recibir la base

1. Pásala al formato de [`deploy/gastos/plantilla.csv`](../deploy/gastos/plantilla.csv): columnas `diputado_id;diputado;anno;mes;tipo;categoria;monto`. Si no viene el Id, basta con el nombre («Vergara, Diego» o «Diego Vergara Rodríguez»). Los montos pueden traer puntos de miles y `$`.
2. Cárgala:
   - con la API: `POST /admin/gastos/csv?fuente=Solicitud%20N°XXXX`, con el CSV como cuerpo (`Content-Type: text/csv`) y un token de administrador;
   - o con la línea de comandos: `python -m app.cli gastos archivo.csv "Solicitud N° XXXX"`.
3. Revisa `sin_identificar` en la respuesta: son los nombres que no se pudieron asociar a un diputado. Corrígelos con su `diputado_id` y vuelve a cargar; cada carga reemplaza los meses que trae.
