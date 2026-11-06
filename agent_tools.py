"""What the assistant can do in the program: every tool it is offered, with its permission level. Tools reach the window
through the same functions as its buttons, so the write guards, journals and busy checks stay exactly as they are.
Anything touching widgets runs on the main thread (host.on_main); the agent thread only waits."""
from __future__ import annotations
import re
from pathlib import Path
from agent_core import Tool
import agent_files,patch_check,patch_loader
from core import readable_category
from live_names import encode_name
from llm_client import CATEGORY_PROMPT,PARAMETER_PROMPT,SYSTEM_PROMPT,TranslationError,translate_names
from parameter_names import validate_label
from popup_names import POPUP_PROMPT,validate_option
from runtime import asset_root

def help_path()->Path:
    bundled=asset_root()/'assistant_help.md'
    return bundled if bundled.is_file() else Path(__file__).resolve().parent/'assistant_help.md'
def help_sections()->list[tuple[str,str]]:
    """The help document split at its ## headings, ### kept inside."""
    try:text=help_path().read_text(encoding='utf8')
    except OSError:return []
    sections=[];title=None;body=[]
    for line in text.splitlines():
        if line.startswith('## '):
            if title:sections.append((title,'\n'.join(body).strip()))
            title=line[3:].strip();body=[]
        elif title:body.append(line)
    if title:sections.append((title,'\n'.join(body).strip()))
    return sections
def help_outline()->str:return '、'.join(title for title,_ in help_sections())
def help_search(query:str,limit:int=3)->list[dict]:
    """Sections ranked by how many of the query's words and two-character runs they contain; titles count more."""
    query=query.casefold();terms=set(re.findall(r'[a-z0-9_.+-]+',query))
    for run in re.findall(r'[一-鿿]+',query):
        terms.add(run);terms.update(run[i:i+2] for i in range(len(run)-1))
    scored=[]
    for title,body in help_sections():
        low=body.casefold();name=title.casefold();score=sum(low.count(t)+name.count(t)*5 for t in terms)
        if score:scored.append((score,title,body))
    scored.sort(key=lambda item:-item[0])
    return [{'section':title,'text':body} for _,title,body in scored[:limit]]

def page_rows(items,args):
    offset=max(0,args.get('offset',0));limit=min(max(1,args.get('limit',50)),200)
    return {'total':len(items),'offset':offset,'items':items[offset:offset+limit]}
def matches(query,*values)->bool:
    return not query or query.casefold() in ' '.join(str(v or '') for v in values).casefold()

