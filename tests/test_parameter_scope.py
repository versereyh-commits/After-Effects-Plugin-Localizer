"""Selected-layer reads share the whole-process structure but never move the write shape."""
import copy,json,shutil,subprocess,unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import Mock,patch
import parameter_names as n

class ScopeTests(unittest.TestCase):
    def setUp(self):
        for field in ('_cache','_project_context','_shape_context','_scan_reason','_unsupported'):
            h=patch.dict(getattr(n,field),{},clear=True);h.start();self.addCleanup(h.stop)
        self.p=SimpleNamespace(pid=1,creation_time=2,ensure_alive=Mock());self.key=(1,2);self.param=n.identity('fx','p')
        self.reply={'readScope':'selected','projectToken':'A','selectionSnapshot':{'compId':3,'layers':[{'index':1,'id':4,'name':'One'}]},'parameters':[{'id':self.param,'name':'Fresh'}]}
        self.catalog=Mock();self.catalog.timings={'scanMs':0,'scanCount':0,'modulesMs':1,'matchPoolMs':1}
        self.catalog.rows.return_value=[{'id':self.param,'effectMatchName':'fx','matchName':'p','name':'old','address':12,'guid':'ab','parent':9}]
        for field,value in [('current_process',lambda **kw:nullcontext(self.p)),('_process_lock',lambda:nullcontext()),('ParameterCatalog',lambda p:self.catalog),('record_read_timing',lambda x:None)]:
            h=patch.object(n,field,value);h.start();self.addCleanup(h.stop)
    def warm(self,token='A'):
        """A structure cache from an earlier scan under this project."""
        n._cache[self.key]={98};n._project_context[self.key]=token;n._shape_context[self.key]=('global',)
    def read(self,reply=None,verify=None,project=None):
        first=copy.deepcopy(reply or self.reply);last=copy.deepcopy(first if verify is None else verify)
        whole=project or {'projectToken':first.get('projectToken'),'parameters':first.get('parameters',[])};self.calls=[]
        def native(p,operations,action='labels',scope=None,**kw):
            self.calls.append(action if scope else 'project')
            return copy.deepcopy((first if action=='catalog' else last) if scope=='selected' else whole)
        with patch.object(n,'run_native',side_effect=native):return n.read_parameters(scope='selected')
    def test_latest_selected_names_counts_and_mixed_state(self):
        self.reply['parameters']*=2;self.reply['parameters'][1]={'id':self.param,'name':'Other'}
        result=self.read();row=result['parameters'][0]
        self.assertEqual((row['name'],row['instances'],row['mixedNames']),('Fresh',2,True));self.assertNotIn('address',row)
        self.assertEqual(result['readScope'],'selected')
    def test_warm_read_reuses_structure_without_moving_write_shape(self):
        self.warm();self.read()
        self.assertEqual(n._cache[self.key],{98});self.assertEqual(n._shape_context[self.key],('global',));self.assertEqual(n._project_context[self.key],'A')
        self.assertNotIn('project',self.calls);self.catalog.rows.assert_called_once_with(False,None)
    def test_same_selection_reuses_cache_with_new_names(self):
        self.warm();self.read();self.reply['parameters'][0]['name']='Newest'
        self.assertEqual(self.read()['parameters'][0]['name'],'Newest');self.assertEqual(n._cache[self.key],{98})
    def test_other_layers_comps_and_counts_reuse_the_structure(self):
        for variant in ('layer','comp','count'):
            self.warm();modified=copy.deepcopy(self.reply);self.catalog.rows.reset_mock()
            if variant=='layer':modified['selectionSnapshot']['layers'][0]['id']=5
            elif variant=='comp':modified['selectionSnapshot']['compId']=9
            else:modified['parameters']*=2
            self.read(modified);self.assertEqual(n._cache[self.key],{98});self.catalog.rows.assert_called_once_with(False,None);self.assertNotIn('project',self.calls)
    def test_project_switch_or_cold_start_takes_project_shape_before_scanning(self):
        self.warm();modified=copy.deepcopy(self.reply);modified['projectToken']='B'
        self.read(modified);self.assertIn('project',self.calls);self.assertNotIn(self.key,n._cache);self.assertEqual(n._project_context[self.key],'B')
        n._cache.clear();n._project_context.clear();n._shape_context.clear()
        self.read();self.assertEqual(self.calls[1],'project');self.assertEqual(n._shape_context[self.key],n._name_signature({self.param:['Fresh']}))
    def test_restarted_host_never_uses_old_structure(self):
        self.warm();self.p.creation_time+=1;self.read();self.assertIn('project',self.calls);self.assertIn(self.key,n._cache)
    def test_new_parameter_type_scans_once_after_taking_project_shape(self):
        self.warm();other=n.identity('new fx','q');self.reply['parameters'].append({'id':other,'name':'Q'})
        base=list(self.catalog.rows.return_value)
        found=base+[{'id':other,'effectMatchName':'new fx','matchName':'q','name':'Q','address':13,'guid':'cd','parent':10}]
        self.catalog.rows.side_effect=lambda rescan,progress:found if rescan else base
        result=self.read();self.assertEqual(len(result['parameters']),2)
        self.assertEqual([c.args[0] for c in self.catalog.rows.call_args_list],[False,True]);self.assertEqual(self.calls.count('project'),1)
    def test_unsupported_type_is_remembered_after_a_scan(self):
        self.warm();self.reply['parameters'].append({'id':n.identity('odd','x'),'name':'X'})
        self.read();self.assertEqual([c.args[0] for c in self.catalog.rows.call_args_list],[False,True])
        self.catalog.rows.reset_mock();result=self.read();self.catalog.rows.assert_called_once_with(False,None);self.assertEqual(len(result['parameters']),1)
    def test_unsupported_parameter_of_a_known_effect_never_scans(self):
        # Every effect has built-in groups the structure never lists; a seen type is covered.
        self.warm();self.reply['parameters'].append({'id':n.identity('fx','ADBE Effect Built In Params'),'name':'B'})
        other=copy.deepcopy(self.reply);other['selectionSnapshot']['layers'][0]['id']=7
        for reply in (self.reply,other):
            self.catalog.rows.reset_mock();result=self.read(reply);self.catalog.rows.assert_called_once_with(False,None);self.assertEqual(len(result['parameters']),1)
    def test_selection_or_project_change_during_read_rejects_but_keeps_structure(self):
        for field in ('selection','project'):
            self.warm();latest=copy.deepcopy(self.reply)
            if field=='selection':latest['selectionSnapshot']['layers']=[]
            else:latest['projectToken']='B'
            latest['parameters']=[]
            with self.assertRaisesRegex(RuntimeError,'发生变化'):self.read(verify=latest)
            self.assertEqual(n._cache[self.key],{98})
    def test_empty_cases_never_construct_catalog_or_expand_scope(self):
        for comp,layers,reason in [(None,[],'noActiveComp'),(3,[],'noSelectedLayers'),(3,self.reply['selectionSnapshot']['layers'],'noSupportedParameters')]:
            response=copy.deepcopy(self.reply);response.update(parameters=[],selectionSnapshot={'compId':comp,'layers':layers})
            with patch.object(n,'ParameterCatalog') as catalog:
                result=self.read(response);catalog.assert_not_called()
            self.assertEqual(result['emptyReason'],reason);self.assertEqual(result['timings']['scanCount'],0)
    def test_unsupported_rows_stay_hidden(self):
        self.catalog.rows.return_value=[];self.assertEqual(self.read()['emptyReason'],'noSupportedParameters')
    def test_invalid_scope_rejected_before_host_access(self):
        with patch.object(n,'current_process') as p:
            with self.assertRaises(ValueError):n.read_parameters(scope='unknown')
            p.assert_not_called()
    def test_bad_snapshot_or_scope_never_accepted(self):
        for field,value in [('selectionSnapshot',{}),('readScope','project'),('projectToken','')]:
            response=copy.deepcopy(self.reply);response[field]=value
            with self.assertRaises(RuntimeError):self.read(response)
    def test_scope_cannot_be_passed_to_write_transport(self):
        with self.assertRaises(ValueError):n.run_native(self.p,[],scope='selected',action='labels')

