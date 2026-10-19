## agent-0.1.0 (2026-10-19)

### Features

- **agent**: the agent runs a pending check in the guest
- **agent**: keys into the serial socket, resize through qga
- **agent**: ship the serial console to the API
- **agent**: boot VMs through UEFI
- **agent**: probe a booting VM to READY or FAILED
- **agent**: pack the seed into an ISO and attach it
- **agent**: test the guest agent client
- **agent**: talk to the guest agent over qga.sock
- **agent**: test the reconcile plan and runtime
- **agent**: reconcile VMs with the desired state
- **agent**: test the QMP client and VM lifecycle
- **agent**: create, list and destroy QEMU VMs
- **agent**: test the image cache
- **agent**: cache base images by digest
- **agent**: read desired state and post transitions
- **agent**: register, claim, heartbeat and fence on a lapsed lease
- **packages**: add api, web, common and agent skeletons with CI

### Build

- **github**: api, web and agent released by their own tags
- **packages**: the MIT license and the publishing metadata
