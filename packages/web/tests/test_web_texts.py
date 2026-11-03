import httpx
import pytest

from jatszoter_web.texts import GENERIC, UNABLE, answered, field_of, sentence, told

ERROR_ID = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("user-name-taken", f"{UNABLE}: a user with this name already exists"),
        ("deployment-id-missing", f"{UNABLE}: the deployment ID is required"),
        ("comment-invalid", f"{UNABLE}: the comment is not valid"),
        ("alpha-beta", GENERIC),
    ],
)
def test_a_code_reads_as_its_sentence(code: str, text: str) -> None:
    assert sentence(code) == text


@pytest.mark.parametrize(
    ("code", "field"),
    [("trainee-id-missing", "trainee_id"), ("name-invalid", "name"), ("body-invalid", None)],
)
def test_a_field_code_names_its_form_field(code: str, field: str | None) -> None:
    assert field_of(code) == field


@pytest.mark.parametrize(
    "body",
    [
        {"error": "<b>alpha</b>", "error_id": "beta"},
        {"detail": "UNIQUE constraint failed: users.name"},
        ["alpha"],
    ],
)
def test_a_body_out_of_shape_gives_nothing_of_itself(body: object) -> None:
    response = httpx.Response(409, json=body)

    assert answered(response) == ("refused", None)
    assert told(response, operator=True) == GENERIC


def test_only_a_signed_in_operator_reads_the_code_sentence() -> None:
    response = httpx.Response(409, json={"error": "user-name-taken", "error_id": ERROR_ID})

    assert told(response, operator=True) == (
        f"{UNABLE}: a user with this name already exists (error {ERROR_ID})"
    )
    assert told(response, operator=False) == f"{GENERIC} (error {ERROR_ID})"
