import codecs,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import panel_localizer as P
from panel_files import Files,inside,digest
import jsx_labels as J

class Panels(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name);self.root=self.base/'ext';(self.root/'CSXS').mkdir(parents=True)
        (self.root/'CSXS/manifest.xml').write_text('<ExtensionManifest ExtensionBundleId="demo" ExtensionBundleName="Demo"><MainPath>./index.html</MainPath></ExtensionManifest>',encoding='utf8')
        self.html='<body><button title="Apply changes">Apply</button><input value="Keep"><div contenteditable="true">User</div><script>var x="Code";</script></body>'
        (self.root/'index.html').write_text(self.html,encoding='utf8');self.make()
    def make(self):self.t=P.inspect(self.root);self.l=P.Localizer(self.t,self.base/'data')
    def test_extract_excludes_input_editable_code(self):
        r=self.l.extract();self.assertEqual({x['text'] for x in r['terms']},{'Apply','Apply changes'})
    def test_apply_repeat_restore_exact_bytes(self):
        original=(self.root/'index.html').read_bytes();r=self.l.extract();self.l.apply(r,{'Apply':'应用'})
        self.assertIn(P.MARK,(self.root/'index.html').read_text());self.assertTrue((self.root/P.OVERLAY).exists())
        r=self.l.extract();self.l.apply(r,{'Apply':'执行'});self.assertEqual((self.root/'index.html').read_text().count(P.MARK),1)
        self.l.restore();self.assertEqual((self.root/'index.html').read_bytes(),original);self.assertFalse((self.root/P.OVERLAY).exists());self.assertEqual(self.l.restore(),0)
    def test_save_dictionary_does_not_patch(self):
        self.l.save({'Apply':'应用'});self.assertEqual((self.root/'index.html').read_text(),self.html)
    def test_changed_after_extract_refused(self):
        r=self.l.extract();(self.root/'index.html').write_text('Changed')
        with self.assertRaisesRegex(ValueError,'变化'):self.l.apply(r,{'Apply':'应用'})
    def test_restore_external_edit_refused(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});(self.root/'index.html').write_text('new version')
        with self.assertRaisesRegex(ValueError,'外部修改'):self.l.restore()
        self.assertEqual((self.root/'index.html').read_text(),'new version')
    def test_manifest_upgrade_refused(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});p=self.root/'CSXS/manifest.xml';p.write_text(p.read_text().replace('Demo','New'));self.make()
        with self.assertRaises(ValueError):self.l.restore()
    def test_upgrade_after_restore_starts_new_baseline(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});self.l.restore()
        p=self.root/'CSXS/manifest.xml';p.write_text(p.read_text().replace('Demo','New'));self.make();self.l.apply(self.l.extract(),{'Apply':'执行'});self.l.restore()
        self.assertEqual((self.root/'index.html').read_text(),self.html)
        self.assertTrue(list(self.l.files.folder.glob('history-*.json')))
    def test_same_id_different_install_isolated(self):
        import shutil
        other=self.base/'other';shutil.copytree(self.root,other);t=P.inspect(other)
        self.assertNotEqual(t.key,self.t.key)
    def test_path_traversal_refused(self):
        for rel in ('../outside','../../foo'):
            with self.assertRaises(ValueError):inside(self.root,rel)
    def test_signed_requires_explicit_flag(self):
        (self.root/'META-INF').mkdir();(self.root/'META-INF/signatures.xml').write_text('signed');self.make();r=self.l.extract()
        with self.assertRaisesRegex(ValueError,'签名'):self.l.preview(r,{'Apply':'应用'})
        self.assertTrue(self.l.preview(r,{'Apply':'应用'},True))
    def test_csp_refused(self):
        (self.root/'index.html').write_text('<meta http-equiv="Content-Security-Policy" content="script-src self">'+self.html)
        with self.assertRaisesRegex(ValueError,'CSP'):self.l.extract()
    def test_subpage_and_remote(self):
        (self.root/'index.html').write_text(self.html+'<iframe src="child.html"></iframe><iframe src="https://example.invalid"></iframe>')
        (self.root/'child.html').write_text('<body>Child text</body>');r=self.l.extract();self.assertEqual(len(r['pages']),2);self.assertTrue(any('远程' in x for x in r['warnings']))
    def test_overlay_collision_refused(self):
        (self.root/P.OVERLAY).write_text('third party')
        with self.assertRaisesRegex(ValueError,'占用'):self.l.preview(self.l.extract(),{'Apply':'应用'})
    def test_cancel_before_write(self):
        with self.assertRaisesRegex(RuntimeError,'取消'):self.l.apply(self.l.extract(),{'Apply':'应用'},cancelled=lambda:True)
        self.assertEqual((self.root/'index.html').read_text(),self.html)
    def test_transaction_record_precedes_target_write(self):
        import panel_files
        original=panel_files.atomic_bytes;observed=[]
        def put(path,raw):
            if Path(path).parent==self.root:observed.append(self.l.files.state()['status'])
            return original(path,raw)
        with patch('panel_files.atomic_bytes',side_effect=put):self.l.apply(self.l.extract(),{'Apply':'应用'})
        self.assertEqual(observed,['prepared','prepared'])
    def test_failed_second_write_rolls_back(self):
        import panel_files
        original=panel_files.atomic_bytes
        def put(path,raw):
            if Path(path).name==P.OVERLAY:raise PermissionError('fixture')
            return original(path,raw)
        with patch('panel_files.atomic_bytes',side_effect=put):
            with self.assertRaises(PermissionError):self.l.apply(self.l.extract(),{'Apply':'应用'})
        self.assertEqual((self.root/'index.html').read_text(),self.html)
    def test_pending_transaction_restore(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});r=self.l.files.state();r['status']='prepared';P.write_json_atomic(self.l.files.record,r)
        with self.assertRaisesRegex(ValueError,'未完成'):self.l.extract()
        self.l.restore();self.assertEqual((self.root/'index.html').read_text(),self.html)
    def test_corrupt_dictionary_preserved(self):
        self.l.home.mkdir(parents=True);p=self.l.home/'dictionary.json';p.write_text('{bad')
        with self.assertRaises(ValueError):self.l.save({'Apply':'应用'})
        self.assertEqual(p.read_text(),'{bad')
    def test_long_and_placeholder_validation(self):
        self.assertEqual(P.validate_text('Hello {name}','你好 {name}'+ '长'*200)[0:2],'你好')
        for v in ('你好','你好 {other}','bad\0'):
            with self.assertRaises(ValueError):P.validate_text('Hello {name}',v)
    def test_bom_encoding_roundtrip(self):
        for raw in (codecs.BOM_UTF8+b'Hello',codecs.BOM_UTF16_LE+'Hello'.encode('utf-16-le'),codecs.BOM_UTF16_BE+'Hello'.encode('utf-16-be')):
            s,e,b=P.decode(raw);self.assertEqual(P.encode(s,e,b),raw)

