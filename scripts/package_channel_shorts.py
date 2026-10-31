"""Verify and copy the three finished local films into E:/Rolki."""
from pathlib import Path
import hashlib
import html
import json
import re
import shutil

source=Path('E:/Atnos/App/exports/three-channels-20260913')
dest=Path('E:/Rolki/_inbox/Trzy-kanaly-20260913')
dest.mkdir(parents=True,exist_ok=True)
spec=json.loads(Path('E:/Atnos/App/config/shorts-three-channels.json').read_text(encoding='utf-8'))
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
labels={'wealth_blueprint':'Inwestycje','children_stories':'Bajka 4–7 lat','horror_stories':'Straszna historia'}
outputs=[]
for channel in spec['channels']:
    folder=source/channel['key']
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    path=Path(result['output'])
    assert path.resolve().is_relative_to(source.resolve()) and sha(path)==result['sha256']
    assert result['width']==1080 and result['height']==1920
    assert result['status']=='rendered_and_technically_verified'
    assert 30<=result['duration_seconds']<=65
    assert (folder/'decode-check.log').read_text(encoding='utf-8').strip()==''
    transcript=json.loads((folder/'transcript-check.json').read_text(encoding='utf-8'))
    assert transcript['status']=='passed'
    audio_log=(folder/'loudness-check.log').read_text(encoding='utf-8')
    loudness=json.loads(re.search(r'\{\s*"input_i".*?\}',audio_log,re.S).group())
    assert -17<float(loudness['input_i'])<-15 and float(loudness['input_tp'])<=-1.0
    captions=json.loads((folder/'captions.json').read_text(encoding='utf-8'))
    assert all(0<=c['start']<c['end']<=result['duration_seconds']+.1 for c in captions['cues'])
    for left,right in zip(captions['cues'],captions['cues'][1:]):assert left['end']<=right['start']+.001
    stem=path.stem
    for original,name in [(path,path.name),(folder/'captions.srt',stem+'.srt'),(folder/'cover.png',stem+'.png')]:
        target=dest/name
        if target.exists():
            assert sha(target)==sha(original),'Do not replace a different delivered file silently'
        else:shutil.copyfile(original,target)
        assert sha(target)==sha(original)
    result.update(delivery=str(dest/path.name),delivery_sha256=sha(dest/path.name),
        integrated_loudness_lufs=float(loudness['input_i']),true_peak_dbtp=float(loudness['input_tp']),
        transcript_similarity=transcript['word_sequence_similarity'],caption_matched_words=captions['matched_words'],
        editorial_review='Assistant reviewed the scripts and six encoded scene samples per film. No human listening claimed.')
    outputs.append(result)

(dest/'manifest.json').write_text(json.dumps(outputs,indent=2,ensure_ascii=False),encoding='utf-8')
lines=['# Trzy shorty po angielsku','',
       'Trzy nowe filmy z narracją, napisami i oryginalną animacją 2D. Format 1080 × 1920, H.264/AAC, 30 fps. Nie zostały opublikowane.','']
for result in outputs:
    name=Path(result['delivery']).name
    lines.append(f"- [{result['title']} — {result['duration_seconds']:.1f} s]({name})")
lines.extend(['','Przy każdym MP4 są okładka PNG i napisy SRT. Napisy są już widoczne w filmie.',
    '', 'Produkcja wspomagana: scenariusze zredagował asystent, ilustracje i animacje powstały w kodzie. Głosy wygenerował lokalny Kokoro, transkrypcję sprawdził Whisper, montaż wykonał FFmpeg. To nie jest deklaracja ukończenia automatycznej produkcji opowieści z formularza Atnosa.',
    '', 'Weryfikacja: pełne dekodowanie każdego MP4, parametry obrazu, ścieżka audio, pomiar głośności i transkrypcja. Asystent obejrzał próbki wszystkich scen. Ludzki odsłuch nie został wykonany.',
    '', 'Źródło merytoryczne filmu inwestycyjnego: [Investor.gov — wpływ opłat na portfel](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated). Kwoty są przykładem przy stałym saldzie; nie prognozą wyników.'])
(dest/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
cards=[]
for channel,result in zip(spec['channels'],outputs):
    name=Path(result['delivery']).name
    cards.append(f'<article><p class="label">{labels[channel["key"]]}</p><h2>{html.escape(result["title"])}</h2><video controls playsinline preload="metadata" poster="{Path(name).stem}.png" src="{name}"></video><p>{result["duration_seconds"]:.1f} s · English · 1080 × 1920</p><a download href="{name}">Otwórz / zapisz MP4</a></article>')
(dest/'Obejrzyj-shorty.html').write_text('''<!doctype html><html lang="pl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Atnos — trzy shorty</title><style>
*{box-sizing:border-box}body{margin:0;padding:42px;background:#0b1720;color:#f5efdf;font-family:Segoe UI,Arial,sans-serif}main{max-width:1120px;margin:auto}h1{font-size:34px;letter-spacing:-1px;margin-bottom:10px}.intro{color:#aabeba;line-height:1.6;margin-bottom:36px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:26px}article{min-width:0;padding:20px;background:#142832;border:1px solid #2c454e;border-radius:18px}h2{font-size:19px;min-height:48px;margin:10px 0 16px}.label{font-size:13px;color:#a7e0c0}video{display:block;width:100%;aspect-ratio:9/16;border-radius:10px;background:#000}article p{font-size:13px;color:#aabeba}a{color:#bce7cb;font-size:14px}@media(max-width:800px){body{padding:20px}.grid{grid-template-columns:1fr}h2{min-height:0}article{max-width:430px;width:100%;margin:auto}}</style><main><h1>Trzy kanały. Trzy shorty.</h1><p class="intro">Nowe filmy po angielsku: inwestycje, spokojna bajka i fikcyjna straszna historia.<br>Wybierz film, żeby go obejrzeć. Wszystkie pliki są zapisane lokalnie na E:.</p><div class="grid">'''+''.join(cards)+'</div></main></html>',encoding='utf-8')
print(json.dumps([{'title':r['title'],'file':r['delivery'],'seconds':r['duration_seconds']} for r in outputs],ensure_ascii=False))
