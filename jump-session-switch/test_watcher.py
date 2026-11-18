import unittest
from watcher import Policy, parse_access, suspend
from datetime import datetime
from unittest.mock import patch


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


class SuspendTests(unittest.TestCase):
    def returned_to_game(self):
        p = Policy(owned=True)
        p.requested('game', 100)
        p.step(110, 'game', False, False, 0)
        return p

    def test_only_arms_after_confirmed_managed_return(self):
        p = Policy(owned=True)
        p.requested('game', 100)
        p.step(105, 'desktop', False, False, 0)
        self.assertIsNone(p.suspend_at)
        p.step(110, 'game', False, False, 0)
        self.assertEqual(p.suspend_at, 140)

    def test_no_sleep_on_ordinary_game_startup(self):
        p = Policy()
        self.assertIsNone(p.step(1000, 'game', False, False, 0))
        self.assertIsNone(p.step(2000, 'game', False, False, 0))
        self.assertIsNone(p.suspend_at)

    def test_full_thirty_second_window(self):
        p = self.returned_to_game()
        self.assertIsNone(p.step(139, 'game', False, False, 0))
        self.assertEqual(p.step(140, 'game', False, False, 0), 'suspend')

    def test_reconnect_cancels_at_deadline(self):
        p = self.returned_to_game()
        self.assertIsNone(p.step(140, 'game', True, False, 140))
        self.assertIsNone(p.suspend_at)
        self.assertIsNone(p.step(1000, 'game', False, False, 0))

    def test_pending_or_other_connection_cancels(self):
        p = self.returned_to_game()
        self.assertIsNone(p.step(140, 'game', False, True, 0))
        self.assertIsNone(p.suspend_at)
        self.assertIsNone(p.step(1000, 'game', False, False, 0))

    def test_unknown_mode_cancels(self):
        p = self.returned_to_game()
        p.step(139, 'unknown', False, False, 0)
        self.assertIsNone(p.suspend_at)
        self.assertIsNone(p.step(1000, 'game', False, False, 0))

    def test_manual_desktop_cancels(self):
        p = self.returned_to_game()
        p.step(139, 'desktop', False, False, 0)
        self.assertIsNone(p.suspend_at)
        self.assertIsNone(p.step(1000, 'game', False, False, 0))

    def test_safety_pause_cancels(self):
        p = self.returned_to_game()
        p.paused_reason = 'test fault'
        self.assertIsNone(p.step(140, 'game', False, False, 0))
        self.assertIsNone(p.suspend_at)

    def test_countdown_consumed_before_suspend_and_not_rearmed_on_wake(self):
        p = self.returned_to_game()
        self.assertEqual(p.step(140, 'game', False, False, 0), 'suspend')
        p.requested('suspend', 140)
        self.assertIsNone(p.suspend_at)
        self.assertEqual(p.transition, '')
        self.assertIsNone(p.step(141, 'game', False, False, 0))
        self.assertIsNone(p.step(5000, 'game', False, False, 0))

    def test_connection_during_switch_prevents_sleep_and_can_enter_kde(self):
        p = Policy(owned=True)
        p.requested('game', 100)
        p.step(110, 'game', True, False, 105)
        self.assertIsNone(p.suspend_at)
        self.assertIsNone(p.step(111, 'game', True, False, 105))
        self.assertEqual(p.step(116, 'game', True, False, 105), 'desktop')

    def test_pending_connection_at_confirmation_does_not_arm(self):
        p = Policy(owned=True)
        p.requested('game', 100)
        p.step(110, 'game', False, True, 0)
        self.assertIsNone(p.suspend_at)
        self.assertIsNone(p.step(1000, 'game', False, False, 0))

    def test_old_state_without_suspend_field_loads(self):
        p = Policy(owned=True, transition='', log_size=20)
        self.assertIsNone(p.suspend_at)

    @patch('watcher.log')
    @patch('watcher.command')
    def test_suspend_is_noninteractive_and_honors_inhibitors(self, run, _log):
        suspend()
        run.assert_called_once_with(['systemctl', '--no-ask-password',
                                     '--check-inhibitors=yes', '--no-block', 'suspend'])


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
