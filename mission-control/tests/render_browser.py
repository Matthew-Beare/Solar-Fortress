"""Run the same computed-style verifier used by the Windows installer."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
OUT = HERE/'render-results'


def main():
    browser = os.environ.get('SF_TEST_BROWSER') or shutil.which('google-chrome') or shutil.which('chromium')
    if not browser:
        raise SystemExit('No Chrome/Chromium executable is available for rendered checks')
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sf-render-') as temp:
        common = [browser, '--headless=new', '--disable-gpu', '--no-first-run',
                  '--no-default-browser-check', '--disable-background-networking',
                  '--user-data-dir='+temp]
        result = subprocess.run(common+['--dump-dom', '--virtual-time-budget=20000',
                                '--window-size=1280,900', (HERE/'verify_render.html').as_uri()],
                                capture_output=True, text=True, timeout=55)
        (OUT/'verification.html').write_text(result.stdout)
        (OUT/'renderer.log').write_text(result.stderr)
        if result.returncode or 'data-sf-render-verification="pass"' not in result.stdout:
            match = re.search(r'<pre id="report">([\s\S]*?)</pre>', result.stdout)
            raise SystemExit('Rendered checks failed: '+(match.group(1) if match else result.stderr[-4000:]))
        match = re.search(r'<pre id="report">([\s\S]*?)</pre>', result.stdout)
        report = json.loads(match.group(1))
        assert report['fixtures'] == 25, report
        assert report['checks'] >= 2500, report
        print(json.dumps(report, indent=2))
        (OUT/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
        for name, width, height in [('inverting',1280,1450),('float',1280,1450),('inverting',390,1900)]:
            target = OUT/(name+'-'+str(width)+'.png')
            url = (HERE/'preview.html').as_uri()+'?case='+name+'&width='+str(width)
            subprocess.run(common+['--screenshot='+str(target), '--virtual-time-budget=1500',
                           '--window-size='+str(max(width,430))+','+str(height), url],
                           capture_output=True, check=True, timeout=25)
            assert target.stat().st_size > 0
        print('Rendered previews saved:', OUT)


if __name__ == '__main__':
    main()
