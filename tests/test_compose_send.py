import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import compose as compose_module
from compose import ComposeView


class _DummyEntry:
    def __init__(self, text=''):
        self._text = text
        self.classes = set()

    def get_text(self):
        return self._text

    def add_css_class(self, name):
        self.classes.add(name)

    def remove_css_class(self, name):
        self.classes.discard(name)


class _DummyButton:
    def __init__(self):
        self.sensitive = True
        self.label = 'Send'

    def set_sensitive(self, value):
        self.sensitive = bool(value)

    def set_label(self, value):
        self.label = value


class _ImmediateThread:
    def __init__(self, target=None, args=None, kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


class ComposeSendTests(unittest.TestCase):
    def test_on_send_passes_reply_target_and_closes_cleanly(self):
        view = ComposeView.__new__(ComposeView)
        backend = mock.Mock()
        parent = mock.Mock()
        app = mock.Mock()
        parent.get_application.return_value = app
        view._parent = parent
        view._reply_to_msg = {'message_id': '<reply@example.com>'}
        view._attachments = [{'name': 'a.txt', 'data': b'hi'}]
        view.to_entry = _DummyEntry('alice@example.com')
        view.subject_entry = _DummyEntry('Subject')
        view.send_btn = _DummyButton()
        view._bcc_switch = SimpleNamespace(get_active=lambda: False)
        view._get_selected_backend = lambda: backend
        view._current_backend_identity = lambda: 'sender@example.com'
        view._buffer_to_plain_text = lambda: 'hello'
        view._buffer_to_html = lambda: '<p>hello</p>'
        view.mark_clean = mock.Mock()
        view._finish_close = mock.Mock()

        with mock.patch.object(compose_module.GLib, 'idle_add', side_effect=lambda fn, *args: fn(*args)):
            with mock.patch.object(compose_module.threading, 'Thread', _ImmediateThread):
                view._on_send(None)

        backend.send_message.assert_called_once_with(
            'alice@example.com',
            'Subject',
            'hello',
            html='<p>hello</p>',
            cc='',
            bcc=[],
            reply_to_msg={'message_id': '<reply@example.com>'},
            attachments=[{'name': 'a.txt', 'data': b'hi'}],
        )
        parent._show_toast.assert_called_once_with('Message sent')
        parent.refresh_visible_mail.assert_called_once_with(True)
        app.wake_background_updates.assert_called_once_with()
        view.mark_clean.assert_called_once_with()
        view._finish_close.assert_called_once_with()

    def test_on_send_supports_bcc_string_recipients(self):
        view = ComposeView.__new__(ComposeView)
        backend = mock.Mock()
        parent = mock.Mock()
        app = mock.Mock()
        parent.get_application.return_value = app
        view._parent = parent
        view._reply_to_msg = None
        view._attachments = []
        view.to_entry = _DummyEntry('alice@example.com')
        view.subject_entry = _DummyEntry('Subject')
        view.send_btn = _DummyButton()
        view._bcc_switch = SimpleNamespace(get_active=lambda: True)
        view._get_selected_backend = lambda: backend
        view._current_backend_identity = lambda: 'sender@example.com'
        view._buffer_to_plain_text = lambda: 'hello'
        view._buffer_to_html = lambda: '<p>hello</p>'
        view.mark_clean = mock.Mock()
        view._finish_close = mock.Mock()

        with mock.patch.object(compose_module.GLib, 'idle_add', side_effect=lambda fn, *args: fn(*args)):
            with mock.patch.object(compose_module.threading, 'Thread', _ImmediateThread):
                view._on_send(None)

        backend.send_message.assert_called_once()
        args, kwargs = backend.send_message.call_args
        self.assertEqual(kwargs.get('bcc'), ['sender@example.com'])
        self.assertEqual(args[0], 'alice@example.com')
        self.assertEqual(args[1], 'Subject')
        self.assertEqual(args[2], 'hello')


if __name__ == '__main__':
    unittest.main()
