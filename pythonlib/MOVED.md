# El desarrollo activo del pythonlib se mudo

El package Python `camoufox` (wrapper + WebExtension) ahora vive y se mantiene
en el monorepo:

**[`LeooNic/Proyecto-ICRON`](https://github.com/LeooNic/Proyecto-ICRON) → `camoufox_lib/`**

## Por que

El wrapper y la extension evolucionan en lockstep con el motor de automatizacion
(`camoufox_mcp/`), el recorder (`icron/`) y los criterios (`criterios/`). Tener
todo en un mismo repo permite:
- Commits atomicos cross-componente (un cambio en `extension/background.js` y
  su consumidor en `camoufox_mcp/server.py` van juntos).
- Cero drift entre el wrapper y el codigo que lo usa.
- `pip install -e ../camoufox_lib` en el monorepo, sin pasar por git+url.

## Que queda aca

Este `pythonlib/` queda como **snapshot historico** (ultimo commit propio:
`aa9effa`). El resto de este fork (`bundle/`, `additions/`, `patches/`,
`scripts/`, `dist/`) sigue siendo lo necesario para **rebuild del binario**
Camoufox. Cuando haya que regenerar el binario, copiar el `pythonlib` actual
del monorepo aca temporalmente (o ajustar el build flow).

## Para usar el wrapper

```bash
git clone https://github.com/LeooNic/Proyecto-ICRON
cd Proyecto-ICRON
./scripts/setup.ps1
```

`scripts/setup.ps1` instala el wrapper editable (`pip install -e camoufox_lib`)
y descarga el binario empaquetado desde el Release `v0.1.0` del propio repo.
