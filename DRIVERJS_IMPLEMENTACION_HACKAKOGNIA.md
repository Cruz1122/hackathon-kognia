# Implementación de Driver.js para recorridos guiados — HackaKognia

> **Tipo de documento:** Especificación de implementación para el frontend y para un agente de desarrollo.
>
> **Objetivo:** Incorporar recorridos guiados que permitan a una persona **entender qué hace el software, cómo se usa y qué está ocurriendo en tiempo real**, sin alterar el comportamiento del agente de voz ni convertir el onboarding en una demostración ficticia.
>
> **Alcance:** Frontend Astro 7 + TypeScript. No requiere cambios en FastAPI ni nuevas APIs.

## 1. Contexto y alcance real

HackaKognia es una plataforma modular para una hackatón, actualmente centrada en un agente de voz local y su monitor. El sistema ya cuenta con flujo de audio por WebSocket, reconocimiento de voz (Sherpa-ONNX), generación de voz (Piper), transcripciones parciales y finales, tokens del agente, ejecución de herramientas, interrupción de respuesta (*barge-in*) y visualizaciones de audio. Tiene proveedores LLM intercambiables.

**No se debe afirmar ni representar como disponible** telefonía PSTN/Telnyx, grabaciones persistidas, replay histórico, analítica comercial operativa, Sales Space, PostgreSQL o continuidad real por WhatsApp mientras dichas capacidades no estén implementadas y verificadas en el repositorio. Pueden figurar como ideas futuras, pero no como funcionalidades utilizables.

Driver.js sirve para **guiar la atención del usuario en el DOM**, no para simular un motor de voz, una llamada telefónica ni una integración backend. La demostración de comportamiento real debe basarse en eventos y componentes ya existentes.

### Resultado esperado

- Botón visible **«Ver recorrido»** o **«¿Cómo funciona?»** en cada pantalla compatible.
- Recorrido general que explique el flujo micrófono → WebSocket → STT → LLM + herramientas → TTS → reproducción en el navegador.
- Recorrido del monitor que señale visualización de audio, transcripción del usuario, actividad/respuesta del agente y ejecución de herramientas cuando sus componentes existan en pantalla.
- Explicaciones **contextuales**, cortas, relacionadas con la función real y escritas en español.
- Repetición manual del recorrido en cualquier momento.
- Ninguna interacción irreversible iniciada automáticamente por el tutorial.
- Estilos propios y compatibles con el sistema visual del proyecto.

## 2. Principios de implementación

1. **Solo frontend:** Driver.js debe vivir en el cliente; no importar la librería durante SSR ni crear endpoints para el tutorial.
2. **Un recorrido por pantalla o flujo:** Definir tours desacoplados del dominio. El motor debe poder reutilizarse después de la hackatón.
3. **Anclas semánticas estables:** Usar atributos `data-tour` en vez de clases generadas por CSS, posiciones o estructuras frágiles del DOM.
4. **Estado real:** Los popovers explican eventos auténticos. No realizar llamadas, ejecutar herramientas, crear ventas ni cambiar datos solo para avanzar un paso.
5. **Accesible y cancelable:** Permitir salir con Escape y con un control visible; respetar teclado, foco y reducción de movimiento.
6. **Reactividad segura:** Si un elemento desaparece por estado, permisos o diseño responsive, omitirlo o esperarlo durante un tiempo acotado; nunca bloquear indefinidamente.
7. **Demo primero:** Implementar un recorrido del monitor antes de construir una plataforma compleja de onboarding.

## 3. Instalación

Desde el directorio que contiene el `package.json` del frontend Astro, ejecutar:

```bash
pnpm add driver.js
```

**Qué hace:** agrega Driver.js como dependencia de producción al paquete del frontend y actualiza su manifiesto y archivo de bloqueo. Si el frontend forma parte de un monorepo, usar el directorio/filtro del paquete correcto; no instalarla en FastAPI ni en un paquete de backend. No usar CDN si la aplicación ya utiliza Vite.

Usar los imports oficiales:

```ts
import { driver } from "driver.js";
import type { DriveStep } from "driver.js";
import "driver.js/dist/driver.css";
```

Evitar instalar paquetes de tipos adicionales: Driver.js ya distribuye tipos TypeScript.

