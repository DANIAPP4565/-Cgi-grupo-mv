# Refuerzo de seguridad y persistencia

## Cambios incorporados

- Contraseñas nuevas con `scrypt`, salt individual y pepper opcional.
- Compatibilidad con hashes PBKDF2 anteriores y migración automática al ingresar correctamente.
- Longitud mínima: 12 caracteres para usuarios y 14 para administradores.
- Bloqueo temporal de 15 minutos después de 5 intentos fallidos.
- Sesión: 30 minutos de inactividad y 8 horas de duración máxima.
- Registro público cerrado por defecto; altas gestionadas por administrador.
- Contraseña provisoria con cambio obligatorio en el primer ingreso.
- Protección para no desactivar o degradar al último administrador activo.
- Auditoría de ingresos, fallos, bloqueos, cambios de contraseña y roles.
- Migración de la base existente sin borrar usuarios ni estudios.

## Repositorio histórico

- Copia estable de SQLite y Excel después de cada escritura relevante.
- Corte histórico semanal con SQLite, Excel y manifiesto SHA-256.
- Conservación local de hasta 52 cortes semanales.
- Subida de copias `latest` e históricas a S3 con cifrado AES-256 del lado del servidor.
- Restauración automática de SQLite y Excel desde S3 después de un redeploy.
- La app impide iniciar una base vacía cuando S3 está configurado y falla la restauración, salvo autorización explícita con `CGI_ALLOW_EMPTY_INIT=true`.

## Despliegue

1. Reemplazar el archivo principal por `app_robusta_segura.py`.
2. Agregar `boto3` y `openpyxl` al `requirements.txt`.
3. Cargar los valores de `secrets_seguridad_ejemplo.toml` en Streamlit Secrets.
4. Mantener `CGI_ALLOW_SELF_REGISTRATION=false`.
5. Activar versionado y reglas de retención en el bucket S3 como protección adicional.
6. Verificar en Administración que figure “Persistencia remota S3 activa”.

## Aclaración sobre la descarga semanal

La generación y conservación del Excel semanal es automática. La descarga al disco de una computadora requiere pulsar el botón de descarga, porque los navegadores suelen bloquear descargas automáticas sin una acción del usuario. El archivo sí permanece disponible en la app y en S3.
