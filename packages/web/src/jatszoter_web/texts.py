import re
import uuid
from typing import Any

import httpx

CODE = re.compile(r"[a-z][a-z0-9-]{0,63}")
GENERIC = "The request was refused"
UNABLE = "Unable to do the request"

# What a signed-in operator reads for each code the API answers; never the API's own text.
TEXTS = {
    "agent-already-registered": "the agent is already registered",
    "agent-not-enabled": "the agent is not enabled",
    "agent-not-found": "the agent is not known",
    "agent-waits-for-operator": "the agent waits for an operator",
    "agent-without-lease": "the agent is not connected",
    "body-invalid": "the request is not valid",
    "bundle-directory-misnamed": "the bundle's top directory is not named like the scenario id",
    "bundle-fetch-failed": "the bundle URL could not be downloaded",
    "bundle-fetch-refused": "the bundle URL names an address the API may not fetch",
    "bundle-fetch-timeout": "the bundle URL did not answer in time",
    "bundle-file-missing": "the bundle lacks a file the scenario names",
    "bundle-invalid": "the bundle is not valid",
    "bundle-layout-invalid": "the bundle must hold one top directory with the files",
    "bundle-member-absolute": "the bundle holds a file with an absolute path",
    "bundle-member-climbs-out": "the bundle holds a path that climbs out with '..'",
    "bundle-member-not-file": "the bundle holds something that is not a regular file",
    "bundle-member-repeated": "the bundle holds a file twice or a file without a name",
    "bundle-member-sparse": "the bundle holds a sparse file",
    "bundle-not-tar-gz": "the bundle is not a tar.gz archive",
    "bundle-too-large": "the bundle is too large",
    "bundle-url-encoded": "the bundle URL answered with a compressed encoding",
    "bundle-url-redirects": "the bundle URL redirects; name the target instead",
    "check-not-found": "the check is not known",
    "check-pending": "a check is already pending",
    "check-wrong-status": "the check is already settled",
    "conflict": "it collides with what is already stored",
    "console-full": "the console is full",
    "deployment-link-live": "the deployment already has a live trainee link",
    "deployment-not-found": "the deployment is not known",
    "deployment-wrong-status": "the deployment is not in a state that allows this",
    "forbidden": "your account may not do this",
    "image-name-taken": "an image with this name already exists",
    "image-not-registered": "the scenario's image is not registered",
    "invalid-request": "the request is not valid",
    "invalid-transition": "the deployment is not in a state that allows this",
    "last-operator": "the last enabled operator cannot be disabled",
    "link-not-found": "the link is not known",
    "login-throttled": "too many attempts, try again later",
    "not-authorized": "the session is no longer valid; log in again",
    "not-found": "it is not known",
    "password-change-required": "the password must be changed",
    "password-unchanged": "the new password must differ from the current one",
    "password-weak": "the password needs at least 12 characters and three of: "
    "lowercase, uppercase, digits, symbols",
    "recording-not-found": "the recording is not known",
    "recording-without-file": "the recording has no file",
    "register-throttled": "too many registrations, try again later",
    "registrations-full": "too many agents wait for an operator",
    "scenario-invalid": "scenario.yaml does not follow the scenario format",
    "scenario-missing": "the bundle has no scenario.yaml",
    "scenario-not-found": "the scenario is not known",
    "scenario-not-yaml": "scenario.yaml is not valid YAML",
    "scenario-too-large": "scenario.yaml is too large",
    "scenario-version-taken": "this scenario version is already stored",
    "scenario-yaml-alias": "scenario.yaml uses a YAML alias; write the value out",
    "throttled": "too many requests, try again later",
    "too-large": "the request is too large",
    "user-name-taken": "a user with this name already exists",
    "user-not-enabled": "the user is not enabled",
    "user-not-found": "the user is not known",
    "user-not-trainee": "the user is not a trainee",
    "viewer-cannot-check": "a viewer cannot request a check",
}

FIELDS = {
    "agent-id": "the agent",
    "deployment-id": "the deployment ID",
    "new-password": "the new password",
    "scenario-id": "the scenario",
    "trainee-id": "the trainee",
    "url": "the bundle URL",
    "user-id": "the user",
}


def answered(response: httpx.Response) -> tuple[str, str | None]:
    """The code and the error id an API refusal carries, each checked for shape."""
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = {}
    code, error_id = body.get("error"), body.get("error_id")
    if not isinstance(code, str) or not CODE.fullmatch(code):
        code = "refused"
    try:
        error_id = str(uuid.UUID(error_id)) if isinstance(error_id, str) else None
    except ValueError:
        error_id = None
    return code, error_id


def field_of(code: str) -> str | None:
    """The form field a `<field>-missing` or `<field>-invalid` code names."""
    for suffix in ("-missing", "-invalid"):
        if code.endswith(suffix) and code != f"body{suffix}":
            return code.removesuffix(suffix).replace("-", "_")
    return None


def sentence(code: str) -> str:
    if code in TEXTS:
        return f"{UNABLE}: {TEXTS[code]}"
    field = field_of(code)
    if field is not None:
        named = FIELDS.get(code.rsplit("-", 1)[0], f"the {field.replace('_', ' ')}")
        verdict = "is required" if code.endswith("-missing") else "is not valid"
        return f"{UNABLE}: {named} {verdict}"
    return GENERIC


def told(response: httpx.Response, operator: bool) -> str:
    """What the page says of a refusal: the code's sentence for a signed-in operator, the
    generic one otherwise, with the error id the API log holds."""
    code, error_id = answered(response)
    text = sentence(code) if operator else GENERIC
    return f"{text} (error {error_id})" if error_id else text