**Documentación oficial:**

- Instalación: https://driverjs.com/docs/installation
- Guía básica: https://driverjs.com/docs/basic-usage
- Configuración y callbacks: https://driverjs.com/docs/configuration
- Referencia de API: https://driverjs.com/docs/api
- Personalización visual: https://driverjs.com/docs/theming
- Recorridos asíncronos: https://driverjs.com/docs/async-tour
- Ciclo de navegación de Astro: https://docs.astro.build/en/guides/view-transitions/

> Verificar que la versión instalada soporte las opciones empleadas en esta especificación (`skipMissingElement`, `waitForElement`, `onDoneClick`). La API detallada aquí corresponde a la documentación oficial consultada el 9 de octubre de 2026; mantener una versión fijada mediante el lockfile.

## 4. Estructura recomendada

Adaptar estos paths a la **estructura real del repositorio**, sin duplicar layouts ni crear paquetes nuevos innecesariamente:

```text
src/
├── components/
│   └── onboarding/
│       └── TourButton.astro
├── lib/
│   └── onboarding/
│       ├── tours.ts          # Pasos, textos y nombres de recorridos
│       └── index.ts          # Lifecycle, instancia, delegación y persistencia
└── styles/
    └── driver-tour.css     # Overrides globales para la UI del popover
```

El layout principal debe importar el pequeño script de inicialización una sola vez. Si el frontend ya cuenta con un patrón de servicios, controladores o componentes equivalente, reutilizarlo.

## 5. Contrato de anclas HTML

Añadir `data-tour` **al nodo estable que se desea resaltar**. No cambiar la estructura de audio, WebSockets, estados ni callbacks existentes.

Ejemplos de integración en componentes ya existentes:

```astro
<!-- Los ejemplos ilustran dónde poner atributos: adaptar a los componentes reales. -->
<section data-tour="voice-session">...</section>
<button data-tour="microphone-control" type="button">...</button>
<div data-tour="connection-status">...</div>
<div data-tour="waveform">...</div>
<section data-tour="customer-transcript">...</section>
<section data-tour="agent-response">...</section>
<section data-tour="tool-events">...</section>
<div data-tour="barge-in-status">...</div>
```

**No crear elementos vacíos solo para satisfacer el tutorial.** Si la funcionalidad usa otro nombre o no tiene nodo, identificar el componente real y colocar allí el atributo. Las anclas de transcripciones y herramientas deben existir de forma estable incluso cuando su contenido esté vacío, si ya existe ese contenedor en la interfaz.

Se recomienda identificar cada área de la aplicación con `data-tour-scope="monitor"` o `data-tour-scope="voice"` en su contenedor principal, y habilitar únicamente los recorridos propios de esa pantalla.

## 6. Definición de recorridos

Crear `src/lib/onboarding/tours.ts`:

