export type BackendErrorPayload = {
  code?: string;
  message: string;
  status?: number;
};

export class BackendError extends Error {
  readonly status?: number;
  readonly code?: string;

  constructor(message: string, options: { status?: number; code?: string } = {}) {
    super(message);
    this.name = 'BackendError';
    this.status = options.status;
    this.code = options.code;
  }
}

const STATUS_MESSAGES: Record<number, string> = {
  400: 'La solicitud no es válida.',
  401: 'La sesión no es válida. Inicia sesión de nuevo.',
  403: 'No tienes permisos para realizar esta acción.',
  404: 'No se encontró el recurso solicitado.',
  409: 'La solicitud entra en conflicto con el estado actual.',
  413: 'El contenido supera el límite permitido.',
  422: 'Revisa los datos enviados.',
  500: 'La API no pudo completar la solicitud.',
  502: 'El servicio externo no pudo responder.',
  503: 'El backend no está disponible en este momento.',
};

const CODE_MESSAGES: Record<string, string> = {
  RAG_EMPTY_UPLOAD: 'Selecciona un documento antes de enviarlo.',
  RAG_UPLOAD_TOO_LARGE: 'El documento supera el límite permitido.',
  RAG_UNAVAILABLE: 'El servicio de conocimiento no está disponible.',
};

const DETAIL_MESSAGES: Record<string, string> = {
  'Invalid email or password.': 'Correo o contraseña inválidos.',
  'Invalid authentication credentials.': 'La sesión no es válida. Inicia sesión de nuevo.',
  'Authentication required.': 'Inicia sesión para continuar.',
  'Authentication service unavailable.': 'El servicio de autenticación no está disponible.',
  'Insufficient permissions.': 'No tienes permisos para realizar esta acción.',
  'Conversation not found.': 'No se encontró la conversación.',
  'Conversation service unavailable.': 'El servicio de conversaciones no está disponible.',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function clean(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const result = value.trim();
  return result || undefined;
}

function collectMessages(value: unknown): string[] {
  const direct = clean(value);
  if (direct) return [direct];
  if (Array.isArray(value)) return value.flatMap(collectMessages);
  if (!isRecord(value)) return [];

  for (const key of ['message', 'msg', 'detail', 'error']) {
    const messages = collectMessages(value[key]);
    if (messages.length) return messages;
  }
  return [];
}

function translateMessage(message: string, status?: number, code?: string): string {
  if (code && CODE_MESSAGES[code]) return CODE_MESSAGES[code];
  if (CODE_MESSAGES[message]) return CODE_MESSAGES[message];
  if (DETAIL_MESSAGES[message]) return DETAIL_MESSAGES[message];
  if (/^[A-Z][A-Z0-9_.-]+$/.test(message)) return status ? STATUS_MESSAGES[status] ?? message : message;
  return message;
}

export function normalizeBackendPayload(
  payload: unknown,
  fallback: string,
  status?: number,
): BackendErrorPayload {
  const record = isRecord(payload) ? payload : undefined;
  const code = clean(record?.code) ?? clean(record?.error_code);
  const candidates = record ? [record.detail, record.message, record.error] : [payload];
  const messages = candidates.map(collectMessages).find((items) => items.length > 0) ?? [];
  const message = code && CODE_MESSAGES[code]
    ? CODE_MESSAGES[code]
    : messages.length
    ? messages.map((item) => translateMessage(item, status, code)).join(' ')
    : STATUS_MESSAGES[status ?? 0] ?? fallback;
  return { message, ...(status === undefined ? {} : { status }), ...(code ? { code } : {}) };
}

export async function backendErrorFromResponse(response: Response, fallback: string): Promise<BackendError> {
  let payload: unknown;
  try {
    const body = await response.text();
    if (body.trim()) {
      try {
        payload = JSON.parse(body) as unknown;
      } catch {
        payload = body;
      }
    }
  } catch {
    payload = undefined;
  }
  const normalized = normalizeBackendPayload(payload, fallback, response.status);
  return new BackendError(normalized.message, normalized);
}

export function backendMessage(payload: unknown, fallback: string): string {
  return normalizeBackendPayload(payload, fallback).message;
}

export function errorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback;
  if (/failed to fetch|networkerror|load failed/i.test(error.message)) return fallback;
  return error.message.trim() || fallback;
}
