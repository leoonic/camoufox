# Camoufox RDP Browser - Manual de uso

## Que es

`RDPBrowser` es un modo de automatizacion alternativo para Camoufox que usa Firefox Remote Debug Protocol (RDP) en lugar de Playwright/Juggler. Es indetectable por PerimeterX, Shopee, Akamai y otros sistemas anti-bot.

## Por que existe

Playwright controla Firefox via Juggler, que deja rastros comportamentales detectables:
- `document.hasFocus()` siempre true
- `focusmanager.testmode` activo
- JSWindowActors inyectados en cada frame
- BFCache deshabilitado
- Sandbox execution contexts extras

RDP no hace nada de esto. Es el protocolo de DevTools (debugger), invisible para las paginas web.

## Instalacion

Requisitos:
```bash
pip install geckordp websockets
```

La libreria esta incluida en el fork de camoufox (`pip install -e pythonlib/`).

## Uso basico

```python
import asyncio
from camoufox.rdp_api import RDPBrowser

async def main():
    async with RDPBrowser(
        executable_path=r"C:\path\to\camoufox.exe",  # opcional si esta instalado
        headless=False,
        viewport={"width": 1920, "height": 1080},
    ) as browser:
        page = await browser.new_page()

        await page.goto("https://example.com")
        print(page.url)

        title = await page.evaluate("document.title")
        html = await page.content()

        await page.screenshot("screenshot.png")

asyncio.run(main())
```

## API de RDPBrowser

### Constructor

```python
RDPBrowser(
    executable_path=None,       # path al binario camoufox.exe (auto-detecta si no se pasa)
    headless=False,             # modo headless
    proxy=None,                 # {"server": "http://host:port", "username": "user", "password": "pass"}
    viewport=None,              # {"width": 1920, "height": 1080} (default)
    rdp_port=6000,              # puerto RDP (cambiar si multiples instancias)
    ws_port=8775,               # puerto WebSocket para extension (cambiar si multiples instancias)
    firefox_user_prefs=None,    # prefs adicionales de Firefox
    profile_path=None,          # directorio de perfil (temporal si no se pasa)
    extension_dir=EXTENSION_DIR # directorio de la extension de input
)
```

### Context manager

```python
async with RDPBrowser(...) as browser:
    page = await browser.new_page()
    # ...
# browser se cierra automaticamente
```

### Metodos

```python
await browser.start()       # inicia browser + RDP + extension
await browser.new_page()    # obtiene pagina (RDPPage)
await browser.close()       # cierra todo
```

## API de RDPPage

### Navegacion

```python
await page.goto(url, wait_until="load", timeout=30000)
await page.reload(timeout=30000)
await page.wait_for_load_state("load", timeout=30000)
page.url  # URL actual (property)
```

### Contenido

```python
html = await page.content()                    # HTML completo
value = await page.evaluate("document.title")  # ejecutar JS arbitrario
rect = await page.query_selector("#element")   # obtener posicion {x, y, w, h}
```

### Interaccion (trusted via extension)

```python
await page.click("#selector")           # click en elemento por CSS selector
await page.fill("#input", "texto")      # escribir en input
await page.mouse.click(x, y)           # click por coordenadas
await page.mouse.move(x, y)            # mover mouse
await page.mouse.down(x, y)            # mousedown
await page.mouse.up(x, y)              # mouseup
await page.mouse.wheel(0, 500)         # scroll vertical
await page.keyboard.type("texto")      # escribir con teclado
await page.keyboard.press("Enter")     # tecla especifica
```

### Screenshots

```python
data = await page.screenshot()              # bytes PNG
data = await page.screenshot("file.png")    # guarda a archivo
```

## Proxy

```python
async with RDPBrowser(
    proxy={
        "server": "http://proxy.example.com:8080",
        "username": "user",
        "password": "pass",
    }
) as browser:
    # Todo el trafico pasa por el proxy
    # Autenticacion manejada automaticamente via extension
```

## Multiples instancias

Para correr multiples browsers en paralelo, usar puertos diferentes:

```python
browser1 = RDPBrowser(rdp_port=6000, ws_port=8775)
browser2 = RDPBrowser(rdp_port=6001, ws_port=8776)
```

## Diferencias con AsyncCamoufox (Playwright)

| Feature | AsyncCamoufox | RDPBrowser |
|---------|--------------|------------|
| Protocolo | Juggler (Playwright) | Firefox RDP (geckordp) |
| Deteccion PX/Shopee | Detectado | Indetectable |
| navigator.webdriver | Parcheado a false | Nativo false |
| Trusted events | Si (via Juggler) | Si (via extension nsIDOMWindowUtils) |
| Fingerprint spoofing | CAMOU_CONFIG env vars | No (usa defaults del binario) |
| Per-context fingerprints | Si (AsyncNewContext) | No |
| humanize (Bezier mouse) | Si | No (usar mouse.move manual) |
| wait_until="networkidle" | Nativo | Polling readyState |
| Dependencias | playwright | geckordp, websockets |

## Cuando usar cada uno

- **RDPBrowser**: sitios con anti-bot avanzado (PerimeterX, Shopee, Akamai), donde Playwright es detectado
- **AsyncCamoufox**: sitios sin anti-bot agresivo, donde se necesita fingerprint spoofing per-context o humanize nativo