```ts
import type { DriveStep } from "driver.js";

export type TourId = "voice" | "monitor";

const voiceSteps: DriveStep[] = [
  {
    popover: {
      title: "Cómo funciona el agente de voz",
      description:
        "Conoce el recorrido de tu audio: navegador, servidor, transcripción, razonamiento y respuesta hablada.",
    },
  },
  {
    element: '[data-tour="microphone-control"]',
    popover: {
      title: "Entrada de voz",
      description:
        "El micrófono captura audio con tu permiso. El navegador envía los fragmentos por WebSocket al backend.",
      side: "bottom",
    },
  },
  {
    element: '[data-tour="connection-status"]',
    popover: {
      title: "Estado de la sesión",
      description:
        "Aquí puedes verificar el estado de la conexión. El tutorial no abre una sesión ni activa el micrófono por sí mismo.",
    },
  },
  {
    element: '[data-tour="customer-transcript"]',
    popover: {
      title: "Reconocimiento de voz",
      description:
        "Sherpa-ONNX transforma audio en texto. Las transcripciones parciales pueden actualizarse antes de que aparezca una versión final.",
    },
  },
  {
    element: '[data-tour="agent-response"]',
    popover: {
      title: "Respuesta del agente",
      description:
        "El modelo genera contenido y, según el caso, puede invocar herramientas. Piper sintetiza la voz que se transmite al navegador.",
    },
  },
  {
    popover: {
      title: "Prueba el flujo",
      description:
        "Cierra el recorrido y comienza una sesión cuando quieras. Observa cómo cambian los indicadores mientras interactúas realmente con el agente.",
    },
  },
];

const monitorSteps: DriveStep[] = [
  {
    popover: {
      title: "Monitor en tiempo real",
      description:
        "Esta pantalla expone señales y eventos de una sesión activa del agente de voz. Los datos que ves deben proceder del sistema real.",
    },
  },
  {
    element: '[data-tour="voice-session"]',
    popover: {
      title: "Sesión",
      description:
        "Este bloque concentra el estado de la sesión. No confundas una sesión de audio en navegador con una llamada telefónica PSTN.",
    },
  },
  {
    element: '[data-tour="waveform"]',
    popover: {
      title: "Actividad acústica",
      description:
        "La onda o el espectrograma muestran cambios de la señal de audio. Una forma de onda no permite inferir, por sí sola, intención ni calidad de respuesta.",
    },
  },
  {
    element: '[data-tour="customer-transcript"]',
    popover: {
      title: "Lo que dice el usuario",
      description:
        "Observa texto parcial y final del reconocimiento. La transcripción puede cambiar a medida que se procesa más audio.",
    },
  },
  {
    element: '[data-tour="agent-response"]',
    popover: {
      title: "Lo que produce el agente",
      description:
        "El monitor muestra la salida generada por el modelo y su evolución, si este componente está disponible en la pantalla actual.",
    },
  },
  {
    element: '[data-tour="tool-events"]',
    popover: {
      title: "Herramientas y acciones",
      description:
        "Cuando el agente invoca una herramienta, aquí puede verse el evento correspondiente. Ver un evento no garantiza que la operación de negocio haya concluido correctamente.",
    },
  },
  {
    element: '[data-tour="barge-in-status"]',
    popover: {
      title: "Interrupciones de voz",
      description:
        "El sistema admite interrupciones del usuario mientras responde el agente. Su comportamiento depende de la sesión y el estado del audio.",
    },
  },
  {
    popover: {
      title: "Explora con una sesión real",
      description:
        "El recorrido ha terminado. Puedes repetirlo desde Ayuda sin detener el monitor ni modificar los eventos de la sesión.",
    },
  },
];

export const TOUR_STEPS: Record<TourId, DriveStep[]> = {
  voice: voiceSteps,
  monitor: monitorSteps,
};

export function isTourId(value: string): value is TourId {
  return Object.prototype.hasOwnProperty.call(TOUR_STEPS, value);
}
```

**Importante:** La definición de `monitor` está pensada para los elementos del monitor real. No se deben agregar anclas o pasos referidos a telephony, analytics ni sales hasta validar que la pantalla y los procesos estén operativos.

## 7. Motor de recorridos y ciclo de vida

Crear `src/lib/onboarding/index.ts`:

