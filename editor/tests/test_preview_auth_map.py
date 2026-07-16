from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from deploy import preview_auth_map


class PreviewAuthMapTests(SimpleTestCase):
    def setUp(self):
        preview_auth_map._cache.clear()

    def test_invalid_rewrite_key_is_denied_without_http_request(self):
        with patch.object(preview_auth_map, "urlopen") as urlopen:
            self.assertFalse(preview_auth_map.authorize("not-a-uuid,token"))
        urlopen.assert_not_called()

    def test_valid_token_is_checked_by_django_and_cached(self):
        response = MagicMock()
        response.__enter__.return_value.status = 204
        session_id = "9abeb6c9-4529-4a16-a408-529101b3bd40"

        with patch.object(preview_auth_map, "urlopen", return_value=response) as urlopen:
            self.assertTrue(preview_auth_map.authorize(f"{session_id},signed%3Atoken", now=10))
            self.assertTrue(preview_auth_map.authorize(f"{session_id},signed%3Atoken", now=11))

        self.assertEqual(urlopen.call_count, 1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Host"), "phpvibe.duszekjk.com")
        self.assertEqual(request.get_header("X-forwarded-proto"), "https")
        self.assertIn(f"/podglad/{session_id}/autoryzuj/", request.full_url)
        self.assertIn("token=signed%3Atoken", request.full_url)
