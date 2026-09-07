# Hackathon Skill

## Objetivo
Entregar una demo funcional con el menor coste de ingeniería razonable.

## Regla de decisión
Prioriza, en este orden: flujo end-to-end funcionando, velocidad, simplicidad, claridad, robustez básica. La perfección arquitectónica queda fuera salvo que desbloquee la demo.

## Implementación
- Empieza por el happy path.
- Prefiere código directo y dependencias conocidas.
- No introduzcas abstracciones para un único uso.
- No generes tests exhaustivos. Usa build, compilación y smoke checks sobre rutas críticas.
- Evita infraestructura que no sea necesaria para demostrar el producto.
- Si una integración externa puede mockearse para desbloquear la demo, hazlo explícitamente y deja un punto único de sustitución.
- Mantén secretos en variables de entorno; nunca los hardcodees.
- Corrige primero errores que bloqueen ejecución, build o demo.

## Definition of Done
1. Backend arranca.
2. Frontend compila.
3. Frontend consume el backend.
4. Camino principal puede demostrarse manualmente.
5. No hay secretos versionados.
