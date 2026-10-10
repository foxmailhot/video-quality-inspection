"""Self-contained, human-readable reports with lossless local re-import."""
import json
from html import escape
from html.parser import HTMLParser
from pathlib import Path

LABELS=dict(black_level='黑帧亮度阈值',black_ratio='黑帧暗像素比例 %',black_frames='黑帧最少帧数',freeze_seconds='定帧最短秒数',freeze_delta='定帧容许画面差',flash_delta='夹帧突变差',return_delta='返回相似差',flash_frames='最多夹入帧数',block_ratio='块边界敏感阈值',noise_level='噪声强度阈值',color_ratio='异常颜色比例 %',width='分析宽度（0 为原始）',crop='四边忽略 %',gpu='自动 GPU',black='黑帧检测',freeze='定帧检测',flash='夹帧检测',artifacts='花屏检测',borders='黑边检测',border_level='黑边亮度阈值',border_ratio='黑边行列暗像素 %',border_percent='最小黑边宽度 %',border_frames='黑边最少帧数',mirror='镜像检测',mirror_delta='镜像匹配差',mirror_gain='镜像匹配改善 %',mirror_frames='镜像最少帧数',mirror_window='镜像回看秒数')
def stamp(t):
    ms=max(0,round(t*1000));return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'
def write_html(path,result,thumbnails=None,snapshot_warning=""):
    thumbnails=thumbnails or {}
    e=lambda value:escape(str(value),quote=True)
    issues=sorted(result.get('issues',[]),key=lambda v:v.get('review')=='误报');info=result.get('info',{})
    confirmed=sum(v.get('review')=='确认问题' for v in issues);false=sum(v.get('review')=='误报' for v in issues)
    rows=[]
    for n,v in enumerate(issues,1):
        state=v.get('review','待复核');cls='confirmed' if state=='确认问题' else 'false' if state=='误报' else 'pending'
        values=[n,v['kind'],stamp(v['start']),stamp(v['end']),f"{v['end']-v['start']:.3f} 秒",f"{v['frame']}–{v['last_frame']}",state,v['detail']]
        shot=thumbnails.get(int(v['frame']))
        preview=f'<img width="240" src="data:image/jpeg;base64,{shot}" alt="起始帧截图"><br>帧 {v["frame"]}' if shot else '无截图'
        rows.append(f'<tr class="{cls}">'+''.join(f'<td>{e(x)}</td>' for x in values)+f'<td>{preview}</td></tr>')
    warnings=''.join(f'<li>{e(w)}</li>' for w in result.get('warnings',[]))
    params=''.join(f'<tr><td>{e(LABELS.get(k,k))}</td><td>{e("开启" if v is True else "关闭" if v is False else v)}</td></tr>' for k,v in result.get('settings',{}).items())
    payload=json.dumps(result,ensure_ascii=False).replace('<','\\u003c')
    html=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>{e(Path(result['path']).name)} · 视频质检报告</title><style>
body{{font:15px/1.6 "Microsoft YaHei",sans-serif;color:#243247;background:#f3f5f7;margin:0}}main{{max-width:1450px;margin:32px auto;background:white;padding:32px;border-radius:12px}}h1{{margin:0;color:#152638}}h2{{margin-top:28px}}.muted{{color:#607086}}.summary{{display:flex;gap:16px;flex-wrap:wrap;margin:22px 0}}.card{{padding:14px 24px;background:#eef4f7;border-radius:8px}}strong{{font-size:24px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d4dce5;padding:9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{background:#e8eef4}}tr.confirmed{{background:#fff0d5}}tr.confirmed td:nth-child(7){{font-weight:bold;color:#a33a00}}tr.false{{color:#7d8792;background:#f1f2f4}}.path{{overflow-wrap:anywhere}}.scroll{{overflow-x:auto}}.params{{max-width:700px}}@media print{{body{{background:white}}main{{margin:0;padding:0}}th{{background:#eee}}thead{{display:table-header-group}}tr{{break-inside:avoid}}.scroll{{overflow:visible}}}}
</style></head><body><main><h1>视频质检报告</h1><p class="path">{e(result['path'])}</p>
<p class="muted">FrameScope {e(result.get('version',''))} · {e(info.get('width','?'))} × {e(info.get('height','?'))} · 时长 {stamp(info.get('duration',0))} · 已扫描 {e(result.get('frames',0))} 帧 · {'扫描完成' if result.get('complete',False) else '扫描存在异常，请检查警告'}</p>
<div class="summary"><div class="card">确认问题<br><strong>{confirmed}</strong></div><div class="card">待复核<br><strong>{len(issues)-confirmed-false}</strong></div><div class="card">误报<br><strong>{false}</strong></div></div>
<p>计算：{e(info.get('backend','未记录'))}　解码：{e(info.get('decode_backend','未记录'))}</p>
{'<h2>检测警告</h2><ul>'+warnings+'</ul>' if warnings else ''}
<h2>问题与复核结果</h2><p>{e(snapshot_warning)}</p><p class="muted">帧号从 0 开始。镜像、定帧、夹帧、花屏及暗景黑边需结合原视频复核。浏览器打印菜单可保存为 PDF。</p>
<div class="scroll"><table><thead><tr>{''.join('<th>'+x+'</th>' for x in ('序号','类型','起点','终点','持续时间','帧号范围','复核结论','检测依据','起始帧缩略图'))}</tr></thead><tbody>{''.join(rows) or '<tr><td colspan="9">本次未检出候选问题。</td></tr>'}</tbody></table></div>
<h2>本次检测参数</h2><table class="params">{params}</table><p class="muted">本报告可离线查看，也可重新导入 FrameScope 恢复标记。原视频不包含在报告中。</p>
<script type="application/json" id="framescope-data">{payload}</script></main></body></html>'''
    Path(path).write_text(html,encoding='utf-8')

def read_report(path):
    text=Path(path).read_text(encoding='utf-8-sig')
    if Path(path).suffix.lower() not in ('.html','.htm'):return json.loads(text)
    class Parser(HTMLParser):
        def __init__(self):super().__init__();self.active=False;self.data=[]
        def handle_starttag(self,tag,attrs):
            if tag=='script' and dict(attrs).get('id')=='framescope-data':self.active=True
        def handle_endtag(self,tag):
            if tag=='script':self.active=False
        def handle_data(self,data):
            if self.active:self.data.append(data)
    parser=Parser();parser.feed(text)
    if not parser.data:raise ValueError('这不是可导入的 FrameScope HTML 报告')
    return json.loads(''.join(parser.data))

def csv_safe(value):
    text=str(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
