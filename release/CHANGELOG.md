# Changelog

## 1.0.0 (2026-11-10)

### Features

- **stack**: build passt in the agent installer
- **stack**: add the stack installer
- **stack**: build the agent from source and list every doctor check
- **web**: show the logo and an SVG favicon
- **api**: check the sample from outside the guest
- **web**: show the disk check of a deployment
- **agent**: check from outside the guest
- **api**: store a verdict read from a stopped VM disk
- **agent**: cap the image cache and evict unused images
- **web**: test the password rule
- **web**: the password rule checked in the browser and in web
- **api**: test the password rule
- **api**: passwords mix three character classes
- **web**: the product is called Jatszoter
- **web**: test the error pages
- **web**: test the replay page
- **web**: the replay page
- **web**: test the deployment detail and the trainee page
- **web**: the deployment detail and the trainee page
- **web**: test the operator list and form screens
- **web**: the operator list and form screens
- **web**: test the session, login and the API relay
- **web**: session, login and the API relay
- **api**: test the routes the Web UI screens need
- **api**: the routes the Web UI screens need
- **agent**: the agent runs a pending check in the guest
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
- **agent**: keys into the serial socket, resize through qga
- **api**: the control holder, keys and resize to the agent
- **api**: a link opens a cookie session and attaches by role
- **api**: users, the trainee link on create, issue and revoke
- **agent**: ship the serial console to the API
- **api**: test the console sockets and the log download
- **api**: serve the console sockets, the viewer hub and the log download
- **api**: test the console store
- **api**: keep the console tail in the cache and the full log in the file store
- **api**: test appending to a stored file
- **api**: append to a stored file and read its size
- **agent**: boot VMs through UEFI
- **agent**: probe a booting VM to READY or FAILED
- **agent**: pack the seed into an ISO and attach it
- **agent**: test the guest agent client
- **agent**: talk to the guest agent over qga.sock
- **api**: test the cloud-init seed
- **api**: render a deployment's cloud-init seed
- **api**: move nginx-502 to Rocky Linux 9
- **agent**: test the reconcile plan and runtime
- **agent**: reconcile VMs with the desired state
- **agent**: test the QMP client and VM lifecycle
- **agent**: create, list and destroy QEMU VMs
- **agent**: test the image cache
- **agent**: cache base images by digest
- **agent**: read desired state and post transitions
- **api**: test assignment, desired state and agent transitions
- **api**: heartbeat assigns deployments, desired state and agent transitions
- **api**: test operator deployments and stop
- **api**: operator deployments and stop before READY
- **api**: register images and scenario bundles
- **api**: add the file store with a disk fake
- **api**: add operator login and signed operator requests
- **agent**: register, claim, heartbeat and fence on a lapsed lease
- **api**: register agents, claim, heartbeat and refresh credentials
- **api**: cover the models with tests
- **api**: add the initial alembic revision
- **api**: add the SQLModel domain models
- **api**: add the deployment lifecycle
- **agent, api, web**: add api, web, common and agent skeletons with CI

### Bug Fixes

- **stack**: pass the bundle fetch networks to the api
- **agent**: pass the host resolver to passt
- **agent**: bound the error texts and follow no redirects
- **web**: keep link tokens and raw paths out of the logs
- **api**: close the low findings of the re-audit
- **api**: sign filer calls with a JWT
- **agent**: refuse to start as root or with an old passt
- **web**: keep no cookie in the API client
- **web**: show the error id on the trainee page on a 5xx
- **agent, api**: leave unpinned deployments to an agent that can run them
- **agent**: send a code and an error id as the fail reason
- **web**: warn when logout was not confirmed
- **web**: show and log an error id on server failures
- **web**: abort the connections when the test upstream stops
- **api**: leave a pending check test room before its deadline
- **web**: show the accelerator of an agent
- **agent**: refuse a VM without KVM unless TCG is allowed
- **api**: keep the accelerator an agent reports
- **web**: bound the close timeout of the test upstream
- **api**: add no-store and nosniff to every answer
- **agent**: send a fresh nonce with every signed request
- **web**: send a fresh nonce with every signed call
- **api**: require a single-use nonce in every signed request
- **agent**: sign the query of API requests and the console socket
- **web**: sign the query of API calls and the operator terminal
- **api**: sign the query string along with the path
- **web**: show the API's error code as web's own sentence with its id
- **api**: answer every error with a code and an error id only
- **web**: forward the browser's address and keep the device cookie
- **api**: count login failures per client address and device token
- **api**: rate-limit agent registration and cap pending ones
- **api**: give Secret an explicit constructor for mypy
- **agent**: match the VM and passt by an exact argv pair before SIGKILL
- **agent**: build against tungstenite 0.30 and hmac 0.13
- **agent, api**: cap console.log at the console limit sent by the API
- **api**: store cache entries as signed JSON instead of pickle
- **web**: refuse a socket whose Host is not a plain host
- **api**: refuse a timestamp that is not an ASCII number
- **api**: stop redirecting a trailing slash to the Host header
- **api**: check the address of every bundle download
- **agent**: register as a new agent when the API knows the id
- **api**: refuse a registration under a known agent id
- **api**: end every credential on a password reset
- **web**: refuse a form body past its limit before the token check
- **api**: refuse a request body past its limit before auth
- **api**: refuse sparse members and YAML aliases in a bundle
- **agent**: give the image download a total deadline
- **api**: bound the bundle fetch in bytes and in time
- **api**: refuse plain http image URLs off loopback
- **agent**: check the address of every image download
- **agent**: keep the guest off the host loopback with passt
- **web**: error pages instead of raw JSON
- **api**: test that the bootstrap loads the guest agent module
- **api**: the guest agent's domain is permissive, so a check reaches the network
- **api**: test a transition whose recording cannot be closed
- **api**: a recording that cannot be closed no longer fails the transition

### Refactor

- **api**: pass settings explicitly
- **api**: replace the cached decorator with a Cached helper
- **api**: pass the session explicitly and drop session_of

### Build

- **agent, api, stack, web**: add the meta release and the agent installer
- **stack**: add the compose stack
- **api, web**: add psycopg for Postgres
- **agent, api, web**: set dependency floors at the audited versions
- **agent**: Bump hmac from 0.12.1 to 0.13.0
- **agent**: Bump sha2 from 0.10.9 to 0.11.1
- **agent**: Bump tokio-tungstenite from 0.24.0 to 0.30.0
- **stack**: drop pip from the runtime images
- **api, web**: Bump the pip group across 3 directories with 9 updates
- **agent**: Bump base64 from 0.22.1 to 0.23.1
- **api, stack, web**: build the images from a hashed lock and a pinned base
- **agent, api, web**: api, web and agent released by their own tags
- **web**: vendored browser code from npm
- **stack**: api and web images
- **agent, api, web**: the MIT license and the publishing metadata
- **api, web**: drop the unused common package
- **api**: add black, flake8 and api dependencies

### Documentation

- **agent**: state the limit of a verdict and the checks from outside