class AppTools:
    def __init__(self,window,host):
        self.w=window;self.host=host;self.tools=self.build()
    def main(self,fn):return self.host.on_main(fn)
    # Effects
    def effect_rows(self):
        w=self.w;names={r['matchName']:r['displayName'] for r in (w.live or {}).get('effects',[])};seen=set();rows=[]
        for p in w.plugins:
            if p.match_name in seen:continue
            seen.add(p.match_name);zh=w.pending.get(p.match_name,'');current=names.get(p.match_name)
            state='未连接' if not w.live else '未加载' if current is None else '待翻译' if not zh.strip() else '已应用' if current==zh else '待应用'
            rows.append({'matchName':p.match_name,'name':p.display_name,'category':readable_category(p.category or '').strip(),'candidate':zh,'checked':p.match_name in w.enabled,'currentInAE':current,'status':state,'favorite':p.match_name in w.favorites})
        return rows
    def list_effects(self,args):
        def go():
            rows=[r for r in self.effect_rows() if matches(args.get('query'),r['name'],r['matchName'],r['candidate'],r['category']) and (not args.get('category') or r['category']==args['category']) and (not args.get('status') or r['status']==args['status']) and (not args.get('checkedOnly') or r['checked'])]
            return page_rows(rows,args)
        return self.main(go)
    def known_effects(self):return {p.match_name for p in self.w.plugins}
    def set_effect_names(self,args):
        def go():
            w=self.w;self.host.snapshot();known=self.known_effects();done={};problems={}
            for key,text in args['names'].items():
                if key not in known:problems[key]='没有这个效果';continue
                if not isinstance(text,str):problems[key]='译名必须是文字';continue
                try:
                    if text.strip():encode_name(text)
                    w.pending[key]=text.strip();done[key]=text.strip()
                except ValueError as error:problems[key]=str(error)
            w.sync_rows(set(done));w.save_translations();w.selection_changed()
            return {'saved':len(done),'problems':problems}
        return self.main(go)
    def check_effects(self,args):
        def go():
            w=self.w;self.host.snapshot();rows=[r for r in self.effect_rows() if matches(args.get('query'),r['name'],r['matchName'],r['candidate'],r['category']) and (not args.get('category') or r['category']==args['category'])]
            keys={r['matchName'] for r in rows} if 'keys' not in args else set(args['keys'])&self.known_effects()
            if 'keys' not in args and not args.get('query') and not args.get('category'):raise ValueError('请给出 keys、query 或 category，避免误勾全部')
            if args['checked']:w.enabled.update(keys)
            else:w.enabled.difference_update(keys);w.drop_ticks(keys)
            w.sync_rows(keys);w.save_enabled();return {'changed':len(keys),'checkedTotal':len(w.enabled)}
        return self.main(go)
    def need_service(self):
        if not self.w.settings.ready():raise RuntimeError('翻译服务还没有设置好')
    def translate_effects(self,args):
        """The user's own wishes travel in instructions; replies get the usual per-name validation."""
        self.need_service();s=self.w.settings;w=self.w;instructions=args.get('instructions','').strip()
        if not any(args.get(name) for name in ('keys','query','category','checkedOnly')):raise ValueError('请用 checkedOnly、keys、query 或 category 指明要翻译哪些效果')
        def setup():
            self.host.snapshot();rows=[r for r in self.effect_rows() if (r['matchName'] in args['keys'] if args.get('keys') else matches(args.get('query'),r['name'],r['matchName'],r['category']) and (not args.get('category') or r['category']==args['category'])) and (not args.get('checkedOnly') or r['checked'])]
            if not args.get('overwrite',True):rows=[r for r in rows if not r['candidate'].strip()]
            return rows
        rows=self.main(setup)
        if not rows:return {'translated':0,'note':'没有符合条件的效果'}
        if len(rows)>500 and not args.get('allowLarge'):raise ValueError(f'一次要翻译 {len(rows)} 个效果，数量很大，请先和用户确认后带 allowLarge=true 重试')
        items=[{'id':r['matchName'],'sourceName':r['name'],'category':r['category']} for r in rows]
        prompt=SYSTEM_PROMPT+('\n用户的翻译要求（只影响译名用词，不改变输出格式和长度限制）：'+instructions if instructions else '')
        def work(progress):
            try:return translate_names(items,s.key,s.base_url,s.model,progress=lambda done,total:progress(f'AI 翻译中……{done}/{total}'),cancelled=self.host.cancelled,service=s.service,usage=w.record_usage,system_prompt=prompt),'',{}
            except TranslationError as failure:return failure.partial,str(failure),{}
        box={}
        def done(result):box['names']=dict(result[0]);box['error']=result[1];w.translated(result)
        outcome=self.host.ui_job(lambda:w.work(work,done))
        names=box.get('names',{})
        return {'translated':len(names),'error':box.get('error',''),'samples':dict(list(names.items())[:40]),**outcome}
    def apply_effects(self,args):
        w=self.w
        def go():
            self.need_live();keys=args.get('keys');picked=keys if keys is not None else sorted(w.enabled)
            values={k:w.pending[k] for k in picked if w.pending.get(k,'').strip()}
            if not values:raise ValueError('这些效果还没有中文候选')
            w.status.setText(f'AI 正在应用 {len(values)} 个名称……');w.writing=set(values);w.work(lambda progress:w.apply_names(values),w.applied)
        return self.host.ui_job(go)
    def effects_question(self,args):
        w=self.w;keys=args.get('keys');picked=keys if keys is not None else sorted(w.enabled);named=[k for k in picked if w.pending.get(k,'').strip()]
        return f'把 {len(named)} 个效果的中文名写入 AE？图层上的默认效果标题也会同步。之后可以恢复。'
    def restore_effects(self,args):
        w=self.w
        def go():
            self.need_live();keys=args.get('keys');with_live=self.w.assistant_env['with_live']
            w.work(lambda progress:with_live(w.restore_effect_names(keys),w.space.exe),w.restored)
        return self.host.ui_job(go)
    def need_live(self):
        if not self.w.space.live:raise RuntimeError(f'{self.w.space.label} 还没有通过适配，不能实时写入')
        if not self.w.live:raise RuntimeError('还没有连接 AE，请先 connect_ae')
    def restore_all(self,args):
        self.need_live();return self.host.ui_job(self.w.restore_all)
    def connect_ae(self,args):
        if not self.w.space.live:raise RuntimeError('当前 AE 版本还没有适配：请用户在“AE 版本”页做“自动识别与适配”')
        return self.host.ui_job(self.w.connect_live)
    def refresh_ae_list(self,args):
        self.need_live();return self.host.ui_job(self.w.refresh_native)
    # Categories
    def list_categories(self,args):
        def go():
            w=self.w;groups={}
            for r in self.effect_rows():groups.setdefault(r['category'],[]).append(r)
            return page_rows([{'category':g,'candidate':w.category_pending.get(g,''),'effects':len(rows),'checked':g in w.ticked} for g,rows in sorted(groups.items())],args)
        return self.main(go)
    def set_category_names(self,args):
        def go():
            w=self.w;self.host.snapshot();saved=0;problems={}
            for group,text in args['names'].items():
                try:
                    if text.strip():encode_name(text)
                    w.category_pending[group]=text.strip();saved+=1
                except (ValueError,AttributeError) as error:problems[group]=str(error)
            w.save_categories();w.paint_groups();return {'saved':saved,'problems':problems}
        return self.main(go)
    def translate_categories(self,args):
        self.need_service();s=self.w.settings;w=self.w;instructions=args.get('instructions','').strip()
        def setup():
            self.host.snapshot();groups={}
            for r in self.effect_rows():groups.setdefault(r['category'],[]).append(r['name'])
            return [{'id':g,'sourceName':g,'category':'该分类下的效果：'+'、'.join(names[:8])} for g,names in groups.items() if g and ('categories' not in args or g in args['categories'])]
        items=self.main(setup)
        if not items:return {'translated':0}
        prompt=CATEGORY_PROMPT+('\n用户的翻译要求（只影响译名用词）：'+instructions if instructions else '')
        def work(progress):
            try:return {},'',translate_names(items,s.key,s.base_url,s.model,cancelled=self.host.cancelled,service=s.service,usage=w.record_usage,system_prompt=prompt)
            except TranslationError as failure:return {},str(failure),failure.partial
        box={}
        def done(result):box.update(names=result[2],error=result[1]);w.translated(result)
        outcome=self.host.ui_job(lambda:w.work(work,done));names=box.get('names',{})
        return {'translated':len(names),'error':box.get('error',''),'names':dict(list(names.items())[:40]),**outcome}
    def apply_categories(self,args):
        self.need_live();return self.host.ui_job(lambda:self.w.apply_categories(args['categories']))
    def restore_categories(self,args):
        self.need_live();return self.host.ui_job(lambda:self.w.restore_categories(args.get('categories')))
    # Parameters and dropdown choices
    def parameter_rows(self):
        page=self.w.parameter_page;rows=[]
        for i,r in enumerate(page.rows):
            if page.is_option(r):rows.append({'id':r['id'],'kind':'下拉选项','name':r['source'],'parameter':r['popup']['name'],'effect':r['popup']['effectMatchName'],'candidate':page.candidate(r),'checked':page.is_checked(r),'status':page.row_status(i),'byteBudget':r['popup']['capacity']})
            else:rows.append({'id':r['id'],'kind':'参数','name':r['name'],'effect':r['effectMatchName'],'candidate':page.candidate(r),'checked':page.is_checked(r),'status':page.row_status(i)})
        return rows
    def list_parameters(self,args):
        def go():
            rows=[r for r in self.parameter_rows() if matches(args.get('query'),r['name'],r['effect'],r['candidate'],r.get('parameter')) and (not args.get('effect') or r['effect']==args['effect']) and (not args.get('status') or r['status']==args['status'])]
            out=page_rows(rows,args)
            if not self.w.parameter_page.rows:out['note']='参数页还没有读取，请先 read_parameters（需要已连接 AE），或用 list_parameter_records 查已保存的译名'
            return out
        return self.main(go)
    def list_parameter_records(self,args):
        def go():
            w=self.w;rows=[{'id':k,'candidate':v,'checked':k in w.parameter_enabled} for k,v in sorted(w.parameter_pending.items()) if v and matches(args.get('query'),k,v)]
            return page_rows(rows,args)
        return self.main(go)
    def read_parameters(self,args):
        w=self.w;page=w.parameter_page
        def go():
            self.need_live();scope=args.get('scope','selected')
            if scope not in ('selected','project'):raise ValueError('scope 只能是 selected 或 project')
            if scope!=page.read_scope:page.set_read_scope(scope)
            page.load()
        outcome=self.host.ui_job(go)
        return {**outcome,'rows':self.main(lambda:len(page.rows)),'pageStatus':self.main(lambda:page.status.text())}
    def set_parameter_names(self,args):
        def go():
            w=self.w;page=w.parameter_page;self.host.snapshot();options={r['id']:r for r in page.rows if page.is_option(r)};saved=0;problems={};choice_store=None
            for key,text in args['names'].items():
                if not isinstance(text,str):problems[key]='译名必须是文字';continue
                try:
                    if key in options:
                        choice_store=page.popup_stores()[0];choice_store.values[key]=validate_option(options[key]['source'],text) if text.strip() else ''
                    else:w.parameter_pending[key]=validate_label(text) if text.strip() else ''
                    saved+=1
                except ValueError as error:problems[key]=str(error)
            w.save_parameters()
            if choice_store:choice_store.save()
            page.refresh_values();return {'saved':saved,'problems':problems}
        return self.main(go)
    def check_parameters(self,args):
        def go():
            w=self.w;page=w.parameter_page;self.host.snapshot();rows={r['id']:r for r in page.rows}
            keys=set(rows) if args.get('all') else set(args.get('ids',()))
            if not keys:raise ValueError('请给出 ids，或 all=true')
            for key in keys:
                if key in rows:page.mark(rows[key],args['checked'])
                elif args['checked']:w.parameter_enabled.add(key)
                else:w.parameter_enabled.discard(key)
            page.save_checks();page.refresh_values();return {'changed':len(keys)}
        return self.main(go)
    def translate_parameters(self,args):
        self.need_service();w=self.w;page=w.parameter_page;s=w.settings;instructions=args.get('instructions','').strip()
        def setup():
            self.host.snapshot();wanted=set(args['ids']) if 'ids' in args else None;labels=[];choices=[]
            for r in page.rows:
                if wanted is not None and r['id'] not in wanted:continue
                if not args.get('overwrite',True) and page.candidate(r).strip():continue
                if not (wanted is not None or page.is_checked(r) or args.get('all')):continue
                if page.is_option(r):choices.append({'id':r['id'],'sourceName':r['source'],'category':f"{r['popup']['effectMatchName']} / {r['popup']['name']}；整组选项预算 {r['popup']['capacity']} {r['popup'].get('encoding','utf8')}字节（含分隔符）"})
                else:labels.append({'id':r['id'],'sourceName':r['name'],'category':'所属效果 '+r['effectMatchName']})
            return labels,choices
        labels,choices=self.main(setup)
        if not labels and not choices:return {'translated':0,'note':'没有符合条件的参数：先读取参数并勾选，或传 ids / all=true'}
        extra='\n用户的翻译要求（只影响用词，不改变输出格式和长度限制）：'+instructions if instructions else ''
        def work(progress):
            errors=[];names={};options={}
            if labels:
                try:names=translate_names(labels,s.key,s.base_url,s.model,progress=lambda d,t:progress(f'AI 翻译参数……{d}/{t}'),cancelled=self.host.cancelled,service=s.service,usage=w.record_usage,system_prompt=PARAMETER_PROMPT+extra)
                except TranslationError as failure:names=failure.partial;errors.append(str(failure))
            if choices:
                try:options=translate_names(choices,s.key,s.base_url,s.model,progress=lambda d,t:progress(f'AI 翻译下拉选项……{d}/{t}'),cancelled=self.host.cancelled,service=s.service,usage=w.record_usage,system_prompt=POPUP_PROMPT+extra,_text_validator=validate_option)
                except TranslationError as failure:options=failure.partial;errors.append(str(failure))
            return names,options,errors
        box={}
        def done(result):
            names,options,errors=result;valid={};bad=[]
            for key,name in names.items():
                try:valid[key]=validate_label(name)
                except ValueError:bad.append(key)
            w.parameter_pending.update(valid);w.save_parameters();store=page.popup_stores()[0];store.values.update(options)
            if options:store.save()
            page.refresh_values();box.update(names=valid,options=options,errors=errors,tooLong=bad)
        outcome=self.host.ui_job(lambda:w.work(work,done))
        return {'translatedParameters':len(box.get('names',{})),'translatedChoices':len(box.get('options',{})),'errors':box.get('errors',[]),'tooLong':box.get('tooLong',[]),'samples':dict(list(box.get('names',{}).items())[:30]),**outcome}
    def apply_parameters(self,args):
        w=self.w;page=w.parameter_page
        def go():
            self.need_live();page.apply()
        outcome=self.host.ui_job(go);return {**outcome,'pageStatus':self.main(lambda:page.status.text())}
    def parameters_question(self,args):
        page=self.w.parameter_page;return f'把勾选的 {len(page.selected())} 个参数和 {len(page.selected_options())} 个下拉选项写入 AE？整个工程里同类效果的已加载实例都会改。之后可以恢复。'
    def restore_parameters(self,args):
        w=self.w;page=w.parameter_page
        def go():
            self.need_live()
            if args.get('all'):page.restore_all()
            else:page.restore()
        outcome=self.host.ui_job(go);return {**outcome,'pageStatus':self.main(lambda:page.status.text())}
    # Schemes, favourites, preferences, pages
    def list_schemes(self,args):
        def go():
            w=self.w;w.refresh_schemes(w.active.title if w.active else None)
            return {'active':w.active.title if w.active else None,'items':[{'title':s.title,'effects':len(s.names),'checked':len(s.enabled),'parameters':len((s.parameters or ({},))[0])} for s in w.schemes]}
        return self.main(go)
    def save_scheme_as(self,args):
        def go():
            w=self.w;path=w.free_path(args['title'].strip());w.write_scheme(path,args['title'].strip(),'已保存新');return {'saved':args['title'].strip()}
        return self.main(go)
    def load_scheme(self,args):
        def go():
            open_scheme=self.w.assistant_env['open_scheme']
            w=self.w;w.refresh_schemes(w.active.title if w.active else None);scheme=next((s for s in w.schemes if s.title==args['title']),None)
            if scheme is None:raise ValueError('没有这个方案')
            self.host.snapshot();saved=open_scheme(scheme.path);w.load_scheme(saved.names,saved.enabled,saved.title,saved.parameters);w.refresh_schemes(saved.title);return {'loaded':saved.title}
        return self.main(go)
    def list_favorites(self,args):
        def go():
            f=self.w.favorites;return {'current':f.current,'folders':{name:list(items) for name,items in f.folders.items()}}
        return self.main(go)
    def set_favorites(self,args):
        def go():
            f=self.w.favorites;self.host.snapshot();name=args.get('folder') or f.current
            if args.get('folder') and f.find(name) is None:name=f.create(name)
            keys=[k for k in args['keys'] if k in self.known_effects()]
            if args['on']:count=f.add(f.find(name),keys)
            else:
                stored=f.folders[f.find(name)];count=sum(1 for k in keys if k in stored);f.folders[f.find(name)]=[k for k in stored if k not in keys]
            message=f.save()
            for listener in f.listeners:listener()
            return {'folder':name,'changed':count,'warning':message}
        return self.main(go)
    PREFERENCES={'dark_mode':'深色模式','motion':'界面动效','bilingual_names':'双语写入效果名','temporary_translation':'临时翻译（退出前恢复）','reapply_names':'AE 重启后自动重新应用效果名'}
    def set_preference(self,args):
        def go():
            w=self.w;on=args['value'];name=args['name']
            if name=='dark_mode':w.set_dark_mode(on)
            elif name=='motion':w.save_motion(on)
            elif name=='bilingual_names':w.set_bilingual(on)
            elif name=='temporary_translation':w.exit_restore.setChecked(on)
            elif name=='reapply_names':w.reapply_box.setChecked(on)
            else:raise ValueError('只能改：'+'、'.join(self.PREFERENCES))
            return {'set':self.PREFERENCES[name],'value':on}
        return self.main(go)
    def go_to_page(self,args):
        def go():
            from shortcuts import PAGES
            if args['page'] not in PAGES:raise ValueError('页面只能是：'+'、'.join(PAGES))
            self.w.go(PAGES.index(args['page']));return {'page':args['page']}
        return self.main(go)
    def app_status(self,args):
        def go():
            w=self.w;APP_VERSION=w.assistant_env['version'];from shortcuts import PAGES
            return {'appVersion':APP_VERSION,'aeVersion':w.space.label,'aeBuild':w.space.version,'adapted':bool(w.space.live),'connected':bool(w.live),'effectsLoadedInAE':len((w.live or {}).get('effects',[])),'effectsListed':len(w.plugins),'candidates':sum(1 for v in w.pending.values() if v),
                    'checkedEffects':len(w.enabled),'parameterRowsRead':len(w.parameter_page.rows),'parameterRecords':sum(1 for v in w.parameter_pending.values() if v),'activeScheme':w.active.title if w.active else None,'page':PAGES[w.pages.currentIndex()],'busy':w.busy(),
                    'translationService':w.settings.provider.name,'model':w.settings.model,'serviceReady':w.settings.ready(),'darkMode':__import__('ui_kit').is_dark(),'patchesActive':patch_loader.active()}
        return self.main(go)
    def list_versions(self,args):
        return self.main(lambda:[{'label':s.label,'build':s.version,'adapted':bool(s.live),'current':s is self.w.space,'path':s.exe} for s in self.w.spaces])
    def read_log(self,args):
        """The last lines of the program's log, with the home folder shortened."""
        def go():
            lines=self.w.log.toPlainText().splitlines()[-min(max(1,args.get('lines',60)),300):];home=str(Path.home())
            return [line.replace(home,'~') for line in lines]
        return self.main(go)
    def open_adapter(self,args):
        def go():
            self.w.go(self.w.pages.indexOf(self.w.pages.widget(3)));self.w.analyze_adapter(self.w.space);return {'opened':True,'note':'已打开适配窗口。启用前必须完成自检和人工核对，AI 不能替用户跳过。'}
        return self.main(go)
    def patch_status(self,args):
        APP_VERSION=self.w.assistant_env['version']
        return {'version':APP_VERSION,'patched':patch_loader.list_patches(APP_VERSION),'activeNow':patch_loader.active(),'modules':patch_loader.list_source(),'notPatchable':sorted(patch_loader.NOT_PATCHABLE),'safeMode':patch_loader.safe_mode,'awaitingConfirmation':patch_loader.trial_state(APP_VERSION).get('modules',[])}
    def read_source(self,args):
        APP_VERSION=self.w.assistant_env['version']
        text=patch_loader.read_source(args['module'],APP_VERSION);offset=max(0,args.get('offset',0));limit=min(args.get('limit',20000),40000)
        return {'module':args['module'],'length':len(text),'offset':offset,'text':text[offset:offset+limit]}
    def search_source(self,args):
        hits=[];pattern=args['text']
        for name in patch_loader.list_source():
            APP_VERSION=self.w.assistant_env['version']
            for number,line in enumerate(patch_loader.read_source(name,APP_VERSION).splitlines(),1):
                if pattern in line:
                    hits.append({'module':name,'line':number,'text':line.strip()[:240]})
                    if len(hits)>=60:return hits
        return hits
    def patch_module(self,args):
        APP_VERSION=self.w.assistant_env['version']
        agent_files.log_change('patch',patch_loader.folder(APP_VERSION)/f"{args['module']}.py")
        return patch_loader.apply_patch(args['module'],APP_VERSION,args['old'],args['new'],check=patch_check.preflight)
    def remove_patch(self,args):
        APP_VERSION=self.w.assistant_env['version']
        agent_files.log_change('unpatch',patch_loader.folder(APP_VERSION)/f"{args['module']}.py")
        return {'removed':patch_loader.remove_patch(args['module'],APP_VERSION),'note':'重启程序后生效'}
    def undo(self,args):return self.main(self.host.undo)

    def build(self)->list[Tool]:
        S=('string','');query=('string','搜索文字',False);limit=('integer','每页条数，默认 50，最多 200',False);offset=('integer','从第几条开始',False)
        keys=('array','效果内部标识 matchName 列表',False);instructions=('string','用户对译法的要求，例如“按影视后期习惯，不要音译”',False)
        return [
            Tool('app_status','程序当前状态：版本、AE 是否连接、是否适配、当前页面、翻译服务等。',self.app_status),
            Tool('help_search','查程序帮助文档，回答功能和报错问题前先查。',lambda a:help_search(a['query']),params={'query':('string','问题或关键词',True)},kind='help'),
            Tool('list_effects','列出当前 AE 版本的效果及其中文候选、勾选和状态。status 可为 待翻译/待应用/已应用/未加载/未连接。',self.list_effects,params={'query':query,'category':('string','分类名',False),'status':('string','状态',False),'checkedOnly':('boolean','只看勾选的',False),'limit':limit,'offset':offset}),
            Tool('list_categories','列出效果分类、中文候选和效果数量。',self.list_categories,params={'limit':limit,'offset':offset}),
            Tool('list_parameters','列出参数页已读取的参数和下拉选项。',self.list_parameters,params={'query':query,'effect':('string','效果内部标识',False),'status':('string','待翻译/待应用/已应用',False),'limit':limit,'offset':offset}),
            Tool('list_parameter_records','列出当前 AE 版本保存过的参数译名，不需要连接 AE。',self.list_parameter_records,params={'query':query,'limit':limit,'offset':offset}),
            Tool('list_schemes','列出翻译方案和当前方案。',self.list_schemes),
            Tool('list_favorites','列出收藏夹。',self.list_favorites),
            Tool('list_versions','列出本机识别到的 AE 版本和是否已适配。',self.list_versions),
            Tool('read_log','读取程序日志的最后几行，用来查问题。',self.read_log,params={'lines':('integer','行数，默认 60',False)}),
            Tool('go_to_page','切换程序页面，方便给用户看。',self.go_to_page,params={'page':('string','效果名称/参数名称/翻译方案/AE 版本/设置与日志/快速搜索/面板与脚本',True)}),

            Tool('translate_effects','用 AI 翻译效果名称并保存为候选（不写入 AE）。翻译勾选的效果用 checkedOnly=true，不用先查列表；也可给 keys 或 query/category。',self.translate_effects,level='data',kind='net',changes=True,params={'checkedOnly':('boolean','只翻译勾选的效果',False),'keys':keys,'query':query,'category':('string','分类名',False),'instructions':instructions,'overwrite':('boolean','是否覆盖已有译名，默认是',False),'allowLarge':('boolean','用户已确认超过 500 个',False)}),
            Tool('set_effect_names','直接保存少量效果译名候选。names 是 {matchName:中文}，空字符串清除。',self.set_effect_names,level='data',kind='write',changes=True,params={'names':('object','译名',True)}),
            Tool('check_effects','勾选或取消效果。给 keys，或用 query/category 选范围。',self.check_effects,level='data',kind='write',changes=True,params={'checked':('boolean','勾选或取消',True),'keys':keys,'query':query,'category':('string','分类名',False)}),
            Tool('translate_categories','用 AI 翻译分类名并保存为候选（不写入 AE）。不给 categories 则翻译全部。',self.translate_categories,level='data',kind='net',changes=True,params={'categories':('array','分类名列表',False),'instructions':instructions}),
            Tool('set_category_names','直接保存分类名候选。names 是 {分类:中文}。',self.set_category_names,level='data',kind='write',changes=True,params={'names':('object','译名',True)}),
            Tool('translate_parameters','用 AI 翻译参数页已读取的参数和下拉选项并保存为候选（不写入 AE）。给 ids，或 all=true，否则翻译勾选项。',self.translate_parameters,level='data',kind='net',changes=True,params={'ids':('array','参数或选项 id',False),'all':('boolean','翻译全部已读取行',False),'instructions':instructions,'overwrite':('boolean','是否覆盖已有译名，默认是',False)}),
            Tool('set_parameter_names','直接保存少量参数或下拉选项译名。names 是 {id:中文}，参数最多 31 字节。',self.set_parameter_names,level='data',kind='write',changes=True,params={'names':('object','译名',True)}),
            Tool('check_parameters','勾选或取消参数和下拉选项。给 ids，或 all=true。',self.check_parameters,level='data',kind='write',changes=True,params={'checked':('boolean','勾选或取消',True),'ids':('array','id 列表',False),'all':('boolean','全部已读取行',False)}),
            Tool('save_scheme_as','把当前表格另存为新方案。',self.save_scheme_as,level='data',kind='write',changes=True,params={'title':('string','方案名称',True)}),
            Tool('load_scheme','切换到某个方案，替换表格内容，不写入 AE。可以撤销。',self.load_scheme,level='data',kind='write',changes=True,params={'title':('string','方案名称',True)}),
            Tool('set_favorites','把效果加入或移出收藏夹；不存在的收藏夹会新建。',self.set_favorites,level='data',kind='write',changes=True,params={'keys':('array','效果内部标识',True),'on':('boolean','加入或移出',True),'folder':('string','收藏夹名，默认当前',False)}),
            Tool('set_preference','改程序设置。name 可为 dark_mode/motion/bilingual_names/temporary_translation/reapply_names。',self.set_preference,level='data',kind='write',changes=True,params={'name':('string','设置项',True),'value':('boolean','开或关',True)}),
            Tool('undo','撤销最近一轮对话对本机数据（候选、勾选、收藏）的改动，不影响已写入 AE 的内容。',self.undo,level='data',kind='write'),

            Tool('connect_ae','连接并读取运行中的 AE。',self.connect_ae,level='ae',kind='write'),
            Tool('read_parameters','从 AE 读取参数和下拉选项到参数页。scope 为 selected（选中图层）或 project（整个工程）。',self.read_parameters,level='ae',params={'scope':('string','读取范围，默认 selected',False)}),
            Tool('apply_effects','把效果中文名写入 AE。不给 keys 则写入勾选项。',self.apply_effects,level='ae',kind='write',changes=True,confirm=self.effects_question,params={'keys':keys}),
            Tool('restore_effects','把效果名恢复为原名。不给 keys 则恢复全部效果名。',self.restore_effects,level='ae',kind='write',changes=True,confirm=lambda a:f"恢复 {len(a['keys']) if 'keys' in a else '全部'} 个效果的原名？",params={'keys':keys}),
            Tool('apply_categories','把分类中文名写入 AE。',self.apply_categories,level='ae',kind='write',changes=True,confirm=lambda a:f"把 {len(a['categories'])} 个分类名写入 AE？",params={'categories':('array','分类名',True)}),
            Tool('restore_categories','恢复分类原名。不给 categories 则恢复全部。',self.restore_categories,level='ae',kind='write',changes=True,confirm=lambda a:'恢复分类原名？',params={'categories':('array','分类名',False)}),
            Tool('apply_parameters','把参数页勾选的参数和下拉选项写入 AE。先用 check_parameters 勾选。',self.apply_parameters,level='ae',kind='write',changes=True,confirm=self.parameters_question),
            Tool('restore_parameters','恢复参数和下拉选项原文。默认恢复勾选项，all=true 恢复全部。',self.restore_parameters,level='ae',kind='write',changes=True,confirm=lambda a:'恢复全部参数和下拉选项？' if a.get('all') else '恢复勾选的参数和下拉选项？',params={'all':('boolean','恢复全部',False)}),
            Tool('restore_everything','恢复本软件在 AE 里改过的全部名称（效果、标题、参数、下拉、分类）。',self.restore_all,level='ae',kind='write',changes=True,confirm=lambda a:'恢复本软件改过的全部名称？'),
            Tool('refresh_ae_list','刷新 AE“效果和预设”列表，让新名称显示出来。',self.refresh_ae_list,level='ae',kind='write'),
            Tool('open_adapter','打开“自动识别与适配”窗口。启用适配必须由用户完成自检和人工核对。',self.open_adapter,level='ae',kind='write'),

            Tool('list_dir','列出本机文件夹。',lambda a:agent_files.list_dir(a['path'],a.get('limit',200)),level='full',params={'path':('string','完整路径',True),'limit':('integer','条数',False)}),
            Tool('read_file','读取本机文字文件。',lambda a:agent_files.read_file(a['path'],a.get('offset',0),a.get('limit',agent_files.MAX_READ)),level='full',kind='file',params={'path':('string','完整路径',True),'offset':offset,'limit':('integer','字符数',False)}),
            Tool('write_file','写入本机文字文件；已有文件先备份。',lambda a:agent_files.write_file(a['path'],a['content']),level='full',kind='write',changes=True,params={'path':('string','完整路径',True),'content':('string','内容',True)}),
            Tool('replace_in_file','替换文件里恰好出现一次的文字，先备份。',lambda a:agent_files.replace_in_file(a['path'],a['old'],a['new']),level='full',kind='write',changes=True,params={'path':('string','完整路径',True),'old':('string','原文字',True),'new':('string','新文字',True)}),
            Tool('move_path','移动或重命名文件、文件夹，目标已存在时不覆盖。',lambda a:agent_files.move_path(a['source'],a['target']),level='full',kind='write',changes=True,params={'source':('string','原路径',True),'target':('string','新路径',True)}),
            Tool('delete_path','删除文件或文件夹；内容移到备份文件夹，可找回。',lambda a:agent_files.delete_path(a['path']),level='full',kind='danger',changes=True,params={'path':('string','完整路径',True)}),
            Tool('patch_status','本程序的补丁状态和可读源码模块列表。',self.patch_status,level='full'),
            Tool('read_source','读取本程序某个模块的源码（已打补丁时读补丁后的）。',self.read_source,level='full',kind='file',params={'module':('string','模块名，不带 .py',True),'offset':offset,'limit':('integer','字符数，默认 20000',False)}),
            Tool('search_source','在本程序全部源码里查文字，返回模块和行号。',self.search_source,level='full',kind='file',params={'text':('string','要找的文字',True)}),
            Tool('patch_module','修补本程序：替换模块里恰好出现一次的文字。保存前会另开一份程序试运行，连助手对话也跑通才保存，没通过会返回卡住的位置，改好再试。重启后生效，用户确认才保留，只对当前版本有效。',self.patch_module,level='full',kind='patch',changes=True,params={'module':('string','模块名',True),'old':('string','原文字',True),'new':('string','新文字',True)}),
            Tool('remove_patch','去掉某个模块的补丁，重启后生效。',self.remove_patch,level='full',kind='write',changes=True,params={'module':('string','模块名',True)}),
            Tool('restore_patch','恢复某模块上一份补丁，核对当前内容后恢复，重启生效。',lambda a:patch_loader.restore_patch(a['module'],self.w.assistant_env['version']),level='full',kind='write',changes=True,params={'module':('string','模块名',True)}),
        ]
