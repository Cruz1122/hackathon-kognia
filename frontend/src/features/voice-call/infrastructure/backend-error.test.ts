import { strict as assert } from 'node:assert';
import test from 'node:test';
import {
  backendErrorFromResponse,
  backendMessage,
  errorMessage,
  normalizeBackendPayload,
} from './backend-error.ts';

test('normalizes FastAPI detail messages and translates known auth errors', async () => {
  const error = await backendErrorFromResponse(
    new Response(JSON.stringify({ detail: 'Invalid email or password.' }), { status: 401 }),
    'No se pudo iniciar sesión.',
  );

  assert.equal(error.name, 'BackendError');
  assert.equal(error.status, 401);
  assert.equal(error.message, 'Correo o contraseña inválidos.');
});

test('normalizes validation arrays without exposing raw HTTP status text', () => {
  const result = normalizeBackendPayload(
    { detail: [{ loc: ['body', 'prompt'], msg: 'Field required' }, { msg: 'Value is too short' }] },
    'Solicitud inválida.',
    422,
  );

  assert.equal(result.message, 'Field required Value is too short');
  assert.equal(result.status, 422);
});

test('uses status fallback for machine-readable backend codes', () => {
  assert.equal(
    backendMessage({ detail: 'RAG_UPLOAD_TOO_LARGE' }, 'No se pudo subir el documento.'),
    'El documento supera el límite permitido.',
  );
  assert.equal(
    backendMessage({ code: 'RAG_EMPTY_UPLOAD' }, 'No se pudo subir el documento.'),
    'Selecciona un documento antes de enviarlo.',
  );
  assert.equal(
    backendMessage(undefined, 'No se pudo subir el documento.'),
    'No se pudo subir el documento.',
  );
});

test('replaces browser network errors with the contextual fallback', () => {
  assert.equal(errorMessage(new TypeError('Failed to fetch'), 'El backend no está disponible.'), 'El backend no está disponible.');
});