RUNNER=r'''
const fs=require('fs'),vm=require('vm'),q=JSON.parse(fs.readFileSync(0,'utf8'));let files={},visits=[],selectionReads=0;
function File(path){this.fsName=path;this.exists=true;this.open=()=>true;this.write=t=>files[path]=t;this.close=()=>{};}
function CompItem(){}
function layer(index,count,comp){const fxs=Array.from({length:count},()=>({matchName:'fx',numProperties:1,property:()=>({matchName:'p',name:'name'+index})}));return {id:q.oldHost?undefined:comp*10+index,index,name:'layer'+index,property:()=>{visits.push([comp,index]);return {numProperties:fxs.length,property:i=>fxs[i-1]};}};}
const one=[layer(1,1,1),layer(2,2,1),layer(3,0,1)],two=[layer(1,1,2)];
const first=Object.assign(new CompItem(),{id:1,numLayers:3,layer:i=>one[i-1]}),second=Object.assign(new CompItem(),{id:2,numLayers:1,layer:i=>two[i-1]});
Object.defineProperty(first,'selectedLayers',{get:()=>{selectionReads++;return q.change&&selectionReads>1?[one[2]]:q.none?[]:q.multi?[one[1],one[0]]:q.empty?[one[2]]:[one[1]];}});
const app={version:'24.2.1x2',project:{activeItem:q.noComp?null:first,file:{fsName:'QA.aep'},numItems:2,item:i=>[first,second][i-1],renderQueue:{rendering:false}}};
const request={action:q.selectionOnly?'selection':'catalog',readScope:q.project?'project':'selected',requestId:'test',hostVersion:'24.2.1x2',permit:'permit',output:'out',expires:Date.now()+60000,operations:[]};
vm.runInNewContext(q.template.replace('@@REQUEST@@',JSON.stringify(request)),{app,File,CompItem,ExternalObject:()=>{throw Error('unexpected native write');}},{timeout:3000});
process.stdout.write(JSON.stringify({result:JSON.parse(files.out),visits}));
'''

