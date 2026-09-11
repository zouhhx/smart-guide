"""Merge voice controls into an existing admin bundle without reverting other UI changes.

Usage: python merge-voice-ui.py BASE_JS BASE_HTML VOICE_SOURCE LAYOUT_CSS OUTPUT_DIR
Fails closed if the expected integration points have changed.
"""
from pathlib import Path
import hashlib
import json
import re
import sys


def merge(base, component):
    edits = [
        ('function uaDigitalHumanManager({notify:I})', component + '\nfunction uaDigitalHumanManager({notify:I})'),
        ('className:"digital-human-console",children:[', 'className:"digital-human-console",children:[(0,w.jsx)(uaLocalVoiceSettings,{notify:I,onSaved:Be}),'),
        ('className:"avatar-broadcast-switch",children:', 'className:"avatar-broadcast-switch",style:Ae?.provider==="qwen3"?{display:"none"}:{},children:'),
        ('await uaAvatarRequest(Xt,Ct==="say"?{text:_e}:{})', 'await (Ae?.provider==="qwen3"&&(Ct==="say"||Ct==="interrupt")?p("/api/tts-live",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:_e,action:Ct==="interrupt"?"stop":"speak"})}):uaAvatarRequest(Xt,Ct==="say"?{text:_e}:{}))'),
    ]
    assert 'function uaLocalVoiceSettings(' not in base, 'Voice component already exists'
    result = base
    for old, new in edits:
        assert result.count(old) == 1, 'Integration point changed: ' + old
        result = result.replace(old, new, 1)
    # Prove all existing code outside these integration points is retained.
    restored = result
    for old, new in reversed(edits):
        assert restored.count(new) == 1
        restored = restored.replace(new, old, 1)
    assert restored == base
    return result


if __name__ == '__main__':
    base_path, html_path, source_path, css_path, output = map(Path, sys.argv[1:])
    output.mkdir(parents=True, exist_ok=True)
    base = base_path.read_text(encoding='utf-8-sig')
    result = merge(base, source_path.read_text(encoding='utf-8-sig'))
    html = html_path.read_text(encoding='utf-8-sig')
    pattern = r'(<script\b[^>]*\bsrc=")/assets/[^"?]+\.js(?:\?[^\"]*)?("[^>]*></script>)'
    html, count = re.subn(pattern, r'\1/assets/index-local-tts-v16.js?v=voice-restore-16\2', html)
    assert count == 1, 'Expected one entry module'
    if '/assets/avatar-layout-v14.css' not in html:
        html = html.replace('</head>', '  <link rel="stylesheet" href="/assets/avatar-layout-v14.css?v=voice-restore-16">\n  </head>')
    (output / 'index-local-tts-v16.js').write_text(result, encoding='utf-8')
    (output / 'index.html').write_text(html, encoding='utf-8')
    (output / 'avatar-layout-v14.css').write_bytes(css_path.read_bytes())
    (output / 'merge-evidence.json').write_text(json.dumps({
        'baseSha256': hashlib.sha256(base_path.read_bytes()).hexdigest(),
        'resultSha256': hashlib.sha256((output / 'index-local-tts-v16.js').read_bytes()).hexdigest(),
        'existingCodePreserved': True,
        'integrationPoints': 4,
    }, indent=2), encoding='utf-8')
