import importlib.util
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('public_source',Path(__file__).resolve().parents[1]/'check-public-source.py')
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)


class PublicSourceTests(unittest.TestCase):
    def test_source_and_examples_allowed(self):
        for name in ('README.md','app/main.py','.env.example','deploy/pi5/pi5.env.example'):
            with self.subTest(name=name): self.assertEqual(checker.inspect_file(name,b'public example'),[])

    def test_distribution_and_encrypted_key_never_public(self):
        for name in ('release.zip','mobile/app.apk','model.onnx','credentials/key.json',
                     'firebase-demo-key.encrypted.json','package-passphrase.txt','db.dump','google-services.json'):
            with self.subTest(name=name): self.assertTrue(checker.inspect_file(name,b'not actual private data'))

    def test_private_key_body_detected_without_returning_value(self):
        key=b'-----BEGIN '+b'PRIVATE KEY-----\n'+b'A'*60
        reasons=checker.inspect_file('innocent.txt',key)
        self.assertIn('private key material',reasons)
        self.assertNotIn('A'*60,str(reasons))

    def test_server_key_json_is_not_public_client_config(self):
        self.assertIn('Google server credential JSON',checker.inspect_file('settings.json',
            b'{"type":"service_account","private_key":"inert-test-fixture"}'))

    def test_links_and_binaries_rejected(self):
        self.assertTrue(checker.inspect_file('link.py',b'target','120000'))
        self.assertTrue(checker.inspect_file('oversize.txt',b'x'*(checker.MAX_FILE_BYTES+1)))

    def test_index_metadata_and_blob_read_end_to_end_without_shell(self):
        oid='a'*40
        payload=b'public code'
        def output(args,**kwargs):
            if args[1:3]==['ls-files','--stage']:return f'100644 {oid} 0\tmain.py\0'.encode()
            if args[1:3]==['cat-file','-s']:return str(len(payload)).encode()
            if args[1:3]==['cat-file','blob']:return payload
            raise AssertionError('Unexpected command')
        stream=io.StringIO()
        with patch.object(checker.subprocess,'check_output',side_effect=output),contextlib.redirect_stdout(stream):
            self.assertEqual(checker.main(),0)
        report=json.loads(stream.getvalue())
        self.assertEqual(report['indexed_files_checked'],1)
        self.assertTrue(report['passed'])


if __name__ == '__main__':unittest.main()