```ts
import { driver } from "driver.js";
import "driver.js/dist/driver.css";
import "../../styles/driver-tour.css";
import { TOUR_STEPS, isTourId, type TourId } from "./tours";

type DriverInstance = ReturnType<typeof driver>;

let currentTour: DriverInstance | null = null;
let listenersInstalled = false;

const TOUR_VERSION = "1";

function completionKey(id: TourId): string {
  return `hackakognia:onboarding:${id}:v${TOUR_VERSION}`;
}

function markCompleted(id: TourId): void {
  try {
    localStorage.setItem(completionKey(id), "completed");
  } catch {
    // El tutorial sigue funcionando aunque el navegador bloquee storage.
  }
}

export function wasCompleted(id: TourId): boolean {
  try {
    return localStorage.getItem(completionKey(id)) === "completed";
  } catch {
    return false;
  }
}

export function stopTour(): void {
  currentTour?.destroy();
  currentTour = null;
}

export function startTour(id: TourId): void {
  stopTour();

  const steps = TOUR_STEPS[id];

  // Evita abrir una introducción genérica cuando ninguna ancla existe.
  const hasRealAnchor = steps.some(
    (step) =>
      typeof step.element === "string" &&
      document.querySelector(step.element) !== null,
  );

  if (!hasRealAnchor) {
    console.warn(`[onboarding] Sin anclas visibles para ${id}`);
    return;
  }

  const prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  const instance = driver({
    steps,
    popoverClass: "hackakognia-tour",
    overlayColor: "#414141",
    overlayOpacity: 0.68,
    overlayClickBehavior: "none",
    showProgress: true,
    progressText: "{{current}} de {{total}}",
    nextBtnText: "Siguiente",
    prevBtnText: "Anterior",
    doneBtnText: "Finalizar",
    closeBtnLabel: "Cerrar recorrido",
    allowClose: true,
    allowKeyboardControl: true,
    disableActiveInteraction: true,
    smoothScroll: !prefersReducedMotion,
    animate: !prefersReducedMotion,
    stagePadding: 10,
    stageRadius: 9999,
    skipMissingElement: true,
    waitForElement: 400,
    onDoneClick: (_element, _step, { driver: active }) => {
      // Solo finalizar marca como completado; cerrar no lo hace.
      markCompleted(id);
      active.destroy();
    },
    onDestroyed: (_element, _step, { driver: destroyed }) => {
      if (currentTour === destroyed) {
        currentTour = null;
      }
    },
  });

  currentTour = instance;
  instance.drive();
}

export function installTourHandlers(): void {
  if (listenersInstalled) return;
  listenersInstalled = true;

  // Delegación: los botones pueden reemplazarse por renderizados de Astro.
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;

    const button = event.target.closest<HTMLElement>("[data-tour-start]");
    if (!button) return;

    const id = button.dataset.tourStart;
    if (!id || !isTourId(id)) return;

    startTour(id);
  });

  // Si Astro utiliza ClientRouter, cerrar antes de intercambiar el DOM.
  document.addEventListener("astro:before-swap", stopTour);
}
```

### Justificación de decisiones

- `disableActiveInteraction: true` impide que el usuario active accidentalmente el micrófono, una herramienta o cualquier acción mientras el paso está resaltado. Se pueden permitir interacciones explícitas en futuros tours específicos.
- `skipMissingElement: true` omite controles inexistentes en vez de presentar una explicación flotante asociada a un elemento ausente. `waitForElement` es una espera **limitada**, no un `setTimeout` arbitrario para iniciar todo el recorrido.
- `onDoneClick` registra la finalización; `onDestroyed` solo limpia el estado de la instancia. No confundir terminar con cerrar.
- `stopTour()` se dispara antes de que Astro cambie el DOM si está habilitada la navegación cliente. Si se usa navegación convencional, la descarga de la página elimina la interfaz anterior.
- `localStorage` guarda únicamente una bandera local de completitud. No guardar transcripciones, audio, IDs de clientes ni datos operativos en el estado del onboarding.
- `stageRadius: 9999` armoniza el recorte con los contenedores completamente redondeados del sistema de diseño; comprobar visualmente componentes muy extensos y ajustar únicamente si el spotlight queda ilegible.

> No añadir `onNextClick` o `onPrevClick` globales sin necesidad: **cuando se sobrescriben, el avance automático deja de producirse** y hay que llamar manualmente a `moveNext()`/`movePrevious()`.

## 8. Botón de ayuda e inicialización con Astro

Crear `src/components/onboarding/TourButton.astro`:

```astro
---
import type { TourId } from "../../lib/onboarding/tours";

interface Props {
  tour: TourId;
  label?: string;
}

const { tour, label = "¿Cómo funciona?" } = Astro.props;
---

<button
  type="button"
  class="tour-launcher"
  data-tour-start={tour}
  aria-label={`Iniciar recorrido: ${label}`}
>
  {label}
</button>
```

Agregar el archivo de arranque a **un layout persistente o global** (no repetir en cada tarjeta). Ejemplo de script de Astro:

```astro
<script>
  import { installTourHandlers } from "../lib/onboarding";
  installTourHandlers();
</script>
```

Colocar el import desde la ruta relativa correcta del layout real. Este script se ejecuta en el navegador; no usar `is:inline` para código que importe módulos TypeScript.

Ejemplo de uso dentro de la página real del monitor:

```astro
---
import TourButton from "../components/onboarding/TourButton.astro";
---

<TourButton tour="monitor" label="Conocer el monitor" />
```

En la pantalla principal del agente usar `tour="voice"`. Mantener un botón de ayuda accesible desde la navegación o cabecera cuando las condiciones de la pantalla permitan iniciar ese recorrido.

