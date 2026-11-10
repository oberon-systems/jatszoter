## web-1.0.0 (2026-11-10)

### Features

- **web**: show the logo and an SVG favicon
- **web**: show the disk check of a deployment

### Bug Fixes

- **web**: keep link tokens and raw paths out of the logs
- **web**: keep no cookie in the API client
- **web**: show the error id on the trainee page on a 5xx
- **web**: warn when logout was not confirmed
- **web**: show and log an error id on server failures
- **web**: abort the connections when the test upstream stops
- **web**: show the accelerator of an agent
- **web**: bound the close timeout of the test upstream
- **web**: send a fresh nonce with every signed call
- **web**: sign the query of API calls and the operator terminal
- **web**: show the API's error code as web's own sentence with its id
- **web**: forward the browser's address and keep the device cookie
- **web**: refuse a socket whose Host is not a plain host
- **web**: refuse a form body past its limit before the token check

### Build

- **release**: add the meta release and the agent installer
- **api**: add psycopg for Postgres
- **packages**: set dependency floors at the audited versions
- **pip**: Bump the pip group across 3 directories with 9 updates
- **docker**: build the images from a hashed lock and a pinned base

## web-0.1.0 (2026-10-19)

### Features

- **web**: test the password rule
- **web**: the password rule checked in the browser and in web
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
- **packages**: add api, web, common and agent skeletons with CI

### Bug Fixes

- **web**: error pages instead of raw JSON

### Build

- **github**: api, web and agent released by their own tags
- **web**: vendored browser code from npm
- **packages**: the MIT license and the publishing metadata
- **packages**: drop the unused common package
