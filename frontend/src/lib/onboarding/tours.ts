import type { DriveStep } from 'driver.js';

export type TourId = 'dashboard' | 'calls' | 'dev' | 'specifications' | 'call' | 'replay';

function isVisible(node: HTMLElement): boolean {
  if (node.hidden || node.closest('[hidden]')) return false;
  if (node.closest('#authPanel .section-view:not(.is-active)')) return false;
  let current: HTMLElement | null = node;
  while (current && current !== document.documentElement) {
    const style = getComputedStyle(current);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    current = current.parentElement;
  }
  const rect = node.getBoundingClientRect();
  return rect.width >= 2 && rect.height >= 2;
}

function anchor(name: string): () => Element {
  return () => {
    const node = document.querySelector(`[data-tour="${name}"]`);
    if (!(node instanceof HTMLElement) || !isVisible(node)) return undefined as unknown as Element;
    return node;
  };
}

function explain(title: string, description: string, side?: 'top' | 'right' | 'bottom' | 'left'): DriveStep['popover'] {
  return { title, description, side };
}

function pointed(name: string, title: string, description: string, side?: 'top' | 'right' | 'bottom' | 'left'): DriveStep {
  return { element: anchor(name), popover: explain(title, description, side) };
}

const dashboardSteps: DriveStep[] = [
  pointed('dashboard-heading', 'Rendimiento comercial', 'Este tablero resume el periodo que elijas. El recorrido solo explica la pantalla; no cambia fechas ni datos.', 'bottom'),
  pointed('dashboard-filters', 'Periodo', 'Desde y Hasta acotan la consulta. Actualizar vuelve a pedir las métricas. El recorrido no pulsa ese botón.', 'bottom'),
  pointed('dashboard-kpis', 'Indicadores', 'Revenue generado, ventas ganadas, tasa de conversión y revenue recuperado, con la variación frente al periodo anterior.', 'bottom'),
  pointed('dashboard-secondary', 'Volumen y recuperación', 'Conversaciones, ventas recuperadas y la tasa de recuperación del mismo periodo.', 'bottom'),
  pointed('dashboard-costs', 'Costo estimado', 'Gasto del periodo calculado con telemetría real: tokens del modelo (OpenAI/Gemini), tokens de entrada de Jev (US$0,042 por millón; la salida es gratis) y caracteres hablados con ElevenLabs (US$22 por 121k). La transcripción corre en local y no cuesta.', 'bottom'),
  pointed('dashboard-signals', 'Señales de las llamadas', 'Cómo se sienten, avanzan y confían las conversaciones. Solo aparece cuando hay llamadas con señales.', 'top'),
  pointed('dashboard-charts', 'Gráficos', 'Conversión, recuperación, ruta comercial, pérdidas, objeciones y productos, con lo que devolvió el servidor.', 'top'),
  pointed('app-header', 'Menú', 'Demo identifica la sesión. Desde aquí se abre Dashboard, Llamadas, Modo dev y Especificaciones. Entrar a Llamadas no inicia una llamada.', 'bottom'),
];

const callsSteps: DriveStep[] = [
  {
    popover: explain(
      'Listado de llamadas',
      'Esta pantalla muestra las llamadas ya registradas. El recorrido no abre ni inicia ninguna.',
    ),
  },
  pointed('calls-heading', 'Llamadas', 'El título de esta vista. Desde aquí se consulta el historial, no el tablero de métricas.', 'bottom'),
  pointed('calls-filters', 'Buscar y filtrar', 'Puedes buscar por cliente o teléfono y filtrar por estado: todas, en vivo, finalizadas o fallidas.', 'bottom'),
  pointed('calls-table', 'Resultados', 'Cada fila muestra cliente, teléfono, inicio, duración y estado. Ver abre esa llamada cuando eliges una fila.', 'top'),
  pointed('calls-empty', 'Sin llamadas', 'Si no hay resultados para la búsqueda o el filtro, el listado lo indica aquí.', 'top'),
  pointed('calls-error', 'No se pudo cargar', 'Si el listado falla, este aviso lo indica. Reintentar vuelve a pedirlo; el recorrido no lo pulsa.', 'top'),
  {
    popover: explain(
      'Abrir una llamada es aparte',
      'Cuando quieras ver una conversación, elige su fila. El marco Recorrido vuelve a abrir estas tarjetas.',
    ),
  },
];