### Con Astro ClientRouter

- La inicialización con delegación se instala una sola vez.
- `astro:before-swap` cierra el tour cuando una navegación va a reemplazar su DOM.
- Si en el futuro se requiere iniciar automáticamente un recorrido después de navegar, usar el evento `astro:page-load`, no asumir que `DOMContentLoaded` se dispara otra vez.
- No permitir que un tour continúe a otra página hasta implementar deliberadamente un flujo multipágina con estado propio.

## 9. Estilos y compatibilidad con el sistema visual

**Restricciones del proyecto** (obligatorias para todos los componentes introducidos):

- Paleta: `#414141` (grafito), `#f7c974` (ámbar), `#faeccf` (crema), `#f8f8f8` (papel). Solo variantes alfa de estos colores.
- Tipografía Urbanist.
- Componentes acotados con geometría full-circle / pill (`border-radius: 9999px`).
- Movimiento suave; sin partículas ni animaciones innecesarias.
- Material gooey con filtro SVG compartido y `stdDeviation="7"` para las **masas visuales** que lo necesiten, nunca sobre los textos.
- Foco visible, contraste suficiente y `prefers-reduced-motion`.

Driver.js tiene estilos propios: **no dejar su apariencia predeterminada**. Como los popovers se insertan fuera del árbol local del componente, los overrides deben ser CSS **global**, no estilos Astro aislados que no coincidan con el elemento.

Crear `src/styles/driver-tour.css`:

```css
/* La fuente Urbanist debe estar cargada por el frontend existente. */
.driver-popover.hackakognia-tour {
  --driver-popover-font-family: "Urbanist", sans-serif;
  box-sizing: border-box;
  width: min(360px, calc(100vw - 28px));
  max-width: calc(100vw - 28px);
  padding: 30px 30px 25px;
  border: 1px solid rgb(65 65 65 / 12%);
  border-radius: 9999px;
  background: #faeccf;
  color: #414141;
  box-shadow: 0 12px 36px rgb(65 65 65 / 12%);
  text-align: center;
}

.driver-popover.hackakognia-tour .driver-popover-arrow {
  display: none;
}

.driver-popover.hackakognia-tour .driver-popover-title {
  color: #414141;
  font-family: "Urbanist", sans-serif;
  font-weight: 800;
  font-size: 17px;
  line-height: 1.2;
}

.driver-popover.hackakognia-tour .driver-popover-description {
  color: #414141;
  font-family: "Urbanist", sans-serif;
  font-size: 14px;
  line-height: 1.45;
}

.driver-popover.hackakognia-tour .driver-popover-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 15px;
}

.driver-popover.hackakognia-tour .driver-popover-progress-text {
  color: #414141;
  font-family: "Urbanist", sans-serif;
}

.driver-popover.hackakognia-tour .driver-popover-footer-btn {
  border: 0;
  border-radius: 9999px;
  padding: 8px 14px;
  background: #414141;
  color: #f8f8f8;
  font-family: "Urbanist", sans-serif;
  font-weight: 700;
  text-shadow: none;
}

.driver-popover.hackakognia-tour .driver-popover-next-btn {
  background: #f7c974;
  color: #414141;
}

.driver-popover.hackakognia-tour .driver-popover-close-btn {
  color: #414141;
  border-radius: 9999px;
  font-family: "Urbanist", sans-serif;
}

.driver-popover.hackakognia-tour button:focus-visible,
.tour-launcher:focus-visible {
  outline: 3px solid #f7c974;
  outline-offset: 3px;
}

.tour-launcher {
  padding: 10px 18px;
  border: 0;
  border-radius: 9999px;
  font: 700 14px "Urbanist", sans-serif;
  color: #414141;
  background: #f7c974;
  cursor: pointer;
  transition: transform 180ms ease, opacity 180ms ease;
}

.tour-launcher:active {
  transform: scale(0.98);
}

@media (prefers-reduced-motion: reduce) {
  .tour-launcher {
    transition: none;
  }
}
```

**Nota de diseño:** Un popover completamente pill con mucho texto puede desaprovechar el espacio horizontal y provocar desbordamientos. Las explicaciones deben ser breves y la maquetación debe probarse a 320 px de ancho, 200 % de zoom y con textos largos. No sacrificar legibilidad por estética: si un caso concreto desborda, reducir contenido o dividir el paso antes de aceptar un componente roto.

