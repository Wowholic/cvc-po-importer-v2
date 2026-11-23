import os
import unittest

from app import app
from auth import create_session, verify_session

USER = "team"
PASSWORD = "correct-horse-1"
SECRET = "s" * 64


def signed_in_client():
    os.environ["AUTH_USER"] = USER
    os.environ["AUTH_PASSWORD"] = PASSWORD
    os.environ["AUTH_SECRET"] = SECRET
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post("/login", data={"user": USER, "password": PASSWORD})
    if response.status_code != 302:
        raise AssertionError(response.get_data(as_text=True))
    return client


class LoginTest(unittest.TestCase):
    def setUp(self):
        os.environ["AUTH_USER"] = USER
        os.environ["AUTH_PASSWORD"] = PASSWORD
        os.environ["AUTH_SECRET"] = SECRET
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_the_desk_asks_for_the_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_the_api_stays_closed(self):
        response = self.client.post("/api/extract")
        self.assertEqual(response.status_code, 401)

    def test_the_right_password_opens_the_desk(self):
        response = self.client.post("/login", data={"user": USER, "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"Add a purchase order", home.data)

    def test_a_wrong_password_stays_on_the_form(self):
        response = self.client.post("/login", data={"user": USER, "password": "not-the-password"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Wrong user or password.", response.data)

    def test_a_changed_password_ends_the_session(self):
        token = create_session()
        self.assertTrue(verify_session(token))
        os.environ["AUTH_PASSWORD"] = "a-different-password"
        self.assertFalse(verify_session(token))
