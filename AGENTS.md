# Hackathon Engineering Rules

Este repositorio prioriza velocidad de entrega, claridad y demo funcional.

1. Construye primero el camino feliz end-to-end.
2. Evita arquitectura prematura: no agregues capas, patrones o servicios sin una necesidad inmediata.
3. Usa librerías maduras antes de implementar infraestructura propia.
4. Mantén endpoints, componentes y módulos pequeños, pero no fragmentes por estética.
5. Valida con `./scripts/build.sh` y el smoke test. No construyas una suite exhaustiva durante la hackathon.
6. Si una solución de 20 líneas resuelve el problema de forma fiable, no la conviertas en 200.
7. No agregues Docker, Kubernetes, colas, Redis, ORM, auth o observabilidad hasta que el producto realmente los necesite.
8. Haz cambios verticales: frontend + API + dato mínimo necesario en el mismo incremento.
9. Ante una decisión reversible, elige rápido la opción más simple y continúa.
10. Antes de cerrar una feature, comprueba que la demo real funciona de punta a punta.
11. Usa el toast reutilizable para todo feedback inmediato de acciones, conexiones, errores, tools y TTS; no dependas únicamente de cambios visuales silenciosos.
