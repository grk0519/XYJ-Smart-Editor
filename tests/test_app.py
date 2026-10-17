import os
import json
import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from unittest.mock import patch

import yaml

os.environ.setdefault('FLASK_SECRET_KEY', 'test-only-secret-key')

from app import app


class EditorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            UPLOAD_FOLDER=str(root / 'uploads'),
            DOWNLOAD_FOLDER=str(root / 'downloads'),
            SESSION_DB=str(root / 'session-tabs.sqlite3'),
        )
        Path(app.config['UPLOAD_FOLDER']).mkdir()
        Path(app.config['DOWNLOAD_FOLDER']).mkdir()
        self.client = app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def post(self, *args, **kwargs):
        response = self.client.post(*args, **kwargs)
        response.get_data()
        response.close()
        return response

    def upload(self, content, filename):
        upload_stream = BytesIO(content)
        response = self.client.post(
            '/edit',
            data={'xmlfile': (upload_stream, filename)},
            content_type='multipart/form-data',
        )
        upload_stream.close()
        response.get_data()
        response.close()
        return response

    def assert_has_site_header(self, response, has_home_link=True):
        self.assertIn(b'class="top-header', response.data)
        self.assertIn(b'XYJ Smart Edit', response.data)
        if has_home_link:
            self.assertIn(b'class="header-home-link" href="/"', response.data)
        else:
            self.assertNotIn(b'class="header-home-link"', response.data)

    def create_session_artifacts(self, upload_id):
        upload_path = Path(app.config['UPLOAD_FOLDER']) / upload_id
        generated_path = Path(app.config['DOWNLOAD_FOLDER']) / f'modified_{upload_id}'
        unrelated_path = Path(app.config['UPLOAD_FOLDER']) / ('unrelated-' + upload_id)
        upload_path.write_text('uploaded', encoding='utf-8')
        generated_path.write_text('generated', encoding='utf-8')
        unrelated_path.write_text('keep', encoding='utf-8')
        with self.client.session_transaction() as session:
            session['upload_id'] = upload_id
            session['file_type'] = 'xml'
            session['download_file'] = generated_path.name
        return upload_path, generated_path, unrelated_path

    def open_tab(self, tab_id):
        response = self.post('/session-activity', data={'tab_id': tab_id})
        self.assertEqual(response.status_code, 204)

    def test_xml_text_and_attributes_survive_preview_and_download(self):
        response = self.upload(b'<root><item code="old">Before</item></root>', 'record.xml')
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertIn(b'name="attr_1_0"', response.data)

        response = self.post('/preview', data={
            'text_0': '',
            'text_1': 'After',
            'attr_1_0': 'new',
        })
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertIn(b'name="attr_1_0" value="new"', response.data)

        response = self.post('/result', data={
            'text_0': '',
            'text_1': 'After',
            'attr_1_0': 'new',
        })
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        download = self.client.get('/download')
        root = ElementTree.fromstring(download.get_data())
        self.assertEqual(root.find('item').text, 'After')
        self.assertEqual(root.find('item').get('code'), 'new')
        download.close()

    def test_xml_back_to_edit_keeps_draft_values(self):
        self.upload(b'<root><item code="old">Before</item></root>', 'record.xml')
        self.post('/preview', data={
            'text_0': '',
            'text_1': 'Draft',
            'attr_1_0': 'draft-code',
        })

        response = self.post('/edit-again', data={
            'text_0': '',
            'text_1': 'Draft',
            'attr_1_0': 'draft-code',
        })
        self.assertIn(b'value="Draft"', response.data)
        self.assertIn(b'value="draft-code"', response.data)

    def test_xml_container_text_is_read_only_but_leaf_text_and_attributes_are_editable(self):
        source = b'<store mode="online"><contact><phone>555-0100</phone></contact></store>'
        response = self.upload(source, 'store.xml')

        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertIn(b'class="xml-node" style="--node-depth: 0"', response.data)
        self.assertIn(b'class="xml-node" style="--node-depth: 2"', response.data)
        self.assertIn(b'xml-structure-label', response.data)
        self.assertNotIn(b'name="text_0"', response.data)
        self.assertNotIn(b'name="text_1"', response.data)
        self.assertIn(b'name="text_2"', response.data)
        self.assertIn(b'name="attr_0_0"', response.data)

    def test_nested_yaml_document_round_trips(self):
        source = b'name: Example\nsettings:\n  retries: 3\nitems:\n  - one\n  - two\n'
        response = self.upload(source, 'config.yaml')
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertIn(b'<textarea', response.data)

        updated = 'name: Changed\nsettings:\n  retries: 5\nitems:\n  - first\n  - second\n'
        response = self.post('/preview', data={'yaml_source': updated})
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertIn(b'retries: 5', response.data)

        response = self.post('/result-yaml', data={'yaml_source': updated})
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        download = self.client.get('/download')
        result = yaml.safe_load(download.get_data())
        self.assertEqual(result['settings']['retries'], 5)
        self.assertEqual(result['items'], ['first', 'second'])
        download.close()

    def test_nested_json_document_round_trips(self):
        source = b'{"store":{"name":"Tech Gadgets","products":[{"id":"P001","price":29.99,"inStock":true}],"tags":["new","sale"]}}'
        response = self.upload(source, 'catalog.json')
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertIn(b'name="json_source"', response.data)

        updated = '{"store":{"name":"Updated Store","products":[{"id":"P001","price":31.5,"inStock":false}],"tags":["featured"]}}'
        response = self.post('/preview', data={'json_source': updated})
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertIn(b'Updated Store', response.data)
        self.assertIn(b'31.5', response.data)

        response = self.post('/edit-again', data={'json_source': updated})
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertIn(b'Updated Store', response.data)

        response = self.post('/result-json', data={'json_source': updated})
        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        download = self.client.get('/download')
        download_data = download.get_data()
        download.close()
        result = json.loads(download_data)
        self.assertEqual(result['store']['products'][0]['price'], 31.5)
        self.assertIs(result['store']['products'][0]['inStock'], False)
        self.assertEqual(result['store']['tags'], ['featured'])

    def test_invalid_json_edit_returns_to_editor_with_error(self):
        self.upload(b'{"valid": true}', 'config.json')

        response = self.post('/preview', data={'json_source': '{invalid'})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid JSON', response.data)
        self.assertIn(b'{invalid', response.data)

    def test_rejects_invalid_json_upload(self):
        response = self.upload(b'{invalid json}', 'broken.json')
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn('upload_id', session)

    def test_rejects_unsafe_xml(self):
        unsafe_xml = b'<!DOCTYPE root [<!ENTITY x "unsafe">]><root>&x;</root>'
        response = self.upload(unsafe_xml, 'unsafe.xml')
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn('upload_id', session)

    def test_rejects_files_over_upload_limit(self):
        with patch('app.MAX_UPLOAD_BYTES', 1):
            response = self.upload(b'xx', 'large.xml')
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn('upload_id', session)

    def test_post_requires_csrf_token(self):
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            response = self.post('/edit', data={})
            self.assertEqual(response.status_code, 400)
        finally:
            app.config['WTF_CSRF_ENABLED'] = False

    def test_clear_session_removes_only_current_session_files(self):
        upload_id = 'a' * 32 + '.xml'
        upload_path, generated_path, unrelated_path = self.create_session_artifacts(upload_id)

        response = self.post('/clear-session')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(upload_path.exists())
        self.assertFalse(generated_path.exists())
        self.assertTrue(unrelated_path.exists())
        with self.client.session_transaction() as session:
            self.assertNotIn('upload_id', session)

    def test_home_hides_clear_session_and_flash_message_can_be_closed(self):
        with self.client.session_transaction() as session:
            session['upload_id'] = 'f' * 32 + '.xml'
            session['file_type'] = 'xml'
            session['_flashes'] = [('success', 'Session files deleted.')]

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response, has_home_link=False)
        self.assertNotIn(b'Clear session and delete files', response.data)
        self.assertIn(b'Session files deleted.', response.data)
        self.assertIn(b'class="flash-close"', response.data)
        self.assertIn(b'aria-label="Close message"', response.data)

    def test_contact_page_has_shared_site_header_without_clear_button(self):
        response = self.client.get('/contact')

        self.assertEqual(response.status_code, 200)
        self.assert_has_site_header(response)
        self.assertNotIn(b'Clear session and delete files', response.data)

    def test_clear_session_removes_json_upload_and_output(self):
        upload_id = 'e' * 32 + '.json'
        upload_path, generated_path, unrelated_path = self.create_session_artifacts(upload_id)

        response = self.post('/clear-session')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(upload_path.exists())
        self.assertFalse(generated_path.exists())
        self.assertTrue(unrelated_path.exists())

    def test_browser_close_notification_removes_current_session_files(self):
        upload_id = 'b' * 32 + '.yaml'
        upload_path, generated_path, unrelated_path = self.create_session_artifacts(upload_id)
        tab_id = '11111111-1111-4111-8111-111111111111'
        self.open_tab(tab_id)

        response = self.post('/session-close', data={'tab_id': tab_id})

        self.assertEqual(response.status_code, 204)
        self.assertFalse(upload_path.exists())
        self.assertFalse(generated_path.exists())
        self.assertTrue(unrelated_path.exists())

    def test_closing_one_tab_preserves_files_while_another_tab_is_active(self):
        upload_id = 'd' * 32 + '.xml'
        upload_path, generated_path, _ = self.create_session_artifacts(upload_id)
        first_tab = '11111111-1111-4111-8111-111111111111'
        second_tab = '22222222-2222-4222-8222-222222222222'
        self.open_tab(first_tab)
        self.open_tab(second_tab)

        response = self.post('/session-close', data={'tab_id': first_tab})
        self.assertEqual(response.status_code, 204)
        self.assertTrue(upload_path.exists())
        self.assertTrue(generated_path.exists())

        response = self.post('/session-close', data={'tab_id': second_tab})
        self.assertEqual(response.status_code, 204)
        self.assertFalse(upload_path.exists())
        self.assertFalse(generated_path.exists())

    def test_browser_close_notification_accepts_page_csrf_token(self):
        upload_id = 'c' * 32 + '.xml'
        upload_path, generated_path, _ = self.create_session_artifacts(upload_id)
        tab_id = '33333333-3333-4333-8333-333333333333'
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            page = self.client.get('/')
            token_match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', page.get_data())
            self.assertIsNotNone(token_match)
            page.close()
            csrf_token = token_match.group(1).decode('ascii')

            self.post('/session-activity', data={'csrf_token': csrf_token, 'tab_id': tab_id})
            response = self.post('/session-close', data={'csrf_token': csrf_token, 'tab_id': tab_id})

            self.assertEqual(response.status_code, 204)
            self.assertFalse(upload_path.exists())
            self.assertFalse(generated_path.exists())
        finally:
            app.config['WTF_CSRF_ENABLED'] = False


if __name__ == '__main__':
    unittest.main()