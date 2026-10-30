#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AirTouch Pro - Беспроводной тачпад для ПК через браузер смартфона.
Управление курсором, жесты, клавиатура, горячие клавиши.
"""

import sys
import os
import time
import json
import socket
import struct
import base64
import hashlib
import threading
import webbrowser
import urllib.parse
import http.server
import subprocess
import re
import ctypes
from ctypes import wintypes

# Настройка UTF-8 для консоли Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ==========================================
# 1. УПРАВЛЕНИЕ СИСТЕМОЙ (WINDOWS CTYPES WIN32)
# ==========================================
user32 = ctypes.windll.user32

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_void_p)
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [('ki', KEYBDINPUT)]
    _anonymous_ = ('_input',)
    _fields_ = [('type', wintypes.DWORD), ('_input', _INPUT)]

class SystemController:
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_HWHEEL = 0x1000

    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    VK_MAP = {
        'enter': 0x0D,
        'backspace': 0x08,
        'tab': 0x09,
        'escape': 0x1B,
        'space': 0x20,
        'win': 0x5B,
        'volume_mute': 0xAD,
        'volume_down': 0xAE,
        'volume_up': 0xAF,
        'media_play_pause': 0xB3,
        'media_next': 0xB0,
        'media_prev': 0xB1,
        'left': 0x25,
        'up': 0x26,
        'right': 0x27,
        'down': 0x28,
        'delete': 0x2E,
    }

    @classmethod
    def move_cursor(cls, dx: float, dy: float):
        idx = int(round(dx))
        idy = int(round(dy))
        if idx != 0 or idy != 0:
            user32.mouse_event(cls.MOUSEEVENTF_MOVE, idx, idy, 0, 0)

    @classmethod
    def mouse_down(cls, button: str = 'left'):
        flag = cls.MOUSEEVENTF_LEFTDOWN if button == 'left' else cls.MOUSEEVENTF_RIGHTDOWN
        user32.mouse_event(flag, 0, 0, 0, 0)

    @classmethod
    def mouse_up(cls, button: str = 'left'):
        flag = cls.MOUSEEVENTF_LEFTUP if button == 'left' else cls.MOUSEEVENTF_RIGHTUP
        user32.mouse_event(flag, 0, 0, 0, 0)

    @classmethod
    def click(cls, button: str = 'left'):
        cls.mouse_down(button)
        cls.mouse_up(button)

    @classmethod
    def double_click(cls):
        cls.click('left')
        time.sleep(0.05)
        cls.click('left')

    @classmethod
    def scroll(cls, dy: float, dx: float = 0):
        if dy != 0:
            user32.mouse_event(cls.MOUSEEVENTF_WHEEL, 0, 0, int(dy * 120), 0)
        if dx != 0:
            user32.mouse_event(cls.MOUSEEVENTF_HWHEEL, 0, 0, int(dx * 120), 0)

    @classmethod
    def press_key(cls, key_name: str):
        key_name = key_name.lower()
        if key_name in cls.VK_MAP:
            vk = cls.VK_MAP[key_name]
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, cls.KEYEVENTF_KEYUP, 0)
        elif key_name == 'ctrl_c':
            cls.hotkey_combo(0x11, 0x43)
        elif key_name == 'ctrl_v':
            cls.hotkey_combo(0x11, 0x56)
        elif key_name == 'ctrl_z':
            cls.hotkey_combo(0x11, 0x5A)
        elif key_name == 'alt_tab':
            cls.hotkey_combo(0x12, 0x09)

    @classmethod
    def hotkey_combo(cls, mod_vk: int, key_vk: int):
        user32.keybd_event(mod_vk, 0, 0, 0)
        user32.keybd_event(key_vk, 0, 0, 0)
        user32.keybd_event(key_vk, 0, cls.KEYEVENTF_KEYUP, 0)
        user32.keybd_event(mod_vk, 0, cls.KEYEVENTF_KEYUP, 0)

    @classmethod
    def type_unicode_text(cls, text: str):
        for char in text:
            code = ord(char)
            inp_down = INPUT(type=1, ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=cls.KEYEVENTF_UNICODE, time=0, dwExtraInfo=None))
            inp_up = INPUT(type=1, ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=cls.KEYEVENTF_UNICODE | cls.KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))
            user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
            user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))


# ==========================================
# 2. ОПРЕДЕЛЕНИЕ СЕТЕВЫХ ИНТЕРФЕЙСОВ
# ==========================================
class AppState:
    connected_clients = 0
    last_client_ip = None
    lock = threading.Lock()

    @classmethod
    def client_connected(cls, ip):
        with cls.lock:
            cls.connected_clients += 1
            cls.last_client_ip = ip

    @classmethod
    def client_disconnected(cls):
        with cls.lock:
            cls.connected_clients = max(0, cls.connected_clients - 1)


def get_network_interfaces():
    """
    Сканирует все доступные интерфейсы Windows.
    Грамотно приоритизирует проводную сеть (Ethernet) и Wi-Fi роутера
    над виртуальными адаптерами (VPN, TUN, TAP, WSL, Hyper-V, Docker).
    """
    interfaces = []
    if sys.platform == 'win32':
        try:
            raw = subprocess.check_output('ipconfig', shell=True).decode('cp866', errors='ignore')
            lines = raw.replace('\r', '').split('\n')
            cur_adapter = 'Локальная сеть'
            for line in lines:
                line_s = line.strip()
                if line_s.endswith(':') and '.' not in line_s:
                    clean = line_s[:-1].replace('адаптер', '').replace('Адаптер', '').strip()
                    cur_adapter = clean or 'Локальная сеть'
                m = re.search(r'IPv4[^\:]*:\s*([\d\.]+)', line)
                if m:
                    ip = m.group(1)
                    if not ip.startswith(('127.', '169.254.')):
                        # Читаемое название для пользователя
                        display_name = cur_adapter
                        name_lower = cur_adapter.lower()
                        is_vpn = any(b in name_lower for b in ['tun', 'tap', 'vpn', 'happ', 'tailscale', 'zerotier'])
                        is_ethernet = 'ethernet' in name_lower or 'lan' in name_lower or 'проводн' in name_lower
                        is_wifi = 'wi-fi' in name_lower or 'wireless' in name_lower or 'беспроводн' in name_lower

                        if is_ethernet:
                            display_name = f"{cur_adapter} (Проводная сеть)"
                        elif is_wifi:
                            display_name = f"{cur_adapter} (Wi-Fi)"
                        elif is_vpn:
                            display_name = f"{cur_adapter} (VPN / Прокси)"

                        # Расчет приоритета
                        score = 0
                        if is_vpn:
                            score -= 100
                        if is_ethernet:
                            score += 45
                        if is_wifi:
                            score += 40
                        if ip.startswith('192.168.'):
                            score += 30
                        elif ip.startswith('10.'):
                            score += 10

                        interfaces.append({
                            'name': display_name,
                            'raw_name': cur_adapter,
                            'ip': ip,
                            'score': score
                        })
        except Exception:
            pass

    # Резервный поиск через socket
    if not interfaces:
        try:
            hostname = socket.gethostname()
            for addr in socket.getaddrinfo(hostname, None):
                if addr[0] == socket.AF_INET:
                    ip = addr[4][0]
                    if not ip.startswith(('127.', '169.254.')):
                        if not any(i['ip'] == ip for i in interfaces):
                            score = 30 if ip.startswith('192.168.') else 10
                            interfaces.append({
                                'name': 'Локальная сеть',
                                'raw_name': 'LAN',
                                'ip': ip,
                                'score': score
                            })
        except Exception:
            pass

    if not interfaces:
        interfaces.append({'name': 'Локальный адрес', 'raw_name': 'Localhost', 'ip': '127.0.0.1', 'score': 0})

    interfaces.sort(key=lambda x: x['score'], reverse=True)
    return interfaces


# ==========================================
# 3. WEBSOCKET ПРОТОКОЛ (ЧИСТЫЙ PYTHON RFC 6455)
# ==========================================
WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def handle_websocket(sock, client_ip):
    AppState.client_connected(client_ip)
    buf = bytearray()
    try:
        sock.settimeout(60.0)
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)

            while len(buf) >= 2:
                b0 = buf[0]
                b1 = buf[1]
                opcode = b0 & 0x0F
                masked = bool(b1 & 0x80)
                plen = b1 & 0x7F

                idx = 2
                if plen == 126:
                    if len(buf) < 4:
                        break
                    plen = struct.unpack(">H", buf[2:4])[0]
                    idx = 4
                elif plen == 127:
                    if len(buf) < 10:
                        break
                    plen = struct.unpack(">Q", buf[2:10])[0]
                    idx = 10

                mask_bytes = b''
                if masked:
                    if len(buf) < idx + 4:
                        break
                    mask_bytes = buf[idx:idx+4]
                    idx += 4

                if len(buf) < idx + plen:
                    break

                raw_payload = buf[idx:idx+plen]
                del buf[:idx+plen]

                if opcode == 8:
                    return
                elif opcode == 9:
                    pong = bytes([0x8A, 0x00])
                    sock.sendall(pong)
                    continue

                if masked and mask_bytes:
                    payload = bytes(b ^ mask_bytes[i % 4] for i, b in enumerate(raw_payload))
                else:
                    payload = bytes(raw_payload)

                try:
                    msg = json.loads(payload.decode('utf-8'))
                    t = msg.get('type')

                    if t == 'move':
                        SystemController.move_cursor(msg.get('dx', 0), msg.get('dy', 0))
                    elif t == 'down':
                        SystemController.mouse_down(msg.get('btn', 'left'))
                    elif t == 'up':
                        SystemController.mouse_up(msg.get('btn', 'left'))
                    elif t == 'click':
                        btn = msg.get('btn', 'left')
                        if btn == 'double':
                            SystemController.double_click()
                        else:
                            SystemController.click(btn)
                    elif t == 'scroll':
                        SystemController.scroll(msg.get('dy', 0), msg.get('dx', 0))
                    elif t == 'key':
                        SystemController.press_key(msg.get('key', ''))
                    elif t == 'text':
                        SystemController.type_unicode_text(msg.get('text', ''))
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        AppState.client_disconnected()


# ==========================================
# 4. ДЕСКТОПНЫЙ ИНТЕРФЕЙС (ТЕПЛЫЙ МИНИМАЛИЗМ)
# ==========================================
DESKTOP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AirTouch — Подключение</title>
  <style>
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }
    body {
      background-color: #f7f5f0;
      color: #2b2623;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 32px 16px;
      -webkit-font-smoothing: antialiased;
    }
    .container {
      max-width: 440px;
      width: 100%;
      background: #ffffff;
      border: 1px solid #e7e2d9;
      border-radius: 20px;
      padding: 40px 36px 36px 36px;
      box-shadow: 0 10px 36px rgba(43, 38, 35, 0.05), 0 1px 3px rgba(43, 38, 35, 0.03);
      text-align: center;
    }
    .header-title {
      font-size: 22px;
      font-weight: 600;
      letter-spacing: -0.02em;
      color: #2b2623;
      margin-bottom: 6px;
    }
    .header-sub {
      font-size: 14px;
      color: #766e65;
      margin-bottom: 28px;
      line-height: 1.4;
    }
    .qr-wrapper {
      background: #ffffff;
      border: 1px solid #ece7df;
      border-radius: 16px;
      padding: 16px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 20px;
    }
    .qr-wrapper img {
      display: block;
      width: 220px;
      height: 220px;
      border-radius: 6px;
    }
    .network-pills {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 20px;
      text-align: left;
    }
    .network-pill {
      background: #faf8f5;
      border: 1px solid #e7e2d9;
      border-radius: 10px;
      padding: 8px 12px;
      font-size: 13px;
      color: #554e47;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      transition: all 0.15s ease;
    }
    .network-pill:hover {
      border-color: #c9c2b7;
      background: #f4f0e8;
    }
    .network-pill.active {
      background: #2b2623;
      color: #ffffff;
      border-color: #2b2623;
    }
    .network-pill .pill-ip {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      font-weight: 500;
    }
    .status-panel {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: #faf8f5;
      border: 1px solid #e7e2d9;
      border-radius: 20px;
      padding: 6px 14px;
      font-size: 13px;
      color: #766e65;
      margin-bottom: 24px;
      transition: all 0.25s ease;
    }
    .status-panel.connected {
      background: #f0f7f3;
      border-color: #cfe3d6;
      color: #266b45;
    }
    .status-point {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #c28543;
      transition: background 0.25s ease;
    }
    .status-panel.connected .status-point {
      background: #2d8a5b;
    }
    .instructions {
      border-top: 1px solid #ece7df;
      padding-top: 20px;
      text-align: left;
      font-size: 13px;
      color: #6a625a;
      line-height: 1.5;
    }
    .instruction-row {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      margin-bottom: 10px;
    }
    .instruction-row:last-child {
      margin-bottom: 0;
    }
    .step-number {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: #f0ebe2;
      color: #6a625a;
      font-size: 11px;
      font-weight: 600;
      flex-shrink: 0;
      margin-top: 1px;
    }
    .url-display {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      color: #8c532b;
      word-break: break-all;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header-title">AirTouch</div>
    <div class="header-sub">Беспроводное управление курсором с мобильного телефона</div>

    <div class="qr-wrapper">
      <img id="qr-image" src="/qr.png?ip={{CURRENT_IP}}" alt="QR код">
    </div>

    <!-- Список доступных адаптеров ПК -->
    <div class="network-pills" id="ip-list">
      <!-- Заполняется динамически -->
    </div>

    <div>
      <div class="status-panel" id="status-panel">
        <div class="status-point"></div>
        <span id="status-label">Ожидание подключения</span>
      </div>
    </div>

    <div class="instructions">
      <div class="instruction-row">
        <div class="step-number">1</div>
        <div>Компьютер подключен по кабелю, телефон — по домашнему Wi-Fi роутера.</div>
      </div>
      <div class="instruction-row">
        <div class="step-number">2</div>
        <div>Отсканируйте QR-код камерой смартфона или откройте адрес: <span class="url-display" id="url-label">http://{{CURRENT_IP}}:{{PORT}}</span></div>
      </div>
      <div class="instruction-row">
        <div class="step-number">3</div>
        <div>Экран смартфона мгновенно станет тачпадом без установки приложений.</div>
      </div>
    </div>
  </div>

  <script>
    const serverPort = {{PORT}};
    const interfaces = {{INTERFACES_JSON}};
    let currentIP = "{{CURRENT_IP}}";

    const qrImage = document.getElementById('qr-image');
    const urlLabel = document.getElementById('url-label');
    const ipList = document.getElementById('ip-list');
    const statusPanel = document.getElementById('status-panel');
    const statusLabel = document.getElementById('status-label');

    // Отрисовка списка сетевых адаптеров, если их несколько
    if (interfaces.length > 1) {
      interfaces.forEach(iface => {
        const row = document.createElement('div');
        row.className = 'network-pill' + (iface.ip === currentIP ? ' active' : '');
        row.innerHTML = `<span>${iface.name}</span><span class="pill-ip">${iface.ip}</span>`;
        row.onclick = () => {
          document.querySelectorAll('.network-pill').forEach(p => p.classList.remove('active'));
          row.classList.add('active');
          currentIP = iface.ip;
          qrImage.src = `/qr.png?ip=${currentIP}&t=${Date.now()}`;
          urlLabel.innerText = `http://${currentIP}:${serverPort}`;
        };
        ipList.appendChild(row);
      });
    } else {
      ipList.style.display = 'none';
    }

    // Проверка статуса подключения
    setInterval(async () => {
      try {
        const res = await fetch('/status');
        const data = await res.json();
        if (data.clients > 0) {
          statusPanel.classList.add('connected');
          statusLabel.innerText = 'Устройство подключено';
        } else {
          statusPanel.classList.remove('connected');
          statusLabel.innerText = 'Ожидание подключения';
        }
      } catch (e) {}
    }, 1000);
  </script>
</body>
</html>
"""


