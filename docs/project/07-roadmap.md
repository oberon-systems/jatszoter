# 07 - Roadmap

Version one is the smallest stand that runs a real exam end to end. Later
items are listed so that version one does not close the door on them.

- [Version one](#version-one)
- [Later](#later)
- [Out of scope](#out-of-scope)

## Version one

In build order; each item is one or more implementation prompts. From 3a on,
every item also ships the agent side of its change and extends the smoke test
to cover it.

1. Repository skeleton: packages, tests, CI.
2. Domain model and migrations ([02](02-domain-model.md)).
3. Agent lifecycle in the API: register, lease, heartbeat, capacity N.
   - 3a. Agent: support what the API already offers - register, keep and
     refresh the credential, heartbeat, fence itself once the local lease
     deadline passes.
   - 3b. Smoke test: the API and one agent against fakeredis and SQLite; the
     agent registers, is enabled and keeps its lease.
4. QEMU runtime: image cache, VM create and destroy, reconcile.
   - Agent: the runtime itself, driven by the desired state.
   - Smoke: a deployment is assigned, started and destroyed on stop.
5. Scenario registry ([03](03-scenario-format.md)).
   - Agent: the desired state carries the scenario version, image URL and
     digest; the image cache checks the digest.
   - Smoke: register the sample scenario and deploy it.
6. cloud-init bootstrap and the bootstrap marker.
   - Agent: build the seed ISO, read the marker through the guest agent,
     eject the ISO.
   - Smoke: the deployment reaches READY.
7. Console pipeline with bounded viewer queues ([04](04-console.md)).
   - Agent: ship the serial console over the agent WebSocket.
   - Smoke: a viewer receives console output.
8. Links, take control.
   - Agent: pass terminal input and resize from the API to the guest.
   - Smoke: a trainee link opens the terminal; take control cuts its input.
9. Recording and replay ([05](05-recording.md)).
   - Agent: no change; the API records the stream it already receives.
   - Smoke: a session leaves a recording that replays.
10. Checks through the guest agent.
    - Agent: run pending checks in the guest and post the result.
    - Smoke: a check fails before the fix and passes after it.
11. Web UI, design-first in Penpot: design system, then each screen.
    - Agent: no change.
    - Smoke: the operator and trainee pages load against the smoke stand.
12. Hardening: limits, TTL sweeps.
    - Agent: bounded retries and backoff towards the API.
    - Smoke: session TTL and bootstrap timeout end their deployments.

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
