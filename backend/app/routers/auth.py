from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..auth import authenticate, clear_session_cookie, current_user, set_session_cookie

router = APIRouter(prefix="/api/auth")


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    authenticate(request, body.email, body.password)
    set_session_cookie(response, body.email.strip().lower())
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    if not current_user(request):
        return Response(status_code=401, content='{"ok":false}', media_type="application/json")
    return {"ok": True}
