# Contexto de la hackatón

**Estado:** aceptado (equipo). No es un contrato de implementación.

## Naturaleza

Dos personas full-stack en frontend, backend, infra, IA y producto. El reto se anuncia al inicio; hay **~6 horas** para adaptar.

El trabajo previo **no** es un producto vertical rígido. Es una plataforma:

- técnicamente sólida;
- visualmente convincente;
- modular y rápida de modificar;
- con piezas reutilizables;
- lista para pivotar.

Apuesta actual: telefonía + agentes + intelligence comercial. Si el reto no encaja, se adapta lo existente. No se fuerza telefonía donde no tenga sentido.

```text
Construir capacidades reutilizables,
no una empresa de telecomunicaciones antes de conocer el reto.
```

## Prioridades (orden)

1. Demo visual convincente
2. Funcionalidad real en los puntos críticos
3. Adaptabilidad
4. Escalabilidad razonable
5. Tests

Hay que mostrar profundidad (frontend, backend, realtime, arquitectura, ML/DL, agentes, datos, integraciones) **sin** sobreingeniería.

## Regla de las seis horas

Cada decisión durante el reto:

```text
¿Esto aumenta materialmente la calidad de la demo
dentro de las próximas 6 horas?
```

Si no: **no hacerlo**. La arquitectura existe para acelerar la demo, no para competir contra ella.

## Check de relevancia (al anunciar el reto)

Clasificar cada pieza: **KEEP / ADAPT / DROP**.

Si telefonía sigue teniendo sentido: conservar core (auth, agente, RAG, realtime, analytics, Postgres, Chroma, worker); adaptar tools, schema de dominio, dashboard, generador sintético; telefonía KEEP o ADAPT.

Si telefonía no aporta: **DROP** el dominio de telefonía; **KEEP** platform core, agent runtime, RAG, Chroma, analytics, worker, auth, DB, realtime, frontend shell.

## Tenancy y auth (producto)

B2B multi-organización. Modelo mínimo `Organization` + `User`. Roles solo **SUPERADMIN** y **ADMIN**. Auth propia; no SaaS de identidad.

El aislamiento por organización debe existir en datos aunque la demo use una sola org.

**Ya en código:** ver [Auth](../auth/README.md) y [Datos](../data/schema.md).