**Gooey:** El popup de Driver.js no necesita aplicar el filtro SVG a todo su contenido: deformaría la tipografía. Si se desean acentos animados, deben ser una capa visual independiente que reaproveche el filtro global `#ui-goo` con `stdDeviation="7"`, y mantenerse fuera de los elementos interactivos y el texto. Para esta primera versión, **preferir el popover limpio y sin nuevas animaciones**.

## 10. Mostrar comportamiento, no solo describir botones

Distinguir tres tipos de experiencia:

| Tipo | Propósito | Comportamiento inicial |
| --- | --- | --- |
| **Tour orientativo** | Reconocer interfaz y flujo conceptual. | Solo resalta y explica. Es el alcance principal. |
| **Ayuda contextual** | Resolver una duda puntual sobre un control. | `driver().highlight(...)` o hints opcionales. |
| **Tour interactivo** | Guiar una acción real y reversible. | Fase posterior; requiere condiciones previas, manejo de errores y consentimiento. |

La mejor secuencia para enseñar el comportamiento del software es:

1. Explicar brevemente qué resuelve la pantalla.
2. Resaltar dónde comienza el flujo.
3. Aclarar qué ocurre en backend y qué evidencia visible lo representa.
4. Distinguir **estado pendiente**, **parcial**, **finalizado** y **error** cuando aplique.
5. Permitir al usuario cerrar el recorrido para ejecutar la tarea real.
6. Proporcionar un botón accesible para repetir la explicación.

**No hacer que «Siguiente» invoque el botón de iniciar grabación, dispare WebSockets, haga POSTs, active herramientas, reproduzca audio ni modifique datos.** El recorrido está desacoplado de las acciones y observadores existentes.

### Ejemplo futuro: UI dinámica

Cuando un paso posterior se renderiza solo tras abrir un panel, Driver.js soporta:

```ts
const example: DriveStep[] = [
  {
    element: '[data-tour="open-panel"]',
    advanceOnClick: true,
    disableActiveInteraction: false,
    popover: {
      title: "Abrir detalles",
      description: "Haz clic aquí para abrir un panel informativo.",
      showButtons: ["close"],
    },
  },
  {
    element: '[data-tour="details-panel"]',
    waitForElement: 3000,
    skipMissingElement: true,
    popover: {
      title: "Detalle",
      description: "Este panel se renderiza después de la acción.",
    },
  },
];
```

**Este fragmento es ilustrativo, no se debe integrar en el tour inicial sin un panel real.** `advanceOnClick` permite que el clic ejecute también la acción normal del elemento. Solo habilitarlo para tareas inocuas/reversibles. No emplearlo para iniciar grabaciones o confirmar operaciones de negocio.

## 11. Estado, datos y seguridad de la demo

- No almacenar en `localStorage` eventos, transcripciones, audio, prompts ni información sensible. Solo el indicador de recorrido completado y su versión.
- No usar la finalización del tour como autorización para ejecutar funciones. El backend valida cualquier permiso real.
- No modificar el estado de la sesión de audio al abrir, avanzar, retroceder o cerrar el tutorial.
- Si se abre un tour durante una sesión activa, `disableActiveInteraction` debe impedir interacciones accidentales con la zona resaltada; **el usuario debe poder salir rápidamente** para recuperar el control.
- No generar métricas analíticas de recorrido salvo que ya exista infraestructura autorizada y un caso de uso concreto.
- No mostrar popovers que prometan un resultado diferente del observado. Las herramientas pueden fallar y las transcripciones parciales pueden corregirse.
- Las descripciones se definen como textos estáticos propios; Driver.js acepta HTML en ellas, por lo que **no concatenar contenido arbitrario de usuarios o LLM** en `description` ni `title`.

## 12. Criterios de aceptación

Antes de dar por terminada la integración, comprobar manualmente lo siguiente:

