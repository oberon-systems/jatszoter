## api-0.1.0 (2026-10-19)

### Features

- **api**: test the password rule
- **api**: passwords mix three character classes
- **api**: test the routes the Web UI screens need
- **api**: the routes the Web UI screens need
- **api**: test the agent check routes and the markers
- **api**: the agent fetches the check script and posts the result
- **api**: test the check routes
- **api**: a check is requested, read and timed out
- **api**: test the recordings listing and download
- **api**: list a deployment's recordings and download one
- **api**: test the recording feed
- **api**: the routes feed the recording
- **api**: test the recording writer
- **api**: the Recording model writes an asciicast
- **api**: the control holder, keys and resize to the agent
- **api**: a link opens a cookie session and attaches by role
- **api**: users, the trainee link on create, issue and revoke
- **api**: test the console sockets and the log download
- **api**: serve the console sockets, the viewer hub and the log download
- **api**: test the console store
- **api**: keep the console tail in the cache and the full log in the file store
- **api**: test appending to a stored file
- **api**: append to a stored file and read its size
- **api**: test the cloud-init seed
- **api**: render a deployment's cloud-init seed
- **scenarios**: move nginx-502 to Rocky Linux 9
- **api**: test assignment, desired state and agent transitions
- **api**: heartbeat assigns deployments, desired state and agent transitions
- **api**: test operator deployments and stop
- **api**: operator deployments and stop before READY
- **api**: register images and scenario bundles
- **api**: add the file store with a disk fake
- **api**: add operator login and signed operator requests
- **api**: register agents, claim, heartbeat and refresh credentials
- **models**: cover the models with tests
- **migrations**: add the initial alembic revision
- **models**: add the SQLModel domain models
- **lifecycle**: add the deployment lifecycle
- **packages**: add api, web, common and agent skeletons with CI

### Bug Fixes

- **api**: test that the bootstrap loads the guest agent module
- **api**: the guest agent's domain is permissive, so a check reaches the network
- **api**: test a transition whose recording cannot be closed
- **api**: a recording that cannot be closed no longer fails the transition

### Refactor

- **api**: pass settings explicitly
- **models**: replace the cached decorator with a Cached helper
- **models**: pass the session explicitly and drop session_of

### Build

- **github**: api, web and agent released by their own tags
- **packages**: the MIT license and the publishing metadata
- **packages**: drop the unused common package
- **tooling**: add black, flake8 and api dependencies
