from fastapi import APIRouter, Depends

from jerome.api.deps import SessionDep, require_super_admin
from jerome.api.schemas import UserCreateIn, UserOut, UserPatchIn, UserWithPasswordOut
from jerome.core import users

router = APIRouter(dependencies=[Depends(require_super_admin)])


@router.get("/users", response_model=list[UserOut])
def list_team(session: SessionDep):
    return users.list_users(session)


@router.post("/users", status_code=201, response_model=UserWithPasswordOut)
def add_member(body: UserCreateIn, session: SessionDep):
    user, temporary = users.create_user(session, body.email, body.name, role=body.role)
    return UserWithPasswordOut(user=UserOut.model_validate(user), temporary_password=temporary)


@router.patch("/users/{user_id}", response_model=UserWithPasswordOut)
def update_member(user_id: int, body: UserPatchIn, session: SessionDep):
    user = users.update_user(session, user_id, name=body.name, active=body.active)
    if body.role is not None:
        users.set_role(session, user_id, body.role)
    temporary = users.reset_password(session, user_id) if body.reset_password else None
    session.refresh(user)
    return UserWithPasswordOut(user=UserOut.model_validate(user), temporary_password=temporary)
