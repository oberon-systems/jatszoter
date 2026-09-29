# 07 - Roadmap

Version one is the smallest stand that runs a real exam end to end. Later
items are listed so that version one does not close the door on them.

- [Version one](#version-one)
- [Later](#later)
- [Out of scope](#out-of-scope)

## Version one

In build order; each item is one or more implementation prompts.

1. Repository skeleton: packages, tests, CI.
2. Domain model and migrations ([02](02-domain-model.md)).
3. Agent lifecycle: register, lease, heartbeat, capacity N.
4. QEMU runtime: image cache, VM create and destroy, reconcile.
5. Scenario registry ([03](03-scenario-format.md)).
6. cloud-init bootstrap and the bootstrap marker.
7. Console pipeline with bounded viewer queues ([04](04-console.md)).
8. Share links, sessions, take control.
9. Recording and replay ([05](05-recording.md)).
10. Checks through the guest agent.
11. Web UI, design-first in Penpot: design system, then each screen.
12. Hardening: limits, TTL sweeps, smoke test.

## Later

- OIDC login for operators, with named operators in the audit trail.
- Console hub across several API processes, sockets routed by
  `deployment_id`.
- Exams: a batch of deployments from one scenario, one link per trainee, a
  shared deadline and a results table.
- Hint reveal by the trainee, with a marker in the recording.
- Reset a deployment to its READY snapshot instead of a new bootstrap.
- Several VMs per deployment, on a private network between them.
- Per-scenario network policy: offline, allow-list, full NAT.

## Out of scope

- Hard isolation of hostile guests ([06](06-security.md#guest-and-host)).
- Public sign-up, tenants and billing.
- A scenario editor in the web UI.
- Graphical desktops in the VM.
