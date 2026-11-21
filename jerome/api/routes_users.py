from fastapi import APIRouter

from jerome.api.deps import SessionDep
from jerome.api.schemas import UserCreateIn, UserOut, UserPatchIn, UserWithPasswordOut
from jerome.core import users

router = APIRouter()


@router.get("/users", response_model=list[UserOut])
def list_team(session: SessionDep):
    return users.list_users(session)


@router.post("/users", status_code=201, response_model=UserWithPasswordOut)
def add_member(body: UserCreateIn, session: SessionDep):
    user, temporary = users.create_user(session, body.email, body.name)
    return UserWithPasswordOut(user=UserOut.model_validate(user), temporary_password=temporary)


@router.patch("/users/{user_id}", response_model=UserWithPasswordOut)
def update_member(user_id: int, body: UserPatchIn, session: SessionDep):
    user = users.update_user(session, user_id, name=body.name, active=body.active)
    temporary = users.reset_password(session, user_id) if body.reset_password else None
    session.refresh(user)
    return UserWithPasswordOut(user=UserOut.model_validate(user), temporary_password=temporary)
