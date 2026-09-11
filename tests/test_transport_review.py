"""An actual truncated HTTP chunk must not escape as a body-bearing traceback."""

import http.client
import io
import json
import unittest

from myhermes.api import API
from myhermes.errors import CompanionError, OfflineError
from myhermes.github import GitHubClient


class BrokenSocket:
    def makefile(self, *args, **kwargs):
        return io.BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"100\r\nPRIVATE_SENTINEL"
        )


class BrokenHTTP:
    def __init__(self):
        self.requests = 0

    def open(self, request, timeout):
        self.requests += 1
        response = http.client.HTTPResponse(BrokenSocket())
        response.begin()
        return response


class TransportReviewTests(unittest.TestCase):
    def test_only_the_explicit_personal_skill_capacity_response_is_classified(self):
        class Reply(io.BytesIO):
            status = 429

        class Opener:
            def __init__(self, body):
                self.body = body

            def open(self, request, timeout):
                return Reply(json.dumps(self.body).encode())

        exact = {"error": "skill_capacity"}
        api = API("https://example.invalid", opener=Opener(exact))
        self.assertEqual(api._request("POST", "/v1/skills/personal", {}), (429, exact))
        for method, path, body in (
            ("GET", "/v1/skills/personal", exact),
            ("POST", "/v1/sync", exact),
            ("POST", "/v1/skills/personal", {"error": "rate_limited"}),
            ("POST", "/v1/skills/personal", {**exact, "detail": "PRIVATE_SENTINEL"}),
        ):
            with self.assertRaises(CompanionError) as caught:
                API("https://example.invalid", opener=Opener(body))._request(method, path, {})
            self.assertNotIn("PRIVATE_SENTINEL", str(caught.exception))

    def test_incomplete_chunk_is_sanitized_and_never_automatically_retried(self):
        for service in ("company", "github"):
            with self.subTest(service=service):
                opener = BrokenHTTP()
                with self.assertRaises(OfflineError) as caught:
                    if service == "company":
                        API("https://example.invalid", opener=opener)._request("GET", "/v1/sync")
                    else:
                        GitHubClient("github_pat_synthetic_credential", opener=opener).identity()
                self.assertNotIn("PRIVATE_SENTINEL", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertEqual(opener.requests, 1)


if __name__ == "__main__":
    unittest.main()