# ==========================================
# 5. МОБИЛЬНЫЙ ИНТЕРФЕЙС (ТЕПЛЫЙ МИНИМАЛИЗМ)
# ==========================================
MOBILE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="theme-color" content="#181614">
  <title>AirTouch</title>
  <style>
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
      -webkit-user-select: none;
    }
    body, html {
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: #181614;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      color: #e5dfd7;
      touch-action: none;
      overscroll-behavior: none;
      -webkit-font-smoothing: antialiased;
    }
    #app {
      display: flex;
      flex-direction: column;
      height: 100vh;
      height: 100dvh;
      padding: 10px 12px 14px 12px;
      gap: 10px;
    }
    .top-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 42px;
      background: #23201d;
      border-radius: 12px;
      padding: 0 12px;
      border: 1px solid #332f2a;
    }
    .status-group {
      display: flex;
      align-items: center;
      gap: 7px;
      font-size: 13px;
      color: #9c9489;
    }
    .status-point {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #c28543;
      transition: background 0.3s;
    }
    .status-point.connected {
      background: #34a86b;
    }
    .top-actions {
      display: flex;
      gap: 6px;
    }
    .pill-btn {
      background: #2e2a26;
      border: 1px solid #3f3a34;
      color: #d6cfc7;
      padding: 5px 10px;
      border-radius: 8px;
      font-size: 12px;
      cursor: pointer;
    }
    .pill-btn:active {
      background: #423d37;
    }
    .touch-layout {
      position: relative;
      flex: 1;
      display: flex;
      gap: 10px;
      min-height: 0;
    }
    #trackpad {
      position: relative;
      flex: 1;
      background: #201d1a;
      border-radius: 16px;
      border: 1px solid #332f2a;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }
    #trackpad.drag-mode {
      border-color: #b56938;
      background: #27221e;
    }
    .pad-hint {
      color: #635b52;
      font-size: 13px;
      text-align: center;
      line-height: 1.6;
      pointer-events: none;
      transition: opacity 0.3s;
      max-width: 240px;
    }
    .drag-label {
      display: none;
      position: absolute;
      top: 14px;
      background: #b56938;
      color: #ffffff;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.04em;
      padding: 4px 10px;
      border-radius: 12px;
    }
    #trackpad.drag-mode .drag-label {
      display: block;
    }
    #scrollbar {
      width: 44px;
      background: #201d1a;
      border-radius: 16px;
      border: 1px solid #332f2a;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      padding: 16px 0;
      color: #635b52;
      font-size: 12px;
    }
    #scrollbar:active {
      background: #2c2723;
      color: #d6cfc7;
    }
    .touch-dot {
      position: absolute;
      width: 36px;
      height: 36px;
      margin-left: -18px;
      margin-top: -18px;
      border-radius: 50%;
      background: rgba(229, 223, 215, 0.15);
      border: 1px solid rgba(229, 223, 215, 0.25);
      pointer-events: none;
      transform: scale(0);
      transition: transform 0.1s ease-out;
    }
    .touch-dot.active {
      transform: scale(1);
    }
    .click-bar {
      display: flex;
      gap: 10px;
      height: 58px;
    }
    .click-btn {
      flex: 1;
      background: #23201d;
      border: 1px solid #332f2a;
      border-radius: 14px;
      color: #d6cfc7;
      font-size: 14px;
      font-weight: 500;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
    }
    .click-btn:active, .click-btn.pressed {
      background: #36312b;
      border-color: #554e46;
      color: #ffffff;
    }
    .click-btn.right {
      flex: 0.85;
    }

    /* Модальные окна */
    .sheet {
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(4px);
      display: none;
      align-items: flex-end;
      z-index: 100;
    }
    .sheet-body {
      width: 100%;
      background: #201d1a;
      border-radius: 20px 20px 0 0;
      padding: 20px 18px 30px 18px;
      border-top: 1px solid #332f2a;
    }
    .sheet.active {
      display: flex;
    }
    .sheet-title {
      font-size: 15px;
      font-weight: 600;
      margin-bottom: 12px;
      display: flex;
      justify-content: space-between;
      color: #e5dfd7;
    }
    .text-input-box {
      width: 100%;
      background: #181614;
      border: 1px solid #3f3a34;
      border-radius: 10px;
      color: #f1ede8;
      font-size: 15px;
      padding: 10px;
      outline: none;
      margin-bottom: 12px;
      font-family: inherit;
    }
    .key-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
    }
    .k-btn {
      background: #282420;
      border: 1px solid #38332d;
      border-radius: 8px;
      color: #d6cfc7;
      padding: 9px 4px;
      font-size: 12px;
      text-align: center;
      cursor: pointer;
    }
    .k-btn:active {
      background: #b56938;
      color: #ffffff;
    }
  </style>
