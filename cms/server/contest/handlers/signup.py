"""Handlers related to the signup interface
"""

from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

import logging
import datetime

import tornado.web

from cms.server import multi_contest
from cms.db import Participation, User, SessionGen, Contest, Team
from cmscommon.crypto import generate_random_password, hash_password

from .base import BaseHandler
from .contest import ContestHandler

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

def add_user(first_name, last_name, username, password, email, timezone=None, preferred_languages=None):
    if password is None:
        password = generate_random_password()
    if preferred_languages is None or preferred_languages == "":
        preferred_languages = []
    else:
        preferred_languages = [lang for lang in preferred_languages.split(",")]
    user = User(
        first_name=first_name,
        last_name=last_name,
        username=username,
        password=password,
        email=email,
        timezone=timezone,
        preferred_languages=preferred_languages
    )

    try:
        with SessionGen() as session:
            session.add(user)
            session.commit()
    except IntegrityError:
        return False

    logger.info("Registered user {} with password {}".format(username, password))

    return True

def add_participation(username, contest_id):
    try:
        with SessionGen() as session:
            user = session.query(User).filter(User.username == username).first()
            if user is None:
                return False
            contest = Contest.get_from_id(contest_id, session)
            if contest is None:
                return False
            participation = Participation(
                user=user,
                contest=contest,
                hidden=False,
                unrestricted=False
            )

            session.add(participation)
            session.commit()
    except IntegrityError:
        return False
    logger.info("Added participation for user {}".format(username))
    return True

class SignupHandler(ContestHandler):
    """Displays the signup interface
    """
    @multi_contest
    def get(self):
        participation = self.current_user

        if not self.contest.allow_signup:
            return tornado.web.HTTPError(404)

        if participation is not None:
            self.redirect("/")
            return

        self.render("signup.html", **self.r_params)

class RegisterHandler(ContestHandler):
    """Register handler
    """
    @multi_contest
    def post(self):
        username = self.get_argument("username", "")
        first_name = self.get_argument("first_name", "")
        last_name = self.get_argument("last_name", "")
        email = self.get_argument("email", "")
        password = self.get_argument("password", "")
        confirm_password = self.get_argument("confirm_password", "")

        if not self.contest.allow_signup:
            return tornado.web.HTTPError(404)

        if password != confirm_password:
            self.redirect("/signup?password_no_match=true")
            return

        user = self.sql_session.query(User).filter(User.username == username).first()
        participation = self.sql_session.query(Participation).filter(Participation.contest == self.contest).filter(Participation.user == user).first()

        if user is not None and participation is not None:
            self.redirect("/signup?user_exist=true")
            return

        if user is None:
            add_user(first_name=first_name, last_name=last_name, username=username, password=hash_password(password), email=email)
        add_participation(username=username, contest_id=self.contest.id)
        self.redirect("/?signup_successful=true")
        return
