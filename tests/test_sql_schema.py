import unittest,tempfile
from pathlib import Path
import sql_schema,engine
class SchemaTests(unittest.TestCase):
 def test_schema_only(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'dump.sql';p.write_text("-- CREATE TABLE hidden (phi text);\nCREATE TABLE reports (accession varchar(32), report_path text, PRIMARY KEY(accession));\nINSERT INTO reports VALUES ('secret', '\nCREATE TABLE fake (name text);');")
   result=sql_schema.inspect(p)
   self.assertEqual([t['table'] for t in result['tables']],['reports'])
   self.assertEqual(result['tables'][0]['potential_link_fields'],['accession','report_path'])
   self.assertNotIn('secret',str(result))
 def test_copy(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'dump.sql';p.write_text('COPY data FROM stdin;\nCREATE TABLE fake (name text);\n\\.\nCREATE TABLE real (study_uid text);')
   self.assertEqual([t['table'] for t in sql_schema.inspect(p)['tables']],['real'])
 def test_folder_scope(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();(root/'A-C').mkdir();(root/'A-C'/'child').mkdir();(root/'D-F').mkdir()
   cfg={'source_roots':[str(root)],'require_mounts':False}
   self.assertEqual(len(engine.selection(cfg,[{'root':0,'relative':'A-C/child'},{'root':0,'relative':'A-C'}])),1)
   self.assertEqual(len(engine.browse_folders(cfg,0)['children']),2)
   for relative in ['../outside','/tmp','A-C/../D-F']:
    with self.assertRaises(ValueError):engine.selected_path(root,relative)
if __name__=='__main__':unittest.main()