@unittest.skipUnless(shutil.which('node'),'Node required for actual JSX isolation')
class ScopeScriptTests(unittest.TestCase):
    def run_script(self,**options):
        template=(Path(__file__).resolve().parents[1]/'scripts/parameter_bridge.jsx').read_text(encoding='utf8')
        result=subprocess.run([shutil.which('node'),'-e',RUNNER],input=json.dumps(dict(template=template,**options)),text=True,encoding='utf8',capture_output=True,check=True)
        return json.loads(result.stdout)
    def test_selected_visits_only_selected_layer_and_counts_duplicates(self):
        r=self.run_script();self.assertEqual(r['visits'],[[1,2]]);self.assertEqual(len(r['result']['parameters']),2)
    def test_multi_selection_sorted_and_whole_project_includes_other_comps(self):
        r=self.run_script(multi=True);self.assertEqual(r['visits'],[[1,1],[1,2]]);self.assertEqual(len(r['result']['parameters']),3)
        r=self.run_script(project=True);self.assertEqual(len(r['result']['parameters']),4);self.assertIn([2,1],r['visits'])
    def test_empty_and_no_selection_never_fall_back(self):
        for options in ({'none':True},{'noComp':True},{'empty':True}):
            r=self.run_script(**options);self.assertEqual(r['result']['parameters'],[]);self.assertNotIn([2,1],r['visits'])
    def test_selection_change_is_rejected_in_bridge(self):
        self.assertIn('发生变化',self.run_script(change=True)['result']['error'])
    def test_snapshot_only_does_not_enumerate_effects_and_old_host_uses_index(self):
        r=self.run_script(selectionOnly=True,oldHost=True);self.assertEqual(r['visits'],[]);self.assertIsNone(r['result']['selectionSnapshot']['layers'][0]['id'])
