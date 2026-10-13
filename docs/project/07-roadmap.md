# 07 - Roadmap

Version one is the smallest stand that runs a real exam end to end. Later
items are listed so that version one does not close the door on them.

- [Version one](#version-one)
- [Later](#later)
- [Out of scope](#out-of-scope)

## Version one

In build order; each item is one or more implementation prompts. From 3a on,
every item also ships the agent side of its change and extends the smoke test
to cover it. An item marked Done has landed on `main`.

1. Repository skeleton: packages, tests, CI. Done.
2. Domain model and migrations ([02](02-domain-model.md)). Done.
3. Agent lifecycle in the API: register, lease, heartbeat, capacity N. Done.
   - 3a. Agent: support what the API already offers - register, keep and
     refresh the credential, heartbeat, fence itself once the local lease
     deadline passes. Done.
   - 3b. Smoke test: the API and one agent against fakeredis and SQLite; the
     agent registers, is enabled and keeps its lease. Done.
4. Operator login, images, scenario registry and the QEMU runtime. Done.
   - API: user login and signed operator requests; images; scenarios from
     a bundle or a URL, stored in SeaweedFS or a local directory;
     deployments created, assigned and stopped.
   - Agent: desired state with image URL and digest, image cache, VM create
     and destroy, reconcile.
   - Smoke: log in, register the sample scenario by URL, deploy it; the
     deployment is assigned, started and destroyed on stop.
5. Merged into 4.
6. cloud-init bootstrap and the bootstrap marker. Done.
   - API: the seed route renders user-data and meta-data from the stored
     bundle.
   - Agent: fetch the seed, pack, attach and eject the seed ISO, probe the
     guest agent and the marker, fail on the marker or the deadline.
   - Scenario: nginx-502 version 2 on rocky-9.
   - Smoke: a Rocky 9 deployment reaches READY and is destroyed on stop.
7. Console pipeline with bounded viewer queues ([04](04-console.md)). Done.
   - API: the agent console socket, the Valkey tail and the full log in the
     file store, the hub, operator attach and the log download.
   - Agent: QEMU logs the serial console to a file; the agent ships it over
     one WebSocket per live VM.
   - Smoke: an operator viewer receives console output after READY and the
     downloaded log holds the boot.
8. Links, take control ([04](04-console.md#links)). Done.
   - API: users made by the operator, the trainee link on create, link
     issue and revoke, the link session cookie, attach by role, the control
     holder, keys and resize passed down the agent's console socket.
   - Agent: write keys into the serial socket and apply a resize through
     the guest agent.
   - Smoke: a trainee link opens the terminal and types; take control cuts
     its input; a resize reaches the guest; a revoked link opens nothing.
9. Recording and replay ([05](05-recording.md)). Done.
   - API: the asciicast of a trainee link from its first open - output from
     the READY offset, resizes and control changes as events, the size
     limit - closed on revoke and when the deployment ends; the recordings
     listing and the download.
   - Agent: no change; the API records the stream it already receives.
   - Smoke: a session leaves a recording that replays.
10. Checks through the guest agent
    ([01](01-architecture.md#data-flows)). Done.
    - API: a check requested by an operator or a trainee link, one pending
      at a time, timed out without an answer; the script and the result
      routes of the agent; results as markers in the recording.
    - Agent: run the pending check in the guest and post the result.
    - Smoke: a check fails before the fix and passes after it.
11. Web UI, design-first in Penpot: design system, then each screen; CSRF
    tokens on state-changing requests; the replay page with a vendored
    asciinema-player and markers as jump points.
    - Agent: no change.
    - Smoke: the operator and trainee pages load against the smoke stand.
12. Hardening: limits, TTL sweeps, the recording retention sweep with
    `JATSZOTER_RECORDING_RETENTION_DAYS`, which also closes a recording
    of a revoked link that was left open.
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
