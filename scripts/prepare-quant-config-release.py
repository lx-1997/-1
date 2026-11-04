from pathlib import Path
import json,difflib,hashlib,shutil,ast,argparse
parser=argparse.ArgumentParser(description='Build exact quant configuration patches over captured production sources')
parser.add_argument('--baseline',type=Path,required=True,help='Captured production deepfocus_api package')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();r=Path(__file__).resolve().parents[1];a=args.output.resolve();a.mkdir(parents=True,exist_ok=True);base=args.baseline.resolve();target=a/'candidate/deepfocus_api';shutil.copytree(base,target,dirs_exist_ok=True)
new={}
s=(r/'backend/deepfocus_api/quant_research_engine.py').read_text()
s=s.replace("profile='legacy', benchmark=None, configuration=None):","benchmark=None, configuration=None):",1)
s=s.replace("configured and (profile != 'legacy' or benchmark not in dataset or benchmark in symbols)","configured and (benchmark not in dataset or benchmark in symbols)",1)
start=s.index("    if profile != 'legacy':",s.index('def replay('));end=s.index('    state =',start);s=s[:start]+s[end:]
start=s.index("        if profile != 'legacy':",s.index('def replay('));end=s.index('        if configured:',start);s=s[:start]+s[end:]
s=s.replace("                if profile != 'legacy': fill_key += [profile, params]\n",'',1)
s=s.replace("progress=None, *, profile='legacy', configuration=None):","progress=None, *, configuration=None):",1)
s=s.replace("    if configured and profile != 'legacy': raise ValueError('配置实验与旧研发候选不能混用')\n",'',1)
start=s.index("    if profile != 'legacy':",s.index('def walk_forward'));end=s.index('    holdout_start =',start);s=s[:start]+s[end:]
s=s.replace("            train_start = dates[max(0,stop-252)] if profile!='legacy' else None\n",'',1).replace('p, start=train_start, end=','p, end=',1)
s=s.replace("    if profile!='legacy': reasons.append('候选使用已见历史开发数据，须完成冻结后的全新验证，禁止晋级')\n",'',1)
assert 'profile' not in s
new['quant_research_engine.py']=s
s=(base/'quant_research.py').read_text();s=s.replace("files = ('quant_research_engine.py'", "files = ('quant_research_config.py','quant_research_engine.py'",1)
needle="request['capital'], progress), 180)";assert s.count(needle)==1;s=s.replace(needle,"request['capital'], progress, configuration=request.get('configuration')), 180)")
needle="    if not job['result']['gate']['passed']: raise ValueError('未通过研究门槛，不能转入模拟跟踪')"
s=s.replace(needle,"    from .quant_research_config import effective\n    if effective(job['request'].get('configuration')) is not None:\n        raise ValueError('自定义参数仍为开发研究，须在冻结后全新数据验证，不能直接晋级')\n"+needle,1)
new['quant_research.py']=s
s=(base/'quant_research_api.py').read_text().replace('from typing import Annotated','from typing import Annotated, Optional',1)
needle="    @field_validator('symbols')";s=s.replace(needle,"    configuration: Optional[dict] = None\n\n    @field_validator('configuration')\n    @classmethod\n    def configuration_valid(cls, value):\n        from .quant_research_config import validate\n        return validate(value) if value is not None else None\n\n"+needle,1)
s=s.replace("key = values.pop('request_id')", "key = values.pop('request_id')\n    if values.get('configuration') is None: values.pop('configuration', None)",1)
new['quant_research_api.py']=s
s=(base/'quant_research_ui.py').read_text();s+='\nfrom .quant_research_config_ui import enhance as config_enhance\nHTML = config_enhance(HTML)\n';new['quant_research_ui.py']=s
for name in ['quant_research_config.py','quant_research_config_ui.py']:new[name]=(r/'backend/deepfocus_api'/name).read_text()
manifest=[]
for name,s in new.items():
 ast.parse(s);(target/name).write_text(s);item={'file':name,'result_sha256':hashlib.sha256(s.encode()).hexdigest()}
 if (base/name).exists():
  old=(base/name).read_text();item['sha256']=hashlib.sha256(old.encode()).hexdigest();item['patches']=[];check=old
  for group in difflib.SequenceMatcher(None,old.splitlines(True),s.splitlines(True),autojunk=False).get_grouped_opcodes(3):
   before=''.join(old.splitlines(True)[group[0][1]:group[-1][2]]);after=''.join(s.splitlines(True)[group[0][3]:group[-1][4]]);assert check.count(before)==1;(item['patches']).append({'old':before,'new':after});check=check.replace(before,after,1)
  assert check==s
  (a/(name+'.candidate.diff')).write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True))))
 else:item['new_file']=s
 manifest.append(item)
(a/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
rows=[[f.relative_to(target).as_posix(),hashlib.sha256(f.read_bytes()).hexdigest()] for f in sorted(target.rglob('*.py')) if not f.name.startswith('._') and not {'tests','__pycache__'}&set(f.relative_to(target).parts)]
release=hashlib.sha256(json.dumps(rows,separators=(',',':'),ensure_ascii=True).encode()).hexdigest();(a/'candidate-source.json').write_text(json.dumps({'release':release,'files':rows},indent=2))
print(json.dumps({'modules_changed':len(manifest),'exact_blocks':sum(len(x.get('patches',[])) for x in manifest),'release':release}))