class PlaintextHook(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'original.jsxbin';self.source.write_bytes(b'@JSXBIN@ES@fixture')
        self.output=self.root/'plain.jsx';self.text='var w=new Window("palette","Title");'
    def test_explicit_decoder_creates_independent_target(self):
        before=self.source.read_bytes();received=[]
        def decoder(raw):received.append(raw);return self.text
        target=P.prepare_plaintext_script(self.source,self.output,decoder=decoder)
        self.assertEqual(received,[before]);self.assertEqual(target.kind,'jsx');self.assertEqual(self.output.read_text(),self.text);self.assertEqual(self.source.read_bytes(),before)
        localizer=P.Localizer(target,self.root/'data');localizer.apply(localizer.extract(),{'Title':'标题'});localizer.restore()
        self.assertEqual(self.output.read_text(),self.text);self.assertEqual(self.source.read_bytes(),before)
    def test_no_implicit_decoder_or_jsxbin_bypass(self):
        with self.assertRaisesRegex(ValueError,'JSXBIN'):P.prepare_plaintext_script(self.source,self.output)
        self.assertFalse(self.output.exists())
    def test_existing_plaintext_bytes_preserve_bom_and_newlines(self):
        source=self.root/'already.jsx';raw=codecs.BOM_UTF16_LE+(self.text+'\r\n').encode('utf-16-le');source.write_bytes(raw)
        P.prepare_plaintext_script(source,self.output);self.assertEqual(self.output.read_bytes(),raw)
    def test_reject_overwrite_source_or_existing_copy_before_callback(self):
        callback=lambda raw:self.fail('must not invoke decoder')
        with self.assertRaises(ValueError):P.prepare_plaintext_script(self.source,self.source,decoder=callback)
        self.output.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError,'已存在'):P.prepare_plaintext_script(self.source,self.output,decoder=callback)
        self.assertEqual(self.output.read_bytes(),b'existing')
    def test_racing_output_is_not_overwritten(self):
        def decoder(raw):self.output.write_bytes(b'created externally');return self.text
        with self.assertRaises(FileExistsError):P.prepare_plaintext_script(self.source,self.output,decoder=decoder)
        self.assertEqual(self.output.read_bytes(),b'created externally');self.assertEqual(list(self.root.glob('.ael-plaintext-*')),[])
    def test_callback_failure_leaves_no_copy(self):
        def decoder(raw):raise RuntimeError('decoder failed')
        with self.assertRaisesRegex(RuntimeError,'decoder failed'):P.prepare_plaintext_script(self.source,self.output,decoder=decoder)
        self.assertFalse(self.output.exists())
    def test_cancel_before_and_after_callback_does_not_export(self):
        with self.assertRaisesRegex(RuntimeError,'取消'):P.prepare_plaintext_script(self.source,self.output,decoder=lambda raw:self.fail(),cancelled=lambda:True)
        stopped=[]
        def decoder(raw):stopped.append(True);return self.text
        with self.assertRaisesRegex(RuntimeError,'取消'):P.prepare_plaintext_script(self.source,self.output,decoder=decoder,cancelled=lambda:bool(stopped))
        self.assertFalse(self.output.exists())
    def test_invalid_decoded_output_keeps_guards(self):
        for result in (None,b'',b'@JSXBIN@ES@',b'eval("code");',b'var x=<xml/>;',b'function f(){'):
            with self.subTest(result=result),self.assertRaises(ValueError):P.prepare_plaintext_script(self.source,self.output,decoder=lambda raw:result)
            self.assertFalse(self.output.exists())
    def test_output_size_limit(self):
        with patch.object(P,'MAX_FILE',40),self.assertRaisesRegex(ValueError,'上限'):P.prepare_plaintext_script(self.source,self.output,decoder=lambda raw:'文'*20)
        self.assertFalse(self.output.exists())
    def test_no_output_when_temporary_write_fails(self):
        with patch.object(P.os,'fsync',side_effect=OSError('disk error')),self.assertRaises(OSError):P.prepare_plaintext_script(self.source,self.output,decoder=lambda raw:self.text)
        self.assertFalse(self.output.exists());self.assertEqual(list(self.root.glob('.ael-plaintext-*')),[])

