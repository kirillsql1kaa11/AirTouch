import http.server, urllib.parse, ctypes, socket, qrcode, sys, subprocess, re
sys.stdout.reconfigure(encoding='utf-8')
u = ctypes.windll.user32

class MouseServer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        if p.path == '/m':
            u.mouse_event(1, int(float(q['x'][0])), int(float(q['y'][0])), 0, 0)
            self.send_response(204); self.end_headers()
        elif p.path == '/c':
            btn = int(q.get('b', [1])[0])
            d, up = (2, 4) if btn == 1 else (8, 16)
            u.mouse_event(d, 0, 0, 0, 0); u.mouse_event(up, 0, 0, 0, 0)
            self.send_response(204); self.end_headers()
        else:
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers()
            self.wfile.write(b"""<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<style>body{margin:0;height:100vh;background:#171513;touch-action:none;user-select:none;display:flex;align-items:center;justify-content:center;color:#d6cfc7;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:16px;letter-spacing:0.02em}</style></head><body>
<div>\xd0\xa2\xd0\xb0\xd1\x87\xd0\xbf\xd0\xb0\xd0\xb4 \xd0\x90\xd0\xba\xd1\x82\xd0\xb8\xd0\xb2\xd0\xb5\xd0\xbd</div>
<script>
let lx=0, ly=0, st=0;
window.ontouchstart = e => { lx = e.touches[0].clientX; ly = e.touches[0].clientY; st = Date.now(); };
window.ontouchmove = e => {
  let cx = e.touches[0].clientX, cy = e.touches[0].clientY;
  fetch(`/m?x=${(cx - lx) * 1.5}&y=${(cy - ly) * 1.5}`);
  lx = cx; ly = cy;
};
window.ontouchend = e => { if (Date.now() - st < 200) fetch('/c?b=1'); };
</script></body></html>""")

# Определение локального IP (приоритет физической Ethernet/Wi-Fi сети над VPN/tun)
ip = '127.0.0.1'
try:
    out = subprocess.check_output('ipconfig', shell=True).decode('cp866', errors='ignore')
    candidates = []
    cname = 'LAN'
    for line in out.splitlines():
        l = line.strip().lower()
        if 'адаптер' in l or 'adapter' in l: cname = l
        m = re.search(r'ipv4[^\:]*:\s*([\d\.]+)', l)
        if m and not m.group(1).startswith(('127.', '169.254.')):
            pen = 100 if any(b in cname for b in ['tun', 'tap', 'vpn', 'happ']) else 0
            candidates.append((30 if m.group(1).startswith('192.168.') else 0) - pen, m.group(1))
    if candidates: ip = sorted(candidates, reverse=True)[0][1]
except Exception: pass

url = f"http://{ip}:8000"
print(f"\n[+] Ссылка для подключения: {url}\n[+] Отсканируйте QR-код камерой телефона:\n")
qr = qrcode.QRCode(); qr.add_data(url); qr.print_ascii(invert=True)
print("\nСервер запущен. Для завершения нажмите Ctrl+C\n")
if __name__ == '__main__':
    http.server.HTTPServer(('0.0.0.0', 8000), MouseServer).serve_forever()