const devSteps: DriveStep[] = [
  {
    popover: explain(
      'Catálogo técnico',
      'Modo dev lista llamadas para inspección técnica. No es el tablero comercial y este recorrido no abre un replay.',
    ),
  },
  pointed('calls-heading', 'Llamadas en modo dev', 'Misma forma de listado, con el acento visual de desarrollo. Sirve para revisar llamadas finalizadas o fallidas.', 'bottom'),
  pointed('calls-filters', 'Buscar y filtrar', 'Puedes buscar por cliente o teléfono y dejar el estado en todas, finalizadas o fallidas.', 'bottom'),
  pointed('calls-table', 'Resultados técnicos', 'Cada fila se puede abrir para ver el replay técnico de esa llamada. El recorrido no entra solo.', 'top'),
  pointed('calls-empty', 'Sin llamadas', 'Si el filtro no encuentra llamadas, el catálogo lo indica aquí.', 'top'),
  pointed('calls-error', 'No se pudo cargar', 'Si el catálogo falla, este aviso lo indica. Reintentar vuelve a pedirlo; el recorrido no lo pulsa.', 'top'),
  {
    popover: explain(
      'El replay queda para después',
      'Abre una fila cuando quieras inspeccionarla. El marco Recorrido vuelve a abrir estas tarjetas.',
    ),
  },
];

const specificationsSteps: DriveStep[] = [
  pointed(
    'specifications-panel',
    'Especificaciones técnicas',
    'Esta pantalla está reservada para fichas técnicas. Todavía no hay especificaciones publicadas.',
    'bottom',
  ),
];

const callSteps: DriveStep[] = [
  {
    popover: explain(
      'Llamada en el navegador',
      'Esta vista muestra audio, transcripción y señales de una llamada real. El recorrido no marca, no enciende el micrófono ni abre la sesión.',
    ),
  },
  pointed('waveform', 'Actividad de audio', 'La onda muestra la señal de la llamada. Una forma no indica, por sí sola, la calidad de la respuesta.', 'bottom'),
  pointed('call-control', 'Empezar la llamada', 'Llamar inicia la sesión de voz con tu permiso. Reiniciar y Pausa están al lado. Este paso no pulsa ninguno.', 'top'),
  pointed('connection-status', 'Estado de la conexión', 'Indica si la sesión de audio está conectando, activa o con un problema. El recorrido no abre el canal.', 'bottom'),
  pointed('conversation', 'Transcripción', 'Aquí aparecen los mensajes de la conversación a medida que el reconocimiento y el agente producen texto.', 'top'),
  pointed('client-panel', 'Cliente y señales', 'El panel lateral concentra el estado del cliente y las señales de la llamada cuando esa información está disponible.', 'left'),
  {
    popover: explain(
      'La llamada sigue siendo tuya',
      'Cierra el recorrido para usar los controles. El marco Recorrido lo repite sin modificar la sesión.',
    ),
  },
];

const replaySteps: DriveStep[] = [
  {
    popover: explain(
      'Replay técnico',
      'Esta vista reproduce lo que ya ocurrió en una llamada. No inicia una sesión nueva ni cambia los eventos.',
    ),
  },
  pointed('waveform', 'Playhead de audio', 'La onda y la posición muestran el audio grabado. Moverla no forma parte de este recorrido.', 'bottom'),
  pointed('replay-summary', 'Resumen técnico', 'Resume la llamada y recuerda que los eventos siguen la posición del audio.', 'bottom'),
  pointed('conversation', 'Transcripción', 'Los mensajes corresponden a lo ocurrido en esa llamada, alineados con el avance del replay.', 'top'),
  pointed('client-panel', 'Cliente y señales', 'El panel muestra el estado del cliente y las señales asociadas a esta repetición, si existen.', 'left'),
  {
    popover: explain(
      'Puedes recorrer el audio',
      'Al cerrar, el replay sigue donde estaba. El marco Recorrido vuelve a explicar la pantalla.',
    ),
  },
];

export const TOUR_STEPS: Record<TourId, DriveStep[]> = {
  dashboard: dashboardSteps,
  calls: callsSteps,
  dev: devSteps,
  specifications: specificationsSteps,
  call: callSteps,
  replay: replaySteps,
};

const TOUR_IDS = new Set<string>(Object.keys(TOUR_STEPS));

export function isTourId(value: string): value is TourId {
  return TOUR_IDS.has(value);
}

export function tourHasVisibleAnchor(id: TourId): boolean {
  return TOUR_STEPS[id].some((step) => {
    if (!step.element) return false;
    if (typeof step.element === 'function') return Boolean(step.element());
    if (typeof step.element === 'string') return Boolean(document.querySelector(step.element));
    return true;
  });
}
