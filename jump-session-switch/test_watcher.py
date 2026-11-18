import unittest
from watcher import Policy, parse_access
from datetime import datetime


class PolicyTests(unittest.TestCase):
    def test_connection_must_settle(self):
        p = Policy()
        self.assertIsNone(p.step(10, 'game', True, False, 9))
        self.assertIsNone(p.step(14, 'game', True, False, 9))
        self.assertEqual(p.step(15, 'game', True, False, 9), 'desktop')

    def test_unauthenticated_or_other_user_inhibits(self):
        p = Policy()
        self.assertIsNone(p.step(10, 'game', False, True, 0))
        self.assertIsNone(p.step(100, 'game', True, True, 99))

    def test_switch_disconnect_does_not_bounce(self):
        p = Policy()
        p.requested('desktop', 10)
        self.assertIsNone(p.step(11, 'unknown', False, False, 0))
        self.assertIsNone(p.step(20, 'desktop', False, False, 0))
        self.assertIsNone(p.step(21, 'desktop', False, False, 0))
        self.assertIsNone(p.step(150, 'desktop', False, False, 0))
        self.assertIsNone(p.step(309, 'desktop', False, False, 0))
        self.assertEqual(p.step(310, 'desktop', False, False, 0), 'game')

    def test_reconnect_cancels_disconnect(self):
        p = Policy(owned=True)
        p.step(100, 'desktop', False, False, 0)
        p.step(200, 'desktop', True, False, 199)
        self.assertIsNone(p.step(220, 'desktop', False, False, 0))
        self.assertIsNone(p.step(339, 'desktop', False, False, 0))
        self.assertEqual(p.step(340, 'desktop', False, False, 0), 'game')

    def test_unmanaged_desktop_is_never_closed(self):
        p = Policy()
        self.assertIsNone(p.step(100, 'desktop', True, False, 99))
        self.assertIsNone(p.step(1000, 'desktop', False, False, 0))

    def test_manual_return_pauses(self):
        p = Policy(owned=True)
        self.assertIsNone(p.step(100, 'game', True, False, 99))
        self.assertTrue(p.paused_reason)
        self.assertIsNone(p.step(200, 'game', True, False, 199))

    def test_failed_transition_pauses_without_retry(self):
        p = Policy()
        p.requested('desktop', 10)
        self.assertIsNone(p.step(101, 'game', True, False, 9))
        self.assertTrue(p.paused_reason)
        self.assertIsNone(p.step(1000, 'game', True, False, 999))

    def test_return_requires_fresh_authentication(self):
        p = Policy(owned=True)
        p.requested('game', 100)
        p.step(110, 'game', False, False, 0)
        self.assertFalse(p.owned)
        self.assertIsNone(p.step(120, 'game', True, False, 99))
        self.assertIsNone(p.step(200, 'game', True, False, 99))
        p.step(210, 'game', True, False, 209)
        self.assertEqual(p.step(215, 'game', True, False, 209), 'desktop')

    def test_unknown_session_resets_disconnect_timer(self):
        p = Policy(owned=True)
        p.step(100, 'desktop', False, False, 0)
        p.step(219, 'unknown', False, False, 0)
        self.assertIsNone(p.step(220, 'desktop', False, False, 0))
        self.assertIsNone(p.step(339, 'desktop', False, False, 0))
        self.assertEqual(p.step(340, 'desktop', False, False, 0), 'game')

    def test_multiple_connections_need_all_closed(self):
        p = Policy(owned=True)
        self.assertIsNone(p.step(100, 'desktop', True, False, 99))
        self.assertIsNone(p.step(500, 'desktop', True, False, 99))


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.t = datetime(2026, 11, 18, 13, 0).timestamp()

    def line(self, body):
        return '11/18/2026 01:00:00 PM  ' + body + '\n'

    def test_request_is_pending_not_authenticated(self):
        text = self.line('Incomming Connection Request: Type:Cloud, ConnectionID:a, PeerID:x')
        self.assertEqual(parse_access(text, self.t - 10, 0, 'ryan'), (False, True, 0, 1))

    def test_authenticate_and_close(self):
        text = self.line('Incomming Connection Established: Type:Cloud, ConnectionID:a, PeerID:x')
        text += self.line('Authentication Succeeded: ConnectionID:a, Type:Local, LocalUser:ryan')
        self.assertEqual(parse_access(text, self.t - 10, 0, 'ryan'), (True, False, self.t, 1))
        text += self.line('Connection closed: ConnectionID:a, PeerID:x')
        self.assertEqual(parse_access(text, self.t - 10, 0, 'ryan'), (False, False, 0, 0))

    def test_old_service_history_is_ignored(self):
        text = self.line('Authentication Succeeded: ConnectionID:a, Type:Local, LocalUser:ryan')
        self.assertEqual(parse_access(text, self.t + 2, 0, 'ryan'), (False, False, 0, 0))

    def test_other_users_inhibit_switches(self):
        text = self.line('Authentication Succeeded: ConnectionID:a, Type:Local, LocalUser:someone_else')
        self.assertEqual(parse_access(text, self.t - 10, 0, 'ryan'), (False, True, 0, 1))

    def test_one_closed_connection_does_not_clear_another(self):
        text = self.line('Authentication Succeeded: ConnectionID:a, Type:Local, LocalUser:ryan')
        text += self.line('Authentication Succeeded: ConnectionID:b, Type:Local, LocalUser:ryan')
        text += self.line('Connection closed: ConnectionID:a, PeerID:x')
        self.assertEqual(parse_access(text, self.t - 10, 0, 'ryan'), (True, False, self.t, 1))


if __name__ == '__main__':
    unittest.main()
