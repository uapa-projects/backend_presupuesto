# 📁 Configuración de Google Drive API & Autenticación

Este documento detalla el flujo para configurar una **Service Account**, generar credenciales y asegurar el acceso a carpetas en **Shared Drives** (Unidades Compartidas) desde un entorno Django.

---

## 🚀 1. Creación del Proyecto y Service Account

1. Ve a [Google Cloud Console](https://console.cloud.google.com).
2. Crea un nuevo proyecto o selecciona uno existente.
3. En el menú lateral, ve a **APIs y Servicios > Biblioteca**.
4. Busca **"Google Drive API"** y haz clic en **Habilitar**.
5. Ve a **APIs y Servicios > Credenciales**.
6. Haz clic en **Crear credenciales > Cuenta de servicio**.
7. Asigna un nombre (ej. `django-drive-manager`) y finaliza la creación.

## 🔑 2. Generación del Archivo de Claves (JSON)

> [!CAUTION]
> **IMPORTANTE:** Nunca subas este archivo a GitHub. Google inhabilitará la clave automáticamente si se detecta en un repositorio público.

1. Dentro de la sección **Cuentas de servicio**, haz clic en el email de la cuenta creada.
2. Ve a la pestaña **Claves (Keys)**.
3. Haz clic en **Agregar clave > Crear clave nueva**.
4. Selecciona el formato **JSON** y descárgalo.
5. Renombra el archivo (ej. `drive-credentials.json`) y guárdalo en la raíz de tu proyecto.
6. **Añade el archivo a tu `.gitignore`**:
   ```text
   drive-credentials.json

---

# 🔐 Acceso con la plataforma (auth_service) y diseño de UAPA Suite

CEP ya no tiene login propio (se eliminó el de Google/Netlify). El acceso lo resuelve `auth_service` (`POST /auth/authenticate`) y el código está en la app `authentication/`.

- **Inicio de sesión único:** CEP lee las cookies compartidas `access_token` / `refresh_token` del dominio `.uapa.edu.do`. Si ya entraste en otra app de la suite, entras directo.
- **Revalidación:** `PlataformaMiddleware` vuelve a validar la sesión cada 5 minutos. Si las cookies compartidas desaparecen, cierra la sesión de CEP.
- **Salir en todas las apps:** cerrar sesión en CEP llama a `/auth/logout`. Cuando otra app cierra sesión, la plataforma envía un aviso firmado (JWT RS256) a `/accounts/logout/` y CEP cierra todas las sesiones de ese correo (tabla `SesionPlataforma`).
- **Usuarios:** CEP **nunca crea usuarios**. El correo tiene que existir en `auth.User` y estar activo. Si no existe, se muestra «Tu cuenta no está registrada en CEP (Presupuesto).»
- **Roles** (`authentication/roles.py`): todos salen de la plataforma (roles de la app `cep` en `user_app_roles`) y se asignan allí, no en CEP:
  - **Supervisor** (antes `is_staff` y el grupo Supervisor): ve y revisa todas las solicitudes. No tiene acceso a Administración.
  - **Administrador** (antes `is_superuser`): todo lo del Supervisor, la pantalla Administración y el admin técnico de Django.
  - **Compra RSS / RSD / RCO** y **Colaborador**: los antiguos grupos de Django.
  - `PlataformaMiddleware` calcula `is_staff` / `is_superuser` en cada petición a partir de los roles (solo en memoria; los valores de `auth_user` ya no cuentan). Un cambio de rol llega en la siguiente revalidación (5 minutos) o al volver a entrar.
  - Los grupos de Django quedan como **espejo** de los roles: se reescriben en cada inicio de sesión o revalidación y solo sirven para saber a quién escribir (correo a Compras), porque la plataforma no da la lista de usuarios por rol. Quien nunca entró después de recibir un rol no está en el espejo.

## Administración (React)

El enlace «Administrar» abre `/administracion`, una pantalla de React (`components/admin/`) con su API en `presupuesto/administracion.py` (`/api/administracion/…`). **Solo la ve el rol Administrador**; el Supervisor no tiene el enlace y la API le responde 403.

- Cuentas analíticas y cuentas contables: agregar, editar y eliminar (si no tienen solicitudes).
- Parámetros (`Configuracion`).

- Las contraseñas (`PLATFORM_SERVICE_PASSWORD` y cualquier parámetro con PASSWORD, SECRET, TOKEN o CLAVE en el nombre) no se listan ni se editan, ni aquí ni en el admin de Django: se cargan con `manage.py clave_servicio`.
- No se administran en CEP: usuarios, roles (en la plataforma), sedes y credenciales de Google.
- El admin de Django (`/admin/`, botón «Admin técnico») queda como respaldo solo para el rol Administrador.
- **Foto:** la foto de Google llega de la plataforma (`foto_url`) y se muestra en la barra superior.

## Configuración (modelo `Configuracion`)

No se usan variables de entorno ni `.env`. La migración `presupuesto/0027_parametros_plataforma` crea estos parámetros:

| Parámetro | Uso |
|---|---|
| `PLATFORM_AUTH_URL` | URL base de auth_service |
| `PLATFORM_APP_SLUG` | `cep` |
| `PLATFORM_SERVICE_USERNAME` | usuario de servicio (`cep-svc`) |
| `PLATFORM_SERVICE_PASSWORD` | queda **vacío**; se guarda con `clave_servicio` |
| `PLATFORM_COOKIE_DOMAIN` | dominio de las cookies compartidas |
| `PLATFORM_STATIC_URL` | URL de static_service (Bootstrap, iconos, `suite.css`, `suite-shell.js`, `acceso.js`) |
| `GOOGLE_OAUTH_CLIENT_ID` | botón de Google de la ventana de acceso |

### Contraseña del usuario de servicio

```bash
python manage.py migrate
python manage.py clave_servicio            # la pide sin mostrarla y la prueba contra la plataforma
python manage.py clave_servicio --sin-probar
```

Si la plataforma rechaza la clave, el comando no la guarda. La clave nunca va en una migración, en el HTML, en JavaScript, en una URL ni en el log.

## Interfaz: toda en React

Django es solo el backend: la API, el acceso (`/accounts/`) y el admin de Django. No dibuja pantallas.

- **`GET /api/sesion/`** (`core/sesion.py`) entrega lo que la interfaz necesita: el usuario, el token CSRF, la URL de static_service (`PLATFORM_STATIC_URL`), la configuración de `suite-shell.js`, los datos de la ventana de acceso (solo sin sesión) y los avisos pendientes de Django (p. ej. «Tu sesión se ha cerrado correctamente.»).
- **La SPA:** cualquier ruta que no sea `api/`, `accounts/`, `admin/` o `django_static/` devuelve `frontend/index.html` tal cual sale del build (`presupuesto/views.py`, sin plantillas ni caché). Los errores 400/403/404/500 de Django responden JSON; las páginas de error y la de «no encontrada» son de React.
- **Esqueleto de la suite** (`Frontend/presupuesto/components/suite/`): React dibuja los contenedores `data-suite-shell` (sidebar, topbar y footer), el JSON `#suite-shell-config`, el menú de CEP (`.suite-subnav`), la portada, los avisos y la ventana `#acceso`. Después carga desde static_service Bootstrap, `suite-shell.js` (con `data-ruta`), `suite.js` y, sin sesión, `acceso.js` y el botón de Google (`services/suite.ts`).
- Todo el CSS de la suite (Bootstrap 5.3.3, Bootstrap Icons, `suite.css`) también se carga desde `PLATFORM_STATIC_URL`. Los estilos propios de CEP están en `styles/cep.css` y solo usan las variables de `suite.css`.
- `hooks/useSuiteShell.ts` actualiza la ruta de la barra superior al navegar sin recargar la página.
- Siguen en Django: el admin técnico (`/admin/`, solo Administrador), los correos y la plantilla del certificado PDF.
- **Agregar CEP u otra app a la suite** se hace en auth_service: registra la app y asigna los permisos. CEP solo consume esa lista (`apps`).

## Pendientes en UAPA Suite

Cada solicitud en estado `PENDIENTE` aparece en el panel «Pendientes en la suite» de **todos los usuarios con rol
Supervisor en CEP** como «Revisar solicitud «…»», con el solicitante, la sede, el monto y el enlace a la solicitud
(`presupuesto/pendientes.py`). Vive en notify_service con la clave `cep:solicitud:<id>` y la cuenta `cep-svc` (ya
tiene el servicio `notify`).

- Se crea al crear la solicitud y se actualiza al editarla.
- Se cierra para todos los supervisores cuando uno la aprueba o la rechaza.
- En `POR_REVISION` también se cierra y vuelve cuando el colaborador resuelve todas las revisiones.

Mientras la solicitud está en `POR_REVISION`, **el colaborador que la pidió** tiene su propio pendiente «Corregir
solicitud «…»» (clave `cep:solicitud:<id>:revision`) con la observación del supervisor, o «N observación(es) por
resolver: …» si hay varias. Se actualiza al resolver cada una y se cierra cuando las resuelve todas (la solicitud
vuelve a `PENDIENTE` y el pendiente regresa a los supervisores) o si un supervisor la aprueba o la rechaza.

Cuando la solicitud está `APROBADA` **y tiene certificado** (lo mismo que dispara el correo «Presupuesto Aprobado»),
**Compras de su sede** tiene el pendiente «Confirmar solicitud «…»» (clave `cep:solicitud:<id>:compras`): Compra RSS
siempre, más Compra RSD o Compra RCO si la sede es esa (`utils.roles_compras`, la misma regla del correo). Su enlace es
la pantalla de confirmar, que confirma al abrirla; se cierra en cuanto Compras la confirma (`confirmado`). No importa
si el certificado se emite antes o después de aprobarla: se abre cuando se cumplen las dos cosas.

Si se elimina la solicitud, desaparecen todos.

Se envía al confirmarse la transacción y nunca rompe la petición: si la plataforma no responde, queda un aviso en el
log. Para la carga inicial (las solicitudes que ya están en `PENDIENTE`) y como respaldo:

```bash
python manage.py sincronizar_pendientes --simular   # cuántas abriría y cerraría, y los supervisores
python manage.py sincronizar_pendientes             # --url-base https://cep.uapa.edu.do (por defecto), --dias 30
```

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
python manage.py test tests                    # todas las pruebas
python manage.py test tests.test_navegador     # solo las de navegador (Playwright)
```

Las pruebas simulan auth_service con `httpx.MockTransport` y no llaman a la plataforma real. Las de navegador revisan cada pantalla a 1440 px y a 390 px: sin desplazamiento horizontal, sin errores de consola y con el esqueleto dibujado.

### Probar en local con el frontend

1. Ejecuta `python manage.py runserver 8080 --insecure`. Sin `local_settings.py`, Django corre con `DEBUG = False` y no sirve los estáticos (el admin saldría sin CSS); `--insecure` los sirve igual. Solo para local: en el servidor los sirve el servidor web después de `collectstatic`.
2. Ejecuta `npm run dev` en el frontend y abre `http://127.0.0.1:3000/`.

Vite sirve la interfaz con recarga en caliente y envía a Django `/api`, `/accounts`, `/admin` y `/django_static`.

Para producción, ejecuta `npm run build` en el frontend: escribe el build directamente en `Backend/frontend/` (`index.html` y `assets/` con hash en el nombre, así no hace falta subir ninguna versión a mano). Después, `python manage.py collectstatic` en el servidor.

## «El servicio de acceso no está disponible»

Primero revisa `logs/plataforma.log`: ahí está la causa exacta.

- **Sin «Inténtalo más tarde»:** falta configuración en CEP. `PLATFORM_AUTH_URL`, `PLATFORM_SERVICE_USERNAME` o `PLATFORM_SERVICE_PASSWORD` está vacío; ejecuta `python manage.py clave_servicio`.
- **Con «Inténtalo más tarde»:** la configuración está completa, pero la plataforma falló. Las causas posibles son:
  - no hay red o el tiempo de espera se agotó;
  - auth_service respondió con un error 5xx;
  - la cuenta de servicio fue rechazada (clave incorrecta);
  - la app `cep` no existe en la plataforma (`app_not_found`).
- **Si auth_service responde con errores 5xx de base de datos**, en el servidor de la plataforma ejecuta:

  ```bash
  alembic upgrade head          # desde el venv del servicio
  sudo systemctl restart platform-auth
  ```

## Pendiente

- `suite-shell.js` no tiene una función para cambiar `data-ruta` después de cargar. CEP la actualiza modificando el DOM (`useSuiteShell.ts`). Cuando exista `SuiteShell.ruta(valor)`, conviene usarla.
- static_service no tiene `img/favicon.png`; se usa `img/cep.ico`.
- `netlify.toml` del frontend ya no se usa.