- [ ] Driver.js está instalado en el paquete Astro correcto y el build TypeScript termina sin errores.
- [ ] El botón «¿Cómo funciona?» aparece donde tiene sentido y abre el recorrido apropiado.
- [ ] Los pasos resaltan **componentes reales** mediante `data-tour`; no hay selectores falsos o dependientes de clases efímeras.
- [ ] Los textos corresponden al estado **real** de HackaKognia, sin atribuir telefonía ni analytics inexistentes.
- [ ] Se puede cerrar por Escape y botón visible; también navegar con teclado y volver atrás.
- [ ] Ningún paso enciende el micrófono, abre una sesión, dispara una tool ni altera el WebSocket.
- [ ] Si faltan nodos por permisos, estado de conexión o tamaño de pantalla, el recorrido no queda bloqueado.
- [ ] No se duplican listeners después de navegar entre pantallas Astro.
- [ ] El tour anterior se destruye al cambiar de ruta, cuando se utilice ClientRouter.
- [ ] La finalización queda registrada; cerrar antes de tiempo **no** marca el tour como completado.
- [ ] El popover usa Urbanist, la paleta exacta y geometría full-circle; no aparece el estilo de fábrica.
- [ ] Se ve correctamente en escritorio, móvil, zoom alto y con `prefers-reduced-motion`.
- [ ] Los controles mantienen foco visible y el contenido es legible.
- [ ] Se puede repetir el recorrido manualmente aunque ya figure como completado.

## 13. Orden recomendado de trabajo

**Entrega mínima prioritaria:**

1. Inspeccionar el frontend real e identificar los componentes del agente de voz y del monitor.
2. Instalar Driver.js en el paquete Astro y conectar CSS global.
3. Etiquetar los nodos existentes con `data-tour`.
4. Crear `tours.ts` con el recorrido `monitor`, ajustando textos al DOM y funciones verificadas.
5. Implementar `index.ts` con estado único, salida segura, navegación y guardado de completitud.
6. Integrar `TourButton.astro`, probar el recorrido y ajustar estilos a la guía gooey.
7. Habilitar `voice` solo si la pantalla dispone de sus anclas.
8. Validar manualmente los criterios de aceptación.

**No implementar de entrada:** centro de ayuda complejo, editor de tours, dashboard analítico del onboarding, backend para guardar preferencias, sistema distribuido, tours multipágina o escenarios simulados. Eso no añade valor material a una demo con plazo de hackatón.

## 14. Instrucciones directas para el agente implementador

> Inspecciona primero la estructura y las pantallas existentes del frontend Astro. Implementa Driver.js exclusivamente en cliente usando TypeScript y el lockfile del proyecto. Conserva el diseño `gooey-ui-system`: Urbanist, paleta `#414141`/`#f7c974`/`#faeccf`/`#f8f8f8`, superficies pill y filtro SVG de viscosidad 7 solo si hay masas gooey; no apliques filtros a textos. Añade anclas semánticas `data-tour` a componentes ya existentes del monitor y crea recorridos en español para explicar su comportamiento, con botones para iniciarlos manualmente. No modifiques WebSocket, STT, LLM, TTS, herramientas, base de datos ni lógica de negocio; no crees controles ficticios, endpoints ni funcionalidades aún no implementadas. Usa un servicio singleton, limpia al navegar con Astro ClientRouter, omite elementos ausentes, permite cancelación y guarda únicamente una bandera versionada de recorrido finalizado. Entrega código, lista de archivos modificados y resultados de build y pruebas. No declares que una función está disponible hasta haberla verificado en el código.

---

### Fuentes primarias

- Driver.js — https://driverjs.com/
- Driver.js — instalación: https://driverjs.com/docs/installation
- Driver.js — configuración: https://driverjs.com/docs/configuration
- Driver.js — API: https://driverjs.com/docs/api
- Driver.js — estilos: https://driverjs.com/docs/theming
- Driver.js — async tour: https://driverjs.com/docs/async-tour
- Astro — ClientRouter y eventos de navegación: https://docs.astro.build/en/guides/view-transitions/

### Fuentes del proyecto

- `PROJECT_CONTEXT — HackaKognia.md`: estado real de implementación, stack y restricciones de hackatón.
- `diseño.md`: skill `gooey-ui-system` obligatoria para cambios visuales del frontend.

**Estado de este documento:** Instrucciones y ejemplos de referencia. Los archivos TypeScript y Astro son propuestas de integración; todavía **no** se ha aplicado ningún cambio al repositorio real.
