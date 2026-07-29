# Diseño: exportación GLB nativa para img2threejs

Fecha: 2026-07-22
Rama: `feat/glb-export`
Estado: aprobado (pendiente de plan de implementación)

## Objetivo

Que la exportación a `.glb` sea una **capacidad nativa** del proyecto img2threejs,
no una herramienta externa. Cualquier modelo generado por el pipeline debe poder
exportarse desde el flujo habitual (un botón "Exportar .glb" en el visor), sin
cambios adicionales por modelo. La implementación debe estar **desacoplada** del
resto del pipeline y ser lo bastante limpia para proponerse como Pull Request al
repositorio original (`hoainho/img2threejs`).

## Contexto del repositorio

- El pipeline (`forge/stage3_build/generate_threejs_factory.py`) emite una factoría
  `create{Type}Model(options): THREE.Group` más `create{Type}LookDevLights()`.
- El **visor HTML no lo produce el pipeline**: lo escribe el agente por proyecto
  (ejemplo: `master-sword/index.html`, que es un visor autocontenido con la factoría
  inline). Tanto el visor artesanal como la factoría generada producen un
  `THREE.Group` **en el navegador**, vía un importmap con `three` y `three/addons/`.
- Los materiales son `MeshPhysicalMaterial` / `MeshStandardMaterial` estándar. Los
  modelos totalmente generados pueden usar `CanvasTexture` (texturas procedurales por
  canvas) y dependen de `document`.

Consecuencia de diseño: exportar **en el navegador** es el único camino robusto que
funciona para todos los modelos (las texturas canvas y la dependencia de `document`
hacen frágil un exportador headless en Node). `GLTFExporter` serializa `CanvasTexture`
horneándola como PNG dentro del `.glb`.

## Decisiones tomadas

1. **Mecanismo**: botón en el visor (navegador). Descartado: CLI headless en Node.
2. **Fork/PR**: rama local + PR preparado. No se hace push ni fork automáticamente.
3. **Formato**: `.glb` binario por defecto (un solo archivo, texturas embebidas).

## Arquitectura

### Módulo desacoplado: `export/glbExporter.js`

Módulo ES único, sin dependencias del pipeline ni de un visor concreto.

API pública:

```js
exportModelToGLB(object3D, options) → Promise<Blob>   // lógica pura, reutilizable
attachExportButton(object3D, options)                 // UI: inyecta botón flotante
```

Opciones (`options`):

- `filename` (string, def. `'model'`): nombre base del archivo descargado.
- `binary` (bool, def. `true`): `.glb` binario si true; `.gltf` + JSON si false.
- `resetRootTransform` (bool, def. `true`): exporta la raíz en pose canónica
  (rotación/posición de la raíz a cero), preservando transforms internos.
- `includeSculptUserData` (bool, def. `false`): si true, conserva un subconjunto
  JSON-safe de `userData`; si false, lo vacía.
- `onExported` (fn, opcional): callback tras exportar (para hooks/telemetría/tests).

Implementación:

- Usa `GLTFExporter` de `three/addons/exporters/GLTFExporter.js` (mismo importmap que
  el visor).
- `exportModelToGLB`: clona la raíz (`root.clone(true)`), sanea `userData`, opcionalmente
  neutraliza el transform de la raíz, exporta con `GLTFExporter`, y resuelve un `Blob`.
- `attachExportButton`: crea un `<button>` con estilos inline, posición fija; al pulsarlo
  deshabilita + muestra estado ("Exportando…"), llama a `exportModelToGLB`, dispara la
  descarga (`URL.createObjectURL`), y rehabilita. Vanilla DOM, sin frameworks.

### Los tres problemas que el módulo resuelve

1. **`userData` circular.** Los modelos generados guardan en `userData.sculptRuntime`
   referencias a `Object3D` (nodes/sockets/meshes/destructionGroups). `GLTFExporter`
   serializa `userData` a `extras` y revienta por referencias circulares. Solución:
   exportar un **clon saneado** que vacía `userData` en cada hijo (o deja un subconjunto
   JSON-safe si `includeSculptUserData`). Los **nombres** viven en `.name` del objeto, así
   que la jerarquía nombrada (`blade`, `guard`, `grip`, `pommel`…) sobrevive para Blender.
2. **Transform animado.** El visor gira el modelo en el bucle de animación. Con
   `resetRootTransform` el clon se exporta en pose canónica, sin hornear el giro.
3. **Solo el modelo, no las luces.** Se exporta el `THREE.Group` del modelo, no las luces
   de escena del visor (Blender ilumina por su cuenta).

### Materiales

Todo es PBR estándar + `CanvasTexture`; `GLTFExporter` los exporta enteros: clearcoat
(`KHR_materials_clearcoat`), emisivo, transmission (`KHR_materials_transmission`), y hornea
las texturas canvas como PNG en el `.glb`.

## Integración como capacidad nativa

- **`grimoire/export/glb_export.md`** (nuevo): documenta la capacidad y la integración de
  una línea en cualquier visor:
  ```js
  import { attachExportButton } from '../export/glbExporter.js';
  attachExportButton(root, { filename: 'master-sword' });
  ```
- **`SKILL.md`** (edit): añadir el export como paso del flujo de salida, para que cada visor
  generado incluya el botón por defecto.
- **`README.md` / `CHANGELOG.md` / `ROADMAP.md`** (edit): documentar la feature para un PR limpio.

## Verificación (fuera del PR)

Cablear `attachExportButton` en `master-sword/index.html` (fuera del repo, es proyecto del
usuario) y verificar con playwright:

1. Abrir el visor, pulsar "Exportar .glb", confirmar la descarga.
2. Validar que el `.glb` es glTF binario correcto: cabecera mágica `glTF`, versión 2, y
   chunk JSON parseable con `meshes`/`materials`/`nodes`.

El import real en Blender queda del lado del usuario.

## Alcance del PR (rama `feat/glb-export`)

Ficheros del repo:

- `export/glbExporter.js` (nuevo)
- `grimoire/export/glb_export.md` (nuevo)
- `SKILL.md` (edit)
- `README.md` (edit)
- `CHANGELOG.md` (edit)
- `ROADMAP.md` (edit)
- `PR_BODY.md` (preparado; artefacto de PR)

No se hace push ni fork. `master-sword` es demo local y no entra en el PR. El documento de
diseño y `PR_BODY.md` son artefactos de planificación locales, no destinados a subirse
upstream salvo que el usuario lo decida.

## Fuera de alcance (YAGNI)

- CLI headless de exportación en Node.
- Editor/ajuste de materiales.
- Formatos de exportación distintos de GLB (OBJ, FBX, USD…).
