import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import thread_renderer
from utils import _thread_inline_image_records
import window as window_module


def _message(uid='1', subject='Original subject'):
    return {
        'uid': uid,
        'subject': subject,
        'sender_name': 'Tester',
        'sender_email': 'tester@example.com',
        'date': datetime(2026, 4, 7, 8, 30, tzinfo=timezone.utc),
    }


class ThreadRenderingTests(unittest.TestCase):
    def _png_bytes(self, width, height):
        pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
        pix.fill(0xff3366ff)
        ok, data = pix.save_to_bufferv('png', [], [])
        self.assertTrue(ok)
        return bytes(data)

    def test_thread_subject_prefers_original_message_subject(self):
        win = window_module.LarkWindow.__new__(window_module.LarkWindow)

        subject = win._thread_subject_for_messages([
            _message('1', subject='Original subject'),
            _message('2', subject='Re: Original subject'),
            _message('3', subject='New topic'),
        ])

        self.assertEqual(subject, 'Original subject')

    def test_thread_html_marks_subject_changes_inside_thread(self):
        records = [
            {
                'msg': _message('1', subject='Original subject'),
                'body_text': 'hello',
                'attachments': [],
                'sender_color': (10, 20, 30),
                'sender_lane': 0,
                'selected': False,
            },
            {
                'msg': _message('2', subject='Different subject'),
                'body_text': 'updated body',
                'attachments': [],
                'sender_color': (10, 20, 30),
                'sender_lane': 0,
                'selected': True,
            },
        ]

        html = thread_renderer.build_thread_html(
            selected_msg=records[-1]['msg'],
            subject='Original subject',
            first_date='',
            last_date='',
            records=records,
            attachments=[],
            is_self_fn=lambda _msg: False,
        )

        self.assertIn('Subject changed', html)
        self.assertIn('Different subject', html)

    def test_thread_inline_image_records_filters_small_logo_like_images(self):
        html = '<p>Hello</p><img src="cid:hero"><img src="cid:footer-logo">'
        attachments = [
            {
                'name': 'hero.png',
                'content_type': 'image/png',
                'disposition': 'inline',
                'content_id': '<hero>',
                'data': self._png_bytes(240, 160),
            },
            {
                'name': 'company-logo.png',
                'content_type': 'image/png',
                'disposition': 'inline',
                'content_id': '<footer-logo>',
                'data': self._png_bytes(32, 32),
            },
        ]

        records = _thread_inline_image_records(html, attachments)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['name'], 'hero.png')

    def test_thread_html_renders_inline_images(self):
        records = [
            {
                'msg': _message('1', subject='Original subject'),
                'body_text': 'hello',
                'attachments': [],
                'inline_images': [{'src': 'data:image/png;base64,abc', 'name': 'hero.png', 'width': 240, 'height': 160}],
                'sender_color': (10, 20, 30),
                'sender_lane': 0,
                'selected': False,
            },
        ]

        html = thread_renderer.build_thread_html(
            selected_msg=records[0]['msg'],
            subject='Original subject',
            first_date='',
            last_date='',
            records=records,
            attachments=[],
            is_self_fn=lambda _msg: False,
        )

        self.assertIn('bubble-inline-images', html)
        self.assertIn('hero.png', html)

    def test_wrap_email_html_frame_wraps_body_contents(self):
        raw_html = '<html><body><table><tr><td>Hello</td></tr></table></body></html>'

        wrapped = window_module._wrap_email_html_frame(raw_html)

        self.assertIn('lark-message-shell', wrapped)
        self.assertIn('lark-message-frame', wrapped)
        self.assertIn('<table>', wrapped)


if __name__ == '__main__':
    unittest.main()