class JSX(unittest.TestCase):
    def labels(self,s):return [r['text'] for r in J.analyse(s)[0]]
    def test_window_control_assignment(self):
        s='var w=new Window("palette","My panel");var b=w.add("button",undefined,"Apply");b.helpTip="Apply changes";var logic="Apply";'
        sites,_=J.analyse(s);self.assertEqual([x['text'] for x in sites],['My panel','Apply','Apply changes'])
        out=J.apply(s,sites,{'Apply':'应用'});self.assertIn('var logic="Apply"',out);self.assertIn('undefined,"\\u5e94\\u7528"',out)
    def test_constants_used_only_in_ui(self):
        s='var caption="Apply";var w=new Window("palette","Title");w.add("button",undefined,caption);'
        self.assertIn('Apply',self.labels(s));self.assertNotIn('Apply',self.labels(s+'if(caption=="Apply"){}'))
    def test_text_used_in_logic_excluded(self):
        s='var w=new Window("palette","Title");var b=w.add("button",undefined,"Apply");if(b.text=="Apply"){}'
        self.assertNotIn('Apply',self.labels(s))
    def test_unknown_receiver_excluded(self):self.assertEqual(self.labels('obj.add("button",null,"Business");obj.text="Business";'),[])
    def test_callback_this_text_computed_and_escape_refused(self):
        base='var w=new Window("palette","Title");var b=w.add("button",null,"Apply");'
        for suffix in ('b.onClick=function(){if(this.text=="Apply")doWork();};','if(b["text"]=="Apply"){}','unknown(b);','var alias=b;'):
            self.assertEqual(self.labels(base+suffix),[])
    def test_external_include_refused(self):
        with self.assertRaisesRegex(ValueError,'外部代码'):J.analyse('#include "library.jsx"\nvar w=new Window("palette","Title");')
    def test_static_resource(self):
        s='var w=new Window("palette","Title");var g=w.add("group { b: Button { text:\'Apply\', helpTip:\'Apply changes\' } }");'
        sites,_=J.analyse(s);self.assertIn('Apply',self.labels(s));out=J.apply(s,sites,{'Apply':'应用'});self.assertIn('Apply changes',out);J.analyse(out)
        resource=next(t.value for t in J.tokens(out) if t.string and t.value.startswith('group'))
        self.assertIn("text:'应用'",resource);self.assertNotIn('\\u5e94',resource)
    def test_dynamic_resource_skipped(self):
        s='var w=new Window("palette","Title");w.add("group {text:"+name+"}");'
        self.assertEqual(self.labels(s),['Title'])
    def test_non_executing_rejection(self):
        for s in ('eval("bad")','@JSXBIN@ES@','var x=<xml/>;','var w=new Window("palette","broken);'):
            with self.assertRaises(ValueError):J.analyse(s)
    def test_comments_preprocessor_unchanged(self):
        s='#target aftereffects\r\n// "Comment"\r\nvar w=new Window("palette","Title");'
        out=J.apply(s,J.analyse(s)[0],{'Title':'标题'});self.assertTrue(out.startswith('#target aftereffects\r\n// "Comment"\r\n'))
    def test_quotes_newline_unicode(self):
        s='var w=new Window("palette","Title");';out=J.apply(s,J.analyse(s)[0],{'Title':'标"题\n测试'});self.assertIn('标"题\n测试',[t.value for t in J.tokens(out) if t.string]);self.assertEqual(self.labels(out),[])
    def test_jsx_localizer_exact_restore(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sample.jsx';raw=b'#target aftereffects\r\nvar w=new Window("palette","Title");';p.write_bytes(raw);l=P.Localizer(P.inspect(p),Path(d)/'data');l.apply(l.extract(),{'Title':'标题'});self.assertNotEqual(p.read_bytes(),raw);l.restore();self.assertEqual(p.read_bytes(),raw)
    def test_division_and_comparisons_preserved(self):
        for expression in ('var n=width/2;','width/=2;','var n=(width+2)/4;','var n=xs[0]/2;','var n=count++/2;','var n=a<b;','var n=a<=b;'):
            with self.subTest(expression=expression):
                s=expression+'var w=new Window("palette","Title");'
                sites,_=J.analyse(s);self.assertEqual([x['text'] for x in sites],['Title'])
                self.assertEqual(J.apply(s,sites,{'Title':'标题'}),expression+'var w=new Window("palette","\\u6807\\u9898");')
    def test_regex_quotes_brackets_and_escapes_are_opaque(self):
        patterns=(r'/[)\]}"\x27]/g',r'/a\/b/i',r'/[\/]/',r'/\(\{\[/m',r'/eval\("text"\)/')
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                s='var r='+pattern+';var w=new Window("palette","Title");'
                sites,_=J.analyse(s);self.assertEqual([x['text'] for x in sites],['Title'])
                self.assertTrue(J.apply(s,sites,{'Title':'标题'}).startswith('var r='+pattern+';'))
    def test_regex_after_control_parenthesis_and_return(self):
        for prefix in ('if (ok) /[)]/.test(x);','while (ok) /[)]/.test(x);','function f(){return /[)]/;}','var x=ok ? /a/ : /b/;'):
            with self.subTest(prefix=prefix):self.assertEqual(self.labels(prefix+'var w=new Window("palette","Title");'),['Title'])
    def test_division_after_regex_and_nested_parenthesis(self):
        self.assertEqual(self.labels('var n=/a/.source.length/2;var n=((a+b))/2;var w=new Window("palette","Title");'),['Title'])
    def test_comment_and_string_dynamic_markers_do_not_refuse(self):
        for prefix in ('// eval("x") Function("x") @JSXBIN@\n','/* eval("x")\n#include "x" */\n','var doc="eval(x), Function(x), @JSXBIN@";'):
            with self.subTest(prefix=prefix):self.assertEqual(self.labels(prefix+'var w=new Window("palette","Title");'),['Title'])
    def test_real_dynamic_execution_and_aliases_still_refused(self):
        for tail in ('eval("code");','new Function("code");','var indirect=eval;','$.evalFile("other.jsx");'):
            with self.subTest(tail=tail),self.assertRaisesRegex(ValueError,'动态执行'):J.analyse('var w=new Window("palette","Title");'+tail)
    def test_regex_cannot_hide_control_text_dependency(self):
        s='var r=/[)]/;var w=new Window("palette","Title");var b=w.add("button",null,"Apply");if(r.test(b.text)){}'
        self.assertNotIn('Apply',self.labels(s))
    def test_ambiguous_regex_context_remains_refused(self):
        with self.assertRaisesRegex(ValueError,'上下文'):J.analyse('if(ok){} /foo/.test(x);')
        for keyword in ('break','continue','debugger','break label','continue label'):
            with self.subTest(keyword=keyword),self.assertRaisesRegex(ValueError,'上下文'):J.analyse(keyword+'\n/new Window("palette","Fake")/;')
    def test_for_each_body_regex_cannot_create_fake_labels(self):
        self.assertEqual(self.labels('for each (x in xs) /new Window("palette","Fake")/.test(x);var w=new Window("palette","Title");'),['Title'])
    def test_broken_or_unknown_regex_refused_with_line(self):
        for expression in ('var r=/unterminated;','var r=/[broken/;','var r=/x/gg;','var r=/x/u;'):
            with self.subTest(expression=expression),self.assertRaisesRegex(ValueError,'第 2 行'):J.analyse('// comment\n'+expression)
    def test_e4x_templates_and_encoded_payload_still_refused(self):
        for s in ('var x=<xml/>;','var x=< xml/>;','var x=<>text</>;','var x=`Template`;','@JSXBIN@ES@'):
            with self.subTest(s=s),self.assertRaises(ValueError):J.analyse(s)
    def test_unclosed_bracket_reports_opening_line(self):
        with self.assertRaisesRegex(ValueError,'括号未闭合.*第 2 行'):J.analyse('// comment\nfunction f(){\nvar n=1;')
    def test_regex_division_encoded_file_apply_restore(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sample.jsx';s='#target aftereffects\r\nvar r=/[)]/g; var n=10/2; var w=new Window("palette","Title");'
            raw=codecs.BOM_UTF16_LE+s.encode('utf-16-le');p.write_bytes(raw)
            l=P.Localizer(P.inspect(p),Path(d)/'data');l.apply(l.extract(),{'Title':'标题'})
            decoded,_,_=P.decode(p.read_bytes());self.assertEqual(decoded,J.apply(s,J.analyse(s)[0],{'Title':'标题'}))
            l.restore();self.assertEqual(p.read_bytes(),raw)

if __name__=='__main__':unittest.main()
class Oversized(unittest.TestCase):
    setUp=Panels.setUp;make=Panels.make
    def page(self,script):
        (self.root/'big.js').write_text(script,encoding='utf8')
        (self.root/'index.html').write_text('<body><button>Apply</button><script src="big.js"></script></body>',encoding='utf8');self.make()
    def test_oversized_script_skipped_with_warning(self):
        self.page('var a={label:"Big Label"};'+' '*400)
        with patch.object(P,'MAX_FILE',180):report=self.l.extract()
        self.assertEqual([r['text'] for r in report['terms']],['Apply']);self.assertIn('big.js',' '.join(report['warnings']))
        self.assertNotIn('big.js',report['hashes'])
        self.l.apply(report,{'Apply':'应用'});self.assertEqual((self.root/'big.js').read_text(encoding='utf8'),'var a={label:"Big Label"};'+' '*400)
    def test_script_within_limit_still_read(self):
        self.page('var a={label:"Big Label"};')
        self.assertIn('Big Label',[r['text'] for r in self.l.extract()['terms']])
    def test_oversized_page_names_the_file(self):
        self.page('');(self.root/'index.html').write_text('<body>'+'x'*400+'</body>',encoding='utf8')
        with patch.object(P,'MAX_FILE',180),self.assertRaisesRegex(ValueError,'index.html'):self.l.extract()
