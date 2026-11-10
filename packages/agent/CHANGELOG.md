## agent-1.0.0 (2026-11-10)

### Features

- **agent**: check from outside the guest
- **agent**: cap the image cache and evict unused images

### Bug Fixes

- **agent**: pass the host resolver to passt
- **agent**: bound the error texts and follow no redirects
- **agent**: refuse to start as root or with an old passt
- **api**: leave unpinned deployments to an agent that can run them
- **agent**: send a code and an error id as the fail reason
- **agent**: refuse a VM without KVM unless TCG is allowed
- **agent**: send a fresh nonce with every signed request
- **agent**: sign the query of API requests and the console socket
- **agent**: match the VM and passt by an exact argv pair before SIGKILL
- **agent**: build against tungstenite 0.30 and hmac 0.13
- **agent**: cap console.log at the console limit sent by the API
- **agent**: register as a new agent when the API knows the id
- **agent**: give the image download a total deadline
- **agent**: check the address of every image download
- **agent**: keep the guest off the host loopback with passt

### Build

- **release**: add the meta release and the agent installer
- **packages**: set dependency floors at the audited versions
- **cargo**: Bump hmac from 0.12.1 to 0.13.0
- **cargo**: Bump sha2 from 0.10.9 to 0.11.1
- **cargo**: Bump tokio-tungstenite from 0.24.0 to 0.30.0
- **cargo**: Bump base64 from 0.22.1 to 0.23.1

### Documentation

- **project**: state the limit of a verdict and the checks from outside

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
