"""Synthetic tokens and mocked HTTPS only; no account reads or external traffic."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_nhk_license as subject


class TransportLimits(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root_patch = patch.object(subject, 'HERE', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.connection = Mock()
        self.response = Mock(status=200)
        self.response.read.return_value = b'opaque-response'
        self.connection.getresponse.return_value = self.response
        self.factory_patch = patch.object(subject.http.client, 'HTTPSConnection', return_value=self.connection)
        self.factory = self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)

    def test_bound_endpoint_and_no_secret_in_marker(self):
        result = subject.send_once(b'challenge', 'synthetic-private-token', {})
        self.assertEqual(result, b'opaque-response')
        self.factory.assert_called_once_with('licence.hsk.st.nhk', timeout=15)
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ('POST', '/widevine/license'))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer synthetic-private-token')
        self.assertNotIn('Cookie', kwargs['headers'])
        marker = (self.root / 'nhk-license-attempt.json').read_text()
        self.assertNotIn('synthetic-private-token', marker)
        self.assertNotIn('challenge', marker)
        self.assertNotIn('opaque-response', marker)

    def test_redirect_is_not_followed(self):
        self.response.status = 302
        self.assertEqual(subject.send_once(b'challenge', 'synthetic', {}), b'')
        self.connection.request.assert_called_once()
        self.response.getheader.assert_not_called()

    def test_uncertain_outcome_cannot_be_retried(self):
        self.connection.getresponse.side_effect = OSError('simulated connection failure')
        with self.assertRaises(OSError):
            subject.send_once(b'challenge', 'synthetic', {})
        with self.assertRaises(FileExistsError):
            subject.send_once(b'challenge', 'synthetic', {})
        self.connection.request.assert_called_once()

    def test_oversized_response_is_not_returned_to_cdm(self):
        self.response.read.return_value = b'x' * (subject.LIMIT + 1)
        with self.assertRaises(ValueError):
            subject.send_once(b'challenge', 'synthetic', {})
        self.connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