</head>
<body>
  <div id="app">
    <div class="top-bar">
      <div class="status-group">
        <div class="status-point" id="status-point"></div>
        <span id="status-text">Подключение</span>
      </div>
      <div class="top-actions">
        <button class="pill-btn" id="sens-btn">1.5x</button>
        <button class="pill-btn" id="open-text-btn">Текст</button>
        <button class="pill-btn" id="open-keys-btn">Клавиши</button>
      </div>
    </div>

    <div class="touch-layout">
      <div id="trackpad">
        <div class="touch-dot" id="touch-dot"></div>
        <div class="drag-label">Перетаскивание</div>
        <div class="pad-hint" id="pad-hint">
          1 палец — курсор<br>
          Тап — левый клик<br>
          2 пальца — правый клик и скролл<br>
          Зажатие — перетаскивание
        </div>
      </div>
      <div id="scrollbar">
        <span>Вверх</span>
        <span>Скролл</span>
        <span>Вниз</span>
      </div>
    </div>

    <div class="click-bar">
      <button class="click-btn" id="lmb-btn">Левая кнопка</button>
      <button class="click-btn right" id="rmb-btn">Правая кнопка</button>
    </div>
  </div>

  <!-- Окно текста -->
  <div class="sheet" id="text-sheet">
    <div class="sheet-body">
      <div class="sheet-title">
        <span>Ввод текста на компьютер</span>
        <span style="cursor:pointer;" onclick="closeSheets()">Закрыть</span>
      </div>
      <textarea id="text-box" class="text-input-box" rows="3" placeholder="Введите текст или включите диктовку голосом..."></textarea>
      <button class="click-btn" style="height:44px;background:#b56938;color:#fff;border:none;" id="send-text-btn">Отправить</button>
    </div>
  </div>

  <!-- Окно клавиш -->
  <div class="sheet" id="keys-sheet">
    <div class="sheet-body">
      <div class="sheet-title">
        <span>Быстрые действия</span>
        <span style="cursor:pointer;" onclick="closeSheets()">Закрыть</span>
      </div>
      <div class="key-grid">
        <div class="k-btn" data-key="enter">Enter</div>
        <div class="k-btn" data-key="backspace">Backspace</div>
        <div class="k-btn" data-key="escape">Esc</div>
        <div class="k-btn" data-key="space">Space</div>
        <div class="k-btn" data-key="win">Win</div>
        <div class="k-btn" data-key="tab">Tab</div>
        <div class="k-btn" data-key="ctrl_z">Ctrl+Z</div>
        <div class="k-btn" data-key="alt_tab">Alt+Tab</div>
        <div class="k-btn" data-key="volume_down">Громкость -</div>
        <div class="k-btn" data-key="volume_up">Громкость +</div>
        <div class="k-btn" data-key="volume_mute">Звук</div>
        <div class="k-btn" data-key="media_play_pause">Пауза</div>
      </div>
    </div>
  </div>

  <script>
    const SENS = [1.0, 1.5, 2.0, 2.5, 3.0];
    let sensIdx = 1;
    let sensitivity = SENS[sensIdx];

    const sensBtn = document.getElementById('sens-btn');
    sensBtn.onclick = () => {
      sensIdx = (sensIdx + 1) % SENS.length;
      sensitivity = SENS[sensIdx];
      sensBtn.innerText = `${sensitivity.toFixed(1)}x`;
      vibrate(8);
    };

    function vibrate(ms) {
      if (navigator.vibrate) {
        try { navigator.vibrate(ms); } catch (e) {}
      }
    }

    // Блокировка засыпания экрана
    async function preventSleep() {
      try {
        if ('wakeLock' in navigator) await navigator.wakeLock.request('screen');
      } catch (e) {}
    }
    document.addEventListener('touchstart', preventSleep, { once: true });

    // WebSocket
    let ws = null;
    const statusPoint = document.getElementById('status-point');
    const statusText = document.getElementById('status-text');

    function connectWS() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${proto}//${window.location.host}/ws`);

      ws.onopen = () => {
        statusPoint.classList.add('connected');
        statusText.innerText = 'Подключено';
      };

      ws.onclose = () => {
        statusPoint.classList.remove('connected');
        statusText.innerText = 'Поиск...';
        setTimeout(connectWS, 1500);
      };

      ws.onerror = () => {
        try { ws.close(); } catch(e) {}
      };
    }
    connectWS();

    function sendCmd(data) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
      } else {
        if (data.type === 'move') {
          fetch(`/m?x=${data.dx}&y=${data.dy}`).catch(() => {});
        } else if (data.type === 'click') {
          fetch(`/c?b=${data.btn === 'left' ? 1 : 2}`).catch(() => {});
        }
      }
    }

    // Тачпад
    const pad = document.getElementById('trackpad');
    const dot = document.getElementById('touch-dot');
    const padHint = document.getElementById('pad-hint');

    let touchStartTime = 0;
    let touchStartX = 0;
    let touchStartY = 0;
    let lastX = 0;
    let lastY = 0;
    let isDragMode = false;
    let dragTimer = null;
    let twoFingerLastY = 0;
    let lastTapTime = 0;

    pad.addEventListener('touchstart', e => {
      e.preventDefault();
      padHint.style.opacity = '0';
      const touches = e.touches;

      if (touches.length === 1) {
        const t = touches[0];
        const rect = pad.getBoundingClientRect();
        touchStartTime = Date.now();
        touchStartX = t.clientX;
        touchStartY = t.clientY;
        lastX = t.clientX;
        lastY = t.clientY;

        dot.style.left = `${t.clientX - rect.left}px`;
        dot.style.top = `${t.clientY - rect.top}px`;
        dot.classList.add('active');

        dragTimer = setTimeout(() => {
          isDragMode = true;
          pad.classList.add('drag-mode');
          sendCmd({ type: 'down', btn: 'left' });
          vibrate([15, 30]);
        }, 360);

      } else if (touches.length === 2) {
        clearTimeout(dragTimer);
        if (isDragMode) {
          isDragMode = false;
          pad.classList.remove('drag-mode');
          sendCmd({ type: 'up', btn: 'left' });
        }
        twoFingerLastY = (touches[0].clientY + touches[1].clientY) / 2;
        touchStartTime = Date.now();
      }
    }, { passive: false });

    pad.addEventListener('touchmove', e => {
      e.preventDefault();
      const touches = e.touches;

      if (touches.length === 1) {
        const t = touches[0];
        const rect = pad.getBoundingClientRect();
        dot.style.left = `${t.clientX - rect.left}px`;
        dot.style.top = `${t.clientY - rect.top}px`;

        let rawDx = t.clientX - lastX;
        let rawDy = t.clientY - lastY;

        const dist = Math.hypot(t.clientX - touchStartX, t.clientY - touchStartY);
        if (dist > 8 && dragTimer) {
          clearTimeout(dragTimer);
          dragTimer = null;
        }

        const speed = Math.hypot(rawDx, rawDy);
        let accel = 1.0;
        if (speed > 8) accel = 1.25;
        if (speed > 22) accel = 1.6;

        let dx = rawDx * sensitivity * accel;
        let dy = rawDy * sensitivity * accel;

        lastX = t.clientX;
        lastY = t.clientY;

        sendCmd({ type: 'move', dx: dx, dy: dy });

      } else if (touches.length === 2) {
        const curMidY = (touches[0].clientY + touches[1].clientY) / 2;
        const deltaY = curMidY - twoFingerLastY;
        twoFingerLastY = curMidY;

        sendCmd({ type: 'scroll', dy: deltaY * 0.25 });
      }
    }, { passive: false });

    pad.addEventListener('touchend', e => {
      e.preventDefault();
      clearTimeout(dragTimer);
      dot.classList.remove('active');

      if (isDragMode) {
        isDragMode = false;
        pad.classList.remove('drag-mode');
        sendCmd({ type: 'up', btn: 'left' });
        vibrate(10);
        return;
      }

      const duration = Date.now() - touchStartTime;

      if (e.touches.length === 0) {
        const dist = Math.hypot(lastX - touchStartX, lastY - touchStartY);
        if (duration < 240 && dist < 10) {
          const now = Date.now();
          if (now - lastTapTime < 280) {
            sendCmd({ type: 'click', btn: 'double' });
            vibrate([10, 20, 10]);
          } else {
            sendCmd({ type: 'click', btn: 'left' });
            vibrate(10);
          }
          lastTapTime = now;
        }
      } else if (e.changedTouches.length === 1 && e.touches.length === 1 && duration < 240) {
        sendCmd({ type: 'click', btn: 'right' });
        vibrate([15, 25]);
      }
    }, { passive: false });

    // Полоса скролла
    const scrollbar = document.getElementById('scrollbar');
    let scrollStartY = 0;

    scrollbar.addEventListener('touchstart', e => {
      e.preventDefault();
      scrollStartY = e.touches[0].clientY;
      vibrate(8);
    }, { passive: false });

    scrollbar.addEventListener('touchmove', e => {
      e.preventDefault();
      const curY = e.touches[0].clientY;
      const dy = curY - scrollStartY;
      scrollStartY = curY;
      sendCmd({ type: 'scroll', dy: dy * 0.45 });
    }, { passive: false });

    // Кнопки мыши
    const lmbBtn = document.getElementById('lmb-btn');
    const rmbBtn = document.getElementById('rmb-btn');

    lmbBtn.addEventListener('touchstart', e => {
      e.preventDefault();
      lmbBtn.classList.add('pressed');
      sendCmd({ type: 'down', btn: 'left' });
      vibrate(15);
    }, { passive: false });

    lmbBtn.addEventListener('touchend', e => {
      e.preventDefault();
      lmbBtn.classList.remove('pressed');
      sendCmd({ type: 'up', btn: 'left' });
    }, { passive: false });

    rmbBtn.addEventListener('touchstart', e => {
      e.preventDefault();
      rmbBtn.classList.add('pressed');
      sendCmd({ type: 'down', btn: 'right' });
      vibrate(20);
    }, { passive: false });

    rmbBtn.addEventListener('touchend', e => {
      e.preventDefault();
      rmbBtn.classList.remove('pressed');
      sendCmd({ type: 'up', btn: 'right' });
    }, { passive: false });

    // Окна
    const textSheet = document.getElementById('text-sheet');
    const keysSheet = document.getElementById('keys-sheet');
    const textBox = document.getElementById('text-box');

    document.getElementById('open-text-btn').onclick = () => {
      textSheet.classList.add('active');
      setTimeout(() => textBox.focus(), 150);
      vibrate(8);
    };

    document.getElementById('open-keys-btn').onclick = () => {
      keysSheet.classList.add('active');
      vibrate(8);
    };

    function closeSheets() {
      textSheet.classList.remove('active');
      keysSheet.classList.remove('active');
      textBox.blur();
    }

    textSheet.addEventListener('click', e => { if (e.target === textSheet) closeSheets(); });
    keysSheet.addEventListener('click', e => { if (e.target === keysSheet) closeSheets(); });

    document.getElementById('send-text-btn').onclick = () => {
      const text = textBox.value;
      if (text) {
        sendCmd({ type: 'text', text: text });
        textBox.value = '';
        vibrate(15);
        closeSheets();
      }
    };

    document.querySelectorAll('.k-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const k = btn.getAttribute('data-key');
        if (k) {
          sendCmd({ type: 'key', key: k });
          vibrate(10);
        }
      });
    });
  </script>
</body>
</html>
"""


# ==========================================
# 6. HTTP СЕРВЕР И ОБРАБОТЧИКИ
# ==========================================
class TouchpadHTTPServer(http.server.BaseHTTPRequestHandler):
    primary_ip = '127.0.0.1'
    server_port = 8000
    all_interfaces = []

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            key = self.headers.get('Sec-WebSocket-Key')
            if key:
                accept_hash = base64.b64encode(hashlib.sha1(key.encode('utf-8') + WS_MAGIC).digest()).decode('utf-8')
                self.send_response(101, 'Switching Protocols')
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', accept_hash)
                self.end_headers()
                handle_websocket(self.connection, self.client_address[0])
                return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(MOBILE_HTML.encode('utf-8'))
            return

        elif path in ('/desktop', '/qr'):
            req_ip = query.get('ip', [self.primary_ip])[0]
            html = (DESKTOP_HTML
                    .replace('{{CURRENT_IP}}', req_ip)
                    .replace('{{PORT}}', str(self.server_port))
                    .replace('{{INTERFACES_JSON}}', json.dumps(self.all_interfaces, ensure_ascii=False)))

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return

        elif path == '/qr.png':
            target_ip = query.get('ip', [self.primary_ip])[0]
            url = f"http://{target_ip}:{self.server_port}"
            try:
                import qrcode
                import io
                qr = qrcode.QRCode(box_size=8, border=2)
                qr.add_data(url)
                qr.make(fit=True)
                # Теплый минималистичный оттенок темного графита вместо резкого черного
                img = qr.make_image(fill_color="#2b2623", back_color="#ffffff")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                png_bytes = buf.getvalue()

                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(png_bytes)))
                self.end_headers()
                self.wfile.write(png_bytes)
                return
            except Exception:
                self.send_error(500, "Ошибка генерации QR")
                return

        elif path == '/status':
            resp = json.dumps({
                'clients': AppState.connected_clients,
                'ip': self.primary_ip,
                'port': self.server_port
            }).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(resp)
            return

        elif path == '/m':
            try:
                x = float(query.get('x', [0])[0])
                y = float(query.get('y', [0])[0])
                SystemController.move_cursor(x, y)
            except Exception:
                pass
            self.send_response(204)
            self.end_headers()
            return

        elif path == '/c':
            btn = query.get('b', ['1'])[0]
            SystemController.click('left' if btn == '1' else 'right')
            self.send_response(204)
            self.end_headers()
            return

        else:
            self.send_error(404, "Not Found")


def run_server(port=8000, open_browser=True):
    interfaces = get_network_interfaces()
    primary_ip = interfaces[0]['ip']
    primary_name = interfaces[0]['name']

    TouchpadHTTPServer.primary_ip = primary_ip
    TouchpadHTTPServer.server_port = port
    TouchpadHTTPServer.all_interfaces = interfaces

    server = http.server.ThreadingHTTPServer(('0.0.0.0', port), TouchpadHTTPServer)

    url = f"http://{primary_ip}:{port}"
    desktop_url = f"http://localhost:{port}/desktop"

    print("\n" + "=" * 56)
    print("   AIRTOUCH WIRELESS TRACKPAD")
    print("=" * 56)
    print(f"\nВыбран сетевой адаптер: {primary_name}")
    print(f"Адрес для смартфона:    {url}")
    print(f"Экран сопряжения на ПК: {desktop_url}")

    if len(interfaces) > 1:
        print("\nДругие обнаруженные сети:")
        for idx, iface in enumerate(interfaces[1:], start=2):
            print(f"  {idx}. {iface['ip']:<15} — {iface['name']}")

    print("\nQR-код для подключения:\n")

    try:
        import qrcode
        qr = qrcode.QRCode()
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception as e:
        print(f"(QR в консоли: {e})")

    print("\n" + "-" * 56)
    print("Для завершения работы нажмите Ctrl + C")
    print("-" * 56 + "\n")

    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(desktop_url)), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()


if __name__ == '__main__':
    port = 8000
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    no_open = '--no-open' in sys.argv
    run_server(port=port, open_browser=not no_open)
